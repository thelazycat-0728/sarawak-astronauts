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
   cache, so a retry a little later is fast. A build still queued is cancelled
   when its last waiting request gives up.

What is cached:

- a full sequence: ``epochs_cache_ttl_s`` (600 s);
- a sequence that lost a visit because every download (or clean) for it failed:
  ``epochs_cache_degraded_ttl_s`` (30 s);
- no exposures at the position (``count: 0``, API.md "no coverage"): 600 s;
- exposures exist but every frame failed to download or to be cleaned (an
  unreadable cached file is removed, so the retry downloads it again): not
  cached, and answered ``502 upstream_error`` (``504 upstream_timeout`` when every
  failure was a timeout), so the client falls back to presets instead of showing
  "no data".

Builds run on at most ``BUILD_SLOTS`` (2) worker threads; more builds wait in a
queue without a thread of their own. At most ``epochs_max_waiters`` (16) requests
wait for builds at once; more get ``503 unavailable`` at once, so waiting requests
cannot use up the worker threads and the queue never holds more than that many
live builds.

``/frame/{id}.png`` uses the stateless ``render_frame`` (cached cutouts need no
IRSA call) with an overall ``frame_deadline_s`` (60 s). At most
``frame_max_concurrent`` (8) renders run at once; a request beyond that waits up
to ``frame_slot_wait_s`` (3 s) for a free slot (the wait counts against the
deadline) and then answers ``503 unavailable``. A render that passed its deadline keeps its slot until it ends, so slow
renders cannot pile up threads. A well-formed id that resolves to nothing (404)
is remembered for ``frame_not_found_ttl_s`` (60 s) per rounded position and band,
so repeating it costs no IRSA query. ``/diff`` and ``/lightcurve`` answer
``503 unavailable`` until they are implemented for live data. Every IRSA request
takes a process-wide slot (``app.cutouts.limit``). ``app.pipeline`` (reproject,
dask) is imported on first use only.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Hashable
from concurrent.futures import CancelledError, Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.cutouts import QueryCache
from app.cutouts.geometry import round_position
from app.cutouts.limit import LimitedSiaSession
from app.errors import ApiError, NotFound, Unavailable, UpstreamError, UpstreamTimeout
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

# Sequence builds running at once (each on one worker thread); more wait in a
# queue and count against their request's deadline. IRSA traffic is capped
# separately.
BUILD_SLOTS = 2
BUILD_WORKER_IDLE_S = 5.0  # an idle build worker thread exits after this long
NOT_FOUND_MAX_ENTRIES = 256
FALLBACK_HINT = "Try again in a minute, or use a preset target."
# Per-visit outcomes that mean "IRSA's data did not reach us", not "no data".
FAILED_OUTCOMES = ("download_failed", "clean_failed")


class TtlCache:
    """Small thread-safe in-memory cache with a default TTL and an entry cap."""

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

    def put(self, key: Hashable, value: Any, ttl_s: float | None = None) -> None:
        with self._lock:
            now = self._clock()
            self._items.pop(key, None)
            self._items[key] = (now + (self.ttl_s if ttl_s is None else ttl_s), value)
            for k in [k for k, (t, _) in self._items.items() if t <= now]:
                del self._items[k]
            while len(self._items) > self.max_entries:
                del self._items[next(iter(self._items))]  # oldest insert

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


@dataclass(frozen=True)
class Built:
    sequence: EpochSequence
    ttl_s: float


def _no_url(epoch_id: str, vmin: float, vmax: float) -> str:
    return ""  # the routes build the real frame_url


def _resolve(future: Future, fn: Callable[[], Any], what: str) -> None:
    """Run ``fn`` and resolve ``future`` with its result or exception, whatever
    it raises, so nobody waits on the future forever."""
    try:
        future.set_result(fn())
    except Exception as exc:  # ApiError (IRSA, input) or a bug; the route maps it
        if not isinstance(exc, ApiError):
            log.exception("%s failed unexpectedly", what)
        future.set_exception(exc)
    except BaseException as exc:  # SystemExit and similar, raised inside fn
        log.exception("%s aborted", what)
        err = RuntimeError(f"{what} aborted ({type(exc).__name__})")
        err.__cause__ = exc
        future.set_exception(err)  # a plain 500 for the waiting requests


