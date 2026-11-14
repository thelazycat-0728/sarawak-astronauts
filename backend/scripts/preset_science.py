"""Measure the preset targets on the cleaned float images (T-006 science check).

Run from the repo root or backend/ after build_presets.py (offline: it reads the
fov 12 manifests in frontend/public/presets/ and the cached cutouts):

    python backend/scripts/preset_science.py
    python backend/scripts/preset_science.py --sheets backend/.cache/scratch

Pixel coordinates are x right (west), y down (south), 0-based, on the north-up
grid at 6.2"/px; the target is at the centre. Every exposure is cleaned again from
its cached cutout, so nothing is measured on the 8-bit PNGs.

- **Movers** (barnards-star, eps-indi-b, wise-0855): per visit, the visit stack
  (``app.pipeline.stack``, the same stack as the preset's ``stacks/visit-<n>.png``)
  is measured on its ``measurable`` image, so filled saturated pixels never count.
  It is centroided (flux-weighted, 7x7 box on the brightest valid pixel within
  4 px of the centre, background = median of the 8-12 px annulus). ``snr`` is
  the 3x3 sum above background over its noise (from the annulus MAD);
  ``single-snr`` is the median of the same on the visit's single exposures.
  When the stack has filled (saturated) pixels within 6 px of the centre, they
  are NaN, so the centroid uses an 11x11 box and 5 re-centring passes on the
  wings, and the centre of the filled-pixel mask (a position, no filled value
  used) is reported as a second motion measure.
  The expected shift uses the SIMBAD proper motion between the visit mean times.
- **Variables** (rw-aur, r-dra): saturated cores are *not* filled here
  (``pipeline_fill_saturated=False``), so they are NaN like masked pixels. Per
  exposure, ``wing`` is the mean surface brightness above background (median of
  the 12-18 px annulus) over the *valid* pixels of the 3-6 px annulus round the
  target (at least half of them must be valid), so saturated, masked and filled
  pixels never count. ``ref`` is the median, over up to 8 isolated field stars,
  of each star's 2.5 px aperture flux divided by its mean over all exposures (an
  aperture counts only where all its pixels are valid). Both are normalised to
  their mean over all exposures; ``wing/ref`` divides out exposure-to-exposure
  calibration and wavelength differences that affect every star alike. Values
  per visit are medians over the visit's exposures.

``--sheets DIR`` also writes, per preset, a contact sheet of the fov 12 frames
(one row per visit, 3x enlarged) and a stack sheet (one row per fov: the first
frame, the visit stacks, the stack diff and the frame diff) for viewing.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import maximum_filter

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from build_presets import PRESET_FOVS, PRESETS_DIR
from irsa_coverage import PRESETS

from app.config import Settings
from app.cutouts import cutout_key, default_cache, make_view
from app.irsa.epochs import parse_epoch_id
from app.models import EpochsResponse
from app.pipeline.stack import stack_frames

PUBLIC = REPO / "frontend" / "public"
SCALE_ARCSEC = 6.2
MOVERS = ("barnards-star", "eps-indi-b", "wise-0855")
# SIMBAD proper motions (mas/yr: pmra*cos(dec), pmdec), from irsa_coverage.py's run.
PROPER_MOTION = {
    "barnards-star": (-802.0, 10362.0),
    "eps-indi-b": (3982.0, -2467.0),
    "wise-0855": (-8124.0, 673.0),
}
DAYS_PER_YEAR = 365.25
REF_VALID_SHARE = 0.8  # a reference star's box must be valid in 80 % of exposures
MIN_RING_SHARE = 0.5  # an exposure's wing value needs half the ring valid
RECENTRE = 5  # centroid iterations when a saturated core is NaN
SATURATED_HALF = 5  # and the centroid box / circle half-size then

# ------------------------------------------------------------------ measurements


def _radius(shape: tuple[int, int], x: float, y: float) -> np.ndarray:
    yy, xx = np.indices(shape)
    return np.hypot(xx - x, yy - y)


def background(img: np.ndarray, x: float, y: float, r_in: float, r_out: float
               ) -> tuple[float, float]:
    """(median, robust sigma from the MAD) of valid pixels in an annulus."""
    r = _radius(img.shape, x, y)
    ring = img[(r >= r_in) & (r <= r_out) & np.isfinite(img)]
    if ring.size == 0:
        return math.nan, math.nan
    med = float(np.median(ring))
    return med, float(1.4826 * np.median(np.abs(ring - med)))


@dataclass(frozen=True)
class Centroid:
    x: float
    y: float
    snr: float


def centroid(img: np.ndarray, x0: float, y0: float, search: float = 4.0,
             half: int = 3, recentre: int = 0) -> Centroid:
    """Flux-weighted centroid in a (2*half+1)^2 box on the brightest valid pixel
    within ``search`` px of (x0, y0); NaN pixels get no weight. ``recentre`` > 0
    then repeats the weighting that many times in a radius ``half + 0.5`` circle
    round the previous estimate, so a star with a missing (saturated, NaN) core
    is centred on its wings instead of on the brightest pixel at the hole's edge."""
    bg, sigma = background(img, x0, y0, 8.0, 12.0)
    r = _radius(img.shape, x0, y0)
    near = np.where((r <= search) & np.isfinite(img), img, -np.inf)
    py, px = np.unravel_index(int(np.argmax(near)), img.shape)
    ys = slice(max(0, py - half), py + half + 1)
    xs = slice(max(0, px - half), px + half + 1)
    box = np.nan_to_num(img[ys, xs] - bg, nan=0.0).clip(min=0.0)
    yy, xx = np.mgrid[ys, xs]
    total = float(box.sum())
    if total <= 0:
        return Centroid(math.nan, math.nan, 0.0)
    core = np.nan_to_num(img[max(0, py - 1):py + 2, max(0, px - 1):px + 2] - bg, nan=0.0)
    snr = float(core.sum()) / (sigma * 3.0) if sigma > 0 else math.inf
    cx, cy = float((box * xx).sum() / total), float((box * yy).sum() / total)
    weights = np.nan_to_num(img - bg, nan=0.0).clip(min=0.0)
    gy, gx = np.indices(img.shape)
    for _ in range(recentre):
        w = np.where(_radius(img.shape, cx, cy) <= half + 0.5, weights, 0.0)
        if float(w.sum()) <= 0:
            break
        cx, cy = float((w * gx).sum() / w.sum()), float((w * gy).sum() / w.sum())
    return Centroid(cx, cy, snr)


