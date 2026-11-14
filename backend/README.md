# Science API (FastAPI)

Implements the Track B endpoints in `docs/API.md`: `/health`, `/epochs`,
`/frame/{id}.png`, `/diff` and `/lightcurve`.

Two data modes, chosen with the `DATA_MODE` environment variable:

- `mock` (default): synthetic frames. Every `/epochs` and `/lightcurve` response has
  `"synthetic": true`.
- `irsa`: real SPHEREx data from IRSA. `/epochs` returns one cleaned exposure per
  visit (survey pass), rendered by the cleaning pipeline in `app/pipeline/`, with
  `"synthetic": false`. `/frame/{id}.png` renders frames from the cutout cache.
  `/diff` and `/lightcurve` answer `503 unavailable` for now, and `/epochs` has
  `diff_url_template: null` and `diffs: []`.

`GET /health` always returns `{"status": "ok"}`; the `X-Data-Mode` response header
(readable from browser JavaScript through CORS) says which mode is running. `POST /explain` (Track C) is not implemented yet;
`app/models.py` holds its request and response models.

## Set up (from `backend/`)

```
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
```

On Linux or macOS use `.venv/bin/python` instead of `.venv/Scripts/python`.

## Run

```
.venv/Scripts/python -m uvicorn app.main:app --reload
```

Then open <http://localhost:8000/docs> or
<http://localhost:8000/epochs?ra=269.4464&dec=4.7668>.

Optional environment variables: `DATA_MODE` (`mock`, the default, or `irsa`),
`CORS_ORIGINS` (comma-separated, default `http://localhost:5173`),
`CORS_ORIGIN_REGEX` (optional; origins it fully matches are also allowed, used for
Netlify deploy previews such as `^https://deploy-preview-\d+--<site>\.netlify\.app$`) and
`CUTOUT_CACHE_DIR` (where cutouts are cached, default `backend/.cache/cutouts/`),
`GEMINI_API_KEY` (turns on the AI explainer, `POST /explain`; free key from
<https://aistudio.google.com>, never commit it), `GEMINI_MODEL` (default
`gemini-3.8-flash`) and `GEMINI_FALLBACK_MODEL` (default `gemini-3.5-flash-lite`,
asked once when the main model is overloaded, out of quota or unknown).

### AI explainer (`POST /explain`)

`app/ai/` sends each question to Google Gemini (free tier) with the glossary
(`app/ai/glossary.json`, a copy of `frontend/src/glossary.json`) and the current view
in the prompt. Without `GEMINI_API_KEY` the endpoint answers `503` and the frontend
uses its offline answers. Limits: 6 questions a minute and 60 a day per IP
(`Settings.explain_rate_per_minute` / `explain_rate_per_day`). The tests use a fake
explainer and never call Gemini. To try it locally (PowerShell):

```
$env:GEMINI_API_KEY = "<your key>"; .venv/Scripts/python -m uvicorn app.main:app --reload
```

Then `POST http://localhost:8000/explain` with `{"question": "What is proper motion?"}`
from <http://localhost:8000/docs>, or run the frontend with `VITE_API_URL=http://localhost:8000`
and open the assistant panel. Token counts are logged at INFO (`app.ai`).

### Live SPHEREx data (`DATA_MODE=irsa`)

PowerShell (on Linux or macOS use `DATA_MODE=irsa .venv/bin/python -m uvicorn app.main:app`):

```
$env:DATA_MODE = "irsa"
.venv/Scripts/python -m uvicorn app.main:app
```

Then open <http://localhost:8000/epochs?ra=269.4464&dec=4.7668&fov=10&band=D2>.

How a request is served: a repeat of a recent view (same position to 4 decimals,
`fov` and `band`, within 10 minutes) comes from memory. Otherwise the API runs one
IRSA SIA query, picks one exposure per visit, downloads those cutouts into the disk
cache, cleans them and returns the sequence. Requests for the same view share one
build.

Expected latency (Barnard's Star, D2, fov 10, 3 visits, measured on a dev machine):

- repeat of a recent view: well under 1 s;
- cutouts already on disk: about 6 s (one SIA query of about 4 s, plus loading the
  pipeline once per process, about 1 to 5 s);
- nothing cached: not measured in this task; earlier tests saw 11 to 24 s per cold
  cutout (downloaded in parallel) and about 120 s when IRSA answered
  "503 Server busy" and downloads were retried;
- each `frame_url` afterwards: well under 1 s while its cutout is cached.

After 90 s (`Settings.epochs_deadline_s`) `/epochs` answers `504 upstream_timeout`.
A build that has started keeps going in the background, so the same view a minute
later is often fast. At most 4 IRSA requests run at once in the whole process
(`Settings.irsa_max_concurrency`). On Render's free plan the disk is wiped on every
restart, so the first request after a restart is a cold one.

When IRSA fails, the API says so instead of reporting "no data":

- exposures exist but every cutout download failed: `502 upstream_error` (or `504`
  when every failure was a timeout), not cached;
- a visit lost because its downloads failed: the shorter sequence is returned but
  cached for only 30 s (`epochs_cache_degraded_ttl_s`);
- `count: 0` means IRSA lists no exposure there (cached normally).

More than 16 requests waiting for builds at once (`epochs_max_waiters`) get
`503 unavailable` straight away. `/frame` gives up after 60 s (`frame_deadline_s`,
`504`). `/health` runs on the event loop, so slow requests cannot delay it. In this
mode the `app.*` log lines (for example the per-build timings) appear in uvicorn's
output.

**The presets are the demo fallback.** The five preset targets (API.md section 4)
are pre-computed files in `frontend/public/presets/` (built with
`backend/scripts/build_sequence.py --preset <id>`) that need neither IRSA nor this
API. When the live API is slow or answers 504, use a preset. (The preset files are
not in the repository yet.)

## Test and lint

```
.venv/Scripts/python -m pytest -q
.venv/Scripts/ruff check --isolated app/ tests/
```

Tests marked `live` call the real IRSA service and are skipped by default. Run them
with `.venv/Scripts/python -m pytest -m live`. To check that IRSA has SPHEREx coverage
for the five presets, run `python backend/scripts/irsa_coverage.py` from the repo root.

CI currently runs `ruff check app/` and a syntax check only, not the pytest suite.

Mock frames live in `app/providers/mock_assets/`, copied from `frontend/public/mock/`
by `python backend/scripts/make_mock_data.py` (run from the repo root).
