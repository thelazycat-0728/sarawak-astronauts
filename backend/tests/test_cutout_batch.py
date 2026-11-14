"""Batch fetch: bounded pool, per-exposure failures, order and cache use (offline)."""

from __future__ import annotations

import pytest
import requests

from app.cutouts import Cutout, CutoutFailure, fetch_cutouts
from app.errors import InvalidParameter
from tests.cutout_helpers import (
    FOV10,
    StreamResponse,
    StreamSession,
    fixture_epoch,
    fixture_exposure,
    make_settings,
    with_epsf,
)

RA, DEC = FOV10["ra"], FOV10["dec"]
GOOD = with_epsf(FOV10)


def routed(url: str):
    if "obs_404" in url:
        return StreamResponse(b"missing", 404)
    if "obs_slow" in url:
        return requests.ReadTimeout()
    return StreamResponse(GOOD)


def exposure(obs_id: str, **changes):
    values = {
        "access_url": f"https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/level2/{obs_id}D2.fits"
    }
    values.update(changes)
    return fixture_exposure(FOV10, obs_id=obs_id, **values)


def test_one_failure_does_not_fail_the_batch(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[routed])
    items = [
        exposure("obs_a"),
        exposure("obs_404"),
        exposure("obs_evil", access_url="https://evil.example/x.fits"),
        exposure("obs_slow"),
        fixture_epoch(FOV10),
    ]
    batch = fetch_cutouts(items, RA, DEC, 10, settings=settings, session=session)
    kinds = [type(r) for r in batch.results]
    assert kinds == [Cutout, CutoutFailure, CutoutFailure, CutoutFailure, Cutout]
    assert [r.exposure.obs_id for r in batch.results] == [
        "obs_a", "obs_404", "obs_evil", "obs_slow", FOV10["exposure"]["obs_id"]
    ]
    fail_404, fail_evil, fail_slow = batch.failures
    assert (fail_404.code, fail_404.reason) == ("upstream_error", "IRSA cutout returned HTTP 404")
    assert fail_evil.code == "upstream_error" and "non-IRSA" in fail_evil.reason
    assert fail_slow.code == "upstream_timeout"
    assert batch.results[4].epoch_id == FOV10["epoch_id"]
    assert batch.downloads == 2 and batch.cache_hits == 0 and len(batch.failures) == 3
    # 1 + 1 + 0 (refused) + 3 (timeout retried twice) + 1
    assert len(session.calls) == 6
    assert batch.bytes_on_disk == sum(c.size_bytes for c in batch.cutouts)
    assert all(c.coverage == 1.0 and c.wavelength_um for c in batch.cutouts)


def test_second_batch_is_all_cache_hits(tmp_path):
    settings = make_settings(tmp_path)
    items = [exposure(f"obs_{i}") for i in range(5)]
    first = fetch_cutouts(items, RA, DEC, 10, settings=settings,
                          session=StreamSession(replies=[routed]))
    assert first.downloads == 5
    silent = StreamSession(replies=[])
    second = fetch_cutouts(items, RA, DEC, 10, settings=settings, session=silent)
    assert second.cache_hits == 5 and second.downloads == 0 and silent.calls == []


def test_pool_is_bounded_by_worker_count(tmp_path):
    settings = make_settings(tmp_path, cutout_workers=2)
    session = StreamSession(replies=[routed], delay_s=0.1)
    batch = fetch_cutouts([exposure(f"obs_{i}") for i in range(6)], RA, DEC, 10,
                          settings=settings, session=session)
    assert batch.downloads == 6
    assert session.max_active == 2


def test_default_pool_runs_four_at_once(tmp_path):
    settings = make_settings(tmp_path)
    assert settings.cutout_workers == 4
    session = StreamSession(replies=[routed], delay_s=0.2)
    fetch_cutouts([exposure(f"obs_{i}") for i in range(8)], RA, DEC, 10,
                  settings=settings, session=session)
    assert session.max_active == 4


@pytest.mark.parametrize(
    "ra, dec, fov",
    [(400.0, DEC, 10), (RA, -91.0, 10), (RA, DEC, 0.5), (RA, DEC, 31), ("1", DEC, 10)],
)
def test_bad_view_raises_before_any_request(tmp_path, ra, dec, fov):
    session = StreamSession(replies=[routed])
    with pytest.raises(InvalidParameter):
        fetch_cutouts([exposure("obs_a")], ra, dec, fov, settings=make_settings(tmp_path),
                      session=session)
    assert session.calls == []


def test_empty_batch(tmp_path):
    batch = fetch_cutouts([], RA, DEC, 10, settings=make_settings(tmp_path))
    assert batch.results == () and batch.downloads == 0


def test_unexpected_errors_become_failure_records(tmp_path, monkeypatch):
    from app.cutouts import service

    def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(service, "measure", boom)
    session = StreamSession(replies=[routed])
    batch = fetch_cutouts([exposure("obs_a")], RA, DEC, 10, settings=make_settings(tmp_path),
                          session=session)
    (failure,) = batch.failures
    assert failure.code == "internal_error" and failure.reason == "RuntimeError"
