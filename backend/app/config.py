"""Settings for the science API.

Only two values come from the environment (names are provisional; Track C owns them):

- ``DATA_MODE``: which data provider serves the endpoints. Only ``mock`` exists for now.
- ``CORS_ORIGINS``: comma-separated list of allowed browser origins.

Everything else is a plain default that code can override (for example in tests).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_CORS_ORIGINS = ("http://localhost:5173",)  # Vite dev server


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    data_mode: str = "mock"
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    # SPHEREx release labels used in responses. T-003 maps them to the real IRSA
    # collection names; keeping them here means a new release needs no code change.
    spherex_releases: tuple[str, ...] = ("DR1", "QR3")

    @classmethod
    def from_env(cls) -> Settings:
        origins = _split_csv(os.environ.get("CORS_ORIGINS", ""))
        return cls(
            data_mode=os.environ.get("DATA_MODE", "mock").strip().lower() or "mock",
            cors_origins=origins or DEFAULT_CORS_ORIGINS,
        )
