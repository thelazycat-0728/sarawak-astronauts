"""Build the presets' light curves and 6-array spectra, offline, from cached cutouts.

Run from the repo root or backend/ (no network):

    python backend/scripts/build_preset_photometry.py                  # all presets
    python backend/scripts/build_preset_photometry.py --preset eps-indi-b
    python backend/scripts/check_presets.py                            # then validate

**Light curves.** Every exposure in a preset's fov-12 manifest is measured with the
live ``/lightcurve`` photometry (``app.pipeline.photometry``: native pixels,
IMAGE - ZODI, a 12" aperture, a median sky annulus, VARIANCE errors, and null
with ``bad`` when any aperture pixel is masked or saturated) on its cached fov-12
cutout (``backend/.cache/cutouts/``). For the fast movers the aperture follows the
SIMBAD proper motion (``preset_science.PROPER_MOTION``). The result is
``<preset>/lightcurve.json``.

**Spectra.** If fetch_spectrum_cutouts.py has fetched a 6-array set for a preset
(``backend/.cache/spectra/<preset>.index.json``), every cutout in it is measured
the same way. The result is ``<preset>/spectrum.json``, sorted by wavelength.

``index.json`` gets ``lightcurve_url`` (and ``spectrum_url``) per preset; no other
field changes. Exit code 1 if a cutout is missing.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from astropy.io import fits
from build_presets import PRESET_FOVS, PRESETS_DIR, URL_ROOT, _write_json, manifest_url
from fetch_spectrum_cutouts import index_path, spectrum_settings
from irsa_coverage import PRESETS
from preset_photometry import (
    APERTURE_ARCSEC,
    LIGHTCURVE_FILE,
    SPECTRUM_FILE,
    PresetLightcurve,
    PresetLightcurvePoint,
    PresetSpectrum,
    SpectrumPoint,
    position_at,
    tracking_for,
)
from preset_science import PROPER_MOTION

from app.config import Settings
from app.cutouts import cutout_key, default_cache, make_view
from app.irsa.epochs import parse_epoch_id
from app.models import BandInfo, EpochsResponse, Target
from app.pipeline.photometry import METHOD, Measurement, extract_stamp, measure

LIGHTCURVE_METHOD = (
    METHOD + " Presets measure every exposure of the sequence (up to 6 per visit), "
    "so points within a visit sample several wavelengths a few hours apart."
)
SPECTRUM_METHOD = METHOD.replace(" Each epoch is at its own wavelength (wavelength_um), "
                                 "so part of a change between points can be colour, "
                                 "not time.", "")
SPECTRUM_NOTE = (
    "Each detector array comes from one visit. SPHEREx observes the arrays in pairs "
    "(D1 with D4, D2 with D5, D3 with D6), and the pairs can be days apart, even "
    "within one visit; arrays can also come from different visits. A variable star "
    "can change in between, which shows as a step at an array boundary. Fluxes have "
    "no aperture correction, and the PSF grows with wavelength, so the "
    "long-wavelength points lose a little more of the star's light."
)


class PhotometryBuildError(Exception):
    pass


def measure_file(path: Path, ra: float, dec: float, settings: Settings,
                 frame_status: str = "good") -> Measurement:
    with fits.open(path, memmap=False) as hdul:
        stamp = extract_stamp(hdul, ra, dec, settings)
    return measure(stamp, APERTURE_ARCSEC, frame_status)


def load_manifest(pid: str, public: Path) -> EpochsResponse:
    path = public / manifest_url(pid, max(PRESET_FOVS), PRESET_FOVS).lstrip("/")
    return EpochsResponse.model_validate(json.loads(path.read_text(encoding="utf-8")))


def build_lightcurve(preset: tuple, public: Path, settings: Settings,
                     cache=None) -> PresetLightcurve:
    pid = preset[0]
    doc = load_manifest(pid, public)
    cache = cache or default_cache(settings)
    fetch_view = make_view(doc.target.ra, doc.target.dec, max(PRESET_FOVS))
    pm = PROPER_MOTION.get(pid)
    points = []
    for e in doc.epochs:
        release, obs_id, band = parse_epoch_id(e.id)
        key = cutout_key(release, obs_id, band, fetch_view.request_ra,
                         fetch_view.request_dec, fetch_view.size_arcmin)
        path = cache.lookup(key)
        if path is None:
            raise PhotometryBuildError(f"{e.id}: cutout not cached; run build_presets.py")
        ra, dec = position_at(doc.target.ra, doc.target.dec, e.mjd, pm)
        m = measure_file(path, ra, dec, settings, e.quality.status)
        points.append(PresetLightcurvePoint(
            epoch_id=e.id, time_utc=e.time_utc, mjd=e.mjd, release=e.release,
            wavelength_um=e.wavelength_um, flux=m.flux, flux_err=m.flux_err,
            quality=m.quality, visit=e.visit))
    points.sort(key=lambda p: p.mjd)
    return PresetLightcurve(synthetic=False, target=doc.target, band=doc.band,
                            aperture_arcsec=APERTURE_ARCSEC, method=LIGHTCURVE_METHOD,
                            points=points, tracking=tracking_for(pm))


# Presets whose fetched 6-array set must not become a spectrum, with the reason. The
# co-adds still use the set (build_preset_coadds.py); they show the problem honestly.
NO_SPECTRUM = {
    "wise-0855": "a G = 16.3 Gaia star 10.6″ west of WISE 0855 dominates the 12″ "
                 "aperture below about 3.5 um, where WISE 0855 itself is invisible",
}


def build_spectrum(preset: tuple, target: Target, settings: Settings) -> PresetSpectrum | None:
    """None when no 6-array set has been fetched for this preset, or the preset is in
    ``NO_SPECTRUM``."""
    pid = preset[0]
    if pid in NO_SPECTRUM:
        return None
    spec_settings = spectrum_settings(settings)
    idx = index_path(pid, spec_settings)
    if not idx.is_file():
        return None
    records = json.loads(idx.read_text(encoding="utf-8"))["cutouts"]
    directory = Path(spec_settings.cutout_cache_dir)
    pm = PROPER_MOTION.get(pid)
    points = []
    for r in records:
        if r.get("wavelength_um") is None:
            continue
        path = directory / r["file"]
        if not path.is_file():
            raise PhotometryBuildError(f"{r['epoch_id']}: {path.name} not in the spectrum "
                                       "cache; run fetch_spectrum_cutouts.py")
        ra, dec = position_at(target.ra, target.dec, r["mjd"], pm)
        m = measure_file(path, ra, dec, settings)
        points.append(SpectrumPoint(
            epoch_id=r["epoch_id"], time_utc=r["time_utc"], mjd=r["mjd"],
            release=r["release"], wavelength_um=round(r["wavelength_um"], 5),
            flux=m.flux, flux_err=m.flux_err, quality=m.quality, band=r["band"],
            visit=r["visit"]))
    points.sort(key=lambda p: (p.wavelength_um, p.mjd))
    bands = [BandInfo.for_band(b) for b in sorted({p.band for p in points})]
    return PresetSpectrum(synthetic=False, target=target, bands=bands,
                          aperture_arcsec=APERTURE_ARCSEC, method=SPECTRUM_METHOD,
                          note=SPECTRUM_NOTE, tracking=tracking_for(pm), points=points)


def summary(points) -> str:
    good = [p for p in points if p.flux is not None]
    q = {k: sum(p.quality == k for p in points) for k in ("good", "warn", "bad")}
    flux = (f", flux {min(p.flux for p in good):.0f}-{max(p.flux for p in good):.0f} uJy "
            f"(median {statistics.median(p.flux for p in good):.0f})") if good else ""
    return f"{len(points)} points, {q['good']} good, {q['warn']} warn, {q['bad']} bad{flux}"


def update_index(root: Path, urls: dict[str, dict]) -> None:
    """Set the given fields on each preset's index.json entry; a value of None
    removes the field."""
    path = root / "index.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    for entry in doc["presets"]:
        for field, url in urls.get(entry["id"], {}).items():
            if url is None:
                entry.pop(field, None)
            else:
                entry[field] = url
    _write_json(path, doc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preset", action="append", choices=[p[0] for p in PRESETS],
                        help="only this preset (repeatable); default all")
    args = parser.parse_args(argv)
    wanted = args.preset or [p[0] for p in PRESETS]
    settings = Settings.from_env()
    public = PRESETS_DIR.parent
    urls: dict[str, dict[str, str]] = {}
    code = 0
    for preset in PRESETS:
        pid = preset[0]
        if pid not in wanted:
            continue
        try:
            lc = build_lightcurve(preset, public, settings)
            spec = build_spectrum(preset, lc.target, settings)
        except PhotometryBuildError as exc:
            print(f"{pid}: FAILED: {exc}")
            code = 1
            continue
        _write_json(PRESETS_DIR / pid / LIGHTCURVE_FILE, lc.model_dump(mode="json"))
        urls[pid] = {"lightcurve_url": f"{URL_ROOT}/{pid}/{LIGHTCURVE_FILE}"}
        print(f"{pid}: light curve {summary(lc.points)}")
        if pid in NO_SPECTRUM:
            (PRESETS_DIR / pid / SPECTRUM_FILE).unlink(missing_ok=True)
            urls[pid]["spectrum_url"] = None
            print(f"{pid}: no spectrum ({NO_SPECTRUM[pid]})")
        elif spec is not None:
            _write_json(PRESETS_DIR / pid / SPECTRUM_FILE, spec.model_dump(mode="json"))
            urls[pid]["spectrum_url"] = f"{URL_ROOT}/{pid}/{SPECTRUM_FILE}"
            print(f"{pid}: spectrum {summary(spec.points)}, bands "
                  f"{[b.id for b in spec.bands]}")
    if urls:
        update_index(PRESETS_DIR, urls)
    return code


if __name__ == "__main__":
    sys.exit(main())
