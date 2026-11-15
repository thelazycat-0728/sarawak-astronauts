# Science API contract

**Version:** 0.5 (2026-11-15) · **Owner:** Track B (Noah) · **Consumers:** Track A (viewer), Track C (AI explainer)

This is the contract between the frontend and the FastAPI science backend. Mock responses that follow it live in `frontend/public/mock/` (see its `README.md`). Any change to this file is announced in the team chat first.

**Changes in 0.5** (since 0.4):
- T-007 (live difference images, `DATA_MODE=irsa`): `/diff` works on the live API for any two epochs of a live `/epochs` response; it no longer answers `503 unavailable` for being unimplemented. Its two frames are rendered like `/frame` and share the `/frame` render slots and 60 s deadline, so `503` now means busy and `504` a timeout (section 1 and `/diff`).
- T-007: live `/epochs` returns a non-null `diff_url_template` (absolute, with the sequence's `ra`, `dec`, `fov` and `band`); `diffs` stays `[]`. Mock and preset responses are unchanged, and no shape changed.

**Changes in 0.4** (since 0.3):
- T-015 (live API limits, `DATA_MODE=irsa`): `/frame` renders run in at most 8 slots; a request waits up to 3 s for a free one (the wait counts against the frame deadline), then answers `503 unavailable` (section 1 error table and `/frame`). `/epochs` still answers `502`/`504` when no cutout could be downloaded (a cutout that downloads but cannot be cleaned now counts as a failed download) and `503` when too many requests wait. All other shapes are unchanged.
- T-015: the mock frames are honest about no data: three mock frames have no-data patches (grey 0), and every mock `quality.flagged_fraction` is the measured share of grey-0 pixels (`frontend/public/mock/`).
- T-016: optional `stacks` in every preset manifest (one mean image per visit plus a last-minus-first stack diff) and `stacks` per entry in `presets/index.json` (section 4); new `look_for` texts for `eps-indi-b` and `wise-0855`.

**Changes in 0.3** (since 0.2):
- `POST /explain` is implemented with Google Gemini (free tier), with a fallback model when the main one is busy: optional `context.mode` (`blink`, `diff`, `stack`), `glossary_terms` are ids from `glossary.json`, per-IP rate limit (`429`), `503` without `GEMINI_API_KEY`.

**Changes in 0.2** (since 0.1):
- T-005 fix-1: `image.nodata_gray` (always 0) and `stretch.asinh_a`; frames use grey 1–255 for data and 0 for no data, with the documented inverse; `quality.flags` lists masked bits only.
- T-014 (live API, `DATA_MODE=irsa`): `/epochs` returns one exposure per visit, `target.name: null`, `diff_url_template: null` and `diffs: []`; `502`/`504` when no cutout could be downloaded; `503` when too many requests wait.
- T-014: `/diff` and `/lightcurve` answer `503 unavailable` from the live API; `/health` sends an `X-Data-Mode` header (`mock` or `irsa`), readable through CORS.
- T-006: presets at fov 12, 6 and 3 (`<id>/manifest.json`, `<id>/fov-6/`, `<id>/fov-3/`), listed in `presets/index.json`; up to 6 exposures per visit; a wavelength-matched diff for variable stars; WISE 0855 re-centred to 133.7609, −7.2423.

## 1. Conventions

- **Base URL:** written here as `<API_BASE>`. It is configurable (the env var name is Track C's call). Example: `http://localhost:8000`.
- **Format:** JSON responses (`application/json`, UTF-8) unless the endpoint returns a PNG. Field names are `snake_case`. Unknown extra fields may appear and should be ignored.
- **Coordinates:** `ra` and `dec` in **decimal degrees, ICRS**. `ra` in `[0, 360)`, `dec` in `[-90, 90]`. Sexagesimal parsing happens in the frontend; the API only takes decimal degrees.
- **Field of view:** `fov` in **arcminutes**, the side of a square cutout. Default `10`, allowed `1` to `30`.
- **Units in names:** fields with units carry a suffix: `_arcmin`, `_arcsec`, `_um` (micrometres), `_px`. Image values are MJy/sr; fluxes are µJy (`"unit": "uJy"`).
- **Times:** every time is given twice: `time_utc` (ISO 8601, UTC, seconds precision, `Z` suffix, mid-exposure) and `mjd` (Modified Julian Date, UTC, 6 decimals).
- **Epoch ids:** opaque strings matching `^[A-Za-z0-9_-]{1,80}$` (no dots, so `{id}.png` is unambiguous). Unique within a response and stable across calls for the same exposure and band. Never parse them.
- **URLs:** responses carry ready-to-use URLs (`frame_url`, `diffs[].url`, `diff_url_template`). Use them verbatim as `<img src>`; the frontend never builds paths. The live API returns absolute URLs; mocks and presets return root-relative paths (`/mock/...`, `/presets/...`). Both work in `<img src>`.
- **Images:** 8-bit greyscale PNG, square, **north up, east left**, the requested position at the image centre. Every frame in a sequence has the same size and the same stretch. In frames, grey **0** (`image.nodata_gray`) means no data (masked, off the detector or empty); valid pixels use grey 1–255 (see `stretch` in section 3 for the mapping and its inverse). Difference images use their own scale (see `/diff`).
- **Provenance:** `/epochs` and `/lightcurve` responses carry `synthetic` (boolean). It is `true` only for mock data; the UI should label such data.

### Errors

Every error, including errors from the PNG endpoints, returns JSON in one shape:

```json
{"error": {"code": "invalid_parameter", "message": "dec must be between -90 and 90"}}
```

| HTTP | `code` | When |
| --- | --- | --- |
| 400 | `invalid_parameter` | Missing, malformed or out-of-range parameter (the backend maps FastAPI's default 422 to this) |
| 404 | `not_found` | Unknown route or epoch id |
| 429 | `rate_limited` | Too many requests (mainly `/explain`) |
| 500 | `internal_error` | Unexpected server error |
| 502 | `upstream_error` | IRSA or another archive returned an error |
| 503 | `unavailable` | A service is not configured (for example no LLM key for `/explain`), a feature is not available from the live API yet (`/lightcurve` with `DATA_MODE=irsa`), or the live API is busy: too many requests are already waiting (`/epochs`) or too many frames are being rendered (`/frame` and `/diff`, which share the render slots). The frontend should fall back to preset frames, or retry a frame or diff a moment later |
| 504 | `upstream_timeout` | An archive did not answer in time. The frontend should fall back to preset frames |

No SPHEREx coverage at a position is **not** an error: `/epochs` returns `200` with `"count": 0` and an empty `epochs` list. When IRSA lists exposures but none of their cutouts could be downloaded, the live API returns `502 upstream_error` (or `504 upstream_timeout` if every download timed out), not `count: 0`.

## 2. Bands

`band` selects one of the six SPHEREx detector arrays. Each array covers 17 of the 102 spectral channels. The default image for an array is a co-add over that array's wavelength range, which gives the best signal-to-noise. Default `band` is **`D2`**.

| `band` | Wavelength range (µm) | Spectral resolution R (approx.) |
| --- | --- | --- |
| `D1` | 0.75–1.09 | 39 |
| `D2` | 1.10–1.62 | 41 |
| `D3` | 1.63–2.41 | 41 |
| `D4` | 2.42–3.82 | 35 |
| `D5` | 3.83–4.41 | 110 |
| `D6` | 4.42–5.00 | 130 |

SPHEREx uses linear variable filters, so within one exposure the wavelength depends on where the source falls on the detector. Each epoch therefore reports `wavelength_um`, the central wavelength at the target position.

**Reserved (Stretch):** individual channels will use `D<array>-<NN>` with `NN` from `01` to `17` (for example `D2-07`). Until that is implemented, any value other than `D1`–`D6` returns `400 invalid_parameter`.

## 3. Endpoints

### `GET /health`

No parameters. Returns `200`:

```json
{"status": "ok"}
```

Every `/health` response carries an `X-Data-Mode` header, `mock` or `irsa`, saying which data the API serves. CORS exposes it (`Access-Control-Expose-Headers`), so browser JavaScript can read it with `response.headers.get("X-Data-Mode")`.

### `GET /epochs?ra&dec&fov&band`

Lists the SPHEREx exposures covering the position, cleaned and rendered as frames, sorted by time (oldest first). The live API returns **one representative exposure per visit** (the best-ranked exposure of each survey pass, chosen from the IRSA metadata before downloading). Preset manifests (section 4) have the same shape but may hold several exposures per visit, so group frames by `visit` rather than assuming one each. The frontend applies its own `epochs` URL selection to this list.

| Param | Type | Unit | Default | Range |
| --- | --- | --- | --- | --- |
| `ra` | float | deg | required | 0 ≤ ra < 360 |
| `dec` | float | deg | required | -90 ≤ dec ≤ 90 |
| `fov` | float | arcmin | 10 | 1–30 |
| `band` | string | | `D2` | `D1`–`D6` |

Response fields (all required unless marked nullable):

| Field | Meaning |
| --- | --- |
| `synthetic` | `true` for mock data only |
| `target` | `{ra, dec, name, frame}`: the resolved cutout centre. `name` is nullable: preset manifests set the preset name; the live API currently always returns `null`. `frame` is `"ICRS"` |
| `fov_arcmin`, `band` | The applied FOV and `{id, wavelength_min_um, wavelength_max_um}` |
| `image` | `{width_px, height_px, pixel_scale_arcsec, orientation, format, nodata_gray}`. The live API uses about 6.2″/px (SPHEREx native). `nodata_gray` is the grey level of pixels with no data, always `0` |
| `stretch` | `{shared, method, vmin, vmax, asinh_a, unit, note}`. One stretch is shared by the whole sequence, so frame-to-frame brightness changes are not display artefacts. Each frame is at a slightly different wavelength (`wavelength_um`), so part of a change can be colour, not time. `asinh_a` is the dimensionless asinh softening (the mock uses `0.03`, the live API `0.1`; always read it from the response). The mapping is given below the table |
| `releases` | Data releases present, for example `["DR1", "QR3"]` |
| `duplicates_removed` | Exposures served by both DR1 and QR3 that were dropped |
| `count` | Length of `epochs` |
| `epochs[]` | `id`, `time_utc`, `mjd`, `visit` (survey pass, 1-based, about every 6 months), `release` (`DR1` or `QR3`), `obs_id` (IRSA exposure id, nullable), `band`, `wavelength_um`, `quality`, `frame_url` |
| `epochs[].quality` | `{status, flagged_fraction, flags}`. `flagged_fraction` is the share of the frame's pixels rendered as no-data (grey 0): masked pixels and pixels off the detector. `status` is `good` (`flagged_fraction` < 5 %), `warn` (< 25 %) or `bad`. `flags` lists the names of the **masked** SPHEREx `FLAGS` bits present in the view, never `SOURCE` (which is never masked). Informational bits that do not mask a pixel are not listed |
| `diff_url_template` | Nullable string containing `{a}` and `{b}`. Replace them with two epoch ids to get a difference image URL. The live API always sets it (an absolute URL carrying the sequence's `ra`, `dec`, `fov` and `band`); `null` for static mocks and presets |
| `diffs[]` | Pre-rendered differences `{a, b, url, scale}`. Empty (`[]`) from the live API |

**Grey levels.** A frame pixel with value `v` (MJy/sr) is drawn as

- `x = clip((v − vmin) / (vmax − vmin), 0, 1)`
- `gray = 1 + round(254 · asinh(x / asinh_a) / asinh(1 / asinh_a))`, so valid pixels are 1–255; pixels with no data are `nodata_gray` (0).

To turn a grey level `g` back into MJy/sr: if `g = 0` there is no value; otherwise `v = vmin + (vmax − vmin) · asinh_a · sinh((g − 1) / 254 · asinh(1 / asinh_a))`. Values below `vmin` come back as `vmin` and values above `vmax` as `vmax`; in between, the error is at most half a grey-level step.

To show a difference of epochs `a` and `b`: use the matching `diffs[]` entry if there is one; otherwise substitute into `diff_url_template` if it is not `null`; otherwise subtraction is unavailable for that pair.

Example (live API, shortened to two epochs; the full mock is `frontend/public/mock/epochs.json`):

```json
{
  "synthetic": false,
  "target": {"ra": 269.4464, "dec": 4.7668, "name": null, "frame": "ICRS"},
  "fov_arcmin": 10.0,
  "band": {"id": "D2", "wavelength_min_um": 1.1, "wavelength_max_um": 1.62},
  "image": {"width_px": 97, "height_px": 97, "pixel_scale_arcsec": 6.2,
            "orientation": "north-up-east-left", "format": "png-gray8", "nodata_gray": 0},
  "stretch": {"shared": true, "method": "asinh", "vmin": 0.0079, "vmax": 1.9739, "asinh_a": 0.1, "unit": "MJy/sr",
              "note": "One stretch is shared by every frame in this sequence, so brightness changes between frames are real."},
  "releases": ["DR1", "QR3"],
  "duplicates_removed": 1,
  "count": 2,
  "epochs": [
    {
      "id": "sx_2025W20_0137_D2",
      "time_utc": "2025-05-14T03:12:40Z",
      "mjd": 60809.133796,
      "visit": 1,
      "release": "DR1",
      "obs_id": "2025W20_2B_0137_1",
      "band": "D2",
      "wavelength_um": 1.496,
      "quality": {"status": "good", "flagged_fraction": 0.004, "flags": []},
      "frame_url": "<API_BASE>/frame/sx_2025W20_0137_D2.png?ra=269.4464&dec=4.7668&fov=10&band=D2&vmin=0.0079&vmax=1.9739"
    },
    {
      "id": "sx_2026W20_0412_D2",
      "time_utc": "2026-05-14T11:40:16Z",
      "mjd": 61174.486296,
      "visit": 3,
      "release": "QR3",
      "obs_id": "2026W20_2A_0412_3",
      "band": "D2",
      "wavelength_um": 1.166,
      "quality": {"status": "warn", "flagged_fraction": 0.071, "flags": ["PERSIST"]},
      "frame_url": "<API_BASE>/frame/sx_2026W20_0412_D2.png?ra=269.4464&dec=4.7668&fov=10&band=D2&vmin=0.0079&vmax=1.9739"
    }
  ],
  "diff_url_template": "<API_BASE>/diff?a={a}&b={b}&ra=269.4464&dec=4.7668&fov=10&band=D2",
  "diffs": []
}
```

Errors: `400 invalid_parameter`, `502 upstream_error` (also when IRSA lists exposures but no cutout could be downloaded), `503 unavailable` (live API: too many requests already waiting; use the presets), `504 upstream_timeout` (the request deadline passed, or every download timed out).

### `GET /frame/{id}.png`

Returns one cleaned frame as `image/png` (8-bit grey, `image.width_px` × `image.height_px`). The frontend only calls this through `frame_url`. The query parameters make the endpoint stateless, so a frame can be re-rendered after a server restart.

| Param | Type | Unit | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` (path) | string | | required | Epoch id from `/epochs` |
| `ra`, `dec`, `fov`, `band` | as `/epochs` | | required | Same values as the sequence |
| `vmin`, `vmax` | float | MJy/sr | per-frame stretch | Pass both or neither; `frame_url` always passes the shared stretch |

Example: `200`, `Content-Type: image/png`, body is the PNG. Errors (JSON): `400 invalid_parameter`, `404 not_found`, `502 upstream_error`, `503 unavailable` (live API: every render slot, 8 by default, stayed busy for the few seconds the request waited for one; retry a moment later), `504 upstream_timeout`.

### `GET /diff?a&b`

Returns `image/png`, the difference **b − a** of two frames from the same sequence, same size as the frames. Grey 128 means no change, white means brighter in `b`, black means fainter in `b`. A source that moved shows as a black and white pair. A pixel with no data in either frame is drawn as 128 (no change), so diffs have no grey-0 no-data level. The frontend calls this through `diff_url_template`.

Live API: both frames are re-created from their ids like `/frame` (cutout cache, cleaning, north-up grid) and subtracted with the same renderer as the preset diffs. The default `scale` leaves out filled saturated star cores. The two frames take two of the `/frame` render slots together and share its 60 s deadline. Live epochs are one exposure per visit at different wavelengths (`wavelength_um`), so part of a difference can be colour, not time.

| Param | Type | Unit | Default | Notes |
| --- | --- | --- | --- | --- |
| `a`, `b` | string | | required | Epoch ids from the same `/epochs` response |
| `ra`, `dec`, `fov`, `band` | as `/epochs` | | required | Filled in by the template |
| `scale` | float | MJy/sr | 99.5th percentile of abs(b − a) | Value mapped to pure white or black; > 0 |

Errors (JSON): `400 invalid_parameter` (also when `a` equals `b`), `404 not_found` (either id unknown, or not of this `band`), `502 upstream_error`, `503 unavailable` (live API: two render slots did not free up within the few seconds the request waited; retry a moment later or use a preset), `504 upstream_timeout` (live API: the two frames were not ready within 60 s).

### `GET /lightcurve?ra&dec&band`

Aperture photometry at a fixed sky position on every epoch, sorted by time.

| Param | Type | Unit | Default | Range |
| --- | --- | --- | --- | --- |
| `ra`, `dec` | float | deg | required | as `/epochs` |
| `band` | string | | `D2` | `D1`–`D6` |
| `aperture` | float | arcsec | 12 | 3–60 (radius) |

`points[]` has `epoch_id` (matches `/epochs` ids for the same band), `time_utc`, `mjd`, `release`, `wavelength_um`, `flux` and `flux_err` (µJy, nullable when the position is masked or off the frame) and `quality` (`good`, `warn` or `bad`). The position is fixed, so a fast-moving star drifts out of the aperture; motion-aware photometry is a Stretch item.

```json
{
  "synthetic": false,
  "target": {"ra": 76.9564, "dec": 30.4014, "name": null, "frame": "ICRS"},
  "band": {"id": "D2", "wavelength_min_um": 1.1, "wavelength_max_um": 1.62},
  "aperture_arcsec": 12.0,
  "unit": "uJy",
  "method": "aperture photometry, median sky annulus, no aperture correction",
  "points": [
    {"epoch_id": "sx_2025W20_0137_D2", "time_utc": "2025-05-14T03:12:40Z", "mjd": 60809.133796,
     "release": "DR1", "wavelength_um": 1.496, "flux": 5953.9, "flux_err": 31.8, "quality": "good"},
    {"epoch_id": "sx_2025W46_0201_D2", "time_utc": "2025-11-11T22:05:18Z", "mjd": 60990.920347,
     "release": "DR1", "wavelength_um": 1.144, "flux": null, "flux_err": null, "quality": "bad"}
  ]
}
```

Errors: `400 invalid_parameter`, `502 upstream_error`, `503 unavailable` (live API: not implemented yet), `504 upstream_timeout`. No coverage returns `200` with an empty `points` list.

### `POST /explain` (implemented by Track C)

Plain-language answer from Google Gemini (free tier; model set by `GEMINI_MODEL`, default `gemini-3.8-flash`), grounded in the glossary and the current view. Code: `backend/app/ai/`. Track C owns the behaviour, rate limits and prompt.

Request body (`application/json`). `question` is required, 1–500 characters, not blank. `context` and each of its fields are optional; `mode` is the viewer mode on screen (`blink`, `diff` or `stack`):

```json
{
  "question": "Why does this dot move between snapshots?",
  "context": {"ra": 269.4464, "dec": 4.7668, "fov_arcmin": 10, "band": "D2",
              "target_name": "Barnard's Star", "epoch_id": "sx_2025W20_0137_D2", "mode": "blink"}
}
```

Response `200`. `ai_generated` is always `true` and the UI must show an "AI-generated" label. `model` is the Gemini model id. `glossary_terms` lists the ids of the `glossary.json` entries the answer relies on (only known ids; may be empty):

```json
{
  "answer": "The bright dot near the centre is Barnard's Star. It is very close to the Sun, so it moves across the sky fast enough to see between snapshots taken months apart.",
  "ai_generated": true,
  "model": "gemini-3.8-flash",
  "glossary_terms": ["pm", "epoch"]
}
```

Errors:
- `400 invalid_parameter`: missing, blank or too long question, or an unknown `mode`.
- `429 rate_limited`: more than 6 questions a minute or 60 a day from one IP, or the Gemini free-tier quota is used up.
- `502 upstream_error`: Gemini failed, timed out (15 s per call), blocked the answer or returned something unusable.

When the main model is overloaded (Gemini's `503` "high demand"), out of quota, unknown or too slow, the API asks the fallback model (`GEMINI_FALLBACK_MODEL`, default `gemini-3.5-flash-lite`) once, and `model` names the one that answered. The errors above apply when both fail.
- `503 unavailable`: no `GEMINI_API_KEY` on the server.

The frontend shows its built-in offline answers (labelled "Offline answer") for every error.

## 4. Preset manifests

Pre-computed demo data for the five test targets lives in static files so the demo works without IRSA or the API. Each preset is rendered at three fields of view (12, 6 and 3 arcmin) from one set of fov-12 cutouts, so every fov shows the same exposures: up to 6 of the best-ranked per visit, leaving out any exposure that is `bad` or whose target centre is mostly masked at any of the three fovs.

- `frontend/public/presets/<preset-id>/manifest.json` (fov 12), `.../<preset-id>/fov-6/manifest.json` and `.../<preset-id>/fov-3/manifest.json`: **exactly the `/epochs` response shape**, with `synthetic: false`, `target.name` set to the preset name, `diff_url_template: null` and the first-versus-last pair as the first entry in `diffs`. **Variable-star presets** (`rw-aur`, `r-dra`) also carry a second, wavelength-matched pair: an exposure from the first visit and one from the last visit at the closest `wavelength_um`, so the difference shows a change in time rather than in colour. Each fov has its own `image` size; all three use the fov-12 `stretch` and diff `scale`s, so a frame looks the same at every fov. The presets' `stretch.note` adds that each frame is at a slightly different wavelength.
- Each manifest's frames and diffs sit next to it: `<folder>/frames/<epoch-id>.png` and `<folder>/diff/<a>__<b>.png`, referenced by root-relative URLs (for example `/presets/barnards-star/frames/<epoch-id>.png` and `/presets/barnards-star/fov-6/frames/<epoch-id>.png`).
- **Visit stacks** (optional, additive field `stacks`, presets only; the live `/epochs` has none). Each preset manifest also carries one stacked image per visit: the per-pixel mean of that visit's cleaned frames over their valid pixels (a pixel with no valid frame is no data, grey 0), rendered with the manifest's own `stretch`, so frames and stacks share one brightness scale. Stacks show faint sources and ~1 px shifts better than single frames. The frames of a visit are at slightly different wavelengths, so a stack is a broadband average over the detector array, **not** a colour or brightness measurement. Files sit at `<folder>/stacks/visit-<n>.png`; `stacks.diff` is the last visit stack minus the first (`<folder>/stacks/diff/visit-<a>__visit-<b>.png`, `/diff` grey convention) with its own `scale`: the 99.5th percentile of `|b − a|` over the stacks' measured pixels (filled saturated cores left out) at fov 12, shared by all three fovs. `quality` has the frame shape: `flagged_fraction` is the share of no-data stack pixels and `flags` the union of the frames' flags. In `index.json`, each `manifests` entry gives `stacks`, the number of visit stacks (0 or absent means none).

```json
{"stacks": {
  "method": "mean",
  "note": "Each visit stack is the mean of that visit's frames over their valid pixels, ...",
  "visits": [
    {"visit": 1, "n_frames": 6, "epoch_ids": ["sx_dr1_2025W35_1A_0146_1_D2", "..."],
     "time_utc": "2025-08-26T05:41:39Z", "mjd": 60913.237262,
     "wavelength_min_um": 1.23823, "wavelength_max_um": 1.62887, "wavelength_mean_um": 1.46594,
     "url": "/presets/barnards-star/stacks/visit-1.png",
     "quality": {"status": "good", "flagged_fraction": 0.0001,
                 "flags": ["NONFUNC", "COLD", "PERSIST", "OUTLIER", "HALO"]}}
  ],
  "diff": {"a_visit": 1, "b_visit": 3, "url": "/presets/barnards-star/stacks/diff/visit-1__visit-3.png",
           "scale": 2.9192}
}}
```

`time_utc` and `mjd` are the mean of the stacked frames' times (MJD to 6 decimals, UTC to the second).
- `frontend/public/presets/index.json` lists the presets, so the frontend never builds paths:

```json
{
  "credit": "SPHEREx data: NASA/IPAC Infrared Science Archive (IRSA)",
  "fovs_arcmin": [12.0, 6.0, 3.0],
  "presets": [
    {"id": "barnards-star", "name": "Barnard's Star", "ra": 269.4464, "dec": 4.7668, "band": "D2",
     "default_fov_arcmin": 6.0, "look_for": "Position shift between visits",
     "manifests": [
       {"fov_arcmin": 12.0, "url": "/presets/barnards-star/manifest.json", "count": 18, "visits": 3, "stacks": 3},
       {"fov_arcmin": 6.0, "url": "/presets/barnards-star/fov-6/manifest.json", "count": 18, "visits": 3, "stacks": 3},
       {"fov_arcmin": 3.0, "url": "/presets/barnards-star/fov-3/manifest.json", "count": 18, "visits": 3, "stacks": 3}
     ]}
  ]
}
```

The frontend loads a preset by fetching the `url` for the chosen fov from `index.json` instead of calling `/epochs`. The preset ids and view settings are:

| `id` | Name | `ra` (deg) | `dec` (deg) | `band` | `fov` | Look for |
| --- | --- | --- | --- | --- | --- | --- |
| `barnards-star` | Barnard's Star | 269.4464 | 4.7668 | D2 | 12, 6, 3 | Position shift between visits |
| `eps-indi-b` | Epsilon Indi B | 331.0951 | -56.8007 | D2 | 12, 6, 3 | Faint brown dwarf that moves about one pixel a year; compare the first and last visit stacks, or see the stack difference, where it shows as a dark and bright pair. |
| `wise-0855` | WISE 0855−0714 | 133.7609 | -7.2423 | D6 | 12, 6, 3 | Too faint to see in single SPHEREx snapshots at 4–5 µm. In the visit stacks it is at best a faint smudge at the centre, clearest in the last visit; its motion (about one pixel, 8″, west per year) is too small to see here. |
| `rw-aur` | RW Aurigae | 76.9564 | 30.4014 | D2 | 12, 6, 3 | Young star whose brightness changes between visits. Part of the frame-to-frame change is colour, because each frame is at a slightly different wavelength. |
| `r-dra` | R Draconis | 248.1674 | 66.7550 | D2 | 12, 6, 3 | Mira variable whose brightness changes between visits. Part of the frame-to-frame change is colour, because each frame is at a slightly different wavelength. |

Coordinate notes: Barnard's Star (J2000 catalogue position 269.4521, +4.6934), Epsilon Indi B (J2000 331.0438, -56.7827) and WISE 0855 (J2000 133.8189, -7.2471) are centred on their SIMBAD positions propagated to about 2025.5 with their proper motions, because they move 10.4″/yr, 4.7″/yr and 8.1″/yr. Track B checked all five centres against SIMBAD in T-006 (all within 0.03′).
