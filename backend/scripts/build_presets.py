"""Build the static preset frame sequences in frontend/public/presets/ (T-006).

Run from the repo root or from backend/ (needs network for the IRSA query, and for
any cutout that is not cached yet):

    python backend/scripts/build_presets.py                  # all five presets
    python backend/scripts/build_presets.py --preset r-dra   # one (repeatable)
    python backend/scripts/check_presets.py                  # then validate

For each preset (``PRESETS`` in irsa_coverage.py):

1. One IRSA query; the cutouts are fetched **once**, sized for the largest view
   (fov 12). Up to 6 of the best-ranked exposures per visit are kept (ranking in
   app.pipeline.select); candidates with ``bad`` quality, low coverage, a target
   core (r <= 2 px) less than half valid, or a failed download are replaced from
   further down the ranking (at most 12 tried per visit), and a visit with no
   acceptable exposure is left out.
2. The same exposures are rendered at fov 12, 6 and 3 from those cutouts, all
   with the fov 12 shared stretch. A frame that is ``bad`` or has a mostly masked
   target core at any fov is dropped from all three. Diffs (b - a, with the fov 12
   scales): first versus last, and for the variable stars also a first-visit /
   last-visit pair at the closest wavelength.
3. Output: ``<id>/manifest.json`` (fov 12, the API.md section 4 path) and
   ``<id>/fov-6/manifest.json``, ``<id>/fov-3/manifest.json``, each with its
   ``frames/`` and ``diff/`` folders and root-relative ``/presets/...`` URLs, and
   ``index.json`` listing every preset with a manifest URL per fov.

Exit code 1 if a query failed, a preset has no frames, or any cutout download
failed (IRSA often answers 503; re-run until there are 0 failures, cached cutouts
are reused). Output is deterministic for a given set of cached cutouts.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from irsa_coverage import PRESETS

from app.config import Settings
from app.cutouts.download import StreamingSession
from app.errors import ApiError
from app.irsa.records import EpochQueryResult
from app.irsa.sia import HttpSession, find_epochs
from app.models import DiffPair, EpochsResponse

PRESETS_DIR = REPO / "frontend" / "public" / "presets"
URL_ROOT = "/presets"
PRESET_FOVS = (12.0, 6.0, 3.0)  # the frontend's fov choices; the first is the largest
MAX_PER_VISIT = 6
MAX_CANDIDATES = 12  # tried per visit
# A frame needs at least half of its target core (r <= 2 px) valid, at every fov.
MIN_CORE_VALID = 0.5
CREDIT = "SPHEREx data: NASA/IPAC Infrared Science Archive (IRSA)"
# Variable stars also get a wavelength-matched first-visit / last-visit diff.
VARIABLES = ("rw-aur", "r-dra")
PRESET_STRETCH_NOTE = ("One stretch is shared by every frame in this sequence, so "
                       "brightness changes between frames are real (each frame is at a "
                       "slightly different wavelength).")
COLOUR_NOTE = ("Part of the frame-to-frame change is colour, because each frame is at "
               "a slightly different wavelength.")

# What to look for (API.md section 4) and the fov the viewer opens with.
LOOK_FOR = {
    "barnards-star": "Position shift between visits",
    "eps-indi-b": ("Faint brown dwarf that moves about one pixel a year; easiest to see "
                   "in the difference image."),
    "wise-0855": ("Too faint to see in single SPHEREx snapshots at 4\u20135 \u00b5m; it only "
                  "shows up when a visit's exposures are stacked. It moves about one pixel "
                  "(8\u2033) west per year."),
    "rw-aur": f"Young star whose brightness changes between visits. {COLOUR_NOTE}",
    "r-dra": f"Mira variable whose brightness changes between visits. {COLOUR_NOTE}",
}
DEFAULT_FOV = {  # 6': the target and its neighbours are both large enough to see
    "barnards-star": 6.0,
    "eps-indi-b": 6.0,
    "wise-0855": 6.0,
    "rw-aur": 6.0,
    "r-dra": 6.0,
}


def fov_label(fov: float) -> str:
    return f"{fov:g}"


def fov_subdir(fov: float, fovs: tuple[float, ...]) -> str:
    """'' for the largest fov (the contract path), else 'fov-<N>/'."""
    return "" if fov == max(fovs) else f"fov-{fov_label(fov)}/"


def manifest_url(pid: str, fov: float, fovs: tuple[float, ...]) -> str:
    return f"{URL_ROOT}/{pid}/{fov_subdir(fov, fovs)}manifest.json"


@dataclass
class FovResult:
    fov: float
    count: int
    visits: list[int]
    statuses: dict[str, int]
    diffs: list[DiffPair] = field(default_factory=list)


@dataclass
class PresetResult:
    pid: str
    name: str
    ra: float
    dec: float
    band: str
    ok: bool = False
    error: str = ""
    downloads: int = 0
    cache_hits: int = 0
    download_failures: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # bad or masked core at some fov
    skipped_visits: list[int] = field(default_factory=list)
    per_visit: dict[int, int] = field(default_factory=dict)
    fovs: list[FovResult] = field(default_factory=list)
    seconds: float = 0.0


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")


def _replace_pngs(folder: Path, files: dict[str, bytes]) -> None:
    """Write ``files`` (name -> bytes) into ``folder`` and delete other PNGs there."""
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.png"):
        if old.name not in files:
            old.unlink()
    for name, data in files.items():
        (folder / name).write_bytes(data)


def matched_pair(epochs) -> tuple[str, str] | None:
    """(a, b): an exposure of the first visit and one of the last visit at the
    closest ``wavelength_um`` (ties: closer in time, then by id), other than the
    first-versus-last pair. None with fewer than two visits."""
    if not epochs:
        return None
    first_v, last_v = min(e.visit for e in epochs), max(e.visit for e in epochs)
    if first_v == last_v:
        return None
    skip = (epochs[0].id, epochs[-1].id)
    pairs = [(abs(a.wavelength_um - b.wavelength_um), b.mjd - a.mjd, a.id, b.id)
             for a in epochs if a.visit == first_v
             for b in epochs if b.visit == last_v and (a.id, b.id) != skip]
    if not pairs:
        return None
    best = min(pairs)
    return best[2], best[3]


def diff_pairs(pid: str, epochs) -> list[tuple[str, str]]:
    """First versus last; for variable stars also the wavelength-matched pair."""
    if len(epochs) < 2:
        return []
    pairs = [(epochs[0].id, epochs[-1].id)]
    if pid in VARIABLES:
        matched = matched_pair(epochs)
        if matched is not None:
            pairs.append(matched)
    return pairs


def write_sequence(seq, pid: str, fov: float, fovs: tuple[float, ...], root: Path,
                   pairs: list[tuple[str, str]],
                   scales: dict[tuple[str, str], float] | None = None) -> FovResult:
    """Write one fov's manifest, frames and ``pairs`` diffs (b - a), each with the
    scale in ``scales`` if given, else the diff's own 99.5th percentile."""
    from app.pipeline.diff import diff_png

    sub = fov_subdir(fov, fovs)
    folder = root / pid / sub
    url_base = f"{URL_ROOT}/{pid}/{sub}"
    resp: EpochsResponse = seq.response
    diffs: list[DiffPair] = []
    diff_files: dict[str, bytes] = {}
    for a, b in pairs:
        given = (scales or {}).get((a, b))
        png, scale = diff_png(seq.cleaned[a].image, seq.cleaned[b].image, given)
        name = f"{a}__{b}.png"
        diff_files[name] = png
        diffs.append(DiffPair(a=a, b=b, url=f"{url_base}diff/{name}", scale=scale))
    stretch = resp.stretch.model_copy(update={"note": PRESET_STRETCH_NOTE})
    resp = resp.model_copy(update={"diffs": diffs, "stretch": stretch})
    EpochsResponse.model_validate_json(resp.model_dump_json())  # round-trip check
    _replace_pngs(folder / "frames", {f"{k}.png": v for k, v in seq.frames.items()})
    _replace_pngs(folder / "diff", diff_files)
    _write_json(folder / "manifest.json", resp.model_dump(mode="json"))
    statuses = [e.quality.status for e in resp.epochs]
    return FovResult(fov=fov, count=resp.count, visits=sorted({e.visit for e in resp.epochs}),
                     statuses={s: statuses.count(s) for s in ("good", "warn", "bad")},
                     diffs=diffs)


