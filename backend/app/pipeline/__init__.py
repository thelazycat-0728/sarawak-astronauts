"""Cleaning pipeline: SPHEREx cutouts to a north-up PNG frame sequence (T-005).

Not wired into the routes yet (T-014 adds the IRSA provider). Modules:

- ``flags``: FLAGS bit names from the FITS header and the bad-pixel mask.
- ``clean``: ZODI subtraction, masking, reprojection and quality for one cutout.
- ``render``: shared asinh stretch, 8-bit PNG, stateless ``render_frame``.
- ``select``: per-visit ranking from SIA metadata, with download fallback.
- ``sequence``: ``build_sequence`` returning an ``/epochs`` response plus PNGs.
- ``diff``: ``b - a`` difference PNGs (grey 128 = no change).
- ``stack``: per-visit mean of cleaned frames over their valid pixels.
"""

from app.pipeline.clean import (
    CleanFrame,
    clean_cutout,
    grid_side,
    quality_status,
    view_wcs,
)
from app.pipeline.diff import diff_png
from app.pipeline.render import compute_stretch, encode_png, render_frame, to_gray8
from app.pipeline.select import edge_distance_arcmin, rank_visit, select_per_visit
from app.pipeline.sequence import Sequence, build_sequence
from app.pipeline.stack import StackFrame, stack_frames, stack_png

__all__ = [
    "CleanFrame",
    "Sequence",
    "StackFrame",
    "build_sequence",
    "clean_cutout",
    "compute_stretch",
    "diff_png",
    "edge_distance_arcmin",
    "encode_png",
    "grid_side",
    "quality_status",
    "rank_visit",
    "render_frame",
    "select_per_visit",
    "stack_frames",
    "stack_png",
    "to_gray8",
    "view_wcs",
]
