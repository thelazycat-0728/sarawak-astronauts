from __future__ import annotations

import pytest

from app.config import Settings
from app.providers import make_provider


def test_defaults(monkeypatch):
    monkeypatch.delenv("DATA_MODE", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    s = Settings.from_env()
    assert s.data_mode == "mock"
    assert s.cors_origins == ("http://localhost:5173",)
    assert s.spherex_releases == ("DR1", "QR3")


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("DATA_MODE", " MOCK ")
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example,")
    s = Settings.from_env()
    assert s.data_mode == "mock"
    assert s.cors_origins == ("https://a.example", "https://b.example")


def test_unknown_data_mode_fails_fast():
    with pytest.raises(ValueError, match="only 'mock'"):
        make_provider(Settings(data_mode="irsa"))