def unusable(seq, min_core_valid: float = MIN_CORE_VALID) -> dict[str, str]:
    """Epoch id -> reason, for frames that are ``bad`` or whose target core is
    mostly masked in this sequence."""
    out = {}
    for e in seq.response.epochs:
        core = seq.cleaned[e.id].core_valid
        if e.quality.status == "bad":
            out[e.id] = f"bad (flagged {e.quality.flagged_fraction:.3f})"
        elif core < min_core_valid:
            out[e.id] = f"core valid {core:.2f}"
    return out


def build_preset(
    preset: tuple,
    root: Path = PRESETS_DIR,
    *,
    fovs: tuple[float, ...] = PRESET_FOVS,
    settings: Settings | None = None,
    session: StreamingSession | None = None,
    sia_session: HttpSession | None = None,
    query: EpochQueryResult | None = None,
) -> PresetResult:
    from app.pipeline import build_sequence

    pid, name, ra, dec, band, _simbad = preset
    settings = settings or Settings.from_env()
    result = PresetResult(pid, name, ra, dec, band)
    started = time.monotonic()
    biggest = max(fovs)

    def url_for(fov: float):
        base = f"{URL_ROOT}/{pid}/{fov_subdir(fov, fovs)}frames/"
        return lambda epoch_id, vmin, vmax: f"{base}{epoch_id}.png"

    try:
        if query is None:
            query = find_epochs(ra, dec, band, settings=settings, session=sia_session)
        first = build_sequence(ra, dec, band, biggest, frame_url=url_for(biggest),
                               mode="per_visit", name=name, settings=settings,
                               session=session, query=query, max_per_visit=MAX_PER_VISIT,
                               max_candidates=MAX_CANDIDATES, keep_rejected=False,
                               min_core_valid=MIN_CORE_VALID)
    except ApiError as exc:
        result.error = f"{exc.code}: {exc.message}"
        result.seconds = time.monotonic() - started
        return result
    result.downloads, result.cache_hits = first.downloads, first.cache_hits
    for a in first.attempts:
        if a.outcome == "download_failed":
            result.download_failures.append(f"{a.epoch_id}: {a.detail}")
        elif a.outcome != "accepted":
            result.rejected.append(f"visit {a.visit} {a.epoch_id}: {a.outcome} ({a.detail})")
    result.skipped_visits = list(first.skipped_visits)

    def render_all(ids: set[str], big=None) -> dict[float, object]:
        """Every fov from the same exposures; smaller fovs reuse the largest fov's
        stretch, so a frame looks the same at every fov (a small view's own
        percentiles are dominated by the target and would hide the field stars)."""
        subset = replace(query, epochs=tuple(e for e in query.epochs if e.id in ids))
        if big is None:
            big = build_sequence(ra, dec, band, biggest, frame_url=url_for(biggest),
                                 mode="all", name=name, settings=settings,
                                 session=session, query=subset)
        shared = (big.response.stretch.vmin, big.response.stretch.vmax)
        out = {biggest: big}
        for fov in fovs:
            if fov == biggest:
                continue
            seq = build_sequence(ra, dec, band, fov, frame_url=url_for(fov), mode="all",
                                 name=name, settings=settings, session=session,
                                 query=subset, cutout_fov=biggest, stretch=shared)
            result.download_failures += [f"{f.epoch_id} (fov {fov:g}): {f.code}: "
                                         f"{f.reason}" for f in seq.failures]
            result.downloads += seq.downloads
            out[fov] = seq
        return out

    ids = {e.id for e in first.response.epochs}
    sequences = render_all(ids, first)
    # Drop a frame from every fov if it is bad or its target is masked at any fov,
    # so the three manifests always hold the same exposures. Quality depends only
    # on the frame, not on the set, so one pass is enough.
    drop: dict[str, str] = {}
    for fov in fovs:
        for epoch_id, why in unusable(sequences[fov]).items():
            drop.setdefault(epoch_id, f"{why} at fov {fov:g}")
    if drop:
        result.dropped = [f"{k}: {v}" for k, v in sorted(drop.items())]
        sequences = render_all(ids - set(drop))
    final = sequences[biggest].response
    for e in final.epochs:
        result.per_visit[e.visit] = result.per_visit.get(e.visit, 0) + 1

    pairs = diff_pairs(pid, final.epochs)
    scales: dict[tuple[str, str], float] | None = None
    for fov in sorted(fovs, reverse=True):  # the largest first: it sets the diff scales
        written = write_sequence(sequences[fov], pid, fov, fovs, root, pairs, scales)
        if scales is None:
            scales = {(d.a, d.b): d.scale for d in written.diffs}
        result.fovs.append(written)
    result.fovs.sort(key=lambda f: fovs.index(f.fov))
    result.ok = final.count > 0 and not result.download_failures
    result.seconds = time.monotonic() - started
    return result