def find_reference_stars(stack: np.ndarray, common: np.ndarray, x0: float, y0: float,
                         n: int = 8, min_dist: float = 15.0, edge: int = 9,
                         nsigma: float = 20.0) -> list[tuple[int, int]]:
    """Up to ``n`` brightest isolated stars (local maxima above ``nsigma``) whose
    7x7 box is valid in every exposure, away from the target and the edges."""
    finite = np.where(np.isfinite(stack), stack, -np.inf)
    med = float(np.nanmedian(stack))
    sigma = float(1.4826 * np.nanmedian(np.abs(stack - med)))
    peaks = (finite == maximum_filter(finite, size=7)) & (finite > med + nsigma * sigma)
    ys, xs = np.nonzero(peaks)
    side_y, side_x = stack.shape
    found = []
    for y, x in sorted(zip(ys, xs, strict=True), key=lambda p: -finite[p[0], p[1]]):
        if not (edge <= x < side_x - edge and edge <= y < side_y - edge):
            continue
        if math.hypot(x - x0, y - y0) < min_dist:
            continue
        if not common[y - 3:y + 4, x - 3:x + 4].all():
            continue
        if any(math.hypot(x - fx, y - fy) < 8 for fy, fx in found):
            continue
        found.append((int(y), int(x)))
        if len(found) == n:
            break
    return found


# ------------------------------------------------------------------ loading


def load_manifest(pid: str) -> EpochsResponse:
    path = PRESETS_DIR / pid / "manifest.json"
    return EpochsResponse.model_validate_json(path.read_text(encoding="utf-8"))


