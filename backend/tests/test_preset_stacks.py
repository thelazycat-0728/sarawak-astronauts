"""Preset visit stacks (T-016): the builder's ``stacks`` section, build_stacks.py
(offline, from the cutout cache), the checker's stack and baseline checks, and the
committed presets. Offline, on the recorded cutout fixture."""

from __future__ import annotations

import io
import json
import shutil
import sys

import numpy as np
import pytest
from PIL import Image

from app.models import EpochsResponse
from tests.conftest import REPO
from tests.cutout_helpers import FOV10, StreamSession, make_settings, ok
from tests.pipeline_helpers import epoch, query_of, square

sys.path.insert(0, str(REPO / "backend" / "scripts"))
import build_presets
import build_stacks
import check_presets
import preset_science as ps
import preset_stacks
from irsa_coverage import PRESETS

BARNARD = PRESETS[0]  # the fixture is a Barnard's Star cutout
FOVS = (10.0, 6.0, 3.0)
SAFE = square(0.5)


def three_visits():
    out = []
    for visit, (week, base) in enumerate([("2025W34_2B_0090", 60908.0),
                                          ("2026W12_1A_0311", 61116.0),
                                          ("2026W35_1A_0092", 61276.0)], start=1):
        for i in (1, 2):
            release = "QR3" if visit == 3 else "DR1"
            out.append(epoch(f"{week}_{i}", visit, base + i * 0.1, SAFE, release=release))
    return out


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("stacks")
    public = tmp / "public"
    root = public / "presets"
    settings = make_settings(tmp)
    result = build_presets.build_preset(BARNARD, root, fovs=FOVS, settings=settings,
                                        session=StreamSession(replies=[ok(FOV10)]),
                                        query=query_of(three_visits()))
    build_presets.write_index([result], root, FOVS)
    (root / "README.md").write_text("test\n", encoding="utf-8")
    return public, result, settings


@pytest.fixture()
def built(base, tmp_path):
    public, result, settings = base
    copy = tmp_path / "public"
    shutil.copytree(public, copy)
    return copy, copy / "presets", result, settings


def run_check(public, root, **kwargs):
    return check_presets.check(root, public, presets=(BARNARD,), fovs=FOVS, **kwargs)


def manifest(root, sub=""):
    path = root / "barnards-star" / sub / "manifest.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def write(path, doc):
    path.write_text(json.dumps(doc), encoding="utf-8")


def write_gray(path, gray):
    buf = io.BytesIO()
    Image.fromarray(gray.astype(np.uint8)).save(buf, format="PNG")
    path.write_bytes(buf.getvalue())


# ------------------------------------------------------------ builder


def test_builder_writes_one_stack_per_visit_with_the_stored_stretch(built):
    public, root, result, _settings = built
    assert [f.stacks for f in result.fovs] == [3, 3, 3]
    scales = set()
    for sub in ("", "fov-6/", "fov-3/"):
        _path, raw = manifest(root, sub)
        doc = EpochsResponse.model_validate(raw)
        st = preset_stacks.Stacks.model_validate(raw["stacks"])
        assert list(raw)[-1] == "stacks"  # appended after the /epochs fields
        assert [s.visit for s in st.visits] == [1, 2, 3]
        for s in st.visits:
            ids = [e.id for e in doc.epochs if e.visit == s.visit]
            assert s.epoch_ids == ids and s.n_frames == 2
            mjds = [e.mjd for e in doc.epochs if e.visit == s.visit]
            assert s.mjd == pytest.approx(sum(mjds) / 2, abs=1e-6)
            assert s.url == f"/presets/barnards-star/{sub}stacks/visit-{s.visit}.png"
            assert s.wavelength_min_um <= s.wavelength_mean_um <= s.wavelength_max_um
        assert (st.diff.a_visit, st.diff.b_visit) == (1, 3)
        scales.add(st.diff.scale)
        with Image.open(public / st.visits[0].url.lstrip("/")) as im:
            assert im.mode == "L" and im.size == (doc.image.width_px, doc.image.height_px)
    assert len(scales) == 1  # every fov uses the largest fov's stack diff scale
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    assert [m["stacks"] for m in index["presets"][0]["manifests"]] == [3, 3, 3]
    errs, summary = run_check(public, root)
    assert errs == [] and summary["stacks"] == 9


