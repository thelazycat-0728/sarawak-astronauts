"""POST /explain and the Gemini explainer. No test calls the real Gemini API."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors

from app.ai import ExplainResult, make_explainer
from app.ai.gemini import GeminiExplainer, parse_reply
from app.ai.prompt import (
    GLOSSARY_PATH,
    describe_view,
    load_glossary,
    system_prompt,
    user_prompt,
)
from app.ai.ratelimit import DAY, MINUTE, RateLimiter
from app.config import Settings
from app.errors import RateLimited, UpstreamError
from app.main import create_app
from app.models import ExplainContext, ExplainResponse
from tests.conftest import REPO


class FakeExplainer:
    name = "fake"
    model = "fake-model-1"

    def __init__(self, result: ExplainResult | None = None, error: Exception | None = None):
        self.result = result or ExplainResult("A plain answer.", ["pm"])
        self.error = error
        self.calls: list[tuple[str, ExplainContext | None]] = []

    async def explain(self, question, context):
        self.calls.append((question, context))
        if self.error:
            raise self.error
        return self.result


def make_client(provider, explainer, **settings) -> TestClient:
    return TestClient(create_app(settings=Settings(**settings), provider=provider, explainer=explainer))


# ------------------------------------------------------------------ route


def test_explain_without_llm_is_503(provider):
    client = make_client(provider, None)
    resp = client.post("/explain", json={"question": "What is this?"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "unavailable"


def test_explain_answers_with_contract_shape(provider):
    fake = FakeExplainer()
    client = make_client(provider, fake)
    body = {
        "question": "  Why does it move?  ",
        "context": {"ra": 269.4464, "dec": 4.7668, "fov_arcmin": 6, "band": "D2",
                    "target_name": "Barnard's Star", "mode": "diff"},
    }
    resp = client.post("/explain", json=body)
    assert resp.status_code == 200
    doc = ExplainResponse.model_validate(resp.json())
    assert doc.answer == "A plain answer." and doc.ai_generated is True
    assert doc.model == "fake-model-1" and doc.glossary_terms == ["pm"]
    question, ctx = fake.calls[0]
    assert question == "Why does it move?" and ctx.mode == "diff" and ctx.band == "D2"


@pytest.mark.parametrize("body", [
    {"question": ""}, {"question": "   "}, {"question": "x" * 501}, {},
    {"question": "Hi", "context": {"mode": "zoom"}},
])
def test_explain_rejects_bad_requests(provider, body):
    fake = FakeExplainer()
    resp = make_client(provider, fake).post("/explain", json=body)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_parameter"
    assert not fake.calls


def test_explain_rate_limit_per_ip(provider):
    fake = FakeExplainer()
    client = make_client(provider, fake, explain_rate_per_minute=2, explain_rate_per_day=10)
    codes = [client.post("/explain", json={"question": "Hi"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert len(fake.calls) == 2


@pytest.mark.parametrize("error,status,code", [
    (RateLimited("quota"), 429, "rate_limited"),
    (UpstreamError("failed"), 502, "upstream_error"),
])
def test_explain_passes_llm_errors_through(provider, error, status, code):
    resp = make_client(provider, FakeExplainer(error=error)).post("/explain", json={"question": "Hi"})
    assert resp.status_code == status
    assert resp.json()["error"] == {"code": code, "message": error.message}


def test_explain_cors_preflight(provider):
    client = make_client(provider, FakeExplainer())
    resp = client.options("/explain", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"


# ------------------------------------------------------------------ settings


def test_make_explainer_needs_a_key():
    assert make_explainer(Settings()) is None
    assert isinstance(make_explainer(Settings(gemini_api_key="k")), GeminiExplainer)


def test_gemini_settings_from_env(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_FALLBACK_MODEL", raising=False)
    s = Settings.from_env()
    assert s.gemini_api_key is None and s.gemini_model == "gemini-3.8-flash"
    assert s.gemini_fallback_model == "gemini-3.5-flash-lite"
    monkeypatch.setenv("GEMINI_API_KEY", " secret-key ")
    monkeypatch.setenv("GEMINI_MODEL", " gemini-3.5-flash-lite ")
    s = Settings.from_env()
    assert s.gemini_api_key == "secret-key" and s.gemini_model == "gemini-3.5-flash-lite"
    assert "secret-key" not in repr(s)


# ------------------------------------------------------------------ prompt and glossary


def test_backend_glossary_matches_frontend():
    frontend = REPO / "frontend" / "src" / "glossary.json"
    assert json.loads(GLOSSARY_PATH.read_text(encoding="utf-8")) == json.loads(
        frontend.read_text(encoding="utf-8")
    ), "copy frontend/src/glossary.json to backend/app/ai/glossary.json"


def test_system_prompt_holds_every_glossary_term():
    prompt = system_prompt()
    for gid, g in load_glossary().items():
        assert f"- {gid}: {g['term']}." in prompt


def test_user_prompt_describes_the_view():
    ctx = ExplainContext(ra=269.4464, dec=4.7668, fov_arcmin=6, band="D6",
                         target_name="WISE 0855", mode="stack")
    text = user_prompt("Where is it?", ctx)
    assert "Target: WISE 0855" in text and "D6 (4.42-5.0 micrometres)" in text
    assert "Viewing mode: Stack" in text and "Field of view: 6 arcmin" in text
    assert "<question>\nWhere is it?\n</question>" in text
    assert describe_view(None) == describe_view(ExplainContext()) == "No view information."


# ------------------------------------------------------------------ reply parsing


def test_parse_reply_keeps_known_terms_once():
    r = parse_reply(json.dumps({"answer": " It moves. ", "glossary_terms": ["pm", "nope", "pm", 3]}))
    assert r == ExplainResult("It moves.", ["pm"])


@pytest.mark.parametrize("text", [None, "", "not json", '{"glossary_terms": []}',
                                  '{"answer": "   ", "glossary_terms": []}', '["a"]'])
def test_parse_reply_rejects_unusable_replies(text):
    with pytest.raises(UpstreamError):
        parse_reply(text)


# ------------------------------------------------------------------ Gemini client


class FakeModels:
    """generate_content returns or raises the given outcomes in turn (the last one repeats)."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes[min(len(self.calls), len(self.outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def models(self) -> list[str]:
        return [c["model"] for c in self.calls]