def cleaned_frames(doc: EpochsResponse, settings: Settings) -> dict:
    """Epoch id -> CleanFrame at the manifest's view, from the cache."""
    from app.pipeline.render import clean_path

    view = make_view(doc.target.ra, doc.target.dec, doc.fov_arcmin)
    cache = default_cache(settings)
    out = {}
    for e in doc.epochs:
        release, obs_id, band = parse_epoch_id(e.id)
        key = cutout_key(release, obs_id, band, view.request_ra, view.request_dec,
                         view.size_arcmin)
        path = cache.lookup(key)
        if path is None:
            raise SystemExit(f"{e.id}: cutout not cached; run build_presets.py first")
        out[e.id] = clean_path(path, view.ra, view.dec, view.fov_arcmin, settings)
    return out


def cleaned_images(doc: EpochsResponse, settings: Settings) -> dict[str, np.ndarray]:
    """Epoch id -> cleaned float image at the manifest's view, from the cache."""
    return {k: f.image for k, f in cleaned_frames(doc, settings).items()}


def visit_groups(doc: EpochsResponse) -> dict[int, list]:
    groups: dict[int, list] = {}
    for e in doc.epochs:
        groups.setdefault(e.visit, []).append(e)
    return dict(sorted(groups.items()))


# ------------------------------------------------------------------ reports