def test_stack_png_is_the_mean_of_the_visit_frames(built):
    """The stack equals the stretch of the mean of the cleaned frames, which
    build_stacks.reclean reproduces from the cache."""
    public, root, _r, settings = built
    _path, raw = manifest(root)
    doc = EpochsResponse.model_validate(raw)
    cleaned = build_stacks.reclean(doc, settings, max(FOVS))
    from app.pipeline.render import to_gray8
    from app.pipeline.stack import mean_valid
    visit1 = [cleaned[e.id].image for e in doc.epochs if e.visit == 1]
    mean, _n = mean_valid(visit1)
    want = to_gray8(mean, doc.stretch.vmin, doc.stretch.vmax, settings)
    with Image.open(public / raw["stacks"]["visits"][0]["url"].lstrip("/")) as im:
        assert np.array_equal(np.asarray(im), want)


def test_build_stacks_reproduces_the_builder_output(built):
    """build_stacks on manifests without stacks gives the builder's bytes."""
    public, root, _r, settings = built
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    for sub in ("", "fov-6/", "fov-3/"):
        path, raw = manifest(root, sub)
        del raw["stacks"]
        build_presets._write_json(path, raw)
        shutil.rmtree(path.parent / "stacks")
    counts = build_stacks.add_stacks(BARNARD, root, public, FOVS, settings)
    assert counts == {10.0: 3, 6.0: 3, 3.0: 3}
    after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before


def test_build_stacks_refuses_when_a_frame_does_not_reproduce(built):
    public, root, _r, settings = built
    _path, raw = manifest(root, "fov-6/")
    frame = public / raw["epochs"][0]["frame_url"].lstrip("/")
    with Image.open(frame) as im:
        gray = np.asarray(im).copy()
    gray[0, 0] = 7 if gray[0, 0] != 7 else 8
    write_gray(frame, gray)
    before = (root / "barnards-star" / "manifest.json").read_bytes()
    with pytest.raises(build_stacks.StackBuildError, match="differs from the committed"):
        build_stacks.add_stacks(BARNARD, root, public, FOVS, settings)
    assert (root / "barnards-star" / "manifest.json").read_bytes() == before


def test_mjd_to_utc():
    assert preset_stacks.mjd_to_utc(60800.5) == "2025-05-05T12:00:00Z"
    assert preset_stacks.mjd_to_utc(60800.700695) == "2025-05-05T16:49:00Z"


# ------------------------------------------------------------ checker: stacks


def test_checker_finds_missing_and_inconsistent_stacks(built):
    public, root, _r, _s = built
    path, raw = manifest(root)
    st = raw["stacks"]
    st["visits"][0]["epoch_ids"].append("sx_dr1_unknown_9_D2")
    st["visits"][1]["mjd"] += 1.0
    st["visits"][2]["url"] = "/presets/barnards-star/stacks/nope.png"
    st["diff"] = None
    write(path, raw)
    path6, raw6 = manifest(root, "fov-6/")
    del raw6["stacks"]
    write(path6, raw6)
    errs, _ = run_check(public, root)
    assert any("epoch ids not in epochs" in e for e in errs)
    assert any("is not the frames' mean" in e for e in errs)
    assert any("does not resolve" in e for e in errs)
    assert any("no stack diff" in e for e in errs)
    assert any("fov-6/manifest.json: stacks missing" in e for e in errs)


