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


def test_irsa_defaults_and_release_mapping():
    s = Settings()
    assert s.irsa_sia_url == "https://irsa.ipac.caltech.edu/SIA"
    assert s.irsa_timeout_s == 30.0
    assert [s.collection_for(r) for r in s.spherex_releases] == ["spherex_dr1", "spherex_qr3"]
    assert s.release_for("spherex_qr3") == "QR3"
    assert s.release_for("spherex_dr1_deep") is None
    with pytest.raises(ValueError, match="no IRSA collection"):
        s.collection_for("DR2")
