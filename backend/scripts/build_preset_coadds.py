"""Co-add each detector array for presets with a fetched 6-array set (T-008), offline.

Run from the repo root or backend/ after fetch_spectrum_cutouts.py (no network):

    python backend/scripts/build_preset_coadds.py
    python backend/scripts/check_presets.py

For every array in ``backend/.cache/spectra/<preset>.index.json`` (one visit per
array, about 20 exposures), each exposure is cleaned onto the same north-up fov-3
grid (ZODI subtraction, FLAGS mask, reprojection, the live pipeline) and the
frames, each minus its own median sky level, are averaged over their valid pixels (``app.pipeline.stack``). Averaging
about 20 exposures lowers the noise by about sqrt(20), and each array spans its
own wavelength range, so the six images show how the field changes colour from
0.75 to 5 um. Each co-add has its own asinh stretch (robust percentiles of its
measurable pixels), so the images are not on one brightness scale; the spectrum
gives the quantitative comparison.

Writes ``<preset>/coadds/<band>.png`` and ``coadds`` on the preset's entry in
``index.json`` (``CoaddEntry`` in preset_photometry.py); nothing else changes.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import warnings
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
from build_preset_photometry import update_index
from build_presets import PRESETS_DIR, URL_ROOT
from fetch_spectrum_cutouts import SPECTRUM_FOV_ARCMIN, index_path, spectrum_settings
from irsa_coverage import PRESETS
from preset_photometry import COADD_DIR, CoaddEntry, CoaddStretch, position_at
from preset_science import PROPER_MOTION

from app.config import Settings
from app.pipeline.render import clean_path, compute_stretch
from app.pipeline.stack import stack_frames, stack_png


def minus_sky(frame):
    """The frame minus the median of its measurable pixels (its residual sky level
    after ZODI subtraction). Without this, frames with slightly different sky
    levels make blocky steps in the mean wherever some frames are masked."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        sky = float(np.nanmedian(frame.measurable))
    if not np.isfinite(sky):
        return frame
    return dataclasses.replace(frame, image=frame.image - sky)


def build_coadds(preset: tuple, settings: Settings,
                 out_root: Path = PRESETS_DIR) -> list[CoaddEntry] | None:
    """Write the co-add PNGs of one preset; None without a fetched 6-array set."""
    pid, _name, ra0, dec0, _band, _simbad = preset
    spec = spectrum_settings(settings)
    idx = index_path(pid, spec)
    if not idx.is_file():
        return None
    records = [r for r in json.loads(idx.read_text(encoding="utf-8"))["cutouts"]
               if r.get("wavelength_um") is not None]
    directory = Path(spec.cutout_cache_dir)
    pm = PROPER_MOTION.get(pid)
    entries = []
    for band in sorted({r["band"] for r in records}):
        recs = [r for r in records if r["band"] == band]
        mean_mjd = sum(r["mjd"] for r in recs) / len(recs)
        ra, dec = position_at(ra0, dec0, mean_mjd, pm)  # centred on the target then
        frames = [minus_sky(clean_path(directory / r["file"], ra, dec, SPECTRUM_FOV_ARCMIN,
                                       settings)) for r in recs]
        st = stack_frames(frames, settings)
        vmin, vmax = compute_stretch([st.measurable], settings)
        png = stack_png(st, vmin, vmax, settings)
        path = out_root / pid / COADD_DIR / f"{band}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        waves = [r["wavelength_um"] for r in recs]
        entries.append(CoaddEntry(
            band=band, url=f"{URL_ROOT}/{pid}/{COADD_DIR}/{band}.png", visit=recs[0]["visit"],
            n_frames=st.n_frames, wavelength_min_um=round(min(waves), 4),
            wavelength_max_um=round(max(waves), 4), fov_arcmin=SPECTRUM_FOV_ARCMIN,
            width_px=st.image.shape[1], height_px=st.image.shape[0],
            stretch=CoaddStretch(vmin=vmin, vmax=vmax, asinh_a=settings.stretch_asinh_a),
            flagged_fraction=round(st.flagged_fraction, 4)))
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preset", action="append", choices=[p[0] for p in PRESETS])
    args = parser.parse_args(argv)
    wanted = args.preset or [p[0] for p in PRESETS]
    settings = Settings.from_env()
    fields: dict[str, dict] = {}
    for preset in PRESETS:
        if preset[0] not in wanted:
            continue
        entries = build_coadds(preset, settings)
        if entries is None:
            continue
        fields[preset[0]] = {"coadds": [e.model_dump(mode="json") for e in entries]}
        print(f"{preset[0]}: " + ", ".join(
            f"{e.band} {e.n_frames} frames {e.wavelength_min_um:.2f}-{e.wavelength_max_um:.2f} um"
            for e in entries))
    if fields:
        update_index(PRESETS_DIR, fields)
    return 0


if __name__ == "__main__":
    sys.exit(main())
