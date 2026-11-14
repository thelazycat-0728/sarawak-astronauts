"""Download one SPHEREx cutout from IRSA's image server (IBE) and drop EPSF.

IRSA's cutout service (https://irsa.ipac.caltech.edu/ibe/cutouts.html) takes only
``center``, ``size`` and ``gzip``. For SPHEREx it returns "a MEF file with the
same number of extensions as the parent image", so it cannot leave out the EPSF
table (about 3.9 MB, fixed size, whatever the cutout size). EPSF is therefore
streamed to disk with the rest and removed afterwards.

Safety rules:

- Only ``https://irsa.ipac.caltech.edu/...`` URLs without user info, port, query
  or fragment are fetched; anything else is refused before any request.
- The query string is built from validated floats only. Redirects are not followed,
  so a response cannot send the client to another host.
- Downloads are streamed to a file in chunks, with a time budget per attempt
  (connect, headers and body together; see ``_download_once``) and a size limit,
  and never held in memory as a whole.
"""

from __future__ import annotations

import logging
import math
import random
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import requests
from astropy.io import fits
from urllib3.exceptions import ReadTimeoutError
from urllib3.util import Timeout as Urllib3Timeout

from app.config import Settings
from app.cutouts.geometry import KEPT_HDUS
from app.cutouts.limit import irsa_slot
from app.errors import ApiError, UpstreamError, UpstreamTimeout
from app.irsa.sia import USER_AGENT

log = logging.getLogger("app.cutouts")

IRSA_HOST = "irsa.ipac.caltech.edu"
CHUNK_BYTES = 64 * 1024
# A 30' cutout is about 7 MB with EPSF; anything far larger is not a cutout.
MAX_CUTOUT_BYTES = 64 * 1024 * 1024
MAX_RETRY_AFTER_S = 30.0
_clock = time.monotonic  # replaced in tests


class StreamingSession(Protocol):
    def get(
        self, url: str, *, stream: bool, timeout: Any, headers: Any, allow_redirects: bool
    ) -> Any: ...


class _Retryable(Exception):
    """Internal: a transient failure (timeout, 5xx, dropped connection)."""

    def __init__(self, error: ApiError, retry_after_s: float = 0.0):
        super().__init__(error.message)
        self.error = error
        self.retry_after_s = retry_after_s


def _retry_after_s(resp: Any) -> float:
    """Seconds from a numeric Retry-After header (IRSA sends 5 with its 503),
    capped at MAX_RETRY_AFTER_S; 0 when absent or not a number."""
    headers = getattr(resp, "headers", None) or {}
    try:
        value = float(headers.get("Retry-After", 0))
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    return min(max(value, 0.0), MAX_RETRY_AFTER_S)


def check_access_url(url: object) -> str:
    """Return the URL if it is a plain https URL on IRSA's host; else UpstreamError."""
    if not isinstance(url, str):
        raise UpstreamError("refusing to fetch a cutout from a non-IRSA URL")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UpstreamError("refusing to fetch a cutout from a non-IRSA URL") from exc
    if (
        parts.scheme != "https"
        or parts.hostname != IRSA_HOST
        or parts.netloc != IRSA_HOST
        or port is not None
        or parts.query
        or parts.fragment
        or not parts.path.startswith("/")
        or any(c.isspace() for c in url)
    ):
        raise UpstreamError("refusing to fetch a cutout from a non-IRSA URL")
    return url


def build_cutout_url(access_url: str, ra: float, dec: float, size_arcmin: float) -> str:
    """IBE cutout URL. ``ra``, ``dec`` and ``size_arcmin`` must be validated floats."""
    base = check_access_url(access_url)
    center = f"{float(ra):.4f},{float(dec):.4f}d"
    size = f"{float(size_arcmin) / 60.0:.6f}"
    return f"{base}?center={center}&size={size}"


def _socket_of(resp: Any) -> Any | None:
    """The socket under a streamed requests response (urllib3 2.x), or None (for
    example for a test double).

    Usually the connection's socket. When the server closes the connection after
    the body (no Content-Length), http.client has already dropped the
    connection's reference, so the socket is taken from the response's file."""
    raw = getattr(resp, "raw", None)
    conn = getattr(raw, "connection", None) or getattr(raw, "_connection", None)
    sock = getattr(conn, "sock", None)
    if sock is None:
        fp = getattr(getattr(raw, "_fp", None), "fp", None)  # http.client response
        sock = getattr(getattr(fp, "raw", None), "_sock", None)  # socket.SocketIO
    return sock if callable(getattr(sock, "settimeout", None)) else None


