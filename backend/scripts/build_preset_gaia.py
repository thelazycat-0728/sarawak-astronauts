"""Gaia DR3 stars for every preset field, as static files for the overlay (T-011).

Run from the repo root or backend/ (needs network, a few seconds per preset):

    python backend/scripts/build_preset_gaia.py
    python backend/scripts/check_presets.py

For each preset, one cone search of ``gaiadr3.gaia_source`` on the ESA Gaia archive
(TAP) around the preset centre, out to the corner of the fov-12 view (8.5'), for
stars brighter than G = 19, brightest first, at most ``MAX_STARS``. Each star keeps
its Gaia DR3 position (epoch 2016.0) and is also moved by its proper motion to the
preset's mean SPHEREx epoch (``ra_epoch``, ``dec_epoch``), so an overlay lines up
with the frames without any maths in the browser.

Writes ``<preset>/gaia.json`` (``PresetGaia`` in preset_photometry.py) and
``gaia_url`` on the preset's entry in ``index.json``; nothing else changes.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from build_preset_photometry import load_manifest, update_index
from build_presets import PRESETS_DIR, URL_ROOT, _write_json
from irsa_coverage import PRESETS
from preset_photometry import (
    GAIA_CREDIT,
    GAIA_FILE,
    GAIA_REF_EPOCH,
    GaiaStar,
    PresetGaia,
    julian_year,
)

GAIA_TAP = "https://gea.esac.esa.int/tap-server/tap"
RADIUS_ARCMIN = 8.5  # the corner of the fov-12 view (6' * sqrt(2) = 8.49')
G_LIMIT = 19.0
MAX_STARS = 400
MAS_PER_DEG = 3.6e6


def query(ra: float, dec: float):
    """The brightest Gaia DR3 stars in the cone (an astropy Table)."""
    import pyvo

    adql = (
        f"SELECT TOP {MAX_STARS + 1} source_id, ra, dec, pmra, pmdec, parallax, "
        "phot_g_mean_mag FROM gaiadr3.gaia_source WHERE 1=CONTAINS(POINT('ICRS', ra, dec), "
        f"CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {RADIUS_ARCMIN / 60.0:.6f})) "
        f"AND phot_g_mean_mag < {G_LIMIT} ORDER BY phot_g_mean_mag"
    )
    return pyvo.dal.TAPService(GAIA_TAP).run_sync(adql).to_table()


def _num(value, digits: int) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return round(f, digits) if math.isfinite(f) else None


def star_from_row(row, epoch: float) -> GaiaStar:
    ra, dec = float(row["ra"]), float(row["dec"])
    pmra, pmdec = _num(row["pmra"], 3), _num(row["pmdec"], 3)
    ra_e, dec_e = ra, dec
    if pmra is not None and pmdec is not None:  # pmra is already pmra * cos(dec)
        dt = epoch - GAIA_REF_EPOCH
        dec_e = dec + pmdec * dt / MAS_PER_DEG
        ra_e = (ra + pmra * dt / MAS_PER_DEG / math.cos(math.radians(dec))) % 360.0
    return GaiaStar(source_id=str(int(row["source_id"])), ra=round(ra, 7), dec=round(dec, 7),
                    pmra=pmra, pmdec=pmdec, parallax=_num(row["parallax"], 3),
                    g_mag=round(float(row["phot_g_mean_mag"]), 3),
                    ra_epoch=round(ra_e, 7), dec_epoch=round(dec_e, 7))


def build_gaia(preset: tuple, table, epoch: float) -> PresetGaia:
    rows = list(table)
    stars = [star_from_row(r, epoch) for r in rows[:MAX_STARS]]
    stars.sort(key=lambda s: s.g_mag)
    return PresetGaia(credit=GAIA_CREDIT, ref_epoch=GAIA_REF_EPOCH, epoch=round(epoch, 3),
                      centre_ra=preset[2], centre_dec=preset[3],
                      radius_arcmin=RADIUS_ARCMIN, g_mag_limit=G_LIMIT,
                      max_stars=MAX_STARS, truncated=len(rows) > MAX_STARS,
                      count=len(stars), stars=stars)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preset", action="append", choices=[p[0] for p in PRESETS])
    args = parser.parse_args(argv)
    wanted = args.preset or [p[0] for p in PRESETS]
    public = PRESETS_DIR.parent
    fields: dict[str, dict] = {}
    for preset in PRESETS:
        pid = preset[0]
        if pid not in wanted:
            continue
        doc = load_manifest(pid, public)
        epoch = julian_year(sum(e.mjd for e in doc.epochs) / len(doc.epochs))
        gaia = build_gaia(preset, query(preset[2], preset[3]), epoch)
        _write_json(PRESETS_DIR / pid / GAIA_FILE, gaia.model_dump(mode="json"))
        fields[pid] = {"gaia_url": f"{URL_ROOT}/{pid}/{GAIA_FILE}"}
        print(f"{pid}: {gaia.count} Gaia DR3 stars (G < {G_LIMIT:g}, r {RADIUS_ARCMIN}'"
              f"{', truncated' if gaia.truncated else ''}), brightest G {gaia.stars[0].g_mag}, "
              f"epoch {gaia.epoch}")
    if fields:
        update_index(PRESETS_DIR, fields)
    return 0


if __name__ == "__main__":
    sys.exit(main())
