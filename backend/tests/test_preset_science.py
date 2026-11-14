"""Measurement helpers of backend/scripts/preset_science.py on synthetic images."""

from __future__ import annotations

import sys

import numpy as np
import pytest

from tests.conftest import REPO

sys.path.insert(0, str(REPO / "backend" / "scripts"))
import preset_science as ps


def star(shape, x, y, amp=100.0, sigma=1.2):
    yy, xx = np.indices(shape)
    return amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))


def test_centroid_finds_a_shifted_star():
    rng = np.random.default_rng(3)
    img = 5.0 + rng.normal(0, 0.1, (61, 61)) + star((61, 61), 31.4, 28.7)
    c = ps.centroid(img, 30.0, 30.0)
    assert c.x == pytest.approx(31.4, abs=0.05) and c.y == pytest.approx(28.7, abs=0.05)
    assert c.snr > 100


def test_centroid_ignores_nan_pixels_and_handles_an_empty_field():
    img = np.zeros((41, 41)) + star((41, 41), 20.0, 20.0)
    img[25, 25] = np.nan
    c = ps.centroid(img, 20.0, 20.0)
    assert c.x == pytest.approx(20.0, abs=0.01) and c.y == pytest.approx(20.0, abs=0.01)
    flat = ps.centroid(np.zeros((41, 41)), 20.0, 20.0)
    assert np.isnan(flat.x) and flat.snr == 0.0


def test_background_ignores_nan_pixels():
    img = np.full((41, 41), 2.0)
    img[20, 29] = np.nan  # inside the 8-12 px annulus
    img[20, 30] = 50.0  # one outlier barely moves the median or the MAD
    med, sigma = ps.background(img, 20, 20, 8, 12)
    assert (med, sigma) == (2.0, 0.0)
    assert all(np.isnan(v) for v in ps.background(img, 20, 20, 30, 31))


def test_variable_report_on_a_synthetic_sequence():
    """A star that doubles in brightness between visits, with a saturated (NaN)
    core, against two constant reference stars."""
    from types import SimpleNamespace

    shape = (61, 61)
    epochs, images = [], {}
    for k, (visit, amp) in enumerate([(1, 100.0), (1, 100.0), (2, 200.0), (2, 200.0)]):
        img = 1.0 + star(shape, 30, 30, amp, sigma=2.5) + star(shape, 10, 50, 50.0)
        img += star(shape, 50, 12, 40.0)
        img[29:32, 29:32] = np.nan  # saturated core, never measured
        eid = f"e{k}"
        images[eid] = img
        epochs.append(SimpleNamespace(id=eid, visit=visit, wavelength_um=1.2 + 0.01 * k,
                                      time_utc=f"2025-0{visit}-01T00:00:00Z"))
    doc = SimpleNamespace(epochs=epochs, image=SimpleNamespace(width_px=61),
                          band=SimpleNamespace(id="D2"), fov_arcmin=12.0)
    rows = ps.report_variable("test", doc, images)
    assert "2 reference stars" in rows[0]
    v1, v2 = (float(r.split()[6]) for r in rows[2:4])  # wing/ref medians
    assert v2 / v1 == pytest.approx(2.0, rel=0.02)


def test_reference_stars_skip_the_target_edges_and_masked_boxes():
    shape = (81, 81)
    rng = np.random.default_rng(5)
    img = rng.normal(0, 0.1, shape)
    for x, y in [(40, 40), (20, 60), (60, 20), (4, 40), (60, 60)]:
        img += star(shape, x, y)
    common = np.ones(shape, dtype=bool)
    common[58:63, 58:63] = False  # (60, 60) is masked in some exposure
    found = ps.find_reference_stars(img, common, 40.0, 40.0)
    assert sorted(found) == [(20, 60), (60, 20)]  # (y, x)
