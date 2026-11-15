"""Per-visit stacks for the static presets (T-016): the ``stacks`` manifest section.

Used by build_presets.py (full rebuild) and build_stacks.py (adds stacks to the
committed presets from the cutout cache). For one manifest:

- one stack per visit: the mean of that visit's cleaned frames over their valid
  pixels (``app.pipeline.stack``), rendered with the manifest's **stored**
  ``stretch``, written to ``<folder>/stacks/visit-<n>.png``;
- one stack diff, last visit minus first visit, with the ``/diff`` convention
  (grey 128 = no change, white brighter in the last visit), written to
  ``<folder>/stacks/diff/visit-<a>__visit-<b>.png``. Its ``scale`` is the 99.5th
  percentile of ``abs(b - a)`` over the *measurable* stack pixels (filled
  saturated cores left out) at the largest fov, shared by every fov.

The ``stacks`` field is optional and additive: the rest of the manifest is the
``/epochs`` shape, unchanged.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import Settings
from app.models import Epoch, EpochId, Quality
from app.pipeline.clean import CleanFrame
from app.pipeline.diff import diff_png, diff_scale
from app.pipeline.stack import StackFrame, stack_frames, stack_png

STACKS_DIR = "stacks"
STACK_METHOD = "mean"
STACK_NOTE = ("Each visit stack is the mean of that visit's frames over their valid "
              "pixels, with the frames' stretch, so faint sources and small shifts show "
              "better. The frames are at slightly different wavelengths, so a stack is a "
              "broadband average, not a colour or brightness measurement.")
MJD_EPOCH = datetime(1858, 11, 17, tzinfo=UTC)


class VisitStack(BaseModel):
    visit: int = Field(ge=1)
    n_frames: int = Field(ge=1)
    epoch_ids: list[EpochId]
    time_utc: str
    mjd: float
    wavelength_min_um: float
    wavelength_max_um: float
    wavelength_mean_um: float
    url: str
    quality: Quality


class StackDiff(BaseModel):
    a_visit: int = Field(ge=1)
    b_visit: int = Field(ge=1)
    url: str
    scale: float = Field(gt=0)


class Stacks(BaseModel):
    method: str
    note: str
    visits: list[VisitStack]
    diff: StackDiff | None


def mjd_to_utc(mjd: float) -> str:
    """ISO UTC to the second, like ``time_utc`` (for example 2025-05-05T16:49:00Z)."""
    when = MJD_EPOCH + timedelta(days=mjd)
    when = (when + timedelta(microseconds=500_000)).replace(microsecond=0)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def stack_name(visit: int) -> str:
    return f"visit-{visit}.png"


def stack_diff_name(a_visit: int, b_visit: int) -> str:
    return f"visit-{a_visit}__visit-{b_visit}.png"


def visit_groups(epochs: Iterable[Epoch]) -> dict[int, list[Epoch]]:
    """Visit -> its epochs in manifest (time) order, visits ascending."""
    groups: dict[int, list[Epoch]] = {}
    for e in epochs:
        groups.setdefault(e.visit, []).append(e)
    return dict(sorted(groups.items()))


def visit_stacks(epochs: list[Epoch], cleaned: dict[str, CleanFrame],
                 settings: Settings) -> dict[int, StackFrame]:
    """Visit -> stack of its cleaned frames."""
    return {visit: stack_frames([cleaned[e.id] for e in group], settings)
            for visit, group in visit_groups(epochs).items()}


def stack_section(epochs: list[Epoch], cleaned: dict[str, CleanFrame],
                  stretch: tuple[float, float], url_base: str, settings: Settings,
                  diff_scale_given: float | None = None,
                  ) -> tuple[Stacks, dict[str, bytes], dict[int, StackFrame]]:
    """(the ``stacks`` section, files relative to the manifest folder, the stacks).

    ``url_base`` is the manifest folder's URL ending in ``/``. ``stretch`` is the
    manifest's stored (vmin, vmax). ``diff_scale_given`` (from the largest fov)
    replaces the diff's own scale."""
    vmin, vmax = stretch
    groups = visit_groups(epochs)
    stacks = visit_stacks(epochs, cleaned, settings)
    files: dict[str, bytes] = {}
    entries: list[VisitStack] = []
    for visit, group in groups.items():
        s = stacks[visit]
        name = stack_name(visit)
        files[f"{STACKS_DIR}/{name}"] = stack_png(s, vmin, vmax, settings)
        mjd = sum(e.mjd for e in group) / len(group)
        wls = [e.wavelength_um for e in group]
        entries.append(VisitStack(
            visit=visit,
            n_frames=s.n_frames,
            epoch_ids=[e.id for e in group],
            time_utc=mjd_to_utc(mjd),
            mjd=round(mjd, 6),
            wavelength_min_um=round(min(wls), 5),
            wavelength_max_um=round(max(wls), 5),
            wavelength_mean_um=round(sum(wls) / len(wls), 5),
            url=f"{url_base}{STACKS_DIR}/{name}",
            quality=Quality(status=s.status, flagged_fraction=s.flagged_fraction,
                            flags=s.flags),
        ))
    diff = None
    if len(groups) >= 2:
        first, last = min(groups), max(groups)
        a, b = stacks[first], stacks[last]
        scale = diff_scale_given
        if scale is None:
            scale = diff_scale(a.measurable, b.measurable)
        if not (math.isfinite(scale) and scale > 0):
            scale = 1.0
        png, used = diff_png(a.image, b.image, scale)
        name = stack_diff_name(first, last)
        files[f"{STACKS_DIR}/diff/{name}"] = png
        diff = StackDiff(a_visit=first, b_visit=last,
                         url=f"{url_base}{STACKS_DIR}/diff/{name}", scale=used)
    section = Stacks(method=STACK_METHOD, note=STACK_NOTE, visits=entries, diff=diff)
    return section, files, stacks


def write_stack_files(folder: Path, files: dict[str, bytes]) -> None:
    """Write ``files`` under ``folder`` and delete other PNGs in the stack folders."""
    for sub in (STACKS_DIR, f"{STACKS_DIR}/diff"):
        d = folder / sub
        d.mkdir(parents=True, exist_ok=True)
        keep = {Path(k).name for k in files if str(Path(k).parent).replace("\\", "/") == sub}
        for old in d.glob("*.png"):
            if old.name not in keep:
                old.unlink()
    for rel, data in files.items():
        (folder / rel).write_bytes(data)
