"""The disk cache: hits, atomic writes, one download per key, eviction (offline)."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from app.cutouts import CutoutCache, cutout_key, default_cache, get_cutout
from app.cutouts.cache import STALE_TEMP_S
from app.errors import UpstreamError
from tests.cutout_helpers import (
    FOV3,
    FOV10,
    StreamResponse,
    StreamSession,
    fixture_epoch,
    fixture_exposure,
    make_settings,
    ok,
)

RA, DEC = FOV10["ra"], FOV10["dec"]


def names(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir()) if directory.exists() else []


def test_key_format_and_manifest_key():
    key = cutout_key("DR1", "2025W34_2B_0090_4", "D2", 269.4464, 4.7668, 15.2)
    assert key == "dr1_2025W34_2B_0090_4_D2_269.4464_+4.7668_15.2"
    assert FOV10["cache_key"] == key
    assert cutout_key("QR3", "x", "D6", 0.0, -56.8007, 2.5) == "qr3_x_D6_0.0000_-56.8007_2.5"


@pytest.mark.parametrize("obs_id", ["../evil", "a/b", "a\\b", "a b", ""])
def test_unsafe_key_rejected(obs_id):
    with pytest.raises(ValueError):
        cutout_key("DR1", obs_id, "D2", 1.0, 2.0, 3.0)


def test_unusable_obs_id_is_an_upstream_error_without_request(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV10)])
    with pytest.raises(UpstreamError):
        get_cutout(fixture_exposure(FOV10, obs_id="../x"), RA, DEC, 10, settings=settings,
                   session=session)
    assert session.calls == []


def test_cache_hit_makes_zero_network_calls(tmp_path):
    settings = make_settings(tmp_path)
    first = StreamSession(replies=[ok(FOV10)])
    a = get_cutout(fixture_epoch(FOV10), RA, DEC, 10, settings=settings, session=first)
    assert not a.from_cache and len(first.calls) == 1
    assert a.path.name == FOV10["cache_key"] + ".fits"
    assert a.epoch_id == FOV10["epoch_id"]

    silent = StreamSession(replies=[])  # any call would fail the test
    b = get_cutout(fixture_epoch(FOV10), RA, DEC, 10, settings=settings, session=silent)
    assert b.from_cache and silent.calls == [] and b.path == a.path
    assert (b.wavelength_um, b.coverage) == (a.wavelength_um, a.coverage)
    # A position that rounds to the same 4 decimals is the same entry.
    c = get_cutout(fixture_epoch(FOV10), RA + 0.00003, DEC - 0.00003, 10, settings=settings,
                   session=silent)
    assert c.from_cache and silent.calls == []
    assert names(Path(settings.cutout_cache_dir)) == [FOV10["cache_key"] + ".fits"]


def test_different_size_or_release_is_a_different_entry(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV10)])
    get_cutout(fixture_exposure(FOV10), RA, DEC, 10, settings=settings, session=session)
    get_cutout(fixture_exposure(FOV10), RA, DEC, 9, settings=settings, session=session)
    get_cutout(fixture_exposure(FOV10, release="QR3"), RA, DEC, 10, settings=settings,
               session=session)
    assert len(session.calls) == 3
    assert len(names(Path(settings.cutout_cache_dir))) == 3


@pytest.mark.parametrize(
    "reply",
    [StreamResponse(b"not found", 404), StreamResponse(b"<html>", 200),
     StreamResponse(b"busy", 503)],
    ids=["http-404", "not-fits", "http-503"],
)
def test_failed_download_leaves_no_cache_entry(tmp_path, reply):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[reply])
    with pytest.raises(UpstreamError):
        get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=settings, session=session)
    cache_dir = Path(settings.cutout_cache_dir)
    assert names(cache_dir) == []  # no entry and no temp files
    # The next request tries again rather than finding a broken entry.
    retry = StreamSession(replies=[ok(FOV3)])
    cutout = get_cutout(fixture_exposure(FOV3), RA, DEC, 3, settings=settings, session=retry)
    assert not cutout.from_cache and len(retry.calls) == 1


def test_two_concurrent_requests_for_one_key_download_once(tmp_path):
    settings = make_settings(tmp_path)
    gate = threading.Event()
    session = StreamSession(replies=[ok(FOV10)], gate=gate)
    results = []

    def worker():
        results.append(get_cutout(fixture_exposure(FOV10), RA, DEC, 10, settings=settings,
                                  session=session))

    t1 = threading.Thread(target=worker)
    t1.start()
    assert session.entered.wait(5)  # the first download is in progress
    t2 = threading.Thread(target=worker)
    t2.start()
    time.sleep(0.2)  # let the second request reach the per-key lock
    gate.set()
    t1.join(10)
    t2.join(10)
    assert len(session.calls) == 1
    assert len(results) == 2 and results[0].path == results[1].path
    assert sorted(r.from_cache for r in results) == [False, True]


def test_concurrent_requests_for_different_keys_run_in_parallel(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV3)], delay_s=0.3)
    threads = [
        threading.Thread(target=get_cutout,
                         args=(fixture_exposure(FOV3, obs_id=f"obs_{i}"), RA, DEC, 3),
                         kwargs={"settings": settings, "session": session})
        for i in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(session.calls) == 3 and session.max_active >= 2


def test_default_cache_is_shared_per_directory(tmp_path):
    a = default_cache(make_settings(tmp_path))
    assert default_cache(make_settings(tmp_path)) is a
    assert default_cache(make_settings(tmp_path / "other")) is not a


# ---------------------------------------------------------------- eviction


def _put(cache: CutoutCache, key: str, size: int, mtime: float | None = None) -> Path:
    path, _ = cache.get_or_create(key, lambda tmp: tmp.write_bytes(b"x" * size))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_eviction_at_cap_removes_oldest_first(tmp_path):
    cache = CutoutCache(tmp_path, max_bytes=300)
    now = time.time()
    a = _put(cache, "a", 100, now - 30)
    b = _put(cache, "b", 100, now - 20)
    c = _put(cache, "c", 100, now - 10)
    assert cache.total_bytes() == 300 and all(p.exists() for p in (a, b, c))
    d = _put(cache, "d", 100)
    assert not a.exists() and b.exists() and c.exists() and d.exists()
    assert cache.total_bytes() == 300


def test_hit_refreshes_age(tmp_path):
    cache = CutoutCache(tmp_path, max_bytes=200)
    now = time.time()
    a = _put(cache, "a", 100, now - 30)
    b = _put(cache, "b", 100, now - 20)
    assert cache.lookup("a") == a  # a is now the most recently used
    _put(cache, "c", 100)
    assert a.exists() and not b.exists()


def test_eviction_removes_several_and_keeps_the_new_file(tmp_path):
    cache = CutoutCache(tmp_path, max_bytes=250)
    now = time.time()
    for i, key in enumerate("abc"):
        _put(cache, key, 80, now - 100 + i)
    big = _put(cache, "big", 400)  # larger than the cap on its own
    assert big.exists()
    assert [p.name for p in cache.entries()] == ["big.fits"]


def test_failed_create_leaves_nothing(tmp_path):
    cache = CutoutCache(tmp_path, max_bytes=10_000)

    def create(tmp: Path) -> None:
        tmp.write_bytes(b"partial")
        raise UpstreamError("boom")

    with pytest.raises(UpstreamError):
        cache.get_or_create("k", create)
    assert names(tmp_path) == []
    assert cache.lookup("k") is None


def test_stale_temp_files_removed_on_eviction(tmp_path):
    cache = CutoutCache(tmp_path, max_bytes=10_000)
    old = tmp_path / ".k.dead.raw.tmp"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - STALE_TEMP_S - 5,) * 2)
    fresh = tmp_path / ".k.live.tmp"
    fresh.write_bytes(b"x")
    _put(cache, "k", 10)
    assert not old.exists() and fresh.exists()
    assert cache.total_bytes() == 10  # temp files do not count


def test_cap_from_settings(tmp_path):
    settings = make_settings(tmp_path, cutout_cache_max_mb=0.5)
    cache = default_cache(settings)
    assert cache.max_bytes == 512 * 1024
    assert cache.directory == Path(settings.cutout_cache_dir).resolve()


# ------------------------------------------------- T-015: Windows rename races


def test_rename_blocked_by_an_existing_final_file_is_a_hit(tmp_path, monkeypatch):
    """Windows: os.replace fails with PermissionError when another process has
    the final file open. The file is then complete, so it counts as a hit."""
    from app.cutouts import cache as cache_mod

    cache = CutoutCache(tmp_path / "c", 10**9)
    key = "dr1_x_D2_1.0000_+2.0000_10.0"
    final = cache.path_for(key)

    def other_process_wins(src, dst):
        Path(dst).write_bytes(b"written by the other process")
        raise PermissionError(13, "Access is denied", str(dst))

    monkeypatch.setattr(cache_mod, "_replace", other_process_wins)
    path, cached = cache.get_or_create(key, lambda temp: temp.write_bytes(b"mine"))
    assert (path, cached) == (final, True)
    assert final.read_bytes() == b"written by the other process"
    assert names(tmp_path / "c") == [final.name]  # our temp file is gone


def test_rename_permission_error_without_a_final_file_still_raises(tmp_path, monkeypatch):
    from app.cutouts import cache as cache_mod

    cache = CutoutCache(tmp_path / "c", 10**9)

    def denied(src, dst):
        raise PermissionError(13, "Access is denied", str(dst))

    monkeypatch.setattr(cache_mod, "_replace", denied)
    with pytest.raises(PermissionError):
        cache.get_or_create("dr1_y_D2_1.0000_+2.0000_10.0", lambda t: t.write_bytes(b"x"))
    assert names(tmp_path / "c") == []


def test_discard_file_removes_and_tolerates_missing(tmp_path):
    from app.cutouts.cache import discard_file

    f = tmp_path / "x.fits"
    f.write_bytes(b"x")
    assert discard_file(f) and not f.exists()
    assert discard_file(f)  # already gone is fine
