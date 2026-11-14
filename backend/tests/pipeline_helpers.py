"""Helpers for the pipeline tests: synthetic cutouts with a known WCS, and epochs
that reuse T-004's recorded cutout fixture under other exposure ids."""

from __future__ import annotations

import io
import math
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from app.irsa.epochs import build_epoch_id
from app.irsa.records import EpochQueryResult, EpochRecord
from app.pipeline.flags import DEFAULT_BITS
from tests.cutout_helpers import FOV10, StreamResponse, fixture_exposure, fixture_path

RA, DEC = 269.4464, 4.7668
SHAPE = (141, 149)  # (ny, nx), like the fov=10 fixture
SCALE_ARCSEC = 6.15


def bit(name: str) -> int:
    return 1 << DEFAULT_BITS[name]


def detector_wcs(ra: float = RA, dec: float = DEC, rotation_deg: float = 33.0,
                 flip: bool = True) -> WCS:
    """A TAN WCS rotated on the sky, optionally mirrored (like real detectors)."""
    s = SCALE_ARCSEC / 3600.0
    t = math.radians(rotation_deg)
    cd = np.array([[-s * math.cos(t), s * math.sin(t)], [s * math.sin(t), s * math.cos(t)]])
    if flip:
        cd = cd @ np.array([[1.0, 0.0], [0.0, -1.0]])
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.crpix = [(SHAPE[1] + 1) / 2.0, (SHAPE[0] + 1) / 2.0]
    wcs.wcs.cd = cd
    return wcs


def add_source(image: np.ndarray, wcs: WCS, ra: float, dec: float,
               amplitude: float = 100.0, sigma_px: float = 1.0) -> None:
    x, y = wcs.all_world2pix([[ra, dec]], 0)[0]
    yy, xx = np.mgrid[0:image.shape[0], 0:image.shape[1]]
    image += amplitude * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma_px**2))


def make_hdul(
    image: np.ndarray,
    zodi: np.ndarray | float = 0.0,
    flags: np.ndarray | int = 0,
    wcs: WCS | None = None,
    image_unit: str = "MJy / sr",
    zodi_unit: str = "MJy / sr",
    flag_names: dict[str, int] | None = None,
) -> fits.HDUList:
    """IMAGE, FLAGS and ZODI extensions sharing one WCS, with MSKN bit names."""
    wcs = wcs or detector_wcs()
    header = wcs.to_header()
    img = fits.ImageHDU(np.asarray(image, dtype=np.float32), header=header, name="IMAGE")
    img.header["BUNIT"] = image_unit
    flag_arr = np.broadcast_to(np.asarray(flags, dtype=np.int32), image.shape).copy()
    fl = fits.ImageHDU(flag_arr, header=header, name="FLAGS")
    for name, number in (flag_names or DEFAULT_BITS).items():
        fl.header[f"MSKN{number:04d}"] = name
    zodi_arr = np.broadcast_to(np.asarray(zodi, dtype=np.float32), image.shape).copy()
    zd = fits.ImageHDU(zodi_arr, header=header, name="ZODI")
    zd.header["BUNIT"] = zodi_unit
    return fits.HDUList([fits.PrimaryHDU(), img, fl, zd])


def fixture_with(**edits: Any) -> bytes:
    """The fov=10 fixture as IRSA would serve it, with FLAGS edited.

    ``flags_or`` sets bits on every pixel.
    """
    with fits.open(fixture_path(FOV10)) as hdul:
        hdul = fits.HDUList([h.copy() for h in hdul])
    if "flags_or" in edits:
        hdul["FLAGS"].data = hdul["FLAGS"].data | np.int32(edits["flags_or"])
    buf = io.BytesIO()
    hdul.writeto(buf)
    return buf.getvalue()


def epoch(obs_id: str, visit: int, mjd: float, footprint=None, release: str = "DR1",
          band: str = "D2") -> EpochRecord:
    exp = fixture_exposure(
        FOV10,
        obs_id=obs_id,
        release=release,
        collection=f"spherex_{release.lower()}",
        t_start_mjd=mjd,
        t_end_mjd=mjd + 0.0013,
        access_url=f"https://irsa.ipac.caltech.edu/ibe/data/spherex/x/{obs_id}{band}.fits",
        footprint=footprint,
    )
    return EpochRecord(id=build_epoch_id(release, obs_id, band), visit=visit, exposure=exp)


def square(half_deg: float, ra: float = RA, dec: float = DEC, dra: float = 0.0,
           ddec: float = 0.0) -> tuple[tuple[float, float], ...]:
    """A square footprint of side 2 * half_deg, shifted by (dra, ddec) degrees."""
    c = math.cos(math.radians(dec))
    r0, d0 = ra + dra / c, dec + ddec
    h = half_deg
    return ((r0 - h / c, d0 - h), (r0 + h / c, d0 - h), (r0 + h / c, d0 + h),
            (r0 - h / c, d0 + h))


def query_of(epochs: list[EpochRecord], duplicates_removed: int = 0) -> EpochQueryResult:
    return EpochQueryResult(ra=RA, dec=DEC, band="D2", epochs=tuple(epochs),
                            duplicates_removed=duplicates_removed,
                            exposures_per_release={"DR1": len(epochs), "QR3": 0})


def served(content: bytes) -> StreamResponse:
    return StreamResponse(content)
