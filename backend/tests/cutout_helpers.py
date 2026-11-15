"""Helpers for the cutout tests: recorded cutout fixtures and a fake streaming session."""

from __future__ import annotations

import io
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits

from app.config import Settings
from app.irsa.records import EpochRecord, Exposure

CUTOUT_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cutouts"
MANIFEST = json.loads((CUTOUT_FIXTURES / "cutouts.json").read_text(encoding="utf-8"))
FOV10 = MANIFEST[0]  # barnard_d2_fov10.fits
FOV3 = MANIFEST[1]  # barnard_d2_fov3.fits


def fixture_path(entry: dict) -> Path:
    return CUTOUT_FIXTURES / entry["file"]


def fixture_exposure(entry: dict, **changes: Any) -> Exposure:
    data = dict(entry["exposure"])
    data.update(changes)
    return Exposure(**data)


def fixture_epoch(entry: dict) -> EpochRecord:
    return EpochRecord(id=entry["epoch_id"], visit=1, exposure=fixture_exposure(entry))


def with_epsf(entry: dict) -> bytes:
    """The fixture as IRSA serves it: an EPSF table between ZODI and WCS-WAVE."""
    with fits.open(fixture_path(entry)) as hdul:
        epsf = fits.BinTableHDU.from_columns(
            [fits.Column(name="PSF", format="16D", array=np.ones((4, 16)))], name="EPSF"
        )
        out = fits.HDUList([hdul[0], hdul[1], hdul[2], hdul[3], hdul[4], epsf, hdul[5]])
        buf = io.BytesIO()
        out.writeto(buf)
    return buf.getvalue()


def without_hdu(entry: dict, name: str) -> bytes:
    with fits.open(fixture_path(entry)) as hdul:
        out = fits.HDUList([h for h in hdul if h.name != name])
        buf = io.BytesIO()
        out.writeto(buf)
    return buf.getvalue()


def make_settings(tmp_path: Path, **changes: Any) -> Settings:
    values: dict[str, Any] = {
        "cutout_cache_dir": str(tmp_path / "cache"),
        "cutout_retry_backoff_s": 0.0,
    }
    values.update(changes)
    return Settings(**values)


@dataclass
class StreamResponse:
    """Stands in for a streamed requests.Response."""

    content: bytes = b""
    status_code: int = 200
    chunk_size: int = 32 * 1024
    error_after: BaseException | None = None  # raised after the first chunk
    headers: dict[str, str] = field(default_factory=dict)
    closed: bool = False
    raw: Any = None  # stands in for urllib3's response (raw.connection.sock)

    def iter_content(self, chunk_size: int = 1):
        for start in range(0, len(self.content), self.chunk_size):
            yield self.content[start:start + self.chunk_size]
            if self.error_after is not None:
                raise self.error_after

    def close(self) -> None:
        self.closed = True


Reply = StreamResponse | BaseException | Callable[[str], "StreamResponse | BaseException"]


@dataclass
class StreamSession:
    """Fake requests.Session for cutout downloads (thread-safe).

    ``replies`` are used in order, one per call; the last one repeats. A reply can
    be a response, an exception to raise, or a function of the URL returning one.
    ``delay_s`` and ``gate`` slow a call down to test concurrency.
    """

    replies: list[Reply] = field(default_factory=list)
    delay_s: float = 0.0
    gate: threading.Event | None = None
    entered: threading.Event = field(default_factory=threading.Event)
    calls: list[dict[str, Any]] = field(default_factory=list)
    active: int = 0
    max_active: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, url, *, stream=False, timeout=None, headers=None, allow_redirects=True):
        with self._lock:
            index = len(self.calls)
            self.calls.append({"url": url, "stream": stream, "timeout": timeout,
                               "allow_redirects": allow_redirects, "headers": headers})
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.entered.set()
            if self.gate is not None:
                assert self.gate.wait(10), "test gate never opened"
            if self.delay_s:
                time.sleep(self.delay_s)
            assert self.replies, "unexpected network call"
            reply = self.replies[min(index, len(self.replies) - 1)]
            if callable(reply) and not isinstance(reply, StreamResponse | BaseException):
                reply = reply(url)
            if isinstance(reply, BaseException):
                raise reply
            # A fresh copy, so one canned response can serve many calls.
            return StreamResponse(reply.content, reply.status_code, reply.chunk_size,
                                  reply.error_after, dict(reply.headers), raw=reply.raw)
        finally:
            with self._lock:
                self.active -= 1


def ok(entry: dict) -> StreamResponse:
    return StreamResponse(with_epsf(entry))
