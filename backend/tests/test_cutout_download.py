"""Cutout download: URL checks, EPSF removal, retries and error mapping (offline)."""

from __future__ import annotations

import numpy as np
import pytest
import requests
from astropy.io import fits
from urllib3.exceptions import ReadTimeoutError

from app.cutouts import download, get_cutout
from app.cutouts.download import build_cutout_url, check_access_url, download_cutout
from app.cutouts.geometry import KEPT_HDUS
from app.errors import UpstreamError, UpstreamTimeout
from tests.cutout_helpers import (
    FOV3,
    FOV10,
    StreamResponse,
    StreamSession,
    fixture_exposure,
    fixture_path,
    make_settings,
    ok,
    with_epsf,
    without_hdu,
)

RA, DEC = FOV10["ra"], FOV10["dec"]
IRSA_FILE = "https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/level2/x/level2_x.fits"

BAD_URLS = [
    "http://irsa.ipac.caltech.edu/ibe/data/x.fits",
    "https://evil.example/ibe/data/x.fits",
    "https://irsa.ipac.caltech.edu.evil.example/x.fits",
    "https://user@irsa.ipac.caltech.edu/x.fits",
    "https://irsa.ipac.caltech.edu:8443/x.fits",
    "https://irsa.ipac.caltech.edu:443/x.fits",
    "https://irsa.ipac.caltech.edu/x.fits?center=1,2&size=1",
    "https://irsa.ipac.caltech.edu/x.fits#frag",
    "https://irsa.ipac.caltech.edu/x y.fits",
    "https://IRSA.ipac.caltech.edu/x.fits",
    "ftp://irsa.ipac.caltech.edu/x.fits",
    "file:///etc/passwd",
    "//irsa.ipac.caltech.edu/x.fits",
    "",
]


def all_files(settings):
    from pathlib import Path

    root = Path(settings.cutout_cache_dir)
    return sorted(p.name for p in root.rglob("*")) if root.exists() else []


# ------------------------------------------------------------------- URLs


def test_irsa_url_accepted():
    assert check_access_url(IRSA_FILE) == IRSA_FILE


@pytest.mark.parametrize("url", BAD_URLS + [None, 42])
def test_non_irsa_url_rejected(url):
    with pytest.raises(UpstreamError, match="non-IRSA"):
        check_access_url(url)


@pytest.mark.parametrize("url", BAD_URLS)
def test_get_cutout_rejects_non_irsa_url_before_any_request(tmp_path, url):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV10)])
    exposure = fixture_exposure(FOV10, access_url=url)
    with pytest.raises(UpstreamError, match="non-IRSA"):
        get_cutout(exposure, RA, DEC, 10, settings=settings, session=session)
    assert session.calls == []
    assert all_files(settings) == []


def test_cutout_url_is_built_from_rounded_floats():
    url = build_cutout_url(IRSA_FILE, 269.4464, -4.7668, 15.2)
    assert url == IRSA_FILE + "?center=269.4464,-4.7668d&size=0.253333"


def test_request_uses_rounded_centre_stream_and_no_redirects(tmp_path):
    settings = make_settings(tmp_path, cutout_timeout_s=12.0)
    session = StreamSession(replies=[ok(FOV10)])
    get_cutout(fixture_exposure(FOV10), 269.44641234, 4.76679, 10, settings=settings,
               session=session)
    (call,) = session.calls
    assert call["url"] == FOV10["exposure"]["access_url"] + (
        "?center=269.4464,4.7668d&size=0.253333"
    )
    assert call["stream"] is True and call["allow_redirects"] is False
    assert call["timeout"] == (12.0, 12.0)
    assert "sarawak-astronauts" in call["headers"]["User-Agent"]


# ------------------------------------------------------------ EPSF removal


@pytest.mark.parametrize("entry", [FOV10, FOV3], ids=["fov10", "fov3"])
def test_epsf_dropped_and_required_hdus_kept_unchanged(tmp_path, entry):
    settings = make_settings(tmp_path)
    served = with_epsf(entry)
    session = StreamSession(replies=[StreamResponse(served)])
    cutout = get_cutout(fixture_exposure(entry), entry["ra"], entry["dec"],
                        entry["fov_arcmin"], settings=settings, session=session)
    assert cutout.size_bytes < len(served)
    with cutout.open() as got, fits.open(fixture_path(entry)) as want:
        assert [h.name for h in got] == ["PRIMARY", *KEPT_HDUS]
        assert "EPSF" not in [h.name for h in got]
        for name in ("IMAGE", "FLAGS", "VARIANCE", "ZODI"):
            assert got[name].data.dtype == want[name].data.dtype
            assert np.array_equal(got[name].data, want[name].data, equal_nan=True)
            assert got[name].header == want[name].header
        assert np.array_equal(got["WCS-WAVE"].data["VALUES"], want["WCS-WAVE"].data["VALUES"])
        assert got[0].header["VERSION"] == want[0].header["VERSION"]


def test_missing_required_hdu_is_an_upstream_error(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[StreamResponse(without_hdu(FOV3, "ZODI"))])
    with pytest.raises(UpstreamError, match="lacks HDUs: ZODI"):
        get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=settings, session=session)
    assert all_files(settings) == []


@pytest.mark.filterwarnings("ignore::astropy.io.fits.verify.VerifyWarning")
@pytest.mark.parametrize("body", [b"<html>Server busy</html>", b"SIMPLE  =  T" + b" " * 3000])
def test_unreadable_body_is_an_upstream_error(tmp_path, body):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[StreamResponse(body)])
    with pytest.raises(UpstreamError, match="unreadable cutout"):
        get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=settings, session=session)
    assert all_files(settings) == []


