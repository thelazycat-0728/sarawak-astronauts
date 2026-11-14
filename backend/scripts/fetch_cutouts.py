"""Fetch every SPHEREx cutout of a preset into the cutout cache and report timings.

Run from the repo root or from backend/ (needs network):

    python backend/scripts/fetch_cutouts.py --preset barnards-star [--limit N]

Uses the preset centre, band and fov from docs/API.md section 4 and the cache in
``Settings`` (``CUTOUT_CACHE_DIR``, default backend/.cache/cutouts/). A second run
should show 0 downloads. Exit code 1 if the IRSA query fails or every cutout fails.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from irsa_coverage import PRESETS

from app.config import Settings
from app.cutouts import default_cache, fetch_cutouts, make_view
from app.errors import ApiError
from app.irsa.sia import find_epochs

PRESET_FOV_ARCMIN = 10.0  # every preset uses fov=10 (API.md section 4)
MB = 1024 * 1024


def main(argv: list[str] | None = None) -> int:
    ids = [p[0] for p in PRESETS]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preset", required=True, choices=ids)
    parser.add_argument("--limit", type=int, default=None,
                        help="only the first N epochs by time")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    pid, name, ra, dec, band, _simbad = next(p for p in PRESETS if p[0] == args.preset)
    settings = Settings.from_env()
    view = make_view(ra, dec, PRESET_FOV_ARCMIN)
    cache = default_cache(settings)
    print(f"preset {pid} ({name}): ra={ra} dec={dec} band={band} "
          f"fov={view.fov_arcmin:g}' -> cutout {view.size_arcmin:g}'")
    print(f"cache {cache.directory} (cap {settings.cutout_cache_max_mb:g} MB, "
          f"{settings.cutout_workers} workers, timeout {settings.cutout_timeout_s:g} s)")

    started = time.monotonic()
    try:
        result = find_epochs(ra, dec, band, settings=settings)
    except ApiError as exc:
        print(f"IRSA query failed: {exc.code}: {exc.message}")
        return 1
    query_s = time.monotonic() - started
    epochs = list(result.epochs)
    if args.limit is not None:
        epochs = epochs[: args.limit]
    print(f"IRSA query: {len(result.epochs)} epochs in {query_s:.1f} s; "
          f"fetching {len(epochs)}")

    batch = fetch_cutouts(epochs, ra, dec, PRESET_FOV_ARCMIN, settings=settings, cache=cache)
    wall_s = time.monotonic() - started

    print(f"exposures   {len(epochs)}")
    print(f"downloads   {batch.downloads}")
    print(f"cache hits  {batch.cache_hits}")
    print(f"failures    {len(batch.failures)}")
    print(f"MB on disk  {batch.bytes_on_disk / MB:.1f} for this preset "
          f"(cache total {cache.total_bytes() / MB:.1f} MB)")
    print(f"wall time   {wall_s:.1f} s (query {query_s:.1f} s, cutouts {batch.elapsed_s:.1f} s)")

    cold = [c for c in batch.cutouts if not c.from_cache]
    if cold:
        times = [c.elapsed_s for c in cold]
        sizes = [c.size_bytes / 1024 for c in cold]
        print(f"cold cutout median {statistics.median(sizes):.0f} KB on disk, "
              f"median {statistics.median(times):.1f} s (min {min(times):.1f}, "
              f"max {max(times):.1f})")
    if batch.cutouts:
        waves = [c.wavelength_um for c in batch.cutouts if c.wavelength_um is not None]
        covs = [c.coverage for c in batch.cutouts]
        example = batch.cutouts[0]
        print(f"wavelength at target {min(waves):.3f}-{max(waves):.3f} um; "
              f"coverage min {min(covs):.3f}, {sum(c < 1 for c in covs)} below 1")
        print(f"example {example.epoch_id}: wavelength {example.wavelength_um} um, "
              f"coverage {example.coverage:.3f}")
    for failure in batch.failures:
        print(f"FAILED {failure.epoch_id}: {failure.code}: {failure.reason}")
    return 1 if epochs and not batch.cutouts else 0


if __name__ == "__main__":
    sys.exit(main())