def index_document(results: list[PresetResult], fovs: tuple[float, ...] = PRESET_FOVS) -> dict:
    presets = []
    for r in results:
        presets.append({
            "id": r.pid,
            "name": r.name,
            "ra": r.ra,
            "dec": r.dec,
            "band": r.band,
            "default_fov_arcmin": DEFAULT_FOV.get(r.pid, max(fovs)),
            "look_for": LOOK_FOR.get(r.pid, ""),
            "manifests": [
                {"fov_arcmin": f.fov, "url": manifest_url(r.pid, f.fov, fovs),
                 "count": f.count, "visits": len(f.visits)}
                for f in r.fovs
            ],
        })
    return {"credit": CREDIT, "fovs_arcmin": list(fovs), "presets": presets}


def write_index(results: list[PresetResult], root: Path = PRESETS_DIR,
                fovs: tuple[float, ...] = PRESET_FOVS) -> Path:
    path = root / "index.json"
    _write_json(path, index_document(results, fovs))
    return path


def _results_from_index(root: Path) -> dict[str, dict]:
    """Index entries already on disk, so a run for some presets keeps the others."""
    try:
        doc = json.loads((root / "index.json").read_text(encoding="utf-8"))
        return {p["id"]: p for p in doc.get("presets", [])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _kept(entry: dict) -> PresetResult:
    r = PresetResult(entry["id"], entry["name"], entry["ra"], entry["dec"], entry["band"],
                     ok=True)
    r.fovs = [FovResult(fov=m["fov_arcmin"], count=m["count"],
                        visits=list(range(1, m["visits"] + 1)), statuses={})
              for m in entry["manifests"]]
    return r


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preset", action="append", choices=[p[0] for p in PRESETS],
                        help="build only this preset (repeatable); default all")
    parser.add_argument("--out", type=Path, default=PRESETS_DIR)
    args = parser.parse_args(argv)
    wanted = args.preset or [p[0] for p in PRESETS]
    settings = Settings.from_env()
    started = time.monotonic()
    existing = _results_from_index(args.out)
    results: list[PresetResult] = []
    exit_code = 0
    for preset in PRESETS:
        if preset[0] not in wanted:
            if preset[0] in existing:
                results.append(_kept(existing[preset[0]]))
            continue
        r = build_preset(preset, args.out, settings=settings)
        print(f"{r.pid}: {r.seconds:.1f} s, downloads {r.downloads}, cache hits "
              f"{r.cache_hits}, download failures {len(r.download_failures)}")
        if r.error:
            print(f"  FAILED: {r.error}")
            exit_code = 1
            if r.pid in existing:
                results.append(_kept(existing[r.pid]))
            continue
        print(f"  frames per visit {dict(sorted(r.per_visit.items()))}, "
              f"skipped visits {r.skipped_visits}")
        for f in r.fovs:
            print(f"  fov {f.fov:g}: {f.count} frames, visits {f.visits}, {f.statuses}")
        for line in r.rejected:
            print(f"  rejected {line}")
        for line in r.dropped:
            print(f"  dropped {line}")
        for d in r.fovs[0].diffs if r.fovs else []:
            print(f"  diff {d.a} -> {d.b} (scale {d.scale})")
        for line in r.download_failures:
            print(f"  DOWNLOAD FAILED {line}")
        if not r.ok:
            exit_code = 1
        results.append(r)
    path = write_index(results, args.out)
    total = sum(p.stat().st_size for p in args.out.rglob("*") if p.is_file())
    print(f"wrote {path} ({len(results)} presets); folder {total / 1e6:.2f} MB; "
          f"wall {time.monotonic() - started:.1f} s")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
