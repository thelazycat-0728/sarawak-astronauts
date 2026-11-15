# Preset frames (real SPHEREx data)

Pre-computed, cleaned SPHEREx frame sequences for the five demo targets, so the
demo works without IRSA or the science API. Unlike `../mock/`, this is **real
data** (`"synthetic": false`).

SPHEREx data: NASA/IPAC Infrared Science Archive (IRSA)

## Layout

- `index.json`: every preset (id, name, ra, dec, band, default fov, what to look
  for) with a ready-made manifest URL per fov. Start here; never build paths.
- `<id>/manifest.json` (fov 12 arcmin), `<id>/fov-6/manifest.json` and
  `<id>/fov-3/manifest.json`: the `GET /epochs` response shape (`docs/API.md`
  sections 3 and 4). Each has its own `frames/` and `diff/` folders, referenced
  by root-relative `/presets/...` URLs.
- Every fov shows the same exposures: up to 6 of the best-ranked per survey visit,
  cut once at fov 12 and rendered at 12, 6 and 3. An exposure that is `bad`, or
  whose target centre is mostly masked, at any of the three fovs is left out.
  Frames are 8-bit grey, north up, east left, with one shared stretch (the same
  at all three fovs); grey 0 means no data. `diffs` holds the last-minus-first
  difference (grey 128 = no change); RW Aur and R Dra also hold a first-visit /
  last-visit pair at the closest wavelength, because each frame is at a slightly
  different wavelength and red stars change colour across the band.
- `stacks` in each manifest (files in `<folder>/stacks/`): one mean image per
  visit, with the frames' stretch, and a last-visit-minus-first-visit stack diff.
  Stacks show faint targets and ~1 px motions better than single frames; they
  are broadband averages (the frames differ in wavelength), not colour or
  brightness measurements.
- `<id>/lightcurve.json` (`lightcurve_url` in `index.json`): aperture photometry
  of the target on every exposure, in the `GET /lightcurve` shape plus `visit`
  per point (and `tracking` for the movers, whose aperture follows their proper
  motion). Saturated targets (Barnard's Star, R Dra) are `bad` with null flux.
- `coadds` on the entries of `eps-indi-b` and `rw-aur`: one co-added image per
  detector array in `<id>/coadds/D1.png` to `D6.png` (one visit, about 20
  exposures each, fov 3), from `backend/scripts/build_preset_coadds.py`.
- `motion` on the movers' entries in `index.json`: the target's position on
  each visit stack and the measured first-to-last shift against the catalogue
  proper motion (`backend/scripts/build_preset_motion.py`).
- `<id>/spectrum.json` (`spectrum_url`, `eps-indi-b` and `rw-aur`): the same
  photometry on one visit of every detector array, sorted by wavelength
  (about 0.75-5 um).

## Regenerate

From the repo root, with the backend virtual environment (needs network the first
time; cutouts are cached in `backend/.cache/cutouts/`):

```
backend/.venv/Scripts/python backend/scripts/build_presets.py
backend/.venv/Scripts/python backend/scripts/check_presets.py
```

IRSA often answers 503. `build_presets.py` exits with code 1 when any cutout
download failed; run it again until it reports 0 failures. On Linux or macOS use
`backend/.venv/bin/python`. `backend/scripts/build_stacks.py` adds or refreshes
the stacks offline from the cutout cache without touching the frames.
`backend/scripts/preset_science.py` measures the targets' motion and brightness
on the cleaned float images.
`backend/scripts/build_preset_photometry.py` writes the light curves (offline,
from the same cache) and the spectra; a spectrum first needs its cutouts:
`backend/scripts/fetch_spectrum_cutouts.py --preset eps-indi-b` (network, into
the separate `backend/.cache/spectra/`).
