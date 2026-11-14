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
- Every fov shows the same exposures: up to 6 of the best-ranked per survey visit
  (none with `bad` quality), cut once at fov 12 and rendered at 12, 6 and 3.
  Frames are 8-bit grey, north up, east left, with one shared stretch (the same
  at all three fovs); grey 0 means no data. `diffs` holds the last-minus-first
  difference (grey 128 = no change).

## Regenerate

From the repo root, with the backend virtual environment (needs network the first
time; cutouts are cached in `backend/.cache/cutouts/`):

```
backend/.venv/Scripts/python backend/scripts/build_presets.py
backend/.venv/Scripts/python backend/scripts/check_presets.py
```

IRSA often answers 503. `build_presets.py` exits with code 1 when any cutout
download failed; run it again until it reports 0 failures. On Linux or macOS use
`backend/.venv/bin/python`. `backend/scripts/preset_science.py` measures the
targets' motion and brightness on the cleaned float images.
