"""Preset light curves and spectra: the tracked position, the builder wiring and
check_presets' photometry rules (offline; the photometry itself is tested in
test_photometry.py)."""

from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

import pytest

from app.pipeline.photometry import METHOD, Measurement
from tests.conftest import REPO

sys.path.insert(0, str(REPO / "backend" / "scripts"))
import build_preset_photometry as bpp
import check_presets
import preset_photometry as pp
from irsa_coverage import PRESETS

PUBLIC = REPO / "frontend" / "public"
EPS = next(p for p in PRESETS if p[0] == "eps-indi-b")


def _year_mjd(year: float) -> float:
    return pp.MJD_J2000 + (year - 2000.0) * pp.DAYS_PER_YEAR


def test_position_at_moves_by_the_proper_motion():
    ra, dec = 331.0951, -56.8007
    assert pp.position_at(ra, dec, 60000.0, None) == (ra, dec)
    ra1, dec1 = pp.position_at(ra, dec, _year_mjd(pp.CENTRE_EPOCH + 1.0), (3982.0, -2467.0))
    assert (dec1 - dec) * 3600 == pytest.approx(-2.467)
    assert (ra1 - ra) * math.cos(math.radians(dec)) * 3600 == pytest.approx(3.982)
    ra0, dec0 = pp.position_at(ra, dec, _year_mjd(pp.CENTRE_EPOCH), (3982.0, -2467.0))
    assert (ra0, dec0) == pytest.approx((ra, dec))


def test_position_at_wraps_ra():
    ra1, _ = pp.position_at(359.99999, 0.0, _year_mjd(pp.CENTRE_EPOCH + 1.0), (100.0, 0.0))
    assert 0.0 <= ra1 < 0.001


def test_spectrum_method_drops_only_the_time_caveat():
    assert "colour, not time" in METHOD
    assert "colour, not time" not in bpp.SPECTRUM_METHOD
    assert bpp.SPECTRUM_METHOD.startswith(METHOD[:40])


class _Cache:
    def lookup(self, key):
        return Path(key)


def test_build_lightcurve_measures_every_manifest_epoch_with_tracking(monkeypatch):
    seen = []

    def fake_measure(path, ra, dec, settings, frame_status="good"):
        seen.append((ra, dec, frame_status))
        return Measurement(1234.5, 12.3, "good") if len(seen) % 3 else Measurement(None, None, "bad")

    monkeypatch.setattr(bpp, "measure_file", fake_measure)
    lc = bpp.build_lightcurve(EPS, PUBLIC, settings=None, cache=_Cache())
    man = bpp.load_manifest("eps-indi-b", PUBLIC)
    assert [(p.epoch_id, p.visit) for p in lc.points] == [(e.id, e.visit) for e in man.epochs]
    assert [p.mjd for p in lc.points] == sorted(p.mjd for p in lc.points)
    assert lc.tracking is not None and lc.tracking.proper_motion_mas_yr == (3982.0, -2467.0)
    assert [s[2] for s in seen] == [e.quality.status for e in man.epochs]
    # the aperture follows the proper motion: the first and last positions differ
    assert seen[0][:2] != seen[-1][:2]
    assert lc.points[2].flux is None and lc.points[2].quality == "bad"


def test_build_lightcurve_refuses_a_missing_cutout():
    class Empty:
        def lookup(self, key):
            return None

    with pytest.raises(bpp.PhotometryBuildError):
        bpp.build_lightcurve(EPS, PUBLIC, settings=None, cache=Empty())


def test_build_spectrum_is_none_without_a_fetched_set(monkeypatch, tmp_path):
    monkeypatch.setattr(bpp, "spectrum_settings",
                        lambda s=None: type("S", (), {"cutout_cache_dir": tmp_path})())
    lc = json.loads((PUBLIC / "presets" / "rw-aur" / pp.LIGHTCURVE_FILE).read_text("utf-8"))
    target = pp.PresetLightcurve.model_validate(lc).target
    assert bpp.build_spectrum(next(p for p in PRESETS if p[0] == "rw-aur"), target,
                              settings=None) is None


# ------------------------------------------------------------------ checker


@pytest.fixture
def public(tmp_path):
    copy = tmp_path / "public"
    shutil.copytree(PUBLIC / "presets", copy / "presets")
    return copy


