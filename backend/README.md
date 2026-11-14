# Science API (FastAPI)

Implements the Track B endpoints in `docs/API.md`: `/health`, `/epochs`,
`/frame/{id}.png`, `/diff` and `/lightcurve`.

**Everything is synthetic mock data for now.** Every `/epochs` and `/lightcurve`
response has `"synthetic": true`. Real SPHEREx data from IRSA comes in a later task,
behind the same routes.

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

Mock frames live in `app/providers/mock_assets/`, copied from `frontend/public/mock/`
by `python backend/scripts/make_mock_data.py` (run from the repo root).
