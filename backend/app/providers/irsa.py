"""Live SPHEREx provider (``DATA_MODE=irsa``).

Request flow for ``/epochs``:

1. Look in a short in-memory cache keyed by the rounded position (4 decimals of a
   degree, as the cutout cache), fov and band. A hit returns at once.
2. Otherwise one build runs per key (concurrent requests for the same view share
   it): ``find_epochs`` (one IRSA SIA query), then
   ``build_sequence(mode="per_visit")``, which downloads one representative
   exposure per visit through the disk cache, cleans it and computes the shared
   stretch. The routes add the absolute ``frame_url`` values.
3. The request waits at most ``Settings.epochs_deadline_s`` (90 s) and then answers
   ``504 upstream_timeout``. A build that has started keeps running and fills the
   cache, so a retry a little later is fast.

``/frame/{id}.png`` uses the stateless ``render_frame`` (cached cutouts need no
IRSA call). ``/diff`` and ``/lightcurve`` answer ``503 unavailable`` until they are
implemented for live data. Every IRSA request takes a process-wide slot
(``app.cutouts.limit``). ``app.pipeline`` (reproject, dask) is imported on first
use only.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Hashable
from concurrent.futures import CancelledError, Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from app.config import Settings
from app.cutouts.geometry import round_position
from app.cutouts.limit import LimitedSiaSession
from app.errors import ApiError, Unavailable, UpstreamTimeout
from app.models import (
    DiffQuery,
    EpochBase,
    FrameQuery,
    LightcurveQuery,
    LightcurveResponse,
    ViewQuery,
)
from app.providers.base import EpochSequence

log = logging.getLogger("app.providers.irsa")

# Sequence builds running at once; more wait for a free slot (and count against
# their request's deadline). IRSA traffic is capped separately.
BUILD_SLOTS = 2


class TtlCache:
    """Small thread-safe in-memory cache with a fixed TTL and an entry cap."""

    def __init__(self, ttl_s: float, max_entries: int,
                 clock: Callable[[], float] = time.monotonic):
        self.ttl_s = ttl_s
        self.max_entries = max(1, int(max_entries))
        self._clock = clock
        self._lock = threading.Lock()
        self._items: dict[Hashable, tuple[float, Any]] = {}

    def get(self, key: Hashable) -> Any | None:
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            if entry[0] <= self._clock():
                del self._items[key]
                return None
            return entry[1]

    def put(self, key: Hashable, value: Any) -> None:
        with self._lock:
            now = self._clock()
            self._items.pop(key, None)
            self._items[key] = (now + self.ttl_s, value)
            for k in [k for k, (t, _) in self._items.items() if t <= now]:
                del self._items[k]
            while len(self._items) > self.max_entries:
                del self._items[next(iter(self._items))]  # oldest insert

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


def _no_url(epoch_id: str, vmin: float, vmax: float) -> str:
    return ""  # the routes build the real frame_url


class IrsaProvider:
    name = "irsa"

    def __init__(
        self,
        settings: Settings,
        *,
        sia_session: Any | None = None,
        cutout_session: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._settings = settings
        self._sia = LimitedSiaSession(settings, sia_session)
        self._cutout_session = cutout_session
        self._cache = TtlCache(settings.epochs_cache_ttl_s,
                               settings.epochs_cache_max_entries, clock)
        self._lock = threading.RLock()
        self._inflight: dict[Hashable, Future] = {}
        self._build_slots = threading.BoundedSemaphore(BUILD_SLOTS)

    # ------------------------------------------------------------ /epochs

    @staticmethod
    def cache_key(q: ViewQuery) -> tuple[float, float, float, str]:
        ra, dec = round_position(q.ra, q.dec)
        return ra, dec, round(q.fov, 6), q.band

    def epochs(self, q: ViewQuery) -> EpochSequence:
        key = self.cache_key(q)
        hit = self._cache.get(key)
        if hit is not None:
            log.info("/epochs %s: cache hit", key)
            return self._for_query(hit, q)
        deadline = self._settings.epochs_deadline_s
        with self._lock:
            future = self._inflight.get(key)
            if future is None:
                future = self._submit(lambda: self._build(q))
                self._inflight[key] = future
                future.add_done_callback(lambda f: self._finished(key, f))
        try:
            seq = future.result(timeout=deadline)
        except (FutureTimeout, CancelledError):
            future.cancel()  # only succeeds while the build is still queued
            raise UpstreamTimeout(
                f"IRSA did not deliver the SPHEREx data for this view within "
                f"{deadline:g} s. Try again in a minute, or use a preset target."
            ) from None
        return self._for_query(seq, q)

    def _submit(self, fn: Callable[[], EpochSequence]) -> Future:
        """Run ``fn`` on a daemon thread once a build slot is free.

        Daemon threads, so a build still running after its deadline never holds
        up server shutdown.
        """
        future: Future = Future()

        def run() -> None:
            with self._build_slots:
                if not future.set_running_or_notify_cancel():
                    return
                try:
                    future.set_result(fn())
                except ApiError as exc:  # an expected IRSA or input error
                    future.set_exception(exc)
                except Exception as exc:
                    log.exception("/epochs build failed unexpectedly")
                    future.set_exception(exc)  # the route turns it into a 500

        threading.Thread(target=run, name="epochs-build", daemon=True).start()
        return future

    def _finished(self, key: Hashable, future: Future) -> None:
        with self._lock:
            if not future.cancelled() and future.exception() is None:
                self._cache.put(key, future.result())  # before leaving in-flight
            if self._inflight.get(key) is future:
                del self._inflight[key]

    def _build(self, q: ViewQuery) -> EpochSequence:
        from app.irsa.sia import find_epochs
        from app.pipeline import build_sequence  # heavy: reproject, dask

        s = self._settings
        started = time.monotonic()
        query = find_epochs(q.ra, q.dec, q.band, settings=s, session=self._sia)
        queried = time.monotonic() - started
        seq = build_sequence(q.ra, q.dec, q.band, q.fov, frame_url=_no_url,
                             mode="per_visit", settings=s,
                             session=self._cutout_session, query=query)
        log.info(
            "/epochs %.4f %+.4f fov=%g %s: %d exposures, %d visits kept, "
            "%d skipped, %d downloads, %d cache hits, query %.1f s, total %.1f s",
            q.ra, q.dec, q.fov, q.band, len(query.epochs), seq.response.count,
            len(seq.skipped_visits), seq.downloads, seq.cache_hits, queried,
            time.monotonic() - started,
        )
        r = seq.response
        return EpochSequence(
            synthetic=False,
            target=r.target,
            fov_arcmin=r.fov_arcmin,
            band=r.band,
            image=r.image,
            stretch=r.stretch,
            releases=r.releases,
            duplicates_removed=r.duplicates_removed,
            epochs=[EpochBase.model_validate(e.model_dump(exclude={"frame_url"}))
                    for e in r.epochs],
            prerendered_diffs=[],
            any_pair_diff=False,
        )

    @staticmethod
    def _for_query(seq: EpochSequence, q: ViewQuery) -> EpochSequence:
        """Echo the requested position (a hit may come from a position that
        rounds to the same 4 decimals, at most 0.18 arcsec away)."""
        if seq.target.ra == q.ra and seq.target.dec == q.dec:
            return seq
        target = seq.target.model_copy(update={"ra": q.ra, "dec": q.dec})
        return seq.model_copy(update={"target": target})

    # ------------------------------------------------------------ other routes

    def frame_png(self, epoch_id: str, q: FrameQuery) -> bytes:
        from app.pipeline import render_frame  # heavy: reproject, dask

        return render_frame(epoch_id, q.ra, q.dec, q.fov, q.band, q.vmin, q.vmax,
                            settings=self._settings, session=self._cutout_session,
                            sia_session=self._sia)

    def diff_png(self, q: DiffQuery) -> bytes:
        raise Unavailable("Difference images are not available for live SPHEREx data "
                          "yet; use a preset target to compare frames.")

    def lightcurve(self, q: LightcurveQuery) -> LightcurveResponse:
        raise Unavailable("Light curves are not available for live SPHEREx data yet; "
                          "use a preset target.")
