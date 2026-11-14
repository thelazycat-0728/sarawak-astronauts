"""Build a cleaned SPHEREx frame sequence and write manifest.json plus frames/*.png.

Run from the repo root or from backend/ (needs network for the IRSA query, and for
any cutout that is not cached yet):

    python backend/scripts/build_sequence.py --preset barnards-star --mode per_visit
    python backend/scripts/build_sequence.py --ra 269.4464 --dec 4.7668 --band D2 \
        --fov 10 --mode all --out backend/.cache/sequences/barnard-all

``--preset`` fills in ra, dec and band from ``PRESETS`` in irsa_coverage.py
(explicit options still win); fov defaults to 10 arcmin (the API default). It
writes root-relative ``/presets/<id>/frames/<epoch>.png`` URLs; without a preset
the URLs are ``frames/<epoch>.png``. This is a debugging tool: the committed
presets (fov 12, 6 and 3) come from build_presets.py. The default output
folder is ``backend/.cache/sequences/<preset or position>-<mode>/``, which git
ignores. This script never writes into frontend/. Exit code 1 if the IRSA query
fails or no frame could be built while IRSA listed exposures.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from irsa_coverage import PRESETS

from app.config import Settings
from app.errors import ApiError
from app.models import BAND_IDS, EpochsResponse

DEFAULT_FOV = 10.0  # the API's default fov (API.md section 1); presets use build_presets.py


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preset", choices=[p[0] for p in PRESETS])
    parser.add_argument("--ra", type=float)
    parser.add_argument("--dec", type=float)
    parser.add_argument("--band", choices=BAND_IDS)
    parser.add_argument("--fov", type=float)
    parser.add_argument("--mode", choices=("per_visit", "all"), default="per_visit")
    parser.add_argument("--out", type=Path, help="output folder (created if needed)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    name = None
    ra, dec, band, fov = args.ra, args.dec, args.band, args.fov
    if args.preset:
        _pid, name, p_ra, p_dec, p_band, _simbad = next(
            p for p in PRESETS if p[0] == args.preset)
        ra = p_ra if ra is None else ra
        dec = p_dec if dec is None else dec
        band = band or p_band
    if ra is None or dec is None:
        parser.error("give --preset, or both --ra and --dec")
    band = band or "D2"
    fov = DEFAULT_FOV if fov is None else fov
    label = args.preset or f"{ra:.4f}_{dec:+.4f}_{band}"
    out = args.out or BACKEND / ".cache" / "sequences" / f"{label}-{args.mode}"

    if args.preset:
        def frame_url(epoch_id: str, vmin: float, vmax: float) -> str:
            return f"/presets/{args.preset}/frames/{epoch_id}.png"
    else:
        def frame_url(epoch_id: str, vmin: float, vmax: float) -> str:
            return f"frames/{epoch_id}.png"

    started = time.monotonic()
    from app.pipeline import build_sequence  # imports reproject (slow on first use)

    import_s = time.monotonic() - started
    settings = Settings.from_env()
    print(f"{label}: ra={ra} dec={dec} band={band} fov={fov:g}' mode={args.mode}")
    try:
        seq = build_sequence(ra, dec, band, fov, frame_url=frame_url, mode=args.mode,
                             name=name, settings=settings)
    except ApiError as exc:
        print(f"FAILED: {exc.code}: {exc.message}")
        return 1
    resp = seq.response
    EpochsResponse.model_validate_json(resp.model_dump_json())  # round-trip check

    t0 = time.monotonic()
    frames_dir = out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("*.png"):
        if old.stem not in seq.frames:
            old.unlink()
    for epoch_id, png in seq.frames.items():
        (frames_dir / f"{epoch_id}.png").write_bytes(png)
    (out / "manifest.json").write_text(
        json.dumps(resp.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    write_s = time.monotonic() - t0
    total_s = time.monotonic() - started

    statuses = [e.quality.status for e in resp.epochs]
    visits = sorted({e.visit for e in resp.epochs})
    print(f"frames      {resp.count} ({', '.join(f'{s} {statuses.count(s)}' for s in ('good', 'warn', 'bad'))})")
    print(f"visits      {len(visits)} {visits}")
    print(f"downloads   {seq.downloads}, cache hits {seq.cache_hits}, "
          f"failures {len(seq.failures)}")
    if args.mode == "per_visit":
        tried = len(seq.attempts)
        rejected = [a for a in seq.attempts if a.outcome != "accepted"]
        print(f"candidates  {tried} tried, {len(rejected)} rejected, "
              f"skipped visits {seq.skipped_visits}")
        for a in rejected:
            print(f"  visit {a.visit} {a.epoch_id}: {a.outcome} ({a.detail})")
    print(f"stretch     asinh vmin={resp.stretch.vmin} vmax={resp.stretch.vmax} MJy/sr")
    print(f"image       {resp.image.width_px}x{resp.image.height_px} px at "
          f"{resp.image.pixel_scale_arcsec}\"/px")
    print(f"releases    {resp.releases}, duplicates removed {resp.duplicates_removed}")
    timings = ", ".join(f"{k} {v:.2f} s" for k, v in seq.timings_s.items())
    print(f"timings     import {import_s:.2f} s, {timings}, write {write_s:.2f} s, "
          f"wall {total_s:.2f} s")
    for failure in seq.failures:
        print(f"FAILED {failure.epoch_id}: {failure.code}: {failure.reason}")
    print(f"wrote       {out / 'manifest.json'} and {len(seq.frames)} frames")
    return 1 if resp.count == 0 and (seq.failures or seq.attempts) else 0


if __name__ == "__main__":
    sys.exit(main())