def _download_failure(seq: Any, visits: set[int] | None = None) -> tuple[int, bool]:
    """(number of failed downloads or cleans, all of them timeouts) among the
    per-visit attempts, limited to ``visits`` when given."""
    failed = [a for a in seq.attempts if a.outcome in FAILED_OUTCOMES
              and (visits is None or a.visit in visits)]
    timeouts = all(a.detail.startswith("upstream_timeout") for a in failed)
    return len(failed), bool(failed) and timeouts


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
        self._waiters: dict[Future, int] = {}  # requests waiting on each build
        self._build_slots = threading.BoundedSemaphore(BUILD_SLOTS)
        self._queue: queue.SimpleQueue[tuple[Future, Callable[[], Built]]] = \
            queue.SimpleQueue()
        self._workers: list[threading.Thread] = []
        self._frame_cond = threading.Condition(threading.Lock())
        self._rendering = 0  # frame renders holding a slot
        self._frames_waiting = 0  # frame requests waiting for a slot
        self._not_found = TtlCache(settings.frame_not_found_ttl_s, NOT_FOUND_MAX_ENTRIES,
                                   clock)
        self._query_cache = QueryCache(clock=clock)

    # ------------------------------------------------------------ /epochs

    @staticmethod
    def cache_key(q: ViewQuery) -> tuple[float, float, float, str]:
        ra, dec = round_position(q.ra, q.dec)
        return ra, dec, round(q.fov, 6), q.band

    @property
    def waiting(self) -> int:
        """Requests currently waiting for a build."""
        with self._lock:
            return sum(self._waiters.values())

    def epochs(self, q: ViewQuery) -> EpochSequence:
        key = self.cache_key(q)
        hit = self._cache.get(key)
        if hit is not None:
            log.info("/epochs %s: cache hit", key)
            return self._for_query(hit, q)
        s = self._settings
        with self._lock:
            if sum(self._waiters.values()) >= max(1, int(s.epochs_max_waiters)):
                raise Unavailable(
                    "Too many live requests are waiting for IRSA right now. "
                    + FALLBACK_HINT)
            future = self._inflight.get(key)
            if future is None or (future.done() and (
                    future.cancelled() or future.exception() is not None)):
                # A failed build may still be listed for a moment, until its
                # done callback runs; never hand its error to a new request.
                future = self._submit(lambda: self._build(q))
                self._inflight[key] = future
                future.add_done_callback(lambda f: self._finished(key, f))
            self._waiters[future] = self._waiters.get(future, 0) + 1
        timed_out = False
        try:
            built: Built = future.result(timeout=s.epochs_deadline_s)
        except (FutureTimeout, CancelledError):
            timed_out = True
        finally:
            with self._lock:
                left = self._waiters.get(future, 1) - 1
                if left > 0:
                    self._waiters[future] = left
                else:
                    self._waiters.pop(future, None)
                    if timed_out:
                        future.cancel()  # only succeeds while the build is queued
        if timed_out:
            raise UpstreamTimeout(
                f"IRSA did not deliver the SPHEREx data for this view within "
                f"{s.epochs_deadline_s:g} s. " + FALLBACK_HINT)
        return self._for_query(built.sequence, q)

    def _submit(self, fn: Callable[[], Built]) -> Future:
        """Queue ``fn`` for one of the ``BUILD_SLOTS`` worker threads.

        No thread is started per build: a queued build is only a future and a
        callable, and it can be cancelled until a worker starts it. Workers are
        daemon threads, so a build still running after its deadline never holds
        up server shutdown, and they exit after ``BUILD_WORKER_IDLE_S`` idle.
        """
        future: Future = Future()
        self._queue.put((future, fn))
        with self._lock:
            self._workers = [w for w in self._workers if w.is_alive()]
            while len(self._workers) < BUILD_SLOTS:
                worker = threading.Thread(target=self._work, name="epochs-build",
                                          daemon=True)
                self._workers.append(worker)
                worker.start()
        return future

    def _work(self) -> None:
        me = threading.current_thread()
        while True:
            try:
                future, fn = self._queue.get(timeout=BUILD_WORKER_IDLE_S)
            except queue.Empty:
                with self._lock:
                    if self._queue.empty():
                        if me in self._workers:
                            self._workers.remove(me)
                        return
                continue
            with self._build_slots:
                if future.set_running_or_notify_cancel():
                    _resolve(future, fn, "/epochs build")

    @property
    def build_threads(self) -> int:
        """Build worker threads alive now (never more than BUILD_SLOTS)."""
        with self._lock:
            return sum(1 for w in self._workers if w.is_alive())

    def _finished(self, key: Hashable, future: Future) -> None:
        with self._lock:
            if not future.cancelled() and future.exception() is None:
                built: Built = future.result()
                self._cache.put(key, built.sequence, built.ttl_s)  # before leaving
            if self._inflight.get(key) is future:
                del self._inflight[key]

    def _build(self, q: ViewQuery) -> Built:
        from app.irsa.sia import find_epochs
        from app.pipeline import build_sequence  # heavy: reproject, dask

        s = self._settings
        started = time.monotonic()
        query = find_epochs(q.ra, q.dec, q.band, settings=s, session=self._sia)
        queried = time.monotonic() - started
        seq = build_sequence(q.ra, q.dec, q.band, q.fov, frame_url=_no_url,
                             mode="per_visit", settings=s,
                             session=self._cutout_session, query=query)
        r = seq.response
        log.info(
            "/epochs %.4f %+.4f fov=%g %s: %d exposures, %d visits kept, "
            "%d skipped, %d downloads, %d cache hits, query %.1f s, total %.1f s",
            q.ra, q.dec, q.fov, q.band, len(query.epochs), r.count,
            len(seq.skipped_visits), seq.downloads, seq.cache_hits, queried,
            time.monotonic() - started,
        )
        ttl = s.epochs_cache_ttl_s
        if query.epochs and r.count == 0:
            failed, timeouts = _download_failure(seq)
            if failed:
                cls = UpstreamTimeout if timeouts else UpstreamError
                raise cls(f"IRSA did not deliver any of the {failed} SPHEREx cutouts "
                          f"tried for this view. " + FALLBACK_HINT)
        elif seq.skipped_visits and _download_failure(seq, set(seq.skipped_visits))[0]:
            ttl = s.epochs_cache_degraded_ttl_s
            log.info("/epochs: visits %s lost to failed downloads; cached for %g s",
                     seq.skipped_visits, ttl)
        return Built(EpochSequence(
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
        ), ttl)

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
        """``render_frame`` with an overall deadline and a concurrency cap.

        Beyond the cap, the request waits up to ``frame_slot_wait_s`` for a slot,
        then answers 503. On timeout the render keeps running on its daemon
        thread (and keeps its slot) and fills the cutout cache for a retry. A 404
        is remembered for ``frame_not_found_ttl_s``.
        """
        s = self._settings
        ra, dec = round_position(q.ra, q.dec)
        miss_key = (epoch_id, ra, dec, q.band)
        known_missing = self._not_found.get(miss_key)
        if known_missing is not None:
            raise NotFound(known_missing)
        started = time.monotonic()
        wait = max(0.0, min(s.frame_slot_wait_s, s.frame_deadline_s))
        if not self._take_frame_slot(wait):
            raise Unavailable("Too many live SPHEREx frames are being rendered right "
                              "now. " + FALLBACK_HINT)
        # The wait for a slot counts against the frame deadline.
        left = max(0.0, s.frame_deadline_s - (time.monotonic() - started))

        def render() -> bytes:
            from app.pipeline import render_frame  # heavy: reproject, dask

            return render_frame(epoch_id, q.ra, q.dec, q.fov, q.band, q.vmin, q.vmax,
                                settings=s, session=self._cutout_session,
                                sia_session=self._sia, query_cache=self._query_cache)

        def run() -> None:
            try:
                _resolve(future, render, "/frame render")
            finally:
                self._free_frame_slot()

        future: Future = Future()
        future.set_running_or_notify_cancel()
        try:
            threading.Thread(target=run, name="frame-render", daemon=True).start()
        except BaseException:
            self._free_frame_slot()
            raise
        try:
            return future.result(timeout=left)
        except FutureTimeout:
            raise UpstreamTimeout(
                f"IRSA did not deliver this SPHEREx frame within {s.frame_deadline_s:g} "
                "s. " + FALLBACK_HINT) from None
        except NotFound as exc:
            self._not_found.put(miss_key, exc.message)
            raise

    def _take_frame_slot(self, wait_s: float) -> bool:
        """Take a render slot, waiting up to ``wait_s`` for one to free up."""
        cap = max(1, int(self._settings.frame_max_concurrent))
        with self._frame_cond:
            self._frames_waiting += 1
            try:
                if not self._frame_cond.wait_for(lambda: self._rendering < cap,
                                                 timeout=wait_s):
                    return False
                self._rendering += 1
                return True
            finally:
                self._frames_waiting -= 1

    def _free_frame_slot(self) -> None:
        with self._frame_cond:
            self._rendering -= 1
            self._frame_cond.notify()

    @property
    def frames_rendering(self) -> int:
        """Frame renders holding a slot now (including ones past their deadline)."""
        with self._frame_cond:
            return self._rendering

    @property
    def frames_waiting(self) -> int:
        """Frame requests waiting for a render slot now."""
        with self._frame_cond:
            return self._frames_waiting

    def diff_png(self, q: DiffQuery) -> bytes:
        raise Unavailable("Difference images are not available for live SPHEREx data "
                          "yet; use a preset target to compare frames.")

    def lightcurve(self, q: LightcurveQuery) -> LightcurveResponse:
        raise Unavailable("Light curves are not available for live SPHEREx data yet; "
                          "use a preset target.")
