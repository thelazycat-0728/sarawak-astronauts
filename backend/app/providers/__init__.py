"""Data providers behind the science routes. Pick one with ``Settings.data_mode``."""

from __future__ import annotations

from app.config import Settings
from app.providers.base import DataProvider, EpochSequence, PrerenderedDiff

__all__ = ["DataProvider", "EpochSequence", "PrerenderedDiff", "make_provider"]


def make_provider(settings: Settings) -> DataProvider:
    if settings.data_mode == "mock":
        from app.providers.mock import MockProvider

        return MockProvider()
    raise ValueError(
        f"unknown DATA_MODE {settings.data_mode!r}; only 'mock' is available"
    )