def _edit(public: Path, rel: str, fn) -> None:
    path = public / "presets" / rel
    doc = json.loads(path.read_text(encoding="utf-8"))
    fn(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _errors(public: Path) -> list[str]:
    errs, _summary = check_presets.check(public=public, require_photometry=True)
    return errs


def test_committed_presets_have_valid_light_curves(public):
    errs, summary = check_presets.check(public=public, baseline=check_presets.BASELINE,
                                        require_photometry=True)
    assert errs == []
    frames_fov12 = sum(bpp.load_manifest(p[0], PUBLIC).count for p in PRESETS)
    assert summary["lightcurve_points"] == frames_fov12


def test_checker_finds_a_null_flux_that_is_not_bad(public):
    def null_first_good(doc):
        p = next(p for p in doc["points"] if p["quality"] != "bad")
        p["flux"] = None
        p["flux_err"] = None

    _edit(public, "eps-indi-b/lightcurve.json", null_first_good)
    assert any("null exactly when bad" in e for e in _errors(public))


def test_checker_finds_missing_points_order_and_url(public):
    _edit(public, "rw-aur/lightcurve.json", lambda d: d["points"].pop())
    _edit(public, "wise-0855/lightcurve.json", lambda d: d["points"].reverse())
    _edit(public, "index.json", lambda d: d["presets"][0].pop("lightcurve_url"))
    errs = _errors(public)
    assert any("rw-aur lightcurve: points are not one per manifest epoch" in e for e in errs)
    assert any("wise-0855 lightcurve: points are not in time order" in e for e in errs)
    assert any("barnards-star: lightcurve_url" in e for e in errs)


def test_checker_wants_tracking_exactly_for_movers(public):
    _edit(public, "eps-indi-b/lightcurve.json", lambda d: d.pop("tracking"))
    _edit(public, "rw-aur/lightcurve.json", lambda d: d.update(
        tracking={"proper_motion_mas_yr": [1, 2], "ref_epoch": 2025.5, "note": "x"}))
    errs = _errors(public)
    assert sum("tracking must be set exactly" in e for e in errs) == 2


def _spectrum(public: Path, points: list[dict], bands: list[str]) -> None:
    lc = json.loads((public / "presets" / "rw-aur" / pp.LIGHTCURVE_FILE).read_text("utf-8"))
    doc = {"synthetic": False, "target": lc["target"],
           "bands": [{"id": b, "wavelength_min_um": 1.0, "wavelength_max_um": 2.0} for b in bands],
           "aperture_arcsec": 12.0, "unit": "uJy", "method": "m", "note": "n",
           "tracking": None, "points": points}
    (public / "presets" / "rw-aur" / pp.SPECTRUM_FILE).write_text(json.dumps(doc), "utf-8")
    _edit(public, "index.json", lambda d: next(p for p in d["presets"] if p["id"] == "rw-aur")
          .update(spectrum_url="/presets/rw-aur/spectrum.json"))


def _point(i: int, band: str, wave: float, flux: float | None = 10.0) -> dict:
    return {"epoch_id": f"sx_dr1_2025W01_1A_000{i}_1_{band}", "time_utc": "2025-01-01T00:00:00Z",
            "mjd": 60676.0, "release": "DR1", "wavelength_um": wave, "flux": flux,
            "flux_err": 1.0 if flux is not None else None,
            "quality": "good" if flux is not None else "bad", "band": band, "visit": 1}


def test_checker_accepts_a_good_spectrum_and_finds_its_errors(public):
    _spectrum(public, [_point(1, "D1", 0.8), _point(2, "D2", 1.2, None)], ["D1", "D2"])
    errs, summary = check_presets.check(public=public)
    assert errs == [] and summary["spectra"] == 1 and summary["spectrum_points"] == 2
    _spectrum(public, [_point(1, "D2", 1.2), _point(2, "D1", 0.8)], ["D1"])
    errs = _errors(public)
    assert any("rw-aur spectrum: points are empty or not in wavelength order" in e for e in errs)
    assert any("rw-aur spectrum: bands do not list exactly" in e for e in errs)


def test_checker_validates_motion_on_movers_only(public):
    def tamper(d):
        by_id = {p["id"]: p for p in d["presets"]}
        by_id["rw-aur"]["motion"] = by_id["eps-indi-b"]["motion"]
        by_id["wise-0855"].pop("motion")
        by_id["barnards-star"]["motion"]["visits"].pop()

    _edit(public, "index.json", tamper)
    errs = _errors(public)
    assert any("rw-aur motion: only proper-motion movers" in e for e in errs)
    assert any("wise-0855 motion: missing" in e for e in errs)
    assert any("barnards-star motion: visits" in e for e in errs)


def test_committed_motion_matches_the_catalogue_for_the_bright_movers():
    index = json.loads((PUBLIC / "presets" / "index.json").read_text("utf-8"))
    motion = {p["id"]: pp.PresetMotion.model_validate(p["motion"])
              for p in index["presets"] if "motion" in p}
    assert sorted(motion) == ["barnards-star", "eps-indi-b", "wise-0855"]
    assert motion["eps-indi-b"].verdict == "visible"
    assert motion["eps-indi-b"].measured.shift_arcsec == pytest.approx(
        motion["eps-indi-b"].expected.shift_arcsec, rel=0.25)
    assert motion["barnards-star"].verdict == "visible"
    assert all(v.saturated for v in motion["barnards-star"].visits)
    assert motion["wise-0855"].verdict == "not visible"


def test_light_curves_are_optional_unless_required(public):
    _edit(public, "index.json", lambda d: d["presets"][0].pop("lightcurve_url"))
    errs, _ = check_presets.check(public=public)
    assert errs == []
    assert any("barnards-star: lightcurve_url" in e for e in _errors(public))


def test_baseline_ignores_the_photometry_urls(public):
    _edit(public, "index.json", lambda d: d["presets"][1].pop("lightcurve_url"))
    errs, _ = check_presets.check(public=public, baseline=check_presets.BASELINE)
    assert not any(e.startswith("baseline") for e in errs)
    _edit(public, "index.json", lambda d: d["presets"][1].update(name="changed"))
    errs, _ = check_presets.check(public=public, baseline=check_presets.BASELINE)
    assert any("baseline: index.json fields" in e for e in errs)
