"""Fetch the cutouts for a preset's 6-array spectrum into the spectrum cache.

Run from the repo root or backend/ (needs network):

    python backend/scripts/fetch_spectrum_cutouts.py --preset eps-indi-b
    python backend/scripts/build_preset_photometry.py --preset eps-indi-b   # then build

A spectrum needs every detector array (D1-D6, about 17 wavelengths each), but the
preset cache only holds the preset's own array. For each array this script picks
one visit (the one with the most exposures, or ``--visit``), takes up to
``--per-array`` of its exposures by time, and downloads small cutouts (fov 3', a
5.3' cutout, enough for the largest photometry stamp) into
``backend/.cache/spectra/`` (or ``$SPECTRUM_CACHE_DIR``). That cache is kept apart from the preset cutouts so
its size cap can never evict the cutouts the presets are rebuilt from.

It also writes ``<cache>/<preset>.index.json``: one record per cutout (epoch id,
array, visit, release, mid-exposure time, wavelength at the target, file name),
so build_preset_photometry.py runs offline. A second run downloads nothing. Exit code 1 if no cutout could be fetched.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from irsa_coverage import PRESETS

from app.config import Settings
from app.cutouts import default_cache, fetch_cutouts
from app.errors import ApiError
from app.irsa.sia import find_epochs

BANDS = ("D1", "D2", "D3", "D4", "D5", "D6")
SPECTRUM_FOV_ARCMIN = 3.0
# SPECTRUM_CACHE_DIR overrides it (for example to share one cache between worktrees).
SPECTRUM_CACHE_DIR = Path(os.environ.get("SPECTRUM_CACHE_DIR", BACKEND / ".cache" / "spectra"))
SPECTRUM_CACHE_MAX_MB = 4000.0
MB = 1024 * 1024


def spectrum_settings(settings: Settings | None = None) -> Settings:
    """Settings whose cutout cache is the separate spectrum cache."""
    settings = settings or Settings.from_env()
    return dataclasses.replace(settings, cutout_cache_dir=SPECTRUM_CACHE_DIR,
                               cutout_cache_max_mb=SPECTRUM_CACHE_MAX_MB)


def choose_epochs(epochs: list, visit: int | None, per_array: int) -> list:
    """One visit's epochs (the fullest visit unless ``visit`` is given), first
    ``per_array`` by time."""
    if not epochs:
        return []
    if visit is None:
        counts = collections.Counter(e.visit for e in epochs)
        visit = max(sorted(counts), key=lambda v: counts[v])
    chosen = sorted((e for e in epochs if e.visit == visit),
                    key=lambda e: e.exposure.time_mid_utc)
    return chosen[:per_array]


def index_path(pid: str, settings: Settings | None = None) -> Path:
    return Path((settings or spectrum_settings()).cutout_cache_dir) / f"{pid}.index.json"


def cutout_record(cutout, visit: int) -> dict:
    ex = cutout.exposure
    return {"epoch_id": cutout.epoch_id, "band": ex.band, "visit": visit,
            "release": ex.release, "obs_id": ex.obs_id,
            "time_utc": ex.time_mid_utc, "mjd": round(ex.t_mid_mjd, 6),
            "wavelength_um": cutout.wavelength_um, "coverage": round(cutout.coverage, 4),
            "file": cutout.path.name}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preset", required=True, choices=[p[0] for p in PRESETS])
    parser.add_argument("--visit", type=int, default=None,
                        help="use this visit in every array (default: the fullest per array)")
    parser.add_argument("--per-array", type=int, default=20)
    parser.add_argument("--bands", default=",".join(BANDS))
    args = parser.parse_args(argv)
    pid, name, ra, dec, _band, _simbad = next(p for p in PRESETS if p[0] == args.preset)
    settings = spectrum_settings()
    cache = default_cache(settings)
    print(f"preset {pid} ({name}) at {ra}, {dec}; cache {cache.directory}")
    total = 0
    records: list[dict] = []
    fetched: set[str] = set()
    for band in args.bands.split(","):
        started = time.monotonic()
        try:
            result = find_epochs(ra, dec, band, settings=settings)
        except ApiError as exc:
            print(f"{band}: IRSA query failed: {exc.code}: {exc.message}")
            continue
        epochs = choose_epochs(list(result.epochs), args.visit, args.per_array)
        if not epochs:
            print(f"{band}: no exposures")
            continue
        batch = fetch_cutouts(epochs, ra, dec, SPECTRUM_FOV_ARCMIN,
                              settings=settings, cache=cache)
        waves = [c.wavelength_um for c in batch.cutouts if c.wavelength_um is not None]
        span = f"{min(waves):.3f}-{max(waves):.3f} um" if waves else "-"
        print(f"{band}: visit {epochs[0].visit}, {len(batch.cutouts)}/{len(epochs)} cutouts "
              f"({batch.downloads} downloaded, {batch.cache_hits} cached, "
              f"{len(batch.failures)} failed), {span}, {time.monotonic() - started:.0f} s",
              flush=True)
        for failure in batch.failures:
            print(f"  FAILED {failure.epoch_id}: {failure.code}: {failure.reason}")
        total += len(batch.cutouts)
        visit_of = {e.id: e.visit for e in epochs}
        records += [cutout_record(c, visit_of[c.epoch_id]) for c in batch.cutouts]
        fetched.add(band)
    out = index_path(pid, settings)
    if out.is_file():  # an array whose query failed this time keeps its earlier records
        earlier = json.loads(out.read_text(encoding="utf-8")).get("cutouts", [])
        kept = [r for r in earlier if r.get("band") not in fetched]
        if kept:
            print(f"kept {len(kept)} earlier records for "
                  f"{sorted({r['band'] for r in kept})} (not fetched this run)")
        records += kept
    records.sort(key=lambda r: (r["band"], r["mjd"]))
    out.write_text(json.dumps({"preset": pid, "ra": ra, "dec": dec,
                               "fov_arcmin": SPECTRUM_FOV_ARCMIN, "cutouts": records},
                              indent=1) + "\n", encoding="utf-8")
    print(f"index {out} ({len(records)} cutouts)")
    print(f"total {total} cutouts; cache {cache.total_bytes() / MB:.0f} MB")
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
