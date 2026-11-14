"""Choose one representative exposure per visit from metadata, before downloading.

**Ranking rule** (inside one visit; ties broken by epoch id):

1. *Safe* exposures first: the target lies at least the view's half-diagonal
   (``fov * sqrt(2) / 2``, 7.07' for fov=10) inside the detector footprint
   (SIA ``s_region``), so the north-up view is on the detector at any rotation.
   Among them, the one closest in time to the middle of the visit wins.
2. Then exposures without a usable footprint, again closest to the visit middle.
3. Then the rest, deepest inside the footprint first.

The visit middle is halfway between its first and last mid-exposure time.

**Fallback.** The top candidate of every visit is downloaded (one batch). A
candidate is rejected when its download fails, its view coverage is below
``per_visit_min_coverage`` (0.9) or its quality is ``bad``; the visit then tries its
next candidate in the next batch, up to ``per_visit_max_candidates`` (3). When
every tried candidate is rejected, the visit keeps the downloaded one with the
lowest flagged fraction if the target is on it, and is skipped otherwise.

**Several per visit** (presets, T-006). ``max_per_visit=N`` keeps up to the N best
accepted candidates of each visit, replacing rejected ones from further down the
ranking, with at most ``max_candidates`` tried per visit. ``keep_rejected=False``
skips a visit with no accepted candidate instead of keeping a rejected one.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.config import Settings
from app.cutouts import Cutout, CutoutFailure
from app.irsa.records import EpochRecord
from app.pipeline.clean import CleanFrame


def _tangent(ra0: float, dec0: float, ra: float, dec: float) -> tuple[float, float] | None:
    """Gnomonic (xi, eta) in arcmin of (ra, dec) about (ra0, dec0); None if > 90 deg."""
    a0, d0, a, d = (math.radians(v) for v in (ra0, dec0, ra, dec))
    cos_c = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(a - a0)
    if cos_c <= 1e-9:
        return None
    xi = math.cos(d) * math.sin(a - a0) / cos_c
    eta = (math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(a - a0)) / cos_c
    return math.degrees(xi) * 60.0, math.degrees(eta) * 60.0


def edge_distance_arcmin(
    footprint: Sequence[tuple[float, float]] | None, ra: float, dec: float
) -> float | None:
    """Distance (arcmin) from (ra, dec) to the nearest footprint edge.

    Positive inside the (convex) polygon, negative outside; None without a usable
    footprint.
    """
    if not footprint or len(footprint) < 3:
        return None
    points = [_tangent(ra, dec, r, d) for r, d in footprint]
    if any(p is None for p in points):
        return None
    pts: list[tuple[float, float]] = [p for p in points if p is not None]
    # Shoelace sum: positive for counter-clockwise corners.
    area2 = sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1]
                for i in range(len(pts)))
    if abs(area2) < 1e-12:
        return None
    orient = 1.0 if area2 > 0 else -1.0
    best = math.inf
    for i in range(len(pts)):
        (x1, y1), (x2, y2) = pts[i - 1], pts[i]
        length = math.hypot(x2 - x1, y2 - y1)
        if length == 0:
            continue
        # Signed distance of the origin (the target) from the edge line, positive
        # on the interior side.
        cross = ((x2 - x1) * (0.0 - y1) - (y2 - y1) * (0.0 - x1)) / length
        best = min(best, orient * cross)
    return best if math.isfinite(best) else None


def group_visits(epochs: Sequence[EpochRecord]) -> dict[int, list[EpochRecord]]:
    groups: dict[int, list[EpochRecord]] = {}
    for epoch in epochs:
        groups.setdefault(epoch.visit, []).append(epoch)
    return dict(sorted(groups.items()))


def rank_visit(
    epochs: Sequence[EpochRecord], ra: float, dec: float, fov_arcmin: float
) -> list[EpochRecord]:
    """The exposures of one visit, best candidate first (see the module rule)."""
    if not epochs:
        return []
    times = [e.exposure.t_mid_mjd for e in epochs]
    middle = (min(times) + max(times)) / 2.0
    need = fov_arcmin * math.sqrt(2.0) / 2.0

    def key(epoch: EpochRecord) -> tuple[int, float, str]:
        edge = edge_distance_arcmin(epoch.exposure.footprint, ra, dec)
        offset = abs(epoch.exposure.t_mid_mjd - middle)
        if edge is not None and edge >= need:
            return (0, offset, epoch.id)
        if edge is None:
            return (1, offset, epoch.id)
        return (2, -edge, epoch.id)

    return sorted(epochs, key=key)


@dataclass(frozen=True)
class Chosen:
    epoch: EpochRecord
    cutout: Cutout
    frame: CleanFrame
    accepted: bool  # False when kept as the best of rejected candidates


@dataclass(frozen=True)
class Attempt:
    visit: int
    epoch_id: str
    outcome: str  # accepted, download_failed, low_coverage or bad_quality
    detail: str = ""


@dataclass
class Selection:
    chosen: list[Chosen] = field(default_factory=list)  # sorted by time
    attempts: list[Attempt] = field(default_factory=list)
    skipped_visits: list[int] = field(default_factory=list)

    @property
    def downloads_tried(self) -> int:
        return len(self.attempts)


FetchFn = Callable[[list[EpochRecord]], Sequence[Cutout | CutoutFailure]]
CleanFn = Callable[[Cutout], CleanFrame]


def select_per_visit(
    epochs: Sequence[EpochRecord],
    ra: float,
    dec: float,
    fov_arcmin: float,
    *,
    fetch: FetchFn,
    clean: CleanFn,
    settings: Settings,
    max_per_visit: int = 1,
    max_candidates: int | None = None,
    keep_rejected: bool = True,
) -> Selection:
    """Up to ``max_per_visit`` exposures per visit (default one); only ranked
    candidates are fetched, in rounds.

    Each round fetches, for every visit that still needs exposures, as many of its
    next-ranked candidates as it is short. A visit stops after ``max_candidates``
    tried candidates (default ``per_visit_max_candidates * max_per_visit``, so 3
    for one per visit). ``keep_rejected=False`` turns off the fallback, so a visit
    with no accepted candidate is skipped instead of keeping a rejected one.
    """
    ranked = {v: rank_visit(g, ra, dec, fov_arcmin) for v, g in group_visits(epochs).items()}
    want = max(1, int(max_per_visit))
    if max_candidates is None:
        limit = max(1, int(settings.per_visit_max_candidates)) * want
    else:
        limit = max(1, int(max_candidates))
    result = Selection()
    accepted: dict[int, list[Chosen]] = {v: [] for v in ranked}
    tried: dict[int, int] = {v: 0 for v in ranked}
    rejected: dict[int, list[tuple[Cutout, CleanFrame]]] = {v: [] for v in ranked}
    while True:
        todo: list[tuple[int, EpochRecord]] = []
        for v, cands in ranked.items():
            short = want - len(accepted[v])
            take = min(short, limit - tried[v], len(cands) - tried[v])
            if take > 0:
                todo += [(v, c) for c in cands[tried[v]:tried[v] + take]]
                tried[v] += take
        if not todo:
            break
        results = fetch([epoch for _, epoch in todo])
        for (visit, epoch), got in zip(todo, results, strict=True):
            if isinstance(got, CutoutFailure):
                result.attempts.append(Attempt(visit, epoch.id, "download_failed",
                                               f"{got.code}: {got.reason}"))
                continue
            frame = clean(got)
            if got.coverage < settings.per_visit_min_coverage:
                outcome = "low_coverage"
            elif frame.status == "bad":
                outcome = "bad_quality"
            else:
                outcome = "accepted"
            detail = f"coverage {got.coverage:.3f}, flagged {frame.flagged_fraction:.4f}"
            result.attempts.append(Attempt(visit, epoch.id, outcome, detail))
            if outcome == "accepted":
                accepted[visit].append(Chosen(epoch, got, frame, accepted=True))
            else:
                rejected[visit].append((got, frame))
    done: list[Chosen] = []
    for visit, candidates in ranked.items():
        if accepted[visit]:
            done += accepted[visit]
            continue
        usable = [(c, f) for c, f in rejected[visit] if c.wavelength_um is not None]
        if usable and keep_rejected:
            cutout, frame = min(usable, key=lambda cf: (cf[1].flagged_fraction,
                                                        cf[0].epoch_id or ""))
            epoch = next(e for e in candidates if e.id == cutout.epoch_id)
            done.append(Chosen(epoch, cutout, frame, accepted=False))
        else:
            result.skipped_visits.append(visit)
    result.chosen = sorted(done, key=lambda c: (c.epoch.exposure.t_mid_mjd, c.epoch.id))
    return result