def gemini_with(models: FakeModels, fallback: str = "gemini-lite") -> GeminiExplainer:
    g = GeminiExplainer(Settings(gemini_api_key="k", gemini_model="gemini-test",
                                 gemini_fallback_model=fallback))
    g._client = SimpleNamespace(aio=SimpleNamespace(models=models))
    return g


async def _explain(g: GeminiExplainer):
    return await g.explain("Why?", ExplainContext(band="D2"))


def run(coro):
    import asyncio

    return asyncio.run(coro)


def reply(answer: str = "Because it is close.", terms=("pm",)):
    usage = SimpleNamespace(prompt_token_count=900, candidates_token_count=60, thoughts_token_count=0)
    text = json.dumps({"answer": answer, "glossary_terms": list(terms)})
    return SimpleNamespace(text=text, usage_metadata=usage)


def api_error(code: int):
    body = {"error": {"code": code, "message": "nope", "status": "X"}}
    return (genai_errors.ClientError if code < 500 else genai_errors.ServerError)(code, body)


def test_gemini_success_sends_prompt_and_schema():
    models = FakeModels(reply())
    result = run(_explain(gemini_with(models)))
    assert result == ExplainResult("Because it is close.", ["pm"], model="gemini-test")
    kw = models.calls[0]
    assert models.models == ["gemini-test"]
    assert "<question>\nWhy?\n</question>" in kw["contents"]
    assert kw["config"].system_instruction == system_prompt()
    assert kw["config"].response_mime_type == "application/json"


@pytest.mark.parametrize("code", [429, 404, 500, 503])
def test_gemini_falls_back_when_main_model_fails(code):
    models = FakeModels(api_error(code), reply("From the lite model."))
    result = run(_explain(gemini_with(models)))
    assert models.models == ["gemini-test", "gemini-lite"]
    assert result.answer == "From the lite model." and result.model == "gemini-lite"


def test_gemini_falls_back_after_a_timeout(monkeypatch):
    import app.ai.gemini as gemini_mod

    calls = []

    async def fake_wait_for(coro, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            coro.close()
            raise TimeoutError
        return await coro

    monkeypatch.setattr(gemini_mod.asyncio, "wait_for", fake_wait_for)
    models = FakeModels(reply())
    result = run(_explain(gemini_with(models)))
    assert result.model == "gemini-lite" and len(calls) == 2


@pytest.mark.parametrize("code,expected", [(429, RateLimited), (404, UpstreamError),
                                           (500, UpstreamError), (503, UpstreamError)])
def test_gemini_errors_when_both_models_fail(code, expected):
    models = FakeModels(api_error(code))
    with pytest.raises(expected):
        run(_explain(gemini_with(models)))
    assert models.models == ["gemini-test", "gemini-lite"]


@pytest.mark.parametrize("code", [400, 401, 403])
def test_gemini_bad_key_does_not_fall_back(code):
    models = FakeModels(api_error(code))
    with pytest.raises(UpstreamError, match="misconfigured"):
        run(_explain(gemini_with(models)))
    assert models.models == ["gemini-test"]


def test_gemini_fallback_off_when_same_model():
    models = FakeModels(api_error(503))
    with pytest.raises(UpstreamError):
        run(_explain(gemini_with(models, fallback="gemini-test")))
    assert models.models == ["gemini-test"]


def test_route_reports_the_model_that_answered(provider):
    fake = FakeExplainer(result=ExplainResult("Hi.", [], model="gemini-lite"))
    resp = make_client(provider, fake).post("/explain", json={"question": "Hi"})
    assert resp.json()["model"] == "gemini-lite"


# ------------------------------------------------------------------ rate limiter


def test_rate_limiter_minute_and_day_windows():
    now = [0.0]
    rl = RateLimiter(per_minute=2, per_day=3, clock=lambda: now[0])
    assert rl.allow("a") and rl.allow("a") and not rl.allow("a")
    assert rl.allow("b")  # per client
    now[0] += MINUTE
    assert rl.allow("a") and not rl.allow("a")  # day cap: 3
    now[0] += DAY
    assert rl.allow("a")


def test_rate_limiter_forgets_clients_when_full():
    now = [0.0]
    rl = RateLimiter(per_minute=5, per_day=5, max_clients=2, clock=lambda: now[0])
    rl.allow("a")
    now[0] += 1
    rl.allow("b")
    now[0] += 1
    assert rl.allow("c")  # evicts "a", the least recent
    assert set(rl._hits) == {"b", "c"}
