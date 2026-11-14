"""The Pydantic models, the frontend mocks and docs/API.md agree."""

from __future__ import annotations

import json
import re

import pytest
from pydantic import ValidationError

from app.models import (
    EpochsResponse,
    ErrorResponse,
    ExplainRequest,
    ExplainResponse,
    HealthResponse,
    LightcurveResponse,
)
from app.providers.mock import ASSETS
from tests.conftest import FRONTEND_MOCK, REPO


def load_mock(name: str) -> dict:
    return json.loads((FRONTEND_MOCK / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name, model",
    [("epochs.json", EpochsResponse), ("lightcurve.json", LightcurveResponse)],
)
def test_frontend_mock_round_trips_through_model(name, model):
    raw = load_mock(name)
    parsed = model.model_validate(raw)
    # Equal after a round trip: no field is missing from the model or the mock.
    assert parsed.model_dump(mode="json") == raw


def test_explain_mock_matches_response_model():
    raw = load_mock("explain.json")
    parsed = ExplainResponse.model_validate(raw)
    # explain.json carries an extra "synthetic" flag; API.md allows extra fields.
    assert parsed.model_dump(mode="json") == {
        k: v for k, v in raw.items() if k != "synthetic"
    }


def test_explain_request_model():
    req = ExplainRequest.model_validate(
        {
            "question": "Why does this dot move?",
            "context": {"ra": 269.4464, "band": "D2"},
        }
    )
    assert req.context is not None and req.context.ra == 269.4464
    assert ExplainRequest.model_validate({"question": "Hi"}).context is None
    for bad in ({"question": ""}, {"question": "x" * 501}, {}):
        with pytest.raises(ValidationError):
            ExplainRequest.model_validate(bad)


def _api_doc_examples() -> list[dict]:
    text = (REPO / "docs" / "API.md").read_text(encoding="utf-8")
    return [
        json.loads(b) for b in re.findall(r"```json\n(.*?)```", text, flags=re.DOTALL)
    ]


def test_api_doc_examples_match_models():
    seen = set()
    for example in _api_doc_examples():
        if "epochs" in example:
            EpochsResponse.model_validate(example)
            seen.add("epochs")
        elif "points" in example:
            LightcurveResponse.model_validate(example)
            seen.add("lightcurve")
        elif "question" in example:
            ExplainRequest.model_validate(example)
            seen.add("explain-request")
        elif "answer" in example:
            ExplainResponse.model_validate(example)
            seen.add("explain-response")
        elif "error" in example:
            ErrorResponse.model_validate(example)
            seen.add("error")
        elif "status" in example:
            HealthResponse.model_validate(example)
            seen.add("health")
    assert seen == {
        "epochs",
        "lightcurve",
        "explain-request",
        "explain-response",
        "error",
        "health",
    }


def test_backend_mock_assets_equal_frontend_mocks():
    """The API never reads frontend/; its copies must stay byte-identical."""
    backend = sorted(
        p.relative_to(ASSETS).as_posix() for p in ASSETS.rglob("*") if p.is_file()
    )
    assert backend, "no backend mock assets"
    for rel in backend:
        assert (ASSETS / rel).read_bytes() == (FRONTEND_MOCK / rel).read_bytes(), rel
    frontend_pngs = {
        p.relative_to(FRONTEND_MOCK).as_posix() for p in FRONTEND_MOCK.rglob("*.png")
    }
    assert frontend_pngs <= set(backend)
    total = sum((ASSETS / rel).stat().st_size for rel in backend)
    assert total < 300_000
