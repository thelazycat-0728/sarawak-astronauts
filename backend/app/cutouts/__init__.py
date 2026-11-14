"""SPHEREx cutouts from IRSA with a bounded disk cache (T-004).

Not wired into the routes yet; the cleaning pipeline (T-005) builds on this.
"""

from app.cutouts.cache import CutoutCache, cutout_key
from app.cutouts.geometry import (
    KEPT_HDUS,
    cutout_size_arcmin,
    measure,
    round_position,
    view_coverage,
    wavelength_at,
)
from app.cutouts.service import (
    Cutout,
    CutoutBatch,
    CutoutFailure,
    CutoutView,
    QueryCache,
    default_cache,
    fetch_cutouts,
    get_cutout,
    make_view,
    resolve_epoch,
)

__all__ = [
    "KEPT_HDUS",
    "Cutout",
    "CutoutBatch",
    "CutoutCache",
    "CutoutFailure",
    "CutoutView",
    "QueryCache",
    "cutout_key",
    "cutout_size_arcmin",
    "default_cache",
    "fetch_cutouts",
    "get_cutout",
    "make_view",
    "measure",
    "resolve_epoch",
    "round_position",
    "view_coverage",
    "wavelength_at",
]
