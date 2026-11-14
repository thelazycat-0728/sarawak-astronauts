# Mock data (synthetic)

Everything in this folder is **synthetic**. It is not SPHEREx data. It exists so the
frontend can be built against the shapes in `docs/API.md` before the backend is ready.
Every JSON file carries `"synthetic": true`.

| File | Mimics |
| --- | --- |
| `epochs.json` | `GET /epochs?ra=269.4464&dec=4.7668&fov=10&band=D2` (Barnard's Star) |
| `frames/*.png` | `GET /frame/{id}.png`: 9 frames, 96 x 96 px, 8-bit grey, one shared stretch |
| `diff/*.png` | `GET /diff`: last frame minus first frame (mid-grey = no change) |
| `lightcurve.json` | `GET /lightcurve` at the variable source's position |
| `explain.json` | `POST /explain` response |

The field has three visits (May 2025, Nov 2025, May 2026) of three exposures each.

- **Moving source:** starts just below the centre and moves steadily north (up),
  about 24 px over the year. The motion is exaggerated (about 14 times faster than
  Barnard's Star really moves) so it is easy to see. Frames in the same visit are
  hours apart, so the source only jumps between visits.
- **Varying source:** about 22 px right and 18 px below the centre (south-west). It
  fades in Nov 2025 and is brightest in May 2026. `lightcurve.json` measures it.
- About 45 static field stars, Gaussian PSF, Gaussian noise.

All URLs in the JSON are root-relative (`/mock/...`), as Vite serves `public/` at `/`.
`diff_url_template` is `null` because static files cannot build arbitrary pairs; use
the pre-rendered pair in `diffs`.

Frames use the grey-level convention in `docs/API.md` section 3: grey 0
(`image.nodata_gray`) means no data, and valid pixels are 1-255 from the asinh
stretch with `stretch.vmin`, `stretch.vmax` and `stretch.asinh_a`. The mock's
`asinh_a` is 0.03; real SPHEREx frames use 0.1, so always read it from the JSON.
The mock has no masked pixels, so grey 0 does not occur in these frames.

## Regenerate

From the repo root (output is deterministic with the pinned versions):

```
python -m venv backend/.venv
backend/.venv/Scripts/python -m pip install -r backend/scripts/requirements-mock.txt
backend/.venv/Scripts/python backend/scripts/make_mock_data.py
backend/.venv/Scripts/python backend/scripts/make_mock_data.py --check
```

On Linux or macOS use `backend/.venv/bin/python` instead.
