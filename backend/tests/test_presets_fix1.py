"""Preset builder and checker additions from T-006 fix-1: wavelength-matched diffs
for variable stars, dropping frames that are unusable at any fov, cross-fov and
target-core checks, and the honest preset texts. Offline, on the recorded fixture.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from app.models import EpochsResponse
from tests.conftest import REPO
from tests.cutout_helpers import FOV10, StreamSession, make_settings, ok
from tests.pipeline_helpers import DEC, RA, epoch, query_of, square

sys.path.insert(0, str(REPO / "backend" / "scripts"))
import build_presets
import check_presets

# A variable-star preset id at the fixture's position (the fixture is Barnard's Star).
VARIABLE = ("rw-aur", "RW Aurigae", RA, DEC, "D2", "RW Aur")
FOVS = (10.0, 6.0, 3.0)
SAFE = square(0.5)


def visits():
    out = []
    for visit, (week, base) in enumerate([("2025W34_2B_0090", 60908.0),
                                          ("2026W35_1A_0092", 61276.0)], start=1):
        for i in (1, 2, 3):
            release = "QR3" if visit == 2 else "DR1"
            out.append(epoch(f"{week}_{i}", visit, base + i * 0.1, SAFE, release=release))
    return out


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("variable")
    public = tmp / "public"
    root = public / "presets"
    result = build_presets.build_preset(VARIABLE, root, fovs=FOVS,
                                        settings=make_settings(tmp),
                                        session=StreamSession(replies=[ok(FOV10)]),
                                        query=query_of(visits()))
    build_presets.write_index([result], root, FOVS)
    (root / "README.md").write_text("test\n", encoding="utf-8")
    return public, result


@pytest.fixture()
def built(base, tmp_path):
    public, result = base
    copy = tmp_path / "public"
    shutil.copytree(public, copy)
    return copy, copy / "presets", result


def run_check(public, root):
    return check_presets.check(root, public, presets=(VARIABLE,), fovs=FOVS)[0]


def manifest(root, sub=""):
    path = root / "rw-aur" / sub / "manifest.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def ep(eid, visit, wl, mjd):
    return SimpleNamespace(id=eid, visit=visit, wavelength_um=wl, mjd=mjd)


# ------------------------------------------------------------ builder


def test_matched_pair_picks_the_closest_wavelength():
    epochs = [ep("a1", 1, 1.20, 1.0), ep("a2", 1, 1.40, 1.1), ep("m", 2, 1.30, 50.0),
              ep("b1", 3, 1.41, 100.0), ep("b2", 3, 1.60, 100.1)]
    assert build_presets.matched_pair(epochs) == ("a2", "b1")
    # The first-versus-last pair (a1, b2) is never repeated, even if it matches best.
    same = [ep("a1", 1, 1.2, 1.0), ep("a2", 1, 1.5, 1.1), ep("b1", 2, 1.4, 9.0),
            ep("b2", 2, 1.2, 9.1)]
    assert build_presets.matched_pair(same) == ("a2", "b1")
    assert build_presets.matched_pair([ep("a", 1, 1.2, 1.0), ep("b", 1, 1.2, 2.0)]) is None
    assert build_presets.diff_pairs("barnards-star", epochs) == [("a1", "b2")]
    assert build_presets.diff_pairs("r-dra", epochs) == [("a1", "b2"), ("a2", "b1")]


def test_variable_preset_has_two_diffs_shared_across_fovs(built):
    public, root, result = built
    assert result.ok and result.dropped == []
    pairs = None
    for sub in ("", "fov-6/", "fov-3/"):
        doc = EpochsResponse.model_validate_json(
            (root / "rw-aur" / sub / "manifest.json").read_text(encoding="utf-8"))
        got = [(d.a, d.b, d.scale) for d in doc.diffs]
        assert len(got) == 2 and (got[0][0], got[0][1]) == (doc.epochs[0].id,
                                                             doc.epochs[-1].id)
        visit = {e.id: e.visit for e in doc.epochs}
        assert (visit[got[1][0]], visit[got[1][1]]) == (1, 2)
        assert pairs in (None, got)
        pairs = got
        assert doc.stretch.note == build_presets.PRESET_STRETCH_NOTE
        assert "slightly different wavelength" in doc.stretch.note
    assert run_check(public, root) == []


def test_frames_unusable_at_any_fov_are_dropped_everywhere(tmp_path, monkeypatch):
    real = build_presets.unusable
    victim = "sx_dr1_2025W34_2B_0090_2_D2"

    def fake(seq, *args):
        out = real(seq, *args)
        if seq.response.fov_arcmin == 3.0:
            out[victim] = "core valid 0.00"
        return out

    monkeypatch.setattr(build_presets, "unusable", fake)
    root = tmp_path / "public" / "presets"
    result = build_presets.build_preset(VARIABLE, root, fovs=FOVS,
                                        settings=make_settings(tmp_path),
                                        session=StreamSession(replies=[ok(FOV10)]),
                                        query=query_of(visits()))
    assert result.dropped == [f"{victim}: core valid 0.00 at fov 3"]
    assert result.per_visit == {1: 2, 2: 3}
    for sub in ("", "fov-6/", "fov-3/"):
        doc = EpochsResponse.model_validate_json(
            (root / "rw-aur" / sub / "manifest.json").read_text(encoding="utf-8"))
        assert victim not in {e.id for e in doc.epochs} and doc.count == 5
        assert not (root / "rw-aur" / sub / "frames" / f"{victim}.png").exists()


def test_unusable_flags_bad_and_masked_cores():
    def frame(core):
        return SimpleNamespace(core_valid=core)

    def epoch_(eid, status):
        return SimpleNamespace(id=eid, quality=SimpleNamespace(status=status,
                                                               flagged_fraction=0.3))

    seq = SimpleNamespace(
        response=SimpleNamespace(epochs=[epoch_("ok", "warn"), epoch_("bad", "bad"),
                                         epoch_("hole", "good")]),
        cleaned={"ok": frame(0.5), "bad": frame(1.0), "hole": frame(0.46)})
    out = build_presets.unusable(seq)
    assert set(out) == {"bad", "hole"} and out["hole"] == "core valid 0.46"


def test_preset_texts_are_honest():
    look = build_presets.LOOK_FOR
    assert "Too faint to see in single SPHEREx snapshots" in look["wise-0855"]
    assert "visibly" not in look["wise-0855"].lower()
    assert look["eps-indi-b"].startswith("Faint brown dwarf that moves about one pixel")
    assert all("co-moving" not in text for text in look.values())
    for pid in build_presets.VARIABLES:
        assert "between visits" in look[pid] and "colour" in look[pid]
    api = (REPO / "docs" / "API.md").read_text(encoding="utf-8")
    for text in look.values():
        assert text in api  # the API.md section 4 table carries the same texts
    assert "co-moving" not in api and "Version:** 0.2" in api


# ------------------------------------------------------------ checker


def test_checker_finds_cross_fov_differences(built):
    public, root, _r = built
    path, doc = manifest(root, "fov-6/")
    doc["stretch"]["vmin"] -= 0.01
    doc["diffs"] = doc["diffs"][:1]
    path.write_text(json.dumps(doc), encoding="utf-8")
    errs = run_check(public, root)
    assert any("stretch differs" in e for e in errs)
    assert any("diff pairs or scales differ" in e for e in errs)
    assert any("no wavelength-matched" in e for e in errs)


def test_checker_finds_different_epochs_between_fovs(built):
    public, root, _r = built
    path, doc = manifest(root, "fov-3/")
    drop = doc["epochs"].pop(1)
    doc["count"] -= 1
    path.write_text(json.dumps(doc), encoding="utf-8")
    errs = run_check(public, root)
    assert any("fov 3 vs 10: epoch ids differ" in e for e in errs), drop["id"]


def test_checker_finds_a_masked_target_core_and_bad_quality(built):
    public, root, _r = built
    path, doc = manifest(root)
    frame = public / doc["epochs"][1]["frame_url"].lstrip("/")
    with Image.open(frame) as im:
        gray = np.asarray(im).copy()
    c = (gray.shape[0] - 1) // 2
    gray[c - 2:c + 3, c - 2:c + 3] = 0  # the target core has no data
    buf = io.BytesIO()
    Image.fromarray(gray).save(buf, format="PNG")
    frame.write_bytes(buf.getvalue())
    doc["epochs"][2]["quality"]["status"] = "bad"
    path.write_text(json.dumps(doc), encoding="utf-8")
    errs = run_check(public, root)
    assert any("target core is 0.00 valid" in e for e in errs)
    assert any("has quality bad" in e for e in errs)


def test_presets_gitattributes_covers_itself():
    text = (REPO / "frontend" / "public" / "presets" / ".gitattributes").read_text(
        encoding="utf-8")
    assert ".gitattributes text eol=lf" in text.splitlines()
