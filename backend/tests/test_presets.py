"""Preset builder and checker (backend/scripts/build_presets.py, check_presets.py).

The builder runs offline on the recorded cutout fixture into a temporary
``public/presets`` folder; the checker must pass on that output, catch broken
copies of it, and pass on the committed frontend/public/presets.
"""

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
import check_presets
from irsa_coverage import PRESETS

BARNARD = PRESETS[0]  # the fixture is a Barnard's Star cutout
FOVS = (10.0, 6.0, 3.0)  # the fixture is sized for fov 10
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
    """One preset built from the fixture into public/presets (built once)."""
    tmp = tmp_path_factory.mktemp("presets")
    public = tmp / "public"
    root = public / "presets"
    session = StreamSession(replies=[ok(FOV10)])
    settings = make_settings(tmp)
    result = build_presets.build_preset(BARNARD, root, fovs=FOVS, settings=settings,
                                        session=session, query=query_of(three_visits()))
    build_presets.write_index([result], root, FOVS)
    (root / "README.md").write_text("test\n", encoding="utf-8")
    return public, result, len(session.calls), settings


@pytest.fixture()
def built(base, tmp_path):
    """A private copy of the built public/ folder: (public, presets root, result)."""
    public, result, _calls, _settings = base
    copy = tmp_path / "public"
    shutil.copytree(public, copy)
    return copy, copy / "presets", result


def run_check(public, root, **kwargs):
    errs, summary = check_presets.check(root, public, presets=(BARNARD,), fovs=FOVS, **kwargs)
    return errs, summary


def test_builder_writes_every_fov_from_one_set_of_cutouts(base, built):
    _public, result, calls, _settings = base
    _copy, root, _result = built
    assert result.ok and not result.error and result.download_failures == []
    assert calls == 6  # each cutout fetched once, at the largest fov
    assert result.per_visit == {1: 2, 2: 2, 3: 2}
    assert [f.fov for f in result.fovs] == list(FOVS)
    stretches = set()
    for fov, sub in ((10.0, ""), (6.0, "fov-6/"), (3.0, "fov-3/")):
        path = root / "barnards-star" / sub / "manifest.json"
        doc = EpochsResponse.model_validate_json(path.read_text(encoding="utf-8"))
        assert doc.fov_arcmin == fov and doc.count == 6 and doc.synthetic is False
        assert doc.target.name == "Barnard's Star" and doc.diff_url_template is None
        prefix = f"/presets/barnards-star/{sub}"
        assert all(e.frame_url == f"{prefix}frames/{e.id}.png" for e in doc.epochs)
        (d,) = doc.diffs
        assert (d.a, d.b) == (doc.epochs[0].id, doc.epochs[-1].id)
        assert d.url == f"{prefix}diff/{d.a}__{d.b}.png" and d.scale > 0
        assert len(list((root / "barnards-star" / sub / "frames").glob("*.png"))) == 6
        stretches.add((doc.stretch.vmin, doc.stretch.vmax, d.scale))
    assert len(stretches) == 1  # every fov uses the largest fov's stretch and scale
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    assert index["credit"] == build_presets.CREDIT
    (entry,) = index["presets"]
    assert [m["url"] for m in entry["manifests"]] == [
        "/presets/barnards-star/manifest.json", "/presets/barnards-star/fov-6/manifest.json",
        "/presets/barnards-star/fov-3/manifest.json"]


def test_checker_passes_on_builder_output(built):
    public, root, _result = built
    errs, summary = run_check(public, root)
    assert errs == []
    assert summary["manifests"] == 3 and summary["frames"] == 18


def test_rebuild_is_byte_identical_and_removes_stale_files(base, built):
    _public, _result, _calls, settings = base
    _copy, root, _r = built
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    stale = root / "barnards-star" / "frames" / "old.png"
    stale.write_bytes(b"x")
    result = build_presets.build_preset(BARNARD, root, fovs=FOVS, settings=settings,
                                        session=StreamSession(replies=[]),  # cache only
                                        query=query_of(three_visits()))
    assert result.ok and result.downloads == 0
    build_presets.write_index([result], root, FOVS)
    after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert not stale.exists() and after == before


def _manifest(root, sub=""):
    path = root / "barnards-star" / sub / "manifest.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def _write(path, doc):
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_checker_finds_a_missing_diff_and_a_bad_url(built):
    public, root, _r = built
    path, doc = _manifest(root, "fov-6/")
    doc["diffs"] = []
    doc["epochs"][0]["frame_url"] = "/presets/../../secret.png"
    _write(path, doc)
    errs, _ = run_check(public, root)
    assert any("no first-versus-last diff" in e for e in errs)
    assert any("does not resolve" in e for e in errs)


def test_checker_finds_a_wrong_frame_size(built):
    public, root, _r = built
    _path, doc = _manifest(root, "fov-3/")
    frame = public / doc["epochs"][2]["frame_url"].lstrip("/")
    buf = io.BytesIO()
    Image.fromarray(np.zeros((5, 5), dtype=np.uint8)).save(buf, format="PNG")
    frame.write_bytes(buf.getvalue())
    errs, _ = run_check(public, root)
    assert any("expected PNG L" in e for e in errs)


def test_checker_needs_two_visits_and_a_valid_shape(built):
    public, root, _r = built
    path, doc = _manifest(root)
    for e in doc["epochs"]:
        e["visit"] = 1
    doc["synthetic"] = True
    _write(path, doc)
    errs, _ = run_check(public, root)
    assert any("need at least 2" in e for e in errs)
    assert any("synthetic must be false" in e for e in errs)
    assert any("visits do not match" in e for e in errs)
    del doc["stretch"]
    _write(path, doc)
    errs, _ = run_check(public, root)
    assert any("not a valid /epochs response" in e for e in errs)


def test_checker_compares_the_index(built):
    public, root, _r = built
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    index["presets"][0]["ra"] += 0.01
    index["presets"][0]["manifests"] = index["presets"][0]["manifests"][:2]
    _write(root / "index.json", index)
    errs, _ = run_check(public, root)
    assert any("ra/dec differ" in e for e in errs)
    assert any("manifests for fovs" in e for e in errs)
    shutil.rmtree(root / "barnards-star" / "fov-3")
    errs, _ = run_check(public, root)
    assert any("fov-3/manifest.json: missing" in e for e in errs)


def test_checker_enforces_the_size_limit(built):
    public, root, _r = built
    errs, summary = run_check(public, root, max_bytes=1000)
    assert any("limit 1000" in e for e in errs) and summary["bytes"] > 1000


def test_committed_presets_pass_the_checker():
    errs, summary = check_presets.check()
    assert errs == [], errs
    assert summary["manifests"] == len(PRESETS) * len(build_presets.PRESET_FOVS)
    assert summary["bytes"] < check_presets.MAX_FOLDER_BYTES


def test_wise_0855_is_centred_on_its_2025_5_position():
    _pid, _name, ra, dec, band, _simbad = next(p for p in PRESETS if p[0] == "wise-0855")
    assert (ra, dec, band) == (133.7609, -7.2423, "D6")