class _Watchdog:
    """Shuts the response's socket down when the attempt deadline passes.

    A blocked ``recv`` in the download thread then returns or fails at once, so a
    server that trickles bytes cannot keep one read going past the deadline.
    ``shutdown`` (not ``close``) is used because it wakes a blocked reader on
    Linux and Windows alike; the response is closed as usual afterwards.
    """

    def __init__(self, sock: Any, after_s: float):
        self.fired = threading.Event()
        self._sock = sock
        self._timer = threading.Timer(max(0.0, after_s), self._fire)
        self._timer.daemon = True
        self._timer.start()

    def _fire(self) -> None:
        self.fired.set()
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass  # already closed: nothing left to stop

    def cancel(self) -> None:
        self._timer.cancel()


def _download_once(url: str, dest: Path, settings: Settings, session: Any) -> int:
    """One attempt, bounded by ``cutout_timeout_s`` (T) as a whole.

    Connecting and waiting for the response headers share T (a urllib3
    ``Timeout(total=T)``). While the body streams, the socket timeout is set to
    the time left before every chunk, the deadline is checked after every chunk,
    and a watchdog timer shuts the socket down at the deadline. So a server that
    stalls, or one that trickles a byte at a time (which would otherwise keep a
    single 64 KB read waiting up to the time left for every byte), ends the
    attempt at T (plus thread scheduling, milliseconds). See ``download_cutout``
    for the bound over all attempts.
    """
    timeout = settings.cutout_timeout_s
    deadline = _clock() + timeout
    too_slow = UpstreamTimeout(f"IRSA cutout did not arrive within {timeout:g} s")
    try:
        resp = session.get(
            url,
            stream=True,
            timeout=Urllib3Timeout(connect=timeout, read=timeout, total=timeout),
            headers={"User-Agent": USER_AGENT},
            allow_redirects=False,
        )
    except requests.Timeout as exc:
        raise _Retryable(too_slow) from exc
    except requests.ConnectionError as exc:
        raise _Retryable(
            UpstreamError(f"IRSA cutout request failed ({type(exc).__name__})")
        ) from exc
    except requests.RequestException as exc:
        raise UpstreamError(f"IRSA cutout request failed ({type(exc).__name__})") from exc

    try:
        status = int(getattr(resp, "status_code", 0))
        if status >= 500:
            raise _Retryable(UpstreamError(f"IRSA cutout returned HTTP {status}"),
                             _retry_after_s(resp))
        if status != 200:
            raise UpstreamError(f"IRSA cutout returned HTTP {status}")
        total = 0
        sock = _socket_of(resp)
        watchdog = None
        if sock is not None and callable(getattr(sock, "shutdown", None)):
            watchdog = _Watchdog(sock, deadline - _clock())
        try:
            total = _stream_body(resp, dest, deadline, too_slow, sock)
        except Exception as exc:
            if watchdog is not None and watchdog.fired.is_set():
                raise _Retryable(too_slow) from exc  # the watchdog cut the read off
            raise
        finally:
            if watchdog is not None:
                watchdog.cancel()
        if watchdog is not None and watchdog.fired.is_set():
            raise _Retryable(too_slow)  # a body cut short at the deadline
        if total == 0:
            raise UpstreamError("IRSA returned an empty cutout")
        return total
    finally:
        close = getattr(resp, "close", None)
        if close is not None:
            close()


