"""The data-provider interface the routes depend on.

A provider knows where the data comes from (synthetic mock now, IRSA in T-003).
It never builds URLs: the routes turn a provider's results into the API.md
response shapes, with absolute URLs taken from the incoming request.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from app.models import (
    BandInfo,
    DiffQuery,
    EpochBase,
    FrameQuery,
    ImageInfo,
    LightcurveQuery,
    LightcurveResponse,
    Stretch,
    Target,
    ViewQuery,
)


class PrerenderedDiff(BaseModel):
    a: str
    b: str
    scale: float


class EpochSequence(BaseModel):
    """Everything in an /epochs response except the URLs."""

    synthetic: bool
    target: Target
    fov_arcmin: float
    band: BandInfo
    image: ImageInfo
    stretch: Stretch
    releases: list[str]
    duplicates_removed: int
    epochs: list[EpochBase]
    prerendered_diffs: list[PrerenderedDiff]
    # True when /diff can render any pair, so the response carries diff_url_template.
    any_pair_diff: bool


class DataProvider(Protocol):
    """Raise app.errors.NotFound for unknown epoch ids and UpstreamError or
    UpstreamTimeout when an archive fails. Methods are synchronous; FastAPI runs
    them in a worker thread."""

    name: str

    def epochs(self, q: ViewQuery) -> EpochSequence: ...

    def frame_png(self, epoch_id: str, q: FrameQuery) -> bytes: ...

    def diff_png(self, q: DiffQuery) -> bytes: ...

    def lightcurve(self, q: LightcurveQuery) -> LightcurveResponse: ...