def test_checker_finds_a_wrong_stack_size_and_no_data_mask(built):
    public, root, _r, _s = built
    _path, raw = manifest(root, "fov-3/")
    first = public / raw["stacks"]["visits"][0]["url"].lstrip("/")
    with Image.open(first) as im:
        gray = np.asarray(im).copy()
    gray[1, 1] = 0  # no data where the frames have data
    write_gray(first, gray)
    second = public / raw["stacks"]["visits"][1]["url"].lstrip("/")
    write_gray(second, np.full((5, 5), 100))
    errs, _ = run_check(public, root)
    assert any("no-data pixels differ" in e for e in errs)
    assert any("expected PNG L" in e for e in errs)


def test_checker_finds_visit_and_index_mismatches(built):
    public, root, _r, _s = built
    path, raw = manifest(root, "fov-6/")
    raw["stacks"]["visits"] = raw["stacks"]["visits"][:2]
    raw["stacks"]["diff"]["scale"] *= 2
    write(path, raw)
    errs, _ = run_check(public, root)
    assert any("stack visits [1, 2] != frame visits [1, 2, 3]" in e for e in errs)
    assert any("stack diff scale differs" in e for e in errs)
    assert any("stack visits or epoch ids differ" in e for e in errs)
    assert any("stacks 3 != 2 visit stacks" in e for e in errs)


# ------------------------------------------------------------ checker: baseline


def test_baseline_passes_unchanged_and_ignores_stacks(built, tmp_path):
    public, root, _r, _s = built
    baseline = tmp_path / "baseline.json"
    doc = check_presets.baseline_document(public, root, (BARNARD,), FOVS)
    baseline.write_text(json.dumps(doc), encoding="utf-8")
    assert len(doc["files"]) == 18 + 3  # frames and diffs of 3 fovs
    path, raw = manifest(root)
    raw["stacks"]["note"] = "changed"  # stacks are not part of the baseline
    build_presets._write_json(path, raw)
    errs, summary = run_check(public, root, baseline=baseline)
    assert errs == [] and summary["baseline_files"] == 21


def test_baseline_finds_changed_frames_stretch_and_fields(built, tmp_path):
    public, root, _r, _s = built
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(check_presets.baseline_document(
        public, root, (BARNARD,), FOVS)), encoding="utf-8")
    path, raw = manifest(root, "fov-3/")
    frame = public / raw["epochs"][1]["frame_url"].lstrip("/")
    with Image.open(frame) as im:
        gray = np.asarray(im).copy()
    gray[2, 2] = 9 if gray[2, 2] != 9 else 10
    write_gray(frame, gray)
    raw["stretch"]["vmax"] += 1.0
    write(path, raw)
    path12, raw12 = manifest(root)
    raw12["epochs"][0]["wavelength_um"] += 0.001
    write(path12, raw12)
    errs, _ = run_check(public, root, baseline=baseline)
    assert any(f"{raw['epochs'][1]['frame_url']} changed" in e for e in errs)
    assert any("fov-3/manifest.json stretch" in e for e in errs)
    assert any("barnards-star/manifest.json fields other than stacks changed" in e
               for e in errs)


def test_committed_presets_have_stacks_and_unchanged_frames():
    errs, summary = check_presets.check(baseline=check_presets.BASELINE)
    assert errs == [], errs
    assert summary["stacks"] > 0
    assert summary["baseline_files"] == summary["frames"] + 21  # 15 + 6 matched diffs


# ------------------------------------------------------------ science


def test_centroid_recentring_handles_a_saturated_hole():
    yy, xx = np.indices((41, 41))
    img = 1000.0 * np.exp(-((xx - 21.6) ** 2 + (yy - 19.4) ** 2) / (2 * 2.0**2))
    img[(np.hypot(xx - 21.6, yy - 19.4) <= 1.6)] = np.nan  # saturated, not filled
    c = ps.centroid(img, 20.0, 20.0, half=5, recentre=5)
    assert c.x == pytest.approx(21.6, abs=0.2) and c.y == pytest.approx(19.4, abs=0.2)
    plain = ps.centroid(img, 20.0, 20.0)  # boxed on the brightest pixel at the hole edge
    assert (np.hypot(c.x - 21.6, c.y - 19.4) < np.hypot(plain.x - 21.6, plain.y - 19.4))
