from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.providers.mock import MockProvider

REPO = Path(__file__).resolve().parents[2]
FRONTEND_MOCK = REPO / "frontend" / "public" / "mock"
BARNARD = {"ra": 269.4464, "dec": 4.7668}


@pytest.fixture(scope="session")
def provider() -> MockProvider:
    return MockProvider()


@pytest.fixture()
def app(provider: MockProvider):
    return create_app(settings=Settings(), provider=provider)


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def png_info(content: bytes) -> tuple[tuple[int, int], str]:
    with Image.open(io.BytesIO(content)) as im:
        return im.size, im.mode


def assert_error(resp, status: int, code: str) -> None:
    assert resp.status_code == status, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