def _stream_body(resp: Any, dest: Path, deadline: float, too_slow: UpstreamTimeout,
                 sock: Any | None) -> int:
    """Write the body to ``dest`` chunk by chunk; returns the bytes written."""
    total = 0
    try:
        with open(dest, "wb") as out:
            chunks = resp.iter_content(chunk_size=CHUNK_BYTES)
            while True:
                left = deadline - _clock()
                if left <= 0:
                    raise _Retryable(too_slow)
                if sock is not None:
                    sock.settimeout(left)  # the next read may wait only this long
                try:
                    chunk = next(chunks)
                except StopIteration:
                    break
                if _clock() > deadline:
                    raise _Retryable(too_slow)
                total += len(chunk)
                if total > MAX_CUTOUT_BYTES:
                    raise UpstreamError("IRSA cutout is larger than expected")
                out.write(chunk)
    except requests.Timeout as exc:
        raise _Retryable(too_slow) from exc
    except requests.ConnectionError as exc:
        if exc.args and isinstance(exc.args[0], ReadTimeoutError):
            raise _Retryable(too_slow) from exc
        raise _Retryable(
            UpstreamError(f"IRSA cutout download broke off ({type(exc).__name__})")
        ) from exc
    except requests.RequestException as exc:  # ChunkedEncodingError and similar
        raise _Retryable(
            UpstreamError(f"IRSA cutout download broke off ({type(exc).__name__})")
        ) from exc
    return total


def download_cutout(
    url: str,
    dest: Path,
    settings: Settings,
    session: StreamingSession | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = lambda: random.uniform(0.5, 1.5),
) -> int:
    """Stream ``url`` into ``dest``; returns bytes written.

    Timeouts, 5xx responses and dropped connections are retried
    ``settings.cutout_retries`` times. Retry n waits
    ``cutout_retry_backoff_s * 2**(n-1)`` times a random factor from 0.5 to 1.5, so
    parallel workers that hit IRSA's "Server busy" 503 at once do not retry in step,
    and never less than a numeric Retry-After header (capped at 30 s).
    Each attempt holds one process-wide IRSA slot (``app.cutouts.limit``); the
    wait between attempts does not.

    **Worst case** with the defaults (T = ``cutout_timeout_s`` = 60 s, 2 retries,
    backoff 5 s): each attempt ends at T (``_download_once``), the waits are at
    most 7.5 s and 15 s (backoff with the 1.5 jitter) or a Retry-After of up to
    30 s each, so one exposure takes at most 3 x 60 + 7.5 + 15 = 202.5 s, or
    3 x 60 + 30 + 30 = 240 s when IRSA sends Retry-After: 30. Time spent waiting
    for a free IRSA slot comes on top. (Before T-015 a single attempt could run
    to about 2 x T, and until T-015 fix-1 a server trickling bytes could keep one
    read going for as long as it trickled.)
    Raises UpstreamTimeout or UpstreamError when every attempt fails.
    """
    check_access_url(url.split("?", 1)[0])
    own_session = session is None
    sess: Any = requests.Session() if own_session else session
    attempts = 1 + max(0, settings.cutout_retries)
    try:
        for attempt in range(attempts):
            try:
                with irsa_slot(settings):
                    return _download_once(url, dest, settings, sess)
            except _Retryable as exc:
                if attempt + 1 >= attempts:
                    raise exc.error from exc
                backoff = settings.cutout_retry_backoff_s * (2**attempt) * jitter()
                wait = max(backoff, exc.retry_after_s)
                log.info("IRSA cutout attempt %d failed (%s); retrying in %.1f s",
                         attempt + 1, exc.error.message, wait)
                sleep(wait)
        raise AssertionError("unreachable")
    finally:
        if own_session:
            sess.close()


def strip_cutout(raw: Path, dest: Path) -> None:
    """Write the primary HDU and KEPT_HDUS of ``raw`` to ``dest`` (no EPSF).

    Headers and data are copied unchanged (no scaling is applied on read).
    Raises UpstreamError if ``raw`` is not a FITS file or lacks a kept HDU.
    """
    try:
        _write_kept_hdus(raw, dest)
    except UpstreamError:
        raise
    except Exception as exc:  # astropy raises many types for a malformed file
        raise UpstreamError("IRSA returned an unreadable cutout") from exc


def _write_kept_hdus(raw: Path, dest: Path) -> None:
    with fits.open(raw, memmap=False, do_not_scale_image_data=True) as hdul:
        names = [hdu.name for hdu in hdul]
        missing = [name for name in KEPT_HDUS if name not in names]
        if missing:
            raise UpstreamError(f"IRSA cutout lacks HDUs: {', '.join(missing)}")
        kept = fits.HDUList([hdul[0], *(hdul[name] for name in KEPT_HDUS)])
        kept.writeto(dest, output_verify="exception")
