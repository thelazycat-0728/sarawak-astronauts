"""Live SPHEREx coverage check for the five preset targets (docs/API.md section 4).

Run from the repo root (needs network):

    python backend/scripts/irsa_coverage.py

Prints one row per preset: exposures per release, duplicates removed, visits and
first and last date in the preset's band. If SIMBAD answers, a second table gives
each preset's SIMBAD position and its separation from the API.md centre, both as
catalogued (J2000) and moved to 2025.5 with SIMBAD's proper motion.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from pyvo.dal.exceptions import DALAccessError

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import Settings
from app.errors import ApiError
from app.irsa.sia import find_epochs

# Network (requests errors are OSErrors), HTTP/VO service and parse failures.
SIMBAD_ERRORS = (OSError, ValueError, KeyError, DALAccessError)

# id, name, ra, dec, band, SIMBAD identifier (API.md section 4)
PRESETS = (
    ("barnards-star", "Barnard's Star", 269.4464, 4.7668, "D2", "Barnard's star"),
    ("eps-indi-b", "Epsilon Indi B", 331.0951, -56.8007, "D2", "eps Ind B"),
    # SIMBAD position moved to 2025.5 (T-003 found the old 133.7951, -7.2451 2.04' off).
    ("wise-0855", "WISE 0855-0714", 133.7609, -7.2423, "D6", "WISE J085510.83-071442.5"),
    ("rw-aur", "RW Aurigae", 76.9564, 30.4014, "D2", "RW Aur"),
    ("r-dra", "R Draconis", 248.1674, 66.7550, "D2", "R Dra"),
)
CENTRE_EPOCH = 2025.5  # API.md: fast movers are centred on their ~2025.5 position


def separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle distance (haversine), degrees in, arcseconds out."""
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    h = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h)))) * 3600


def coverage_rows(settings: Settings) -> list[str]:
    header = (
        f"{'preset':<14} {'band':<4} {'per release':<16} {'dups':>4} {'visits':>6} "
        f"{'first (UTC)':<11} {'last (UTC)':<11} exposures per visit"
    )
    rows = [header, "-" * len(header)]
    for pid, _name, ra, dec, band, _simbad in PRESETS:
        try:
            result = find_epochs(ra, dec, band, settings=settings)
        except ApiError as exc:
            rows.append(f"{pid:<14} {band:<4} ERROR {exc.code}: {exc.message}")
            continue
        per_release = " ".join(f"{k}={v}" for k, v in result.exposures_per_release.items())
        epochs = result.epochs
        first = epochs[0].exposure.time_mid_utc[:10] if epochs else "-"
        last = epochs[-1].exposure.time_mid_utc[:10] if epochs else "-"
        per_visit = [0] * result.visit_count
        for e in epochs:
            per_visit[e.visit - 1] += 1
        rows.append(
            f"{pid:<14} {band:<4} {per_release:<16} {result.duplicates_removed:>4} "
            f"{result.visit_count:>6} {first:<11} {last:<11} {per_visit}"
        )
    return rows


def _num(value: object) -> float:
    """Float from a SIMBAD cell; 0 when masked or missing (no proper motion known)."""
    if value is None or np.ma.is_masked(value):
        return 0.0
    number = float(value)  # type: ignore[arg-type]
    return number if math.isfinite(number) else 0.0


def _simbad_client():
    """A SIMBAD client on the first server (CDS, then the Harvard mirror) that answers.

    Returns (client, server name, list of failure notes)."""
    from astroquery.simbad import SimbadClass, conf

    notes = []
    for server in conf.servers_list:
        try:
            with conf.set_temp("server", server):
                simbad = SimbadClass(timeout=30)
                simbad.add_votable_fields("pmra", "pmdec")  # first network call
            return simbad, server, notes
        except SIMBAD_ERRORS as exc:
            notes.append(f"{server}: {type(exc).__name__}: {str(exc).splitlines()[0][:90]}")
    return None, None, notes


def simbad_rows() -> list[str]:
    try:
        from astroquery.simbad import conf
    except ImportError as exc:
        return [f"SIMBAD skipped: astroquery not installed ({exc})"]
    simbad, server, notes = _simbad_client()
    rows = [f"SIMBAD failed on {n}" for n in notes]
    if simbad is None:
        return rows + ["SIMBAD unreachable on every server; positions not checked"]
    rows.append(f"SIMBAD server: {server}")
    header = (
        f"{'preset':<14} {'SIMBAD ra':>10} {'SIMBAD dec':>10} {'pm (mas/yr)':>12} "
        f"{'ra 2025.5':>10} {'dec 2025.5':>10} {'sep J2000':>9} {'sep 2025.5':>10}"
    )
    rows += [header, "-" * len(header)]
    for pid, _name, ra, dec, _band, ident in PRESETS:
        try:
            with conf.set_temp("server", server):
                table = simbad.query_object(ident)
        except SIMBAD_ERRORS as exc:
            rows.append(f"{pid:<14} SIMBAD error: {type(exc).__name__}: {str(exc)[:80]}")
            continue
        if table is None or len(table) == 0:
            rows.append(f"{pid:<14} SIMBAD has no object named {ident!r}")
            continue
        row = table[0]
        s_ra, s_dec = float(row["ra"]), float(row["dec"])
        pmra = _num(row["pmra"])
        pmdec = _num(row["pmdec"])
        years = CENTRE_EPOCH - 2000.0
        moved_dec = s_dec + pmdec * years / 3.6e6
        moved_ra = s_ra + pmra * years / 3.6e6 / math.cos(math.radians(s_dec))
        sep_j2000 = separation_arcsec(ra, dec, s_ra, s_dec)
        sep_now = separation_arcsec(ra, dec, moved_ra, moved_dec)
        pm = f"{pmra:.0f},{pmdec:.0f}"
        rows.append(
            f"{pid:<14} {s_ra:>10.5f} {s_dec:>10.5f} {pm:>12} "
            f"{moved_ra:>10.5f} {moved_dec:>10.5f} "
            f"{sep_j2000 / 60:>8.2f}' {sep_now / 60:>9.2f}'"
        )
    return rows


def main() -> int:
    settings = Settings()
    releases = ", ".join(
        f"{r}={settings.collection_for(r)}" for r in settings.spherex_releases
    )
    print(f"IRSA SIA {settings.irsa_sia_url} ({releases})")
    print("\n".join(coverage_rows(settings)))
    print()
    print("\n".join(simbad_rows()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
