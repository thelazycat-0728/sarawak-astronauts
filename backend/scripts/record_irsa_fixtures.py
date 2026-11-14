"""Re-record the IRSA SIA fixtures used by the offline tests.

Run from the repo root or from backend/ (needs network):

    python backend/scripts/record_irsa_fixtures.py

Writes backend/tests/fixtures/irsa/:

- barnard_d2.xml      the exact query app.irsa.sia sends for Barnard's Star, D2
- barnard_d2_d3.xml   a BAND interval spanning the D2/D3 boundary (both arrays),
                      trimmed to 3 rows per release and array, for the band filter
- empty.xml           Barnard's Star restricted to a TIME window before launch
                      (every sky position tried had coverage, see the T-003 report)
- sia_error.xml       IRSA's error document for a non-numeric POS value

The test results depend on these files, so re-run the tests after recording.
"""

from __future__ import annotations

import io
import sys
import warnings
from collections import defaultdict
from pathlib import Path

import requests
from astropy.io.votable import parse as parse_votable

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import Settings
from app.irsa.sia import USER_AGENT, build_sia_params

OUT = BACKEND / "tests" / "fixtures" / "irsa"
BARNARD = (269.4464, 4.7668)
ROWS_PER_GROUP = 3


def get(settings: Settings, params: list[tuple[str, str]]) -> bytes:
    resp = requests.get(
        settings.irsa_sia_url,
        params=params,
        timeout=180,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    return resp.content


def trim(content: bytes) -> bytes:
    """Keep the first ROWS_PER_GROUP rows per (collection, bandpass)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        votable = parse_votable(io.BytesIO(content), verify="ignore")
    table = next(
        t for r in votable.resources if r.type == "results" for t in r.tables
    )
    seen: dict[tuple[str, str], int] = defaultdict(int)
    keep = []
    for i, row in enumerate(table.array):
        key = (str(row["obs_collection"]), str(row["energy_bandpassname"]))
        if seen[key] < ROWS_PER_GROUP:
            seen[key] += 1
            keep.append(i)
    table.array = table.array[keep]
    out = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        votable.to_xml(out)
    return out.getvalue()


def main() -> int:
    settings = Settings()
    collections = [settings.collection_for(r) for r in settings.spherex_releases]
    OUT.mkdir(parents=True, exist_ok=True)
    ra, dec = BARNARD
    base = build_sia_params(ra, dec, "D2", collections)
    pos = [p for p in base if p[0] == "POS"]
    colls = [p for p in base if p[0] == "COLLECTION"]

    files = {
        "barnard_d2.xml": get(settings, base),
        "barnard_d2_d3.xml": trim(get(settings, pos + [("BAND", "1.55e-6 1.70e-6")] + colls)),
        "empty.xml": get(settings, base + [("TIME", "60000 60001")]),
        "sia_error.xml": get(settings, [("POS", "CIRCLE abc 4.7668 0.0003")] + colls),
    }
    for name, content in files.items():
        (OUT / name).write_bytes(content)
        print(f"{name}: {len(content)} bytes")
    total = sum(p.stat().st_size for p in OUT.iterdir() if p.is_file())
    print(f"total {total} bytes in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
