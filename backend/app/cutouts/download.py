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
- Downloads are streamed to a file in chunks, with a total time limit and a size
  limit, and never held in memory as a whole.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import requests
from astropy.io import fits
from urllib3.exceptions import ReadTimeoutError

from app.config import Settings
from app.cutouts.geometry import KEPT_HDUS
from app.errors import ApiError, UpstreamError, UpstreamTimeout
from app.irsa.sia import USER_AGENT

log = logging.getLogger("app.cutouts")

IRSA_HOST = "irsa.ipac.caltech.edu"
CHUNK_BYTES = 64 * 1024
# A 30' cutout is about 7 MB with EPSF; anything far larger is not a cutout.
MAX_CUTOUT_BYTES = 64 * 1024 * 1024
_clock = time.monotonic  # replaced in tests


class StreamingSession(Protocol):
    def get(
        self, url: str, *, stream: bool, timeout: Any, headers: Any, allow_redirects: bool
    ) -> Any: ...


class _Retryable(Exception):
    """Internal: a transient failure (timeout, 5xx, dropped connection)."""

    def __init__(self, error: ApiError):
        super().__init__(error.message)
        self.error = error


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


def _download_once(url: str, dest: Path, settings: Settings, session: Any) -> int:
    timeout = settings.cutout_timeout_s
    deadline = _clock() + timeout
    too_slow = UpstreamTimeout(f"IRSA cutout did not arrive within {timeout:g} s")
    try:
        resp = session.get(
            url,
            stream=True,
            timeout=(timeout, timeout),
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
            raise _Retryable(UpstreamError(f"IRSA cutout returned HTTP {status}"))
        if status != 200:
            raise UpstreamError(f"IRSA cutout returned HTTP {status}")
        total = 0
        try:
            with open(dest, "wb") as out:
                for chunk in resp.iter_content(chunk_size=CHUNK_BYTES):
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
        if total == 0:
            raise UpstreamError("IRSA returned an empty cutout")
        return total
    finally:
        close = getattr(resp, "close", None)
        if close is not None:
            close()


def download_cutout(
    url: str,
    dest: Path,
    settings: Settings,
    session: StreamingSession | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Stream ``url`` into ``dest``; returns bytes written.

    Timeouts, 5xx responses and dropped connections are retried
    ``settings.cutout_retries`` times with exponential backoff. Raises
    UpstreamTimeout or UpstreamError when every attempt fails.
    """
    check_access_url(url.split("?", 1)[0])
    own_session = session is None
    sess: Any = requests.Session() if own_session else session
    attempts = 1 + max(0, settings.cutout_retries)
    try:
        for attempt in range(attempts):
            try:
                return _download_once(url, dest, settings, sess)
            except _Retryable as exc:
                if attempt + 1 >= attempts:
                    raise exc.error from exc
                wait = settings.cutout_retry_backoff_s * (2**attempt)
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
