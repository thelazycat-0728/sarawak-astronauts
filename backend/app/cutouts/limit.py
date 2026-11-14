"""A process-wide cap on concurrent requests to IRSA.

``Settings.irsa_max_concurrency`` (default 4) bounds how many SIA queries and
cutout downloads are in flight at once, across every request and thread. Cutout
downloads take a slot per attempt inside ``download_cutout`` (the slot is free
while a retry waits); SIA queries take one through ``LimitedSiaSession``, which
the IRSA provider passes to ``find_epochs`` and ``render_frame``.

There is one semaphore per configured limit. The API uses a single ``Settings``,
so in practice the whole process shares one semaphore.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import requests

from app.config import Settings

_slots: dict[int, threading.BoundedSemaphore] = {}
_guard = threading.Lock()


def irsa_semaphore(settings: Settings) -> threading.BoundedSemaphore:
    limit = max(1, int(settings.irsa_max_concurrency))
    with _guard:
        sem = _slots.get(limit)
        if sem is None:
            sem = _slots[limit] = threading.BoundedSemaphore(limit)
        return sem


@contextmanager
def irsa_slot(settings: Settings) -> Iterator[None]:
    """Hold one IRSA request slot for the duration of the block."""
    sem = irsa_semaphore(settings)
    sem.acquire()
    try:
        yield
    finally:
        sem.release()


class LimitedSiaSession:
    """An ``app.irsa.sia.HttpSession`` that takes an IRSA slot for each request.

    Wraps ``inner`` (for example a test double); without one, every request uses
    a fresh ``requests.Session``, as ``fetch_sia`` does by default. A streamed
    request (``stream=True``) has its body read inside the slot, with the size
    cap of ``app.irsa.sia.read_capped``, and comes back already buffered.
    """

    def __init__(self, settings: Settings, inner: Any | None = None):
        self._settings = settings
        self._inner = inner

    def get(self, url: str, *, params: Any, timeout: Any, headers: Any,
            stream: bool = False) -> Any:
        extra = {"stream": True} if stream else {}
        with irsa_slot(self._settings):
            if self._inner is not None:
                resp = self._inner.get(url, params=params, timeout=timeout,
                                       headers=headers, **extra)
                return self._buffer(resp, timeout) if stream else resp
            with requests.Session() as session:
                resp = session.get(url, params=params, timeout=timeout, headers=headers,
                                   **extra)
                return self._buffer(resp, timeout) if stream else resp

    def _buffer(self, resp: Any, timeout: Any) -> Any:
        from app.irsa.sia import BufferedResponse, read_capped

        try:
            content = read_capped(resp, self._settings.irsa_sia_max_bytes,
                                  float(timeout) if isinstance(timeout, int | float)
                                  else self._settings.irsa_timeout_s)
            return BufferedResponse(int(getattr(resp, "status_code", 0)),
                                    getattr(resp, "headers", None), content)
        finally:
            close = getattr(resp, "close", None)
            if close is not None:
                close()
