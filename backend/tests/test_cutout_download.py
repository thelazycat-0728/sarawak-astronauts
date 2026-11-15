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
    timeout = call["timeout"]  # one budget for connect, headers and body
    assert (timeout.connect_timeout, timeout.total) == (12.0, 12.0)
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
                        session, sleep=waits.append, jitter=lambda: 1.0)
    assert waits == [1.5, 3.0]


def test_backoff_has_jitter(tmp_path):
    settings = make_settings(tmp_path, cutout_retry_backoff_s=4.0, cutout_retries=2)
    waits: list[float] = []
    factors = iter([0.5, 1.5])
    with pytest.raises(UpstreamTimeout):
        download_cutout(IRSA_FILE + "?center=1,2d&size=0.1", tmp_path / "x", settings,
                        StreamSession(replies=[requests.ReadTimeout()]),
                        sleep=waits.append, jitter=lambda: next(factors))
    assert waits == [2.0, 12.0]
    assert make_settings(tmp_path).cutout_retry_backoff_s == 0.0  # tests never wait


@pytest.mark.parametrize(
    "header, expected",
    [("5", [5.0, 5.0]), ("120", [30.0, 30.0]), ("soon", [1.0, 2.0]), (None, [1.0, 2.0])],
)
def test_retry_after_is_a_minimum_wait(tmp_path, header, expected):
    settings = make_settings(tmp_path, cutout_retry_backoff_s=1.0, cutout_retries=2)
    headers = {"Retry-After": header} if header is not None else {}
    busy = StreamResponse(b"Server busy, retry later.", 503, headers=headers)
    waits: list[float] = []
    with pytest.raises(UpstreamError, match="HTTP 503"):
        download_cutout(IRSA_FILE + "?center=1,2d&size=0.1", tmp_path / "x", settings,
                        StreamSession(replies=[busy]), sleep=waits.append, jitter=lambda: 1.0)
    assert waits == expected


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


# ------------------------------------------- T-015: the deadline bounds an attempt


class _Sock:
    def __init__(self):
        self.timeouts: list[float] = []

    def settimeout(self, value):
        self.timeouts.append(value)


def test_each_read_may_wait_only_for_the_time_left(tmp_path, monkeypatch):
    now = [100.0]

    def clock():
        now[0] += 1.0  # every look at the clock costs a second
        return now[0]

    monkeypatch.setattr(download, "_clock", clock)
    sock = _Sock()
    raw = type("Raw", (), {"connection": type("Conn", (), {"sock": sock})()})()
    body = with_epsf(FOV3)
    reply = StreamResponse(body, chunk_size=len(body) // 3 + 1, raw=raw)
    settings = make_settings(tmp_path, cutout_timeout_s=60.0)
    download_cutout(IRSA_FILE + "?center=1,2d&size=0.1", tmp_path / "x", settings,
                    StreamSession(replies=[reply]))
    # Before each of the 3 chunks and the end of the stream, the socket gets the
    # time left: deadline 161 minus the clock (102, 104, 106, 108).
    assert sock.timeouts == [59.0, 57.0, 55.0, 53.0]


def test_attempt_stops_at_the_deadline_before_another_read(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(download, "_clock", lambda: now[0])
    sock = _Sock()
    raw = type("Raw", (), {"connection": type("Conn", (), {"sock": sock})()})()

    class Slow(StreamResponse):
        def iter_content(self, chunk_size=1):
            for chunk in super().iter_content(chunk_size):
                now[0] += 4.0  # each chunk takes 4 s to arrive
                yield chunk

    body = with_epsf(FOV3)

    class OneReply:
        def get(self, url, **kwargs):
            return Slow(body, chunk_size=1024, raw=raw)

    settings = make_settings(tmp_path, cutout_timeout_s=10.0, cutout_retries=0)
    with pytest.raises(UpstreamTimeout, match="within 10 s"):
        download_cutout(IRSA_FILE + "?center=1,2d&size=0.1", tmp_path / "x", settings,
                        OneReply())
    assert sock.timeouts == [10.0, 6.0, 2.0]  # never more than the time left


def test_a_stalled_real_server_ends_the_attempt_at_the_deadline(tmp_path):
    """Real sockets on localhost: headers after 1.2 s, a little body, then a stall.
    With a 2 s budget the attempt ends at about 2 s (it used to be 1.2 + 2 s)."""
    import socket
    import threading
    import time

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    done = threading.Event()

    def serve():
        conn, _ = listener.accept()
        with conn:
            conn.recv(65536)
            time.sleep(1.2)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000000\r\n\r\n" + b"x" * 1000)
            done.wait(10)  # stall until the client gives up

    th = threading.Thread(target=serve, daemon=True)
    th.start()
    settings = make_settings(tmp_path, cutout_timeout_s=2.0)
    with requests.Session() as session:
        session.trust_env = False  # no proxy for localhost
        started = time.monotonic()
        with pytest.raises(download._Retryable) as info:
            download._download_once(f"http://127.0.0.1:{port}/x", tmp_path / "x",
                                    settings, session)
        elapsed = time.monotonic() - started
    done.set()
    th.join(5)
    listener.close()
    assert isinstance(info.value.error, UpstreamTimeout)
    assert 1.9 <= elapsed < 2.7, elapsed


def _trickle_server(headers: bytes, stop):
    """A localhost HTTP server that sends ``headers`` and then one byte every
    0.1 s until ``stop`` is set (or 4 s pass)."""
    import socket
    import threading
    import time

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        conn, _ = listener.accept()
        with conn:
            conn.recv(65536)
            conn.sendall(headers)
            end = time.monotonic() + 4
            while not stop.is_set() and time.monotonic() < end:
                try:
                    conn.sendall(b"x")
                except OSError:
                    return  # the client hung up
                time.sleep(0.1)

    th = threading.Thread(target=serve, daemon=True)
    th.start()
    return listener, th


class _NoWatchdog:
    """Stands in for download._Watchdog to show what happens without it."""

    def __init__(self, sock, after_s):
        import threading

        self.fired = threading.Event()

    def cancel(self):
        pass


@pytest.mark.parametrize("headers", [
    b"HTTP/1.1 200 OK\r\nContent-Length: 1000000\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n",  # ends at EOF, no length
], ids=["content-length", "until-eof"])
@pytest.mark.parametrize("watchdog", [True, False], ids=["watchdog", "without"])
def test_a_trickling_server_ends_the_attempt_at_the_deadline(tmp_path, monkeypatch,
                                                            headers, watchdog):
    """A byte every 0.1 s keeps one 64 KB read busy: without the watchdog the
    attempt runs as long as the server trickles (the T-015 review saw 6 s for
    T = 1 s); with it, the attempt ends at T."""
    import threading
    import time

    if not watchdog:
        monkeypatch.setattr(download, "_Watchdog", _NoWatchdog)
    stop = threading.Event()
    listener, th = _trickle_server(headers, stop)
    port = listener.getsockname()[1]
    settings = make_settings(tmp_path, cutout_timeout_s=1.0)
    try:
        with requests.Session() as session:
            session.trust_env = False  # no proxy for localhost
            started = time.monotonic()
            with pytest.raises(download._Retryable) as info:
                download._download_once(f"http://127.0.0.1:{port}/x", tmp_path / "x",
                                        settings, session)
            elapsed = time.monotonic() - started
    finally:
        stop.set()
        th.join(10)
        listener.close()
    if watchdog:
        assert isinstance(info.value.error, UpstreamTimeout)
        assert 0.9 <= elapsed < 1.6, elapsed
    else:
        assert elapsed > 3.0, elapsed  # documents the bug the watchdog fixes
