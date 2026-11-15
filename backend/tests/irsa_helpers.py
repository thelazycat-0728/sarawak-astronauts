"""Shared helpers for the IRSA tests: recorded fixtures and a fake HTTP session."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.irsa.records import Exposure

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "irsa"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@dataclass
class FakeResponse:
    content: bytes = b""
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    error_after: BaseException | None = None  # raised after the first chunk
    chunks_read: int = 0
    closed: bool = False

    def iter_content(self, chunk_size: int = 1):
        for start in range(0, len(self.content), max(1, chunk_size)):
            self.chunks_read += 1
            yield self.content[start:start + chunk_size]
            if self.error_after is not None:
                raise self.error_after

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeSession:
    """Stands in for requests.Session: returns a canned response or raises."""

    response: FakeResponse | None = None
    error: BaseException | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def get(self, url, *, params=None, timeout=None, headers=None, stream=False):
        self.calls.append({"url": url, "params": params, "timeout": timeout,
                           "stream": stream})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def make_exposure(
    obs_id: str = "2025W34_2B_0090_4",
    *,
    band: str = "D2",
    release: str = "DR1",
    mjd: float = 60908.98,
) -> Exposure:
    collection = f"spherex_{release.lower()}"
    return Exposure(
        obs_id=obs_id,
        band=band,
        release=release,
        collection=collection,
        publisher_did=f"ivo://irsa.ipac/{collection}?{obs_id}/{band}",
        t_start_mjd=mjd,
        t_end_mjd=mjd + 115.0 / 86400.0,
        exptime_s=113.6,
        wavelength_min_um=1.1,
        wavelength_max_um=1.65,
        access_url=f"https://irsa.ipac.caltech.edu/ibe/data/x/level2_{obs_id}{band}.fits",
    )
