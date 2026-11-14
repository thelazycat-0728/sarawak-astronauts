# Science API contract

**Version:** 0.1 (2026-11-14) · **Owner:** Track B (Noah) · **Consumers:** Track A (viewer), Track C (AI explainer)

This is the contract between the frontend and the FastAPI science backend. Mock responses that follow it live in `frontend/public/mock/` (see its `README.md`). Any change to this file is announced in the team chat first.

## 1. Conventions

- **Base URL:** written here as `<API_BASE>`. It is configurable (the env var name is Track C's call). Example: `http://localhost:8000`.
- **Format:** JSON responses (`application/json`, UTF-8) unless the endpoint returns a PNG. Field names are `snake_case`. Unknown extra fields may appear and should be ignored.
- **Coordinates:** `ra` and `dec` in **decimal degrees, ICRS**. `ra` in `[0, 360)`, `dec` in `[-90, 90]`. Sexagesimal parsing happens in the frontend; the API only takes decimal degrees.
- **Field of view:** `fov` in **arcminutes**, the side of a square cutout. Default `10`, allowed `1` to `30`.
- **Units in names:** fields with units carry a suffix: `_arcmin`, `_arcsec`, `_um` (micrometres), `_px`. Image values are MJy/sr; fluxes are µJy (`"unit": "uJy"`).
- **Times:** every time is given twice: `time_utc` (ISO 8601, UTC, seconds precision, `Z` suffix, mid-exposure) and `mjd` (Modified Julian Date, UTC, 6 decimals).
- **Epoch ids:** opaque strings matching `^[A-Za-z0-9_-]{1,80}$` (no dots, so `{id}.png` is unambiguous). Unique within a response and stable across calls for the same exposure and band. Never parse them.
- **URLs:** responses carry ready-to-use URLs (`frame_url`, `diffs[].url`, `diff_url_template`). Use them verbatim as `<img src>`; the frontend never builds paths. The live API returns absolute URLs; mocks and presets return root-relative paths (`/mock/...`, `/presets/...`). Both work in `<img src>`.
- **Images:** 8-bit greyscale PNG, square, **north up, east left**, the requested position at the image centre. Every frame in a sequence has the same size and the same stretch.
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
| 503 | `unavailable` | A service is not configured (for example no LLM key for `/explain`) |
| 504 | `upstream_timeout` | An archive did not answer in time. The frontend should fall back to preset frames |

No SPHEREx coverage at a position is **not** an error: `/epochs` returns `200` with `"count": 0` and an empty `epochs` list.

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

### `GET /epochs?ra&dec&fov&band`

Lists every SPHEREx exposure covering the position, cleaned and rendered as frames, sorted by time (oldest first). The frontend applies its own `epochs` URL selection to this list.

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
| `target` | `{ra, dec, name, frame}`: the resolved cutout centre. `name` is nullable (a preset name or SIMBAD name when known). `frame` is `"ICRS"` |
| `fov_arcmin`, `band` | The applied FOV and `{id, wavelength_min_um, wavelength_max_um}` |
| `image` | `{width_px, height_px, pixel_scale_arcsec, orientation, format}`. The live API uses about 6.2″/px (SPHEREx native) |
| `stretch` | `{shared, method, vmin, vmax, unit, note}`. One stretch is shared by the whole sequence, so frame-to-frame brightness changes are real |
| `releases` | Data releases present, for example `["DR1", "QR3"]` |
| `duplicates_removed` | Exposures served by both DR1 and QR3 that were dropped |
| `count` | Length of `epochs` |
| `epochs[]` | `id`, `time_utc`, `mjd`, `visit` (survey pass, 1-based, about every 6 months), `release` (`DR1` or `QR3`), `obs_id` (IRSA exposure id, nullable), `band`, `wavelength_um`, `quality`, `frame_url` |
| `epochs[].quality` | `{status, flagged_fraction, flags}`. `status` is `good` (<5 % of cutout pixels flagged), `warn` (<25 %) or `bad`. `flags` lists the SPHEREx `FLAGS` bit names present, never `SOURCE` |
| `diff_url_template` | Nullable string containing `{a}` and `{b}`. Replace them with two epoch ids to get a difference image URL. `null` for static mocks and presets |
| `diffs[]` | Pre-rendered differences `{a, b, url, scale}`. May be empty from the live API |

To show a difference of epochs `a` and `b`: use the matching `diffs[]` entry if there is one; otherwise substitute into `diff_url_template` if it is not `null`; otherwise subtraction is unavailable for that pair.

Example (live API, shortened to two epochs; the full mock is `frontend/public/mock/epochs.json`):

```json
{
  "synthetic": false,
  "target": {"ra": 269.4464, "dec": 4.7668, "name": "Barnard's Star", "frame": "ICRS"},
  "fov_arcmin": 10.0,
  "band": {"id": "D2", "wavelength_min_um": 1.1, "wavelength_max_um": 1.62},
  "image": {"width_px": 97, "height_px": 97, "pixel_scale_arcsec": 6.2,
            "orientation": "north-up-east-left", "format": "png-gray8"},
  "stretch": {"shared": true, "method": "asinh", "vmin": 0.0079, "vmax": 1.9739, "unit": "MJy/sr",
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
      "quality": {"status": "warn", "flagged_fraction": 0.071, "flags": ["TRANSIENT"]},
      "frame_url": "<API_BASE>/frame/sx_2026W20_0412_D2.png?ra=269.4464&dec=4.7668&fov=10&band=D2&vmin=0.0079&vmax=1.9739"
    }
  ],
  "diff_url_template": "<API_BASE>/diff?a={a}&b={b}&ra=269.4464&dec=4.7668&fov=10&band=D2",
  "diffs": []
}
```

Errors: `400 invalid_parameter`, `502 upstream_error`, `504 upstream_timeout`.

### `GET /frame/{id}.png`

Returns one cleaned frame as `image/png` (8-bit grey, `image.width_px` × `image.height_px`). The frontend only calls this through `frame_url`. The query parameters make the endpoint stateless, so a frame can be re-rendered after a server restart.

| Param | Type | Unit | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` (path) | string | | required | Epoch id from `/epochs` |
| `ra`, `dec`, `fov`, `band` | as `/epochs` | | required | Same values as the sequence |
| `vmin`, `vmax` | float | MJy/sr | per-frame stretch | Pass both or neither; `frame_url` always passes the shared stretch |

Example: `200`, `Content-Type: image/png`, body is the PNG. Errors (JSON): `400 invalid_parameter`, `404 not_found`, `502 upstream_error`, `504 upstream_timeout`.

### `GET /diff?a&b`

Returns `image/png`, the difference **b − a** of two frames from the same sequence, same size as the frames. Grey 128 means no change, white means brighter in `b`, black means fainter in `b`. A source that moved shows as a black and white pair. The frontend calls this through `diff_url_template`.

| Param | Type | Unit | Default | Notes |
| --- | --- | --- | --- | --- |
| `a`, `b` | string | | required | Epoch ids from the same `/epochs` response |
| `ra`, `dec`, `fov`, `band` | as `/epochs` | | required | Filled in by the template |
| `scale` | float | MJy/sr | 99.5th percentile of abs(b − a) | Value mapped to pure white or black; > 0 |

Errors (JSON): `400 invalid_parameter` (also when `a` equals `b`), `404 not_found`, `502 upstream_error`, `504 upstream_timeout`.

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

Errors: `400 invalid_parameter`, `502 upstream_error`, `504 upstream_timeout`. No coverage returns `200` with an empty `points` list.

### `POST /explain` (implemented by Track C)

Plain-language answer from an LLM, grounded in the glossary and the current view. Shape only; Track C owns the behaviour, rate limits and prompt.

Request body (`application/json`). `question` is required, 1–500 characters. `context` and each of its fields are optional:

```json
{
  "question": "Why does this dot move between snapshots?",
  "context": {"ra": 269.4464, "dec": 4.7668, "fov_arcmin": 10, "band": "D2",
              "target_name": "Barnard's Star", "epoch_id": "sx_2025W20_0137_D2"}
}
```

Response `200`. `ai_generated` is always `true` and the UI must show an "AI-generated" label. `glossary_terms` lists the `glossary.json` entry ids used for grounding (may be empty):

```json
{
  "answer": "The bright dot near the centre is Barnard's Star. It is very close to the Sun, so it moves across the sky fast enough to see between snapshots taken months apart.",
  "ai_generated": true,
  "model": "provider-model-name",
  "glossary_terms": ["proper-motion", "epoch"]
}
```

Errors: `400 invalid_parameter`, `429 rate_limited`, `502 upstream_error` (LLM failed), `503 unavailable` (no LLM configured).

## 4. Preset manifests

Pre-computed demo data for the five test targets lives in static files so the demo works without IRSA or the API:

- `frontend/public/presets/<preset-id>/manifest.json`: **exactly the `/epochs` response shape**, with `synthetic: false`, `target.name` set to the preset name, `diff_url_template: null` and at least the first-versus-last pair in `diffs`.
- `frontend/public/presets/<preset-id>/frames/<epoch-id>.png` and `.../diff/<a>__<b>.png`, referenced by root-relative URLs (`/presets/<preset-id>/frames/<epoch-id>.png`).

The frontend loads a preset by fetching `/presets/<preset-id>/manifest.json` instead of calling `/epochs`. The preset ids and view settings are:

| `id` | Name | `ra` (deg) | `dec` (deg) | `band` | `fov` | Look for |
| --- | --- | --- | --- | --- | --- | --- |
| `barnards-star` | Barnard's Star | 269.4464 | 4.7668 | D2 | 10 | Position shift between visits |
| `eps-indi-b` | Epsilon Indi B | 331.0951 | -56.8007 | D2 | 10 | Faint co-moving brown dwarf pair |
| `wise-0855` | WISE 0855−0714 | 133.7951 | -7.2451 | D6 | 10 | Motion; only visible at long wavelengths |
| `rw-aur` | RW Aurigae | 76.9564 | 30.4014 | D2 | 10 | Brightness changes |
| `r-dra` | R Draconis | 248.1674 | 66.7550 | D2 | 10 | Brightness changes (Mira variable) |

Coordinate notes: Barnard's Star (J2000 catalogue position 269.4521, +4.6934) and Epsilon Indi B (J2000 331.0438, -56.7827) are centred on their positions propagated to about 2025.5 with their proper motions, because they move 10.4″/yr and 4.7″/yr. WISE 0855 uses the position given in PROJECT_SUMMARY §2; it moves about 8″/yr west. Track B will confirm all five centres when it computes the preset frames.
