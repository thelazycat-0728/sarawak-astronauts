"""SIA retries on transient IRSA failures, and the always-allowed local CORS origins."""

from __future__ import annotations

from dataclasses import replace

import pytest
import requests
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import UpstreamError, UpstreamTimeout
from app.irsa import sia
from app.main import create_app
from app.providers.mock import MockProvider
from tests.irsa_helpers import FakeResponse

PARAMS = [("POS", "CIRCLE 1 2 0.0003")]


class ScriptedSession:
    """Returns (or raises) the next scripted reply on each get()."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def get(self, url, *, params=None, timeout=None, headers=None, stream=False):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


@pytest.fixture
def sleeps(monkeypatch):
    waited: list[float] = []
    monkeypatch.setattr(sia, "_sleep", waited.append)
    return waited


def ok(body: bytes = b"<VOTABLE/>") -> FakeResponse:
    return FakeResponse(content=body)


def test_a_502_then_success_is_retried(sleeps):
    session = ScriptedSession(FakeResponse(b"busy", status_code=502), ok(b"data"))
    assert sia.fetch_sia(PARAMS, Settings(), session) == b"data"
    assert session.calls == 2 and sleeps == [0.5]


def test_a_dropped_connection_is_retried(sleeps):
    session = ScriptedSession(requests.ConnectionError("reset"), ok(b"data"))
    assert sia.fetch_sia(PARAMS, Settings(), session) == b"data"
    assert session.calls == 2


@pytest.mark.parametrize("status", [502, 503, 504])
def test_persistent_transient_errors_give_up_after_the_attempts(sleeps, status):
    session = ScriptedSession(*[FakeResponse(b"x", status_code=status)] * 3)
    with pytest.raises(UpstreamError, match=f"HTTP {status}") as info:
        sia.fetch_sia(PARAMS, Settings(), session)
    assert type(info.value) is UpstreamError  # a plain 502 for the API
    assert session.calls == 3 and sleeps == [0.5, 1.5]


@pytest.mark.parametrize("status", [400, 404, 500])
def test_other_http_errors_are_not_retried(sleeps, status):
    session = ScriptedSession(FakeResponse(b"x", status_code=status), ok())
    with pytest.raises(UpstreamError, match=f"HTTP {status}"):
        sia.fetch_sia(PARAMS, Settings(), session)
    assert session.calls == 1 and sleeps == []


def test_a_timeout_is_not_retried(sleeps):
    session = ScriptedSession(requests.ReadTimeout("slow"), ok())
    with pytest.raises(UpstreamTimeout):
        sia.fetch_sia(PARAMS, Settings(), session)
    assert session.calls == 1


def test_one_attempt_means_no_retry(sleeps):
    session = ScriptedSession(FakeResponse(b"x", status_code=503), ok())
    with pytest.raises(UpstreamError):
        sia.fetch_sia(PARAMS, replace(Settings(), irsa_sia_attempts=1), session)
    assert session.calls == 1 and sleeps == []


# ------------------------------------------------------------------ CORS


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5173"])
def test_the_local_dev_server_is_allowed_next_to_the_configured_site(origin):
    settings = replace(Settings(), cors_origins=("https://example.netlify.app",))
    client = TestClient(create_app(settings=settings, provider=MockProvider()))
    for allowed in (origin, "https://example.netlify.app"):
        r = client.get("/health", headers={"Origin": allowed})
        assert r.headers.get("access-control-allow-origin") == allowed
        assert "access-control-allow-credentials" not in r.headers
    r = client.get("/health", headers={"Origin": "https://elsewhere.example"})
    assert "access-control-allow-origin" not in r.headers
