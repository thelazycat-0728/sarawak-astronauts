"""Get SPHEREx cutouts for a view, singly or in a batch, through the disk cache.

Not wired into the routes yet (route wiring comes with T-005).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Hashable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astropy.io import fits

from app.config import Settings
from app.cutouts.cache import CutoutCache, cutout_key, is_resource_error, open_cached
from app.cutouts.download import (
    StreamingSession,
    build_cutout_url,
    check_access_url,
    download_cutout,
    strip_cutout,
)
from app.cutouts.geometry import (
    cutout_size_arcmin,
    measure,
    round_position,
    validate_fov,
)
from app.errors import ApiError, NotFound, UpstreamError
from app.irsa.epochs import parse_epoch_id
from app.irsa.records import EpochQueryResult, EpochRecord, Exposure
from app.irsa.sia import HttpSession, find_epochs, validate_position

log = logging.getLogger("app.cutouts")


@dataclass(frozen=True)
class CutoutView:
    """A validated view plus the rounded centre and size used for the request."""

    ra: float
    dec: float
    fov_arcmin: float
    request_ra: float
    request_dec: float
    size_arcmin: float


def make_view(ra: object, dec: object, fov: object = 10.0) -> CutoutView:
    ra_f, dec_f = validate_position(ra, dec)
    fov_f = validate_fov(fov)
    req_ra, req_dec = round_position(ra_f, dec_f)
    return CutoutView(ra_f, dec_f, fov_f, req_ra, req_dec, cutout_size_arcmin(fov_f))


@dataclass(frozen=True)
class Cutout:
    """A cached cutout (primary, IMAGE, FLAGS, VARIANCE, ZODI, WCS-WAVE)."""

    exposure: Exposure
    epoch_id: str | None
    path: Path
    from_cache: bool
    size_bytes: int
    elapsed_s: float  # time to get the file (download or hit), excluding measurements
    wavelength_um: float | None  # at the target pixel; None if the target is off it
    coverage: float  # fraction of the north-up view on the detector, 0 to 1

    def open(self) -> fits.HDUList:
        """Open the cached file. Use as a context manager; data is read into memory
        (no memmap), so the file is free to be evicted once it is closed."""
        return fits.open(self.path, memmap=False)


@dataclass(frozen=True)
class CutoutFailure:
    exposure: Exposure
    epoch_id: str | None
    code: str  # API.md error code, for example "upstream_timeout"
    reason: str


@dataclass(frozen=True)
class CutoutBatch:
    view: CutoutView
    results: tuple[Cutout | CutoutFailure, ...]  # same order as the input
    elapsed_s: float

    @property
    def cutouts(self) -> list[Cutout]:
        return [r for r in self.results if isinstance(r, Cutout)]

    @property
    def failures(self) -> list[CutoutFailure]:
        return [r for r in self.results if isinstance(r, CutoutFailure)]

    @property
    def downloads(self) -> int:
        return sum(1 for c in self.cutouts if not c.from_cache)

    @property
    def cache_hits(self) -> int:
        return sum(1 for c in self.cutouts if c.from_cache)

    @property
    def bytes_on_disk(self) -> int:
        return sum(c.size_bytes for c in self.cutouts)


# --------------------------------------------------------------------- caches

_caches: dict[tuple[str, int], CutoutCache] = {}
_caches_guard = threading.Lock()


def default_cache(settings: Settings) -> CutoutCache:
    """One shared CutoutCache per directory and cap, so per-key locks are shared."""
    key = (str(Path(settings.cutout_cache_dir).resolve()),
           int(settings.cutout_cache_max_mb * 1024 * 1024))
    with _caches_guard:
        cache = _caches.get(key)
        if cache is None:
            cache = _caches[key] = CutoutCache(key[0], key[1])
        return cache


def _unpack(item: Exposure | EpochRecord) -> tuple[Exposure, str | None]:
    if isinstance(item, EpochRecord):
        return item.exposure, item.id
    return item, None


# ---------------------------------------------------------------- one cutout


def get_cutout(
    item: Exposure | EpochRecord,
    ra: object,
    dec: object,
    fov: object = 10.0,
    *,
    settings: Settings | None = None,
    cache: CutoutCache | None = None,
    session: StreamingSession | None = None,
    view: CutoutView | None = None,
) -> Cutout:
    """Cutout of one exposure for a view, from the cache or downloaded from IRSA.

    Raises InvalidParameter for a bad view, and UpstreamError or UpstreamTimeout
    when IRSA fails (a non-IRSA ``access_url`` is refused before any request).
    """
    settings = settings or Settings()
    view = view or make_view(ra, dec, fov)
    cache = cache or default_cache(settings)
    exposure, epoch_id = _unpack(item)
    check_access_url(exposure.access_url)
    try:
        key = cutout_key(exposure.release, exposure.obs_id, exposure.band,
                         view.request_ra, view.request_dec, view.size_arcmin)
    except ValueError as exc:
        raise UpstreamError("IRSA exposure id is not usable") from exc
    url = build_cutout_url(exposure.access_url, view.request_ra, view.request_dec,
                           view.size_arcmin)

    def create(temp: Path) -> None:
        raw = cache.temp_path(key, "raw")
        try:
            download_cutout(url, raw, settings, session)
            strip_cutout(raw, temp)
        finally:
            raw.unlink(missing_ok=True)

    started = time.monotonic()
    path, from_cache = cache.get_or_create(key, create)
    elapsed = time.monotonic() - started
    # Headers only (measure needs no image data). A file that cannot be read is
    # evicted by open_cached; a geometry problem in measure is not a damaged file,
    # so it keeps the file (re-downloading would give the same bytes).
    with open_cached(path, load_data=False) as hdul:
        try:
            wavelength, coverage = measure(hdul, view.ra, view.dec, view.fov_arcmin)
        except (KeyError, ValueError) as exc:
            raise UpstreamError("cached cutout has no usable sky geometry") from exc
    try:
        size = path.stat().st_size
    except OSError as exc:
        if is_resource_error(exc):
            raise
        raise UpstreamError("cached cutout could not be read") from exc
    return Cutout(exposure, epoch_id, path, from_cache, size, elapsed, wavelength, coverage)


# ------------------------------------------------------------------- a batch


def fetch_cutouts(
    items: Iterable[Exposure | EpochRecord],
    ra: object,
    dec: object,
    fov: object = 10.0,
    *,
    settings: Settings | None = None,
    cache: CutoutCache | None = None,
    session: StreamingSession | None = None,
) -> CutoutBatch:
    """Cutouts for many exposures with a bounded thread pool.

    A bad view raises InvalidParameter before any request. After that, one failed
    exposure never fails the batch: it comes back as a CutoutFailure.
    """
    settings = settings or Settings()
    view = make_view(ra, dec, fov)
    cache = cache or default_cache(settings)
    todo = list(items)
    started = time.monotonic()

    def one(item: Exposure | EpochRecord) -> Cutout | CutoutFailure:
        exposure, epoch_id = _unpack(item)
        try:
            return get_cutout(item, view.ra, view.dec, view.fov_arcmin, settings=settings,
                              cache=cache, session=session, view=view)
        except ApiError as exc:
            log.info("cutout %s failed: %s", exposure.obs_id, exc.message)
            return CutoutFailure(exposure, epoch_id, exc.code, exc.message)
        except Exception as exc:  # keep the batch alive whatever happens
            if is_resource_error(exc):
                raise  # out of memory or file handles: a 500, not a per-file failure
            log.exception("cutout %s failed unexpectedly", exposure.obs_id)
            return CutoutFailure(exposure, epoch_id, "internal_error", type(exc).__name__)

    if not todo:
        return CutoutBatch(view, (), 0.0)
    workers = max(1, min(int(settings.cutout_workers), len(todo)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cutout") as pool:
        results = tuple(pool.map(one, todo))
    return CutoutBatch(view, results, time.monotonic() - started)


# --------------------------------------------------------- epoch id lookups


class QueryCache:
    """Small thread-safe in-memory cache of position queries with a TTL."""

    def __init__(self, max_entries: int = 64, clock: Callable[[], float] = time.monotonic):
        self.max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        self._items: dict[Hashable, tuple[float, Any]] = {}

    def get_or_load(self, key: Hashable, ttl_s: float, load: Callable[[], Any]) -> Any:
        now = self._clock()
        with self._lock:
            entry = self._items.get(key)
            if entry is not None and entry[0] > now:
                return entry[1]
        value = load()  # errors propagate and are not cached
        with self._lock:
            self._items[key] = (self._clock() + ttl_s, value)
            if len(self._items) > self.max_entries:
                expired = [k for k, (t, _) in self._items.items() if t <= self._clock()]
                for k in expired:
                    del self._items[k]
                while len(self._items) > self.max_entries:
                    del self._items[next(iter(self._items))]  # oldest insert
        return value

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_query_cache = QueryCache()


def resolve_epoch(
    epoch_id: object,
    ra: object,
    dec: object,
    *,
    settings: Settings | None = None,
    session: HttpSession | None = None,
    query_cache: QueryCache | None = None,
) -> EpochRecord:
    """The exposure behind an epoch id, re-found by a position query at (ra, dec).

    IRSA's SIA lookup by ID is slow (112 s in T-003), so this re-runs the 3-6 s
    position query (cached for ``settings.epoch_query_ttl_s``) and matches the id.
    Raises NotFound for a malformed id or one with no match at this position.
    """
    settings = settings or Settings()
    ra_f, dec_f = validate_position(ra, dec)
    if not isinstance(epoch_id, str):
        raise NotFound("unknown epoch id")
    try:
        _release, _obs_id, band = parse_epoch_id(epoch_id)
    except ValueError as exc:
        raise NotFound("unknown epoch id") from exc
    cache = query_cache if query_cache is not None else _query_cache
    req_ra, req_dec = round_position(ra_f, dec_f)
    key = (req_ra, req_dec, band, settings.spherex_releases, settings.spherex_collections)
    result: EpochQueryResult = cache.get_or_load(
        key,
        settings.epoch_query_ttl_s,
        lambda: find_epochs(ra_f, dec_f, band, settings=settings, session=session),
    )
    for epoch in result.epochs:
        if epoch.id == epoch_id:
            return epoch
    raise NotFound(f"epoch {epoch_id} not found at this position")
