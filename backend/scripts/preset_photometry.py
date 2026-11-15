"""Preset light curves and spectra: models, file names and the tracked position.

Shared by build_preset_photometry.py (writes them) and check_presets.py (validates
them). Both files sit next to a preset's fov-12 manifest:

- ``<preset>/lightcurve.json``: the ``GET /lightcurve`` shape (API.md section 3),
  measured on **every** exposure of the preset (up to 6 per visit), with ``visit``
  on each point and ``tracking`` when the aperture follows the target's proper
  motion. Sorted by time.
- ``<preset>/spectrum.json`` (only presets with a fetched 6-array set): the same
  points across all detector arrays, each with its ``band`` and ``visit``, sorted
  by wavelength. ``bands`` lists the arrays covered.
- ``coadds`` on a preset's ``index.json`` entry: one co-added image per detector
  array (``CoaddEntry``), from the same fetched 6-array set as the spectrum.
- ``<preset>/gaia.json``: Gaia DR3 stars in the fov-12 field for the overlay
  (``PresetGaia``), with positions moved to the preset's mean epoch.
- ``motion`` on a mover's ``index.json`` entry: per-visit stack positions and the
  measured against the catalogue shift (``PresetMotion``).
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel

from app.models import BandId, BandInfo, LightcurvePoint, LightcurveResponse, Target

LIGHTCURVE_FILE = "lightcurve.json"
SPECTRUM_FILE = "spectrum.json"
COADD_DIR = "coadds"
GAIA_FILE = "gaia.json"
GAIA_REF_EPOCH = 2016.0  # Gaia DR3 positions
GAIA_CREDIT = ("This work has made use of data from the European Space Agency (ESA) "
               "mission Gaia (https://www.cosmos.esa.int/gaia), processed by the Gaia "
               "Data Processing and Analysis Consortium (DPAC).")
APERTURE_ARCSEC = 12.0  # the /lightcurve default
CENTRE_EPOCH = 2025.5  # preset centres are the targets' ~2025.5 positions (API.md section 4)
MJD_J2000 = 51544.5
DAYS_PER_YEAR = 365.25
MAS_PER_DEG = 3.6e6


class Tracking(BaseModel):
    """The aperture follows the target: position = centre + proper motion x (t - ref)."""

    proper_motion_mas_yr: tuple[float, float]  # (pmra * cos(dec), pmdec), SIMBAD
    ref_epoch: float  # Julian year of the preset centre
    note: str


class PresetLightcurvePoint(LightcurvePoint):
    visit: int


class PresetLightcurve(LightcurveResponse):
    points: list[PresetLightcurvePoint]
    tracking: Tracking | None = None


class SpectrumPoint(LightcurvePoint):
    band: BandId
    visit: int


class PresetSpectrum(BaseModel):
    synthetic: bool
    target: Target
    bands: list[BandInfo]
    aperture_arcsec: float
    unit: str = "uJy"
    method: str
    note: str
    tracking: Tracking | None = None
    points: list[SpectrumPoint]


class VisitPosition(BaseModel):
    """The target's position on one visit stack (fov-12 pixels, x west, y south)."""

    visit: int
    n_frames: int
    time_utc: str  # first exposure of the visit
    mjd: float  # mean of the visit's exposures
    x_px: float
    y_px: float
    snr: float | None  # None for a saturated star (the core-mask position)
    saturated: bool


class Shift(BaseModel):
    dx_px: float
    dy_px: float
    shift_arcsec: float
    rate_arcsec_yr: float
    pa_deg: float  # direction of motion, east of north


class PresetMotion(BaseModel):
    """``motion`` on a mover's index.json entry (build_preset_motion.py)."""

    method: str
    pixel_scale_arcsec: float
    first_visit: int
    last_visit: int
    dt_yr: float
    proper_motion_mas_yr: tuple[float, float]
    visits: list[VisitPosition]
    measured: Shift
    expected: Shift
    verdict: Literal["visible", "not visible"]
    note: str


class CoaddStretch(BaseModel):
    vmin: float
    vmax: float
    asinh_a: float


class CoaddEntry(BaseModel):
    """One entry of ``coadds`` on a preset's index.json entry (build_preset_coadds.py):
    the mean of one visit's exposures in one detector array, north up, east left."""

    band: BandId
    url: str
    visit: int
    n_frames: int
    wavelength_min_um: float
    wavelength_max_um: float
    fov_arcmin: float
    width_px: int
    height_px: int
    stretch: CoaddStretch  # this image's own asinh stretch (grey 0 = no data)
    flagged_fraction: float


class GaiaStar(BaseModel):
    source_id: str  # a string: 64-bit ids do not fit a JavaScript number
    ra: float  # Gaia DR3, epoch 2016.0
    dec: float
    pmra: float | None  # mas/yr, times cos(dec); null for 2-parameter solutions
    pmdec: float | None
    parallax: float | None  # mas
    g_mag: float
    ra_epoch: float  # moved by the proper motion to PresetGaia.epoch
    dec_epoch: float


class PresetGaia(BaseModel):
    """``<preset>/gaia.json`` (build_preset_gaia.py)."""

    credit: str
    ref_epoch: float
    epoch: float  # Julian year of the preset's mean SPHEREx exposure
    centre_ra: float
    centre_dec: float
    radius_arcmin: float
    g_mag_limit: float
    max_stars: int
    truncated: bool
    count: int
    stars: list[GaiaStar]


def julian_year(mjd: float) -> float:
    return 2000.0 + (mjd - MJD_J2000) / DAYS_PER_YEAR


def position_at(ra: float, dec: float, mjd: float,
                pm: tuple[float, float] | None) -> tuple[float, float]:
    """The target position at ``mjd``: the preset centre (at CENTRE_EPOCH) moved by
    its proper motion (mas/yr, pmra already times cos(dec)); unchanged without one."""
    if pm is None:
        return ra, dec
    dt = julian_year(mjd) - CENTRE_EPOCH
    pmra, pmdec = pm
    dec_t = dec + pmdec * dt / MAS_PER_DEG
    ra_t = (ra + pmra * dt / MAS_PER_DEG / math.cos(math.radians(dec))) % 360.0
    return ra_t, dec_t


def tracking_for(pm: tuple[float, float] | None) -> Tracking | None:
    if pm is None:
        return None
    return Tracking(proper_motion_mas_yr=pm, ref_epoch=CENTRE_EPOCH,
                    note="The aperture follows the target's proper motion, so a fast "
                         "mover stays centred in every exposure.")