def nanmean_stack(images: list[np.ndarray]) -> np.ndarray:
    """Per-pixel mean over the valid values; NaN where no image has data."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels
        return np.nanmean(np.stack(images), axis=0)


def report_mover(pid: str, doc: EpochsResponse, images: dict[str, np.ndarray],
                 frames: dict | None = None, settings: Settings | None = None) -> list[str]:
    """Per visit: centroid and S/N on the visit stack. With ``frames`` (epoch id ->
    CleanFrame), the stack is ``app.pipeline.stack`` on those frames and is
    measured on its ``measurable`` image (filled saturated pixels left out); the
    median single-exposure S/N is shown for comparison."""
    side = doc.image.width_px
    c0 = (side - 1) / 2.0
    rows = [(f"{pid} ({doc.band.id}, fov {doc.fov_arcmin:g}, centre {c0:.1f}): "
             "visit  n  date        x      y     snr  single-snr  filled-px  "
             "exposure x/y scatter  saturated-core x/y")]
    first = None
    first_core = last_core = None
    for visit, epochs in visit_groups(doc).items():
        filled_px = 0
        core_xy = None
        if frames is not None:
            st = stack_frames([frames[e.id] for e in epochs], settings)
            stack = st.measurable
            near = _radius(stack.shape, c0, c0) <= 6.0
            if st.filled is not None and (st.filled & near).any():
                sel = st.filled & near
                filled_px = int(sel.sum())
                ys, xs = np.nonzero(sel)
                core_xy = (float(xs.mean()), float(ys.mean()))
        else:
            stack = nanmean_stack([images[e.id] for e in epochs])
        # A saturated core is NaN in the measurable stack: centre on the wings.
        half, again = (SATURATED_HALF, RECENTRE) if filled_px else (3, 0)
        c = centroid(stack, c0, c0, half=half, recentre=again)
        singles = [centroid(images[e.id], c0, c0, half=half, recentre=again)
                   for e in epochs]
        single_snr = float(np.median([s.snr for s in singles]))
        sx = np.nanstd([s.x for s in singles]) if len(singles) > 1 else 0.0
        sy = np.nanstd([s.y for s in singles]) if len(singles) > 1 else 0.0
        mjd = float(np.mean([e.mjd for e in epochs]))
        if first is None:
            first = (mjd, c)
            first_core = core_xy
        last_core = core_xy
        core_text = f"{core_xy[0]:.2f}/{core_xy[1]:.2f}" if core_xy else "-"
        rows.append(f"  {visit:>5} {len(epochs):>2}  {epochs[0].time_utc[:10]}  "
                    f"{c.x:6.2f} {c.y:6.2f} {c.snr:6.1f}  {single_snr:9.1f}  "
                    f"{filled_px:9d}  {sx:.2f}/{sy:.2f}             {core_text}")
    last_mjd, last = mjd, c
    dt_yr = (last_mjd - first[0]) / DAYS_PER_YEAR
    pmra, pmdec = PROPER_MOTION[pid]
    exp_dx = -pmra * dt_yr / 1000.0 / SCALE_ARCSEC  # east is left
    exp_dy = -pmdec * dt_yr / 1000.0 / SCALE_ARCSEC  # north is up
    dx, dy = last.x - first[1].x, last.y - first[1].y
    rows.append(f"  first->last visit: dx {dx:+.2f} px, dy {dy:+.2f} px "
                f"(|d| {math.hypot(dx, dy):.2f} px over {dt_yr:.2f} yr); expected "
                f"dx {exp_dx:+.2f}, dy {exp_dy:+.2f} (|d| {math.hypot(exp_dx, exp_dy):.2f})")
    if first_core and last_core:
        cdx, cdy = last_core[0] - first_core[0], last_core[1] - first_core[1]
        rows.append(f"  saturated core (centre of the filled-pixel mask, no filled "
                    f"values used): dx {cdx:+.2f} px, dy {cdy:+.2f} px")
    return rows


def report_variable(pid: str, doc: EpochsResponse, images: dict[str, np.ndarray]) -> list[str]:
    side = doc.image.width_px
    c0 = (side - 1) / 2.0
    ids = [e.id for e in doc.epochs]
    cube = np.stack([images[i] for i in ids])
    valid = np.isfinite(cube)
    mostly = valid.mean(axis=0) >= REF_VALID_SHARE
    r = _radius(mostly.shape, c0, c0)
    core_nan = [int((~np.isfinite(images[i]) & (r <= 2)).sum()) for i in ids]
    ring = (r >= 3) & (r <= 6)
    stack = nanmean_stack(list(cube))
    refs = find_reference_stars(stack, mostly, c0, c0)
    wing = np.full(len(ids), np.nan)
    aper = np.full(len(ids), np.nan)
    ring_valid = np.zeros(len(ids))
    ref_flux = np.full((len(ids), len(refs)), np.nan)
    target_ap = r <= 3.0
    for k, i in enumerate(ids):
        img = images[i]
        bg, _ = background(img, c0, c0, 12.0, 18.0)
        sel = ring & np.isfinite(img)
        ring_valid[k] = sel.sum() / ring.sum()
        if ring_valid[k] >= MIN_RING_SHARE:
            wing[k] = float((img[sel] - bg).mean())
        if np.isfinite(img[target_ap]).all():  # not saturated or masked
            aper[k] = float((img[target_ap] - bg).sum())
        for j, (y, x) in enumerate(refs):
            ap = _radius(img.shape, x, y) <= 2.5
            if np.isfinite(img[ap]).all():
                rbg, _ = background(img, x, y, 5.0, 8.0)
                ref_flux[k, j] = float((img[ap] - rbg).sum())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        wing = wing / np.nanmean(wing)
        if np.isfinite(aper).any():
            aper = aper / np.nanmean(aper)
        if refs:
            ref_ratio = np.nanmedian(ref_flux / np.nanmean(ref_flux, axis=0), axis=1)
        else:
            ref_ratio = np.ones(len(ids))
    rows = [(f"{pid} ({doc.band.id}, fov {doc.fov_arcmin:g}): {len(refs)} reference stars; "
             f"valid share of the 3-6 px ring {ring_valid.min():.2f}-{ring_valid.max():.2f}; "
             f"core NaN px (r<=2, unfilled) {min(core_nan)}-{max(core_nan)}"),
            ("  visit  n  date        wl_um   wing   ref    wing/ref  (min-max)      "
             "ap/ref (n unsaturated)")]
    for visit, epochs in visit_groups(doc).items():
        idx = [ids.index(e.id) for e in epochs]
        w, rr = wing[idx], ref_ratio[idx]
        q = w / rr
        a = aper[idx] / rr
        n_ap = int(np.isfinite(a).sum())
        ap_text = f"{np.nanmedian(a):6.3f} ({n_ap})" if n_ap else "   -   (0)"
        wl = float(np.mean([e.wavelength_um for e in epochs]))
        rows.append(f"  {visit:>5} {len(epochs):>2}  {epochs[0].time_utc[:10]}  {wl:.3f}  "
                    f"{np.nanmedian(w):5.3f}  {np.nanmedian(rr):5.3f}  {np.nanmedian(q):6.3f}    "
                    f"{np.nanmin(q):.3f}-{np.nanmax(q):.3f}   {ap_text}")
    # SPHEREx sees the target at a different wavelength in every exposure, so
    # compare visits at similar wavelengths (red stars change a lot across D2).
    rows.append("  per exposure, by wavelength (visit: wl_um wing/ref):")
    order = sorted(range(len(ids)), key=lambda k: doc.epochs[k].wavelength_um)
    for k in order:
        rows.append(f"    {doc.epochs[k].visit}: {doc.epochs[k].wavelength_um:.3f} "
                    f"{wing[k] / ref_ratio[k]:.3f}")
    return rows


def contact_sheet(pid: str, doc: EpochsResponse, out: Path, zoom: int = 3) -> Path:
    groups = visit_groups(doc)
    side = doc.image.width_px * zoom
    cols = max(len(v) for v in groups.values())
    sheet = Image.new("L", (cols * (side + 4), len(groups) * (side + 4)), 60)
    for row, epochs in enumerate(groups.values()):
        for col, e in enumerate(epochs):
            with Image.open(PUBLIC / e.frame_url.lstrip("/")) as im:
                big = im.resize((side, side), Image.NEAREST)
            sheet.paste(big, (col * (side + 4), row * (side + 4)))
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"sheet-{pid}.png"
    sheet.save(path)
    return path


def stack_sheet(pid: str, out: Path, tile: int = 360) -> Path:
    """One row per fov (12, 6, 3): the first frame, every visit stack, the stack
    diff and the first-versus-last frame diff, each enlarged to ``tile`` px."""
    rows = []
    for fov in PRESET_FOVS:
        sub = "" if fov == max(PRESET_FOVS) else f"fov-{fov:g}/"
        raw = json.loads((PRESETS_DIR / pid / sub / "manifest.json").read_text(
            encoding="utf-8"))
        urls = [raw["epochs"][0]["frame_url"]]
        urls += [s["url"] for s in raw["stacks"]["visits"]]
        urls += [raw["stacks"]["diff"]["url"], raw["diffs"][0]["url"]]
        rows.append(urls)
    cols = max(len(r) for r in rows)
    sheet = Image.new("L", (cols * (tile + 6), len(rows) * (tile + 6)), 60)
    for r, urls in enumerate(rows):
        for c, url in enumerate(urls):
            with Image.open(PUBLIC / url.lstrip("/")) as im:
                sheet.paste(im.resize((tile, tile), Image.NEAREST),
                            (c * (tile + 6), r * (tile + 6)))
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"stacks-{pid}.png"
    sheet.save(path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preset", action="append", choices=[p[0] for p in PRESETS])
    parser.add_argument("--sheets", type=Path, help="write contact sheets here")
    args = parser.parse_args(argv)
    wanted = args.preset or [p[0] for p in PRESETS]
    display = Settings.from_env()
    photometry = replace(display, pipeline_fill_saturated=False)
    for pid, *_rest in PRESETS:
        if pid not in wanted:
            continue
        doc = load_manifest(pid)
        assert doc.fov_arcmin == max(PRESET_FOVS)
        if pid in MOVERS:
            frames = cleaned_frames(doc, display)
            measured = {k: f.measurable for k, f in frames.items()}
            rows = report_mover(pid, doc, measured, frames, display)
        else:
            rows = report_variable(pid, doc, cleaned_images(doc, photometry))
        print("\n".join(rows))
        if args.sheets:
            print(f"  sheet {contact_sheet(pid, doc, args.sheets)}")
            print(f"  stack sheet {stack_sheet(pid, args.sheets)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
