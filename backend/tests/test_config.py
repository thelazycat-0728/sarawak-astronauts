from __future__ import annotations

from pathlib import Path

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
    with pytest.raises(ValueError, match="unknown DATA_MODE"):
        make_provider(Settings(data_mode="dr2"))


def test_irsa_defaults_and_release_mapping():
    s = Settings()
    assert s.irsa_sia_url == "https://irsa.ipac.caltech.edu/SIA"
    assert s.irsa_timeout_s == 30.0
    assert [s.collection_for(r) for r in s.spherex_releases] == ["spherex_dr1", "spherex_qr3"]
    assert s.release_for("spherex_qr3") == "QR3"
    assert s.release_for("spherex_dr1_deep") is None
    with pytest.raises(ValueError, match="no IRSA collection"):
        s.collection_for("DR2")


def test_cutout_defaults_and_cache_dir_env(monkeypatch):
    monkeypatch.delenv("CUTOUT_CACHE_DIR", raising=False)
    s = Settings.from_env()
    backend = Path(__file__).resolve().parents[1]
    assert Path(s.cutout_cache_dir) == backend / ".cache" / "cutouts"
    assert (s.cutout_workers, s.cutout_timeout_s, s.cutout_retries) == (4, 60.0, 2)
    assert s.cutout_cache_max_mb == 512.0 and s.epoch_query_ttl_s == 600.0
    monkeypatch.setenv("CUTOUT_CACHE_DIR", " /tmp/sx-cache ")
    assert Settings.from_env().cutout_cache_dir == "/tmp/sx-cache"
    monkeypatch.setenv("CUTOUT_CACHE_DIR", "  ")
    assert Settings.from_env().cutout_cache_dir == s.cutout_cache_dir
