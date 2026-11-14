"""Live IRSA cutout checks. Deselected by default; run with: python -m pytest -q -m live"""

from __future__ import annotations

import pytest
from astropy.io import fits

from app.config import Settings
from app.cutouts import QueryCache, get_cutout, resolve_epoch
from app.irsa.sia import find_epochs
from app.models import BAND_RANGES_UM

pytestmark = pytest.mark.live

BARNARD = (269.4464, 4.7668)


def test_barnard_d2_cutout_fov10(tmp_path):
    settings = Settings(cutout_cache_dir=str(tmp_path))
    epoch = find_epochs(*BARNARD, "D2", settings=settings).epochs[0]
    cutout = get_cutout(epoch, *BARNARD, 10, settings=settings)
    assert not cutout.from_cache
    with fits.open(cutout.path) as hdul:  # plain astropy, as T-005 will
        names = [h.name for h in hdul]
        assert {"IMAGE", "FLAGS", "ZODI", "WCS-WAVE"} <= set(names)
        assert "VARIANCE" in names and "EPSF" not in names
        assert hdul["IMAGE"].data.ndim == 2 and min(hdul["IMAGE"].data.shape) > 100
    # Near a detector edge the target wavelength can sit slightly outside the
    # API.md band range (1.088-1.660 um seen for D2 in T-004), so allow 0.05 um.
    lo, hi = BAND_RANGES_UM["D2"]
    assert cutout.wavelength_um is not None
    assert lo - 0.05 <= cutout.wavelength_um <= hi + 0.05
    assert 0.0 < cutout.coverage <= 1.0
    assert cutout.size_bytes < 1_000_000

    again = get_cutout(epoch, *BARNARD, 10, settings=settings)
    assert again.from_cache and again.path == cutout.path


def test_barnard_epoch_id_resolves():
    first = find_epochs(*BARNARD, "D2").epochs[0]
    epoch = resolve_epoch(first.id, *BARNARD, query_cache=QueryCache())
    assert epoch.exposure.obs_id == first.exposure.obs_id
