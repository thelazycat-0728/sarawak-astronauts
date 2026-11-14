# Science API (FastAPI)

Implements the Track B endpoints in `docs/API.md`: `/health`, `/epochs`,
`/frame/{id}.png`, `/diff` and `/lightcurve`.

**Everything is synthetic mock data for now.** Every `/epochs` and `/lightcurve`
response has `"synthetic": true`. Real SPHEREx data from IRSA comes in a later task,
behind the same routes.

The IRSA lookup itself is in `app/irsa/` (SIA v2 query, exposure parsing, DR1/QR3
de-duplication and grouping into visits), but no provider uses it yet, so
`DATA_MODE=mock` is still the only mode. `POST /explain` (Track C) is not
implemented yet; `app/models.py` holds its request and response models.

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

Optional environment variables: `DATA_MODE` (default `mock`, the only mode so far)
and `CORS_ORIGINS` (comma-separated, default `http://localhost:5173`).

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
