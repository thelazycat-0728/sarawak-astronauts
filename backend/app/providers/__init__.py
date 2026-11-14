"""Data providers behind the science routes. Pick one with ``Settings.data_mode``.

Providers are imported only when selected, so mock mode never loads the IRSA or
cleaning-pipeline code (reproject, dask).
"""

from __future__ import annotations

from app.config import Settings
from app.providers.base import DataProvider, EpochSequence, PrerenderedDiff

__all__ = ["DataProvider", "EpochSequence", "PrerenderedDiff", "make_provider"]

DATA_MODES = ("mock", "irsa")


def make_provider(settings: Settings) -> DataProvider:
    if settings.data_mode == "mock":
        from app.providers.mock import MockProvider

        return MockProvider()
    if settings.data_mode == "irsa":
        from app.providers.irsa import IrsaProvider

        return IrsaProvider(settings)
    raise ValueError(
        f"unknown DATA_MODE {settings.data_mode!r}; use one of {', '.join(DATA_MODES)}"
    )
