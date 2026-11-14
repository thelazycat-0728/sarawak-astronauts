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
    # SPHEREx release labels used in responses. Keeping them here means a new
    # release needs no code change. The order matters: when the same exposure is
    # served by more than one release, the release listed first wins.
    spherex_releases: tuple[str, ...] = ("DR1", "QR3")
    # Release label -> IRSA SIA collection (Wide Survey spectral images). Names come
    # from IRSA's spherex_intro tutorial and Irsa.list_collections(filter='spherex').
    spherex_collections: tuple[tuple[str, str], ...] = (
        ("DR1", "spherex_dr1"),
        ("QR3", "spherex_qr3"),
    )
    # IRSA Simple Image Access v2 endpoint (the one astroquery's Irsa.query_sia uses).
    irsa_sia_url: str = "https://irsa.ipac.caltech.edu/SIA"
    # Seconds allowed for connecting to IRSA and for each read from it.
    irsa_timeout_s: float = 30.0

    def collection_for(self, release: str) -> str:
        """IRSA collection name for a release label; ValueError if unmapped."""
        for label, collection in self.spherex_collections:
            if label == release:
                return collection
        raise ValueError(f"no IRSA collection configured for release {release!r}")

    def release_for(self, collection: str) -> str | None:
        """Release label for an IRSA collection name, or None if unknown."""
        for label, name in self.spherex_collections:
            if name == collection:
                return label
        return None

    @classmethod
    def from_env(cls) -> Settings:
        origins = _split_csv(os.environ.get("CORS_ORIGINS", ""))
        return cls(
            data_mode=os.environ.get("DATA_MODE", "mock").strip().lower() or "mock",
            cors_origins=origins or DEFAULT_CORS_ORIGINS,
        )
