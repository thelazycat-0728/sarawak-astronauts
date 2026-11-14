"""Synthetic mock provider.

Serves the Barnard's Star mock sequence from ``mock_assets/`` (byte-identical
copies of frontend/public/mock, written by backend/scripts/make_mock_data.py).
Any position, FOV and band is accepted: the same synthetic frames come back,
with the requested values echoed and the epoch ids and wavelengths adapted to
the requested band. Every response sets ``synthetic: true``.
"""

from __future__ import annotations

import io
import math
import re
from pathlib import Path

import numpy as np
from PIL import Image

from app.errors import NotFound
from app.models import (
    BAND_RANGES_UM,
    BandInfo,
    DiffQuery,
    EpochBase,
    EpochsResponse,
    FrameQuery,
    LightcurvePoint,
    LightcurveQuery,
    LightcurveResponse,
    Target,
    ViewQuery,
)
from app.providers.base import EpochSequence, PrerenderedDiff

ASSETS = Path(__file__).resolve().parent / "mock_assets"
# Must match STRETCH_ASINH_A in backend/scripts/make_mock_data.py (used to undo the stretch).
STRETCH_ASINH_A = 0.03
_ID_RE = re.compile(r"^(mock_\d{8}_\d{2})_(D[1-6])$")
_SAME_TARGET_DEG = 1e-6


def _decode(png: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(png)) as im:
        return np.asarray(im.convert("L"), dtype=np.float64) / 255.0


def _encode(data: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(data.astype(np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


class MockProvider:
    name = "mock"

    def __init__(self, assets: Path = ASSETS):
        doc = EpochsResponse.model_validate_json(
            (assets / "epochs.json").read_text(encoding="utf-8")
        )
        self._doc = doc
        self._lc = LightcurveResponse.model_validate_json(
            (assets / "lightcurve.json").read_text(encoding="utf-8")
        )
        self._band = doc.band.id
        self._stems: list[str] = []
        self._epochs: dict[str, EpochBase] = {}
        self._frames: dict[str, bytes] = {}
        for e in doc.epochs:
            stem = self._stem(e.id)
            self._stems.append(stem)
            self._epochs[stem] = EpochBase.model_validate(e.model_dump())
            self._frames[stem] = (assets / "frames" / f"{e.id}.png").read_bytes()
        self._diffs: dict[tuple[str, str], tuple[float, bytes]] = {}
        for d in doc.diffs:
            png = (assets / "diff" / f"{d.a}__{d.b}.png").read_bytes()
            self._diffs[(self._stem(d.a), self._stem(d.b))] = (d.scale, png)

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _stem(epoch_id: str) -> str:
        m = _ID_RE.match(epoch_id)
        if m is None:
            raise ValueError(f"unexpected mock epoch id {epoch_id!r}")
        return m.group(1)

    def _resolve(self, epoch_id: str, band: str) -> str:
        m = _ID_RE.match(epoch_id)
        if m is None or m.group(2) != band or m.group(1) not in self._frames:
            raise NotFound(f"unknown epoch id {epoch_id} for band {band}")
        return m.group(1)

    def _wavelength(self, wl_um: float, band: str) -> float:
        """Map a mock D2 wavelength to the same relative position in ``band``."""
        lo0, hi0 = BAND_RANGES_UM[self._band]
        lo, hi = BAND_RANGES_UM[band]
        return round(lo + (wl_um - lo0) / (hi0 - lo0) * (hi - lo), 3)

    def _to_mjysr(self, png: bytes) -> np.ndarray:
        """Undo the mock asinh stretch (approximate: 8-bit, clipped at vmin/vmax)."""
        s = self._doc.stretch
        y = _decode(png)
        x = STRETCH_ASINH_A * np.sinh(y * np.arcsinh(1.0 / STRETCH_ASINH_A))
        return s.vmin + x * (s.vmax - s.vmin)

    @staticmethod
    def _stretch(img: np.ndarray, vmin: float, vmax: float) -> np.ndarray:
        x = np.clip((img - vmin) / (vmax - vmin), 0.0, 1.0)
        y = np.arcsinh(x / STRETCH_ASINH_A) / np.arcsinh(1.0 / STRETCH_ASINH_A)
        return np.round(y * 255.0)

    def _target(self, ra: float, dec: float) -> Target:
        t = self._doc.target
        same = abs(ra - t.ra) < _SAME_TARGET_DEG and abs(dec - t.dec) < _SAME_TARGET_DEG
        return Target(ra=ra, dec=dec, name=t.name if same else None)

    # ------------------------------------------------------------ provider API

    def epochs(self, q: ViewQuery) -> EpochSequence:
        image = self._doc.image.model_copy(
            update={
                "pixel_scale_arcsec": round(q.fov * 60.0 / self._doc.image.width_px, 4)
            }
        )
        epochs = [
            self._epochs[stem].model_copy(
                update={
                    "id": f"{stem}_{q.band}",
                    "band": q.band,
                    "wavelength_um": self._wavelength(
                        self._epochs[stem].wavelength_um, q.band
                    ),
                }
            )
            for stem in self._stems
        ]
        return EpochSequence(
            synthetic=True,
            target=self._target(q.ra, q.dec),
            fov_arcmin=q.fov,
            band=BandInfo.for_band(q.band),
            image=image,
            stretch=self._doc.stretch,
            releases=sorted({e.release for e in epochs}),
            duplicates_removed=self._doc.duplicates_removed,
            epochs=epochs,
            prerendered_diffs=[
                PrerenderedDiff(a=f"{a}_{q.band}", b=f"{b}_{q.band}", scale=scale)
                for (a, b), (scale, _) in self._diffs.items()
            ],
            any_pair_diff=True,
        )

    def frame_png(self, epoch_id: str, q: FrameQuery) -> bytes:
        png = self._frames[self._resolve(epoch_id, q.band)]
        s = self._doc.stretch
        if q.vmin is None or q.vmax is None:
            return png
        if math.isclose(q.vmin, s.vmin) and math.isclose(q.vmax, s.vmax):
            return png
        return _encode(self._stretch(self._to_mjysr(png), q.vmin, q.vmax))

    def diff_png(self, q: DiffQuery) -> bytes:
        a, b = self._resolve(q.a, q.band), self._resolve(q.b, q.band)
        pre = self._diffs.get((a, b))
        if pre is not None and (q.scale is None or math.isclose(q.scale, pre[0])):
            return pre[1]
        d = self._to_mjysr(self._frames[b]) - self._to_mjysr(self._frames[a])
        scale = q.scale or float(np.percentile(np.abs(d), 99.5)) or 1.0
        return _encode(np.round(128.0 + 127.0 * np.clip(d / scale, -1.0, 1.0)))

    def lightcurve(self, q: LightcurveQuery) -> LightcurveResponse:
        points = [
            LightcurvePoint.model_validate(
                p.model_dump()
                | {
                    "epoch_id": f"{self._stem(p.epoch_id)}_{q.band}",
                    "wavelength_um": self._wavelength(p.wavelength_um, q.band),
                }
            )
            for p in self._lc.points
        ]
        return self._lc.model_copy(
            update={
                "target": Target(ra=q.ra, dec=q.dec, name=None),
                "band": BandInfo.for_band(q.band),
                "aperture_arcsec": q.aperture,
                "points": points,
            }
        )
