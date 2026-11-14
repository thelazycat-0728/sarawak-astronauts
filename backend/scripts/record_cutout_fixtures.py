"""Re-record the SPHEREx cutout fixtures used by the offline tests.

Run from the repo root or from backend/ (needs network):

    python backend/scripts/record_cutout_fixtures.py

Writes backend/tests/fixtures/cutouts/ (EPSF already removed, under 1 MB total):

- barnard_d2_fov10.fits   Barnard's Star, D2, fov=10, the first exposure by time
                          (the target sits near the detector's short-wavelength edge)
- barnard_d2_fov3.fits    Barnard's Star, D2, fov=3, an exposure from the middle
                          of the sequence (target near mid-band)
- cutouts.json            the exposure record and view behind each file

The cutouts go through app.cutouts.get_cutout itself (into a temporary cache),
so the files are exactly what the cache stores.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import Settings
from app.cutouts import get_cutout
from app.irsa.sia import find_epochs

OUT = BACKEND / "tests" / "fixtures" / "cutouts"
BARNARD = (269.4464, 4.7668)
# (file name, index into the time-sorted epochs, fov)
PICKS = (("barnard_d2_fov10.fits", 0, 10.0), ("barnard_d2_fov3.fits", 30, 3.0))
LIMIT_BYTES = 1_000_000


def main() -> int:
    ra, dec = BARNARD
    result = find_epochs(ra, dec, "D2")
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(cutout_cache_dir=tmp)
        for name, index, fov in PICKS:
            epoch = result.epochs[index]
            cutout = get_cutout(epoch, ra, dec, fov, settings=settings)
            shutil.copyfile(cutout.path, OUT / name)
            manifest.append(
                {
                    "file": name,
                    "epoch_id": epoch.id,
                    "ra": ra,
                    "dec": dec,
                    "fov_arcmin": fov,
                    "cache_key": cutout.path.stem,
                    "wavelength_um": cutout.wavelength_um,
                    "coverage": cutout.coverage,
                    "exposure": dataclasses.asdict(epoch.exposure),
                }
            )
            print(f"{name}: {epoch.id} {cutout.size_bytes} bytes "
                  f"wavelength={cutout.wavelength_um} coverage={cutout.coverage}")
    (OUT / "cutouts.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    total = sum(p.stat().st_size for p in OUT.iterdir() if p.is_file())
    print(f"total {total} bytes in {OUT}")
    if total >= LIMIT_BYTES:
        print("fixtures are over 1 MB; pick smaller cutouts", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