# --------------------------------------------------- retries and error mapping


@pytest.mark.parametrize(
    "error", [requests.Timeout("slow"), requests.ReadTimeout(), requests.ConnectTimeout()]
)
def test_timeouts_retried_then_mapped_to_upstream_timeout(tmp_path, error):
    settings = make_settings(tmp_path, cutout_timeout_s=7)
    session = StreamSession(replies=[error])
    with pytest.raises(UpstreamTimeout) as info:
        get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=settings, session=session)
    assert info.value.message == "IRSA cutout did not arrive within 7 s"
    assert info.value.status_code == 504
    assert len(session.calls) == 3  # 1 + 2 retries
    assert all_files(settings) == []


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_5xx_retried_then_mapped_to_upstream_error(tmp_path, status):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[StreamResponse(b"Server busy, retry later.", status)])
    with pytest.raises(UpstreamError, match=f"HTTP {status}") as info:
        get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=settings, session=session)
    assert info.value.status_code == 502
    assert len(session.calls) == 3


@pytest.mark.parametrize("status", [204, 301, 302, 400, 403, 404])
def test_other_statuses_not_retried(tmp_path, status):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[StreamResponse(with_epsf(FOV3), status)])
    with pytest.raises(UpstreamError, match=f"HTTP {status}"):
        get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=settings, session=session)
    assert len(session.calls) == 1
    assert all_files(settings) == []


def test_503_then_success(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[StreamResponse(b"busy", 503), ok(FOV3)])
    cutout = get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=settings, session=session)
    assert len(session.calls) == 2 and not cutout.from_cache


def test_connection_errors_retried_and_mapped(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[requests.ConnectionError("reset")])
    with pytest.raises(UpstreamError, match=r"request failed \(ConnectionError\)"):
        get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=settings, session=session)
    assert len(session.calls) == 3


def test_non_transport_request_errors_not_retried(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[requests.TooManyRedirects()])
    with pytest.raises(UpstreamError, match="TooManyRedirects"):
        get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=settings, session=session)
    assert len(session.calls) == 1


def test_broken_stream_retried_then_succeeds(tmp_path):
    settings = make_settings(tmp_path)
    broken = StreamResponse(with_epsf(FOV3), chunk_size=4096,
                            error_after=requests.exceptions.ChunkedEncodingError("cut"))
    session = StreamSession(replies=[broken, ok(FOV3)])
    cutout = get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=settings, session=session)
    assert len(session.calls) == 2
    with cutout.open() as hdul:
        assert [h.name for h in hdul] == ["PRIMARY", *KEPT_HDUS]


def test_read_timeout_mid_stream_maps_to_upstream_timeout(tmp_path):
    settings = make_settings(tmp_path)
    stalled = StreamResponse(
        with_epsf(FOV3), chunk_size=4096,
        error_after=requests.ConnectionError(ReadTimeoutError(None, "/x", "read timed out")),
    )
    session = StreamSession(replies=[stalled])
    with pytest.raises(UpstreamTimeout):
        get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=settings, session=session)
    assert len(session.calls) == 3
    assert all_files(settings) == []


def test_total_time_limit_while_streaming(tmp_path, monkeypatch):
    ticks = iter(range(0, 10_000, 5))
    monkeypatch.setattr(download, "_clock", lambda: float(next(ticks)))
    settings = make_settings(tmp_path, cutout_timeout_s=12)
    session = StreamSession(replies=[StreamResponse(with_epsf(FOV3), chunk_size=1024)])
    with pytest.raises(UpstreamTimeout, match="within 12 s"):
        get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=settings, session=session)
    assert len(session.calls) == 3
    assert all_files(settings) == []


def test_oversized_download_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "MAX_CUTOUT_BYTES", 10_000)
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV3)])
    with pytest.raises(UpstreamError, match="larger than expected"):
        get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=settings, session=session)
    assert len(session.calls) == 1
    assert all_files(settings) == []


def test_empty_body_rejected(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[StreamResponse(b"")])
    with pytest.raises(UpstreamError, match="empty cutout"):
        get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=settings, session=session)


def test_backoff_doubles(tmp_path):
    settings = make_settings(tmp_path, cutout_retry_backoff_s=1.5, cutout_retries=2)
    session = StreamSession(replies=[requests.ReadTimeout()])
    waits: list[float] = []
    with pytest.raises(UpstreamTimeout):
        download_cutout(IRSA_FILE + "?center=1,2d&size=0.1", tmp_path / "x", settings,
                        session, sleep=waits.append)
    assert waits == [1.5, 3.0]


def test_zero_retries_means_one_attempt(tmp_path):
    settings = make_settings(tmp_path, cutout_retries=0)
    session = StreamSession(replies=[requests.ReadTimeout()])
    with pytest.raises(UpstreamTimeout):
        get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=settings, session=session)
    assert len(session.calls) == 1


def test_default_session_is_created_and_closed(tmp_path, monkeypatch):
    created: list[StreamSession] = []

    class Factory(StreamSession):
        closed = False

        def __init__(self):
            super().__init__(replies=[ok(FOV3)])
            created.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(download.requests, "Session", Factory)
    get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=make_settings(tmp_path))
    assert len(created) == 1 and created[0].closed and len(created[0].calls) == 1
