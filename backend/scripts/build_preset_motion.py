"""Measure the fast movers' motion on the preset visit stacks and add it to index.json.

Run from the repo root or backend/ (no network, from the cutout cache):

    python backend/scripts/build_preset_motion.py
    python backend/scripts/check_presets.py

For each mover (``preset_science.MOVERS``) the fov-12 exposures are cleaned again
from the cache and stacked per visit (``app.pipeline.stack``). The target is then
located on each visit stack's ``measurable`` image:

- unsaturated: the flux-weighted centroid (``preset_science.centroid``);
- saturated core (filled pixels near the centre in every visit): the centre of
  the filled-pixel mask, a position from geometry only, because the wings alone
  under-read the shift.

The first-to-last-visit shift is compared with the SIMBAD proper motion over the
same time. ``index.json`` gets ``motion`` on each mover's entry (the model is
``PresetMotion`` in preset_photometry.py); no other field changes.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
from build_preset_photometry import update_index
from build_presets import PRESETS_DIR
from irsa_coverage import PRESETS
from preset_photometry import PresetMotion, Shift, VisitPosition
from preset_science import (
    DAYS_PER_YEAR,
    MOVERS,
    PROPER_MOTION,
    RECENTRE,
    SATURATED_HALF,
    _radius,
    centroid,
    cleaned_frames,
    load_manifest,
    visit_groups,
)

from app.config import Settings
from app.models import EpochsResponse
from app.pipeline.stack import stack_frames

CORE_SEARCH_PX = 6.0  # filled pixels this close to the centre belong to the target
VISIBLE_TOLERANCE = 0.5  # measured shift within 50 % of the expected one (vector)
MIN_SNR = 20.0  # unsaturated stacks need this S/N for a "visible" verdict

METHOD = (
    "Target position on each visit stack (the mean of the visit's cleaned, north-up "
    "exposures, fov 12): the flux-weighted centroid, or for a saturated star the "
    "centre of its saturated-core mask. x grows to the right (west) and y downwards "
    "(south), in pixels of pixel_scale_arcsec; shifts are last visit minus first "
    "visit, and pa_deg is the direction east of north."
)


def shift(dx_px: float, dy_px: float, dt_yr: float, scale: float) -> Shift:
    east, north = -dx_px * scale, -dy_px * scale  # east is left, north is up
    total = math.hypot(east, north)
    return Shift(dx_px=round(dx_px, 2), dy_px=round(dy_px, 2),
                 shift_arcsec=round(total, 2),
                 rate_arcsec_yr=round(total / dt_yr, 2) if dt_yr > 0 else 0.0,
                 pa_deg=round(math.degrees(math.atan2(east, north)) % 360.0, 1))


def measure_motion(pid: str, doc: EpochsResponse, frames: dict,
                   settings: Settings) -> PresetMotion:
    scale = doc.image.pixel_scale_arcsec
    c0 = (doc.image.width_px - 1) / 2.0
    visits = []
    for visit, epochs in visit_groups(doc).items():
        st = stack_frames([frames[e.id] for e in epochs], settings)
        stack = st.measurable
        near = _radius(stack.shape, c0, c0) <= CORE_SEARCH_PX
        core = st.filled & near if st.filled is not None else None
        if core is not None and core.any():
            ys, xs = np.nonzero(core)
            x, y, snr, saturated = float(xs.mean()), float(ys.mean()), math.inf, True
        else:
            c = centroid(stack, c0, c0, half=3)
            x, y, snr, saturated = c.x, c.y, c.snr, False
        visits.append(VisitPosition(
            visit=visit, n_frames=len(epochs), time_utc=epochs[0].time_utc,
            mjd=round(float(np.mean([e.mjd for e in epochs])), 6),
            x_px=round(x, 2), y_px=round(y, 2),
            snr=None if saturated else round(snr, 1), saturated=saturated))
    if any(v.saturated for v in visits) and not all(v.saturated for v in visits):
        # mixed: use the wing centroid everywhere, so all visits use one method
        for i, (visit, epochs) in enumerate(visit_groups(doc).items()):
            stack = stack_frames([frames[e.id] for e in epochs], settings).measurable
            c = centroid(stack, c0, c0, half=SATURATED_HALF, recentre=RECENTRE)
            visits[i] = visits[i].model_copy(update={"x_px": round(c.x, 2),
                                                     "y_px": round(c.y, 2)})
    first, last = visits[0], visits[-1]
    dt_yr = (last.mjd - first.mjd) / DAYS_PER_YEAR
    pmra, pmdec = PROPER_MOTION[pid]
    measured = shift(last.x_px - first.x_px, last.y_px - first.y_px, dt_yr, scale)
    exp_dx = -pmra * dt_yr / 1000.0 / scale
    exp_dy = -pmdec * dt_yr / 1000.0 / scale
    expected = shift(exp_dx, exp_dy, dt_yr, scale)
    miss = math.hypot(measured.dx_px - exp_dx, measured.dy_px - exp_dy)
    snr_ok = all(v.saturated or (v.snr or 0) >= MIN_SNR for v in visits)
    visible = snr_ok and miss <= VISIBLE_TOLERANCE * math.hypot(exp_dx, exp_dy)
    note = (f"Measured {measured.shift_arcsec:.1f}″ towards PA {measured.pa_deg:.0f}° "
            f"against {expected.shift_arcsec:.1f}″ towards {expected.pa_deg:.0f}° "
            f"expected over {dt_yr:.2f} yr.")
    if not visible:
        note += (" The target is too faint in these stacks to measure its motion; "
                 "the expected track is from the catalogue proper motion.")
    return PresetMotion(method=METHOD, pixel_scale_arcsec=scale,
                        first_visit=first.visit, last_visit=last.visit,
                        dt_yr=round(dt_yr, 3), proper_motion_mas_yr=(pmra, pmdec),
                        visits=visits, measured=measured, expected=expected,
                        verdict="visible" if visible else "not visible", note=note)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preset", action="append", choices=list(MOVERS))
    args = parser.parse_args(argv)
    wanted = args.preset or list(MOVERS)
    settings = Settings.from_env()
    fields: dict[str, dict] = {}
    for pid, *_rest in PRESETS:
        if pid not in wanted:
            continue
        doc = load_manifest(pid)
        motion = measure_motion(pid, doc, cleaned_frames(doc, settings), settings)
        fields[pid] = {"motion": motion.model_dump(mode="json")}
        m, e = motion.measured, motion.expected
        print(f"{pid}: {motion.verdict}; measured dx {m.dx_px:+.2f} dy {m.dy_px:+.2f} px "
              f"({m.shift_arcsec} arcsec, PA {m.pa_deg}); expected dx {e.dx_px:+.2f} "
              f"dy {e.dy_px:+.2f} ({e.shift_arcsec} arcsec, PA {e.pa_deg}); "
              f"saturated {[v.saturated for v in motion.visits]}")
    update_index(PRESETS_DIR, fields)
    return 0


if __name__ == "__main__":
    sys.exit(main())
