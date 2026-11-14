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
says which mode is running. `POST /explain` (Track C) is not implemented yet;
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
`CORS_ORIGINS` (comma-separated, default `http://localhost:5173`) and
`CUTOUT_CACHE_DIR` (where cutouts are cached, default `backend/.cache/cutouts/`).

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
