"""Several exposures per visit, cutouts reused at a smaller fov, and b - a diffs
(T-006 additions to app.pipeline; offline)."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from app.cutouts import cutout_size_arcmin, fetch_cutouts
from app.errors import InvalidParameter
from app.pipeline import build_sequence, diff_png
from app.pipeline.clean import grid_side
from app.pipeline.diff import diff_gray, diff_scale
from app.pipeline.render import clean_path
from app.pipeline.select import select_per_visit
from tests.cutout_helpers import FOV10, StreamResponse, StreamSession, make_settings, ok
from tests.pipeline_helpers import DEC, RA, bit, epoch, fixture_with, query_of, square

SAFE = square(0.5)


def visits(n: int = 4):
    """Two visits of ``n`` exposures; inside a visit, rank follows closeness to
    the visit middle, so for n=4 the order is _2, _3, _1, _4 (ties by id)."""
    v1 = [epoch(f"2025W34_2B_0090_{i}", 1, 60908.0 + i * 0.1, SAFE) for i in range(1, n + 1)]
    v2 = [epoch(f"2026W12_1A_0311_{i}", 2, 61116.0 + i * 0.1, SAFE) for i in range(1, n + 1)]
    return v1 + v2


def obs_of(call) -> str:
    return call["url"].split("/x/")[1].split("D2.fits")[0]


def run(epochs, session, settings, **kwargs):
    def fetch(items):
        return fetch_cutouts(items, RA, DEC, 10, settings=settings, session=session).results

    def clean(cutout):
        return clean_path(cutout.path, RA, DEC, 10, settings)

    return select_per_visit(epochs, RA, DEC, 10, fetch=fetch, clean=clean,
                            settings=settings, **kwargs)


# ------------------------------------------------------------ max_per_visit


def test_keeps_the_best_ranked_n_per_visit(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV10)])
    result = run(visits(), session, settings, max_per_visit=2)
    assert sorted(obs_of(c) for c in session.calls) == [
        "2025W34_2B_0090_2", "2025W34_2B_0090_3", "2026W12_1A_0311_2", "2026W12_1A_0311_3"]
    assert [c.epoch.id for c in result.chosen] == [
        "sx_dr1_2025W34_2B_0090_2_D2", "sx_dr1_2025W34_2B_0090_3_D2",
        "sx_dr1_2026W12_1A_0311_2_D2", "sx_dr1_2026W12_1A_0311_3_D2"]
    assert all(c.accepted for c in result.chosen)
    mjds = [c.epoch.exposure.t_mid_mjd for c in result.chosen]
    assert mjds == sorted(mjds)


def test_fewer_exposures_than_wanted_keeps_them_all(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV10)])
    result = run(visits(3), session, settings, max_per_visit=6)
    assert len(result.chosen) == 6 and len(session.calls) == 6


def test_rejected_candidates_are_replaced_from_further_down(tmp_path):
    settings = make_settings(tmp_path)
    hot = StreamResponse(fixture_with(flags_or=bit("HOT")))  # every pixel masked: bad

    def reply(url):
        if "0090_2" in url:
            return hot
        if "0311_3" in url:
            return StreamResponse(b"", status_code=404)
        return ok(FOV10)

    session = StreamSession(replies=[reply])
    result = run(visits(), session, settings, max_per_visit=2)
    chosen = [c.epoch.id.split("_D2")[0][-6:] for c in result.chosen]
    assert chosen == ["0090_1", "0090_3", "0311_1", "0311_2"]
    outcomes = {(a.epoch_id[-9:-3], a.outcome) for a in result.attempts}
    assert ("0090_2", "bad_quality") in outcomes and ("0311_3", "download_failed") in outcomes
    assert len(session.calls) == 6  # 4 in round one, one replacement per visit after


def test_max_candidates_and_keep_rejected(tmp_path):
    settings = make_settings(tmp_path)
    hot = StreamResponse(fixture_with(flags_or=bit("HOT")))

    def reply(url):
        return hot if "0090_" in url else ok(FOV10)

    session = StreamSession(replies=[reply])
    result = run(visits(), session, settings, max_per_visit=2, max_candidates=3,
                 keep_rejected=False)
    assert sum("0090_" in c["url"] for c in session.calls) == 3  # capped
    assert result.skipped_visits == [1]  # all bad, and no fallback
    assert [c.epoch.visit for c in result.chosen] == [2, 2]

    session = StreamSession(replies=[reply])
    kept = run(visits(), session, make_settings(tmp_path / "b"), max_per_visit=2,
               max_candidates=3)
    v1 = [c for c in kept.chosen if c.epoch.visit == 1]
    assert len(v1) == 1 and not v1[0].accepted  # the default fallback keeps one


def test_build_sequence_passes_max_per_visit(tmp_path):
    settings = make_settings(tmp_path)
    seq = build_sequence(RA, DEC, "D2", 10, frame_url=lambda i, a, b: f"/f/{i}.png",
                         settings=settings, session=StreamSession(replies=[ok(FOV10)]),
                         query=query_of(visits()), max_per_visit=3)
    assert seq.response.count == 6
    assert [e.visit for e in seq.response.epochs] == [1, 1, 1, 2, 2, 2]


# ------------------------------------------------------------ cutout_fov


def test_cutout_fov_reuses_the_large_cutouts_at_a_smaller_fov(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV10)])
    query = query_of(visits(1))
    big = build_sequence(RA, DEC, "D2", 10, frame_url=lambda i, a, b: f"/f/{i}.png",
                         mode="all", settings=settings, session=session, query=query)
    assert big.downloads == 2 and len(session.calls) == 2
    size_param = f"size={cutout_size_arcmin(10) / 60:.6f}"
    assert all(c["url"].endswith(size_param) for c in session.calls)
    for fov in (6, 3):
        small = build_sequence(RA, DEC, "D2", fov, frame_url=lambda i, a, b: f"/s/{i}.png",
                               mode="all", settings=settings, session=session, query=query,
                               cutout_fov=10)
        assert small.downloads == 0 and small.cache_hits == 2
        side = grid_side(fov, settings.pipeline_pixel_scale_arcsec)
        assert (small.response.image.width_px, small.response.fov_arcmin) == (side, fov)
        for png in small.frames.values():
            with Image.open(io.BytesIO(png)) as im:
                assert im.size == (side, side)
    assert len(session.calls) == 2  # nothing new was downloaded


def test_cutout_fov_must_not_be_smaller_than_fov(tmp_path):
    with pytest.raises(InvalidParameter):
        build_sequence(RA, DEC, "D2", 10, frame_url=lambda i, a, b: "", cutout_fov=6,
                       settings=make_settings(tmp_path), query=query_of([]))


# ------------------------------------------------------------ diffs


def _decode(png: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(png)) as im:
        assert im.mode == "L"
        return np.asarray(im)


def test_diff_of_equal_frames_is_mid_grey():
    a = np.random.default_rng(1).normal(size=(9, 9))
    png, scale = diff_png(a, a.copy())
    assert scale == 1.0  # abs(b - a) is all zero
    assert (_decode(png) == 128).all()


def test_diff_sign_scale_and_no_data():
    a = np.zeros((4, 4))
    b = np.zeros((4, 4))
    b[0, 0] = 2.0  # brighter in b
    b[0, 1] = -2.0  # fainter in b
    b[1, 1] = 1.0
    a[3, 3] = np.nan  # no data in a: no change
    gray, scale = diff_gray(a, b, scale=2.0)
    assert scale == 2.0
    assert gray[0, 0] == 255 and gray[0, 1] == 1 and gray[1, 1] == 192 and gray[3, 3] == 128
    assert diff_scale(a, b) == pytest.approx(np.percentile(np.abs(b - np.nan_to_num(a))[
        np.isfinite(a)], 99.5), rel=1e-4)


def test_diff_rejects_bad_input():
    with pytest.raises(InvalidParameter):
        diff_png(np.zeros((3, 3)), np.zeros((4, 4)))
    with pytest.raises(InvalidParameter):
        diff_png(np.zeros((3, 3)), np.zeros((3, 3)), scale=0.0)
    full = np.full((3, 3), np.nan)
    assert diff_scale(full, full) == 1.0
