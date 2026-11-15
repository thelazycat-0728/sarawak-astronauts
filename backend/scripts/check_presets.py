"""Validate the static preset sequences in frontend/public/presets/ (offline).

Usage, from the repo root or backend/:

    python backend/scripts/check_presets.py

Checks, for every preset in ``PRESETS`` (irsa_coverage.py) and every fov in
``PRESET_FOVS`` (build_presets.py):

- the manifest exists at its path (fov 12: ``<id>/manifest.json``, others
  ``<id>/fov-<N>/manifest.json``) and validates as ``app.models.EpochsResponse``;
- ``synthetic`` is false, ``target.name`` is set, ``diff_url_template`` is null,
  ``fov_arcmin`` and ``band`` match the preset, and the epochs are in time order;
- every ``frame_url`` and diff URL is a root-relative path to a file under
  ``frontend/public/``, and every frame and diff PNG is 8-bit grey of the size in
  ``image``;
- no frame has ``bad`` quality, and in every frame PNG at least half of the
  target core (pixels within 2 px of the centre) is valid (grey > 0);
- there are at least 2 visits, and ``diffs`` holds the first-versus-last pair;
  variable stars (``VARIABLES``) also hold a second first-visit / last-visit pair;
- the fovs of a preset share epoch ids, stretch and diff pairs and scales;
- ``index.json`` lists exactly these presets with matching name, position, band
  and a manifest URL per fov, and the whole folder is under 10 MB.

Stacks (T-016, ``preset_stacks.py``), for every manifest:

- ``stacks`` validates, holds one stack per visit of the frames (same visits, the
  visit's epoch ids in order, each id in ``epochs``), with a mean ``mjd`` and a
  wavelength range that match those epochs;
- every stack URL resolves to an 8-bit grey PNG of the frame size, whose
  no-data pixels (grey 0) are exactly those with no data in every frame of the
  visit, and whose ``quality.flagged_fraction`` is the share of those pixels;
- the stack diff (first visit to last visit) exists and resolves, and the fovs of
  a preset share the stack diff scale; ``index.json`` gives ``stacks`` per manifest.

Light curves and spectra (``preset_photometry.py``), for every preset:

- ``index.json`` gives ``lightcurve_url`` (``/presets/<id>/lightcurve.json``;
  required for every preset when run as a script),
  which validates as the ``/lightcurve`` shape plus ``visit`` per point and an
  optional ``tracking`` (present exactly for the proper-motion movers). It holds one
  point per epoch of the fov-12 manifest (same ids and visits, in time order), with
  the manifest's target and band;
- a point's ``flux`` and ``flux_err`` are both null exactly when its quality is
  ``bad``, and ``flux_err`` is positive otherwise;
- ``spectrum_url`` is optional (``/presets/<id>/spectrum.json``). The spectrum
  validates, its points are in wavelength order with the same flux rule, ids are
  unique, and ``bands`` lists exactly the arrays of its points.
- ``motion`` (``build_preset_motion.py``) sits exactly on the proper-motion movers'
  entries (required when run as a script), validates as ``PresetMotion``, and has
  one position per visit of the fov-12 manifest, from the first to the last visit.

With ``--baseline`` (the default ``presets_baseline.json`` next to this script,
used by ``main``), every frame and diff PNG and every manifest field other than
``stacks`` must have the recorded SHA-256, and the stretch the recorded values:
the stacks must not change the existing frames. ``--write-baseline`` records the
current files (only after a deliberate rebuild of the frames).

Exit code 0 when everything passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from pydantic import ValidationError

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from build_presets import CREDIT, MIN_CORE_VALID, PRESET_FOVS, VARIABLES, manifest_url
from irsa_coverage import PRESETS
from preset_photometry import (
    LIGHTCURVE_FILE,
    SPECTRUM_FILE,
    PresetLightcurve,
    PresetMotion,
    PresetSpectrum,
)
from preset_science import PROPER_MOTION
from preset_stacks import STACKS_DIR, Stacks, stack_diff_name, stack_name, visit_groups

from app.models import EpochsResponse
from app.stretch import NODATA_GRAY

PUBLIC = REPO / "frontend" / "public"
BASELINE = Path(__file__).resolve().parent / "presets_baseline.json"
MAX_FOLDER_BYTES = 10_000_000
MIN_VISITS = 2
POSITION_TOLERANCE_DEG = 1e-9
CORE_RADIUS_PX = 2.0
MJD_TOLERANCE = 1e-5
WAVELENGTH_TOLERANCE_UM = 1e-5
FRACTION_TOLERANCE = 1e-4


def url_to_file(url: str, public: Path) -> Path | None:
    """The file a root-relative URL points to, or None if it is not a plain path
    to an existing file inside ``public``."""
    if not isinstance(url, str) or not url.startswith("/") or "?" in url or "#" in url:
        return None
    if ".." in url.split("/") or "\\" in url:
        return None
    path = (public / url.lstrip("/")).resolve()
    try:
        path.relative_to(public.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def _check_png(path: Path, size: tuple[int, int], where: str, errs: list[str],
               min_core_valid: float | None = None) -> None:
    """Format, mode and size; with ``min_core_valid``, also the valid (grey > 0)
    share of the pixels within CORE_RADIUS_PX of the image centre (the target)."""
    try:
        with Image.open(path) as im:
            if im.format != "PNG" or im.mode != "L" or im.size != size:
                errs.append(f"{where}: {path.name} is {im.format} {im.mode} {im.size}, "
                            f"expected PNG L {size}")
                return
            gray = np.asarray(im)
    except OSError as exc:
        errs.append(f"{where}: {path.name} cannot be read ({exc})")
        return
    if min_core_valid is not None:
        ny, nx = gray.shape
        yy, xx = np.indices(gray.shape)
        core = np.hypot(xx - (nx - 1) / 2.0, yy - (ny - 1) / 2.0) <= CORE_RADIUS_PX
        share = float(np.mean(gray[core] != NODATA_GRAY)) if core.any() else 0.0
        if share < min_core_valid:
            errs.append(f"{where}: {path.name} target core is {share:.2f} valid, "
                        f"need {min_core_valid}")


def check_manifest(path: Path, preset: tuple, fov: float, public: Path,
                   errs: list[str]) -> EpochsResponse | None:
    band = preset[4]
    where = str(path.relative_to(public)).replace("\\", "/")
    try:
        doc = EpochsResponse.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError:
        errs.append(f"{where}: missing")
        return None
    except ValidationError as exc:
        errs.append(f"{where}: not a valid /epochs response: {exc.errors()[:3]}")
        return None
    if doc.synthetic is not False:
        errs.append(f"{where}: synthetic must be false")
    if not doc.target.name:
        errs.append(f"{where}: target.name must be set")
    if doc.diff_url_template is not None:
        errs.append(f"{where}: diff_url_template must be null")
    if doc.fov_arcmin != fov:
        errs.append(f"{where}: fov_arcmin {doc.fov_arcmin} != {fov}")
    if doc.band.id != band:
        errs.append(f"{where}: band {doc.band.id} != {band}")
    ids = [e.id for e in doc.epochs]
    if len(set(ids)) != len(ids):
        errs.append(f"{where}: duplicate epoch ids")
    if [e.mjd for e in doc.epochs] != sorted(e.mjd for e in doc.epochs):
        errs.append(f"{where}: epochs are not in time order")
    size = (doc.image.width_px, doc.image.height_px)
    for e in doc.epochs:
        if e.quality.status == "bad":
            errs.append(f"{where}: {e.id} has quality bad")
        png = url_to_file(e.frame_url, public)
        if png is None:
            errs.append(f"{where}: frame_url {e.frame_url!r} does not resolve under "
                        "frontend/public/")
        else:
            _check_png(png, size, where, errs, MIN_CORE_VALID)
    visits = {e.visit for e in doc.epochs}
    if len(visits) < MIN_VISITS:
        errs.append(f"{where}: {len(visits)} visit(s), need at least {MIN_VISITS}")
    for d in doc.diffs:
        if d.a not in ids or d.b not in ids:
            errs.append(f"{where}: diff {d.a}/{d.b} is not a pair of epoch ids")
        png = url_to_file(d.url, public)
        if png is None:
            errs.append(f"{where}: diff url {d.url!r} does not resolve under "
                        "frontend/public/")
        else:
            _check_png(png, size, where, errs)
    if ids and not any(d.a == ids[0] and d.b == ids[-1] for d in doc.diffs):
        errs.append(f"{where}: no first-versus-last diff ({ids[0]} -> {ids[-1]})")
    if preset[0] in VARIABLES and ids:
        first_v = {e.id for e in doc.epochs if e.visit == min(visits)}
        last_v = {e.id for e in doc.epochs if e.visit == max(visits)}
        if not any(d.a in first_v and d.b in last_v and (d.a, d.b) != (ids[0], ids[-1])
                   for d in doc.diffs):
            errs.append(f"{where}: no wavelength-matched first/last-visit diff")
    return doc


def _gray(path: Path) -> np.ndarray | None:
    try:
        with Image.open(path) as im:
            return np.asarray(im)
    except OSError:
        return None


def check_stacks(path: Path, doc: EpochsResponse, public: Path,
                 errs: list[str]) -> Stacks | None:
    """The ``stacks`` section of one manifest (see the module docstring)."""
    where = str(path.relative_to(public)).replace("\\", "/")
    folder_url = "/" + where.rsplit("/", 1)[0] + "/"
    try:
        raw = json.loads(path.read_text(encoding="utf-8")).get("stacks")
    except (OSError, ValueError, AttributeError):
        raw = None
    if raw is None:
        errs.append(f"{where}: stacks missing")
        return None
    try:
        stacks = Stacks.model_validate(raw)
    except ValidationError as exc:
        errs.append(f"{where}: stacks not valid: {exc.errors()[:3]}")
        return None
    size = (doc.image.width_px, doc.image.height_px)
    ids = {e.id for e in doc.epochs}
    groups = visit_groups(doc.epochs)
    got_visits = [s.visit for s in stacks.visits]
    if got_visits != list(groups):
        errs.append(f"{where}: stack visits {got_visits} != frame visits {list(groups)}")
    for s in stacks.visits:
        w = f"{where} stack visit {s.visit}"
        unknown = [i for i in s.epoch_ids if i not in ids]
        if unknown:
            errs.append(f"{w}: epoch ids not in epochs: {unknown}")
        group = groups.get(s.visit, [])
        if s.epoch_ids != [e.id for e in group]:
            errs.append(f"{w}: epoch_ids are not the visit's frames")
        if s.n_frames != len(s.epoch_ids):
            errs.append(f"{w}: n_frames {s.n_frames} != {len(s.epoch_ids)} epoch ids")
        if group:
            mjd = sum(e.mjd for e in group) / len(group)
            wls = [e.wavelength_um for e in group]
            if abs(s.mjd - mjd) > MJD_TOLERANCE:
                errs.append(f"{w}: mjd {s.mjd} is not the frames' mean {mjd:.6f}")
            if (abs(s.wavelength_min_um - min(wls)) > WAVELENGTH_TOLERANCE_UM
                    or abs(s.wavelength_max_um - max(wls)) > WAVELENGTH_TOLERANCE_UM
                    or abs(s.wavelength_mean_um - sum(wls) / len(wls))
                    > WAVELENGTH_TOLERANCE_UM):
                errs.append(f"{w}: wavelength range does not match the frames")
        if s.url != f"{folder_url}{STACKS_DIR}/{stack_name(s.visit)}":
            errs.append(f"{w}: url {s.url!r} is not {STACKS_DIR}/{stack_name(s.visit)}")
        png = url_to_file(s.url, public)
        if png is None:
            errs.append(f"{w}: url {s.url!r} does not resolve under frontend/public/")
            continue
        n_errs = len(errs)
        _check_png(png, size, w, errs)
        if len(errs) > n_errs:
            continue
        gray = _gray(png)
        frames = [_gray(f) for e in group if (f := url_to_file(e.frame_url, public))]
        if gray is None or not frames or any(f is None or f.shape != gray.shape
                                             for f in frames):
            continue
        any_data = np.logical_or.reduce([f != NODATA_GRAY for f in frames])
        if not np.array_equal(gray != NODATA_GRAY, any_data):
            errs.append(f"{w}: no-data pixels differ from the frames' common no-data")
        share = float(np.mean(gray == NODATA_GRAY))
        if abs(s.quality.flagged_fraction - share) > FRACTION_TOLERANCE:
            errs.append(f"{w}: flagged_fraction {s.quality.flagged_fraction} != "
                        f"no-data share {share:.4f}")
    d = stacks.diff
    if d is None:
        errs.append(f"{where}: no stack diff")
    elif groups:
        first, last = min(groups), max(groups)
        if (d.a_visit, d.b_visit) != (first, last):
            errs.append(f"{where}: stack diff is visit {d.a_visit} -> {d.b_visit}, "
                        f"want {first} -> {last}")
        if d.url != f"{folder_url}{STACKS_DIR}/diff/{stack_diff_name(first, last)}":
            errs.append(f"{where}: stack diff url {d.url!r} is not the expected path")
        png = url_to_file(d.url, public)
        if png is None:
            errs.append(f"{where}: stack diff url {d.url!r} does not resolve")
        else:
            _check_png(png, size, f"{where} stack diff", errs)
    return stacks


def check_fovs(manifests: dict[tuple[str, float], EpochsResponse], presets: tuple,
               fovs: tuple[float, ...], errs: list[str],
               stacks: dict[tuple[str, float], Stacks] | None = None) -> None:
    """The fovs of a preset hold the same exposures, stretch, diff pairs and
    stacks (visits, epoch ids and stack diff scale)."""
    stacks = stacks or {}
    for preset in presets:
        pid = preset[0]
        docs = {fov: manifests[(pid, fov)] for fov in fovs if (pid, fov) in manifests}
        if len(docs) < 2:
            continue
        ref_fov = max(docs)
        ref = docs[ref_fov]
        ref_st = stacks.get((pid, ref_fov))
        for fov, doc in docs.items():
            if fov == ref_fov:
                continue
            where = f"{pid} fov {fov:g} vs {ref_fov:g}"
            st = stacks.get((pid, fov))
            if st is not None and ref_st is not None:
                if ([(s.visit, s.epoch_ids) for s in st.visits]
                        != [(s.visit, s.epoch_ids) for s in ref_st.visits]):
                    errs.append(f"{where}: stack visits or epoch ids differ")
                if ((st.diff and st.diff.scale) != (ref_st.diff and ref_st.diff.scale)):
                    errs.append(f"{where}: stack diff scale differs")
            if [e.id for e in doc.epochs] != [e.id for e in ref.epochs]:
                errs.append(f"{where}: epoch ids differ")
            if (doc.stretch.vmin, doc.stretch.vmax) != (ref.stretch.vmin, ref.stretch.vmax):
                errs.append(f"{where}: stretch differs")
            if ([(d.a, d.b, d.scale) for d in doc.diffs]
                    != [(d.a, d.b, d.scale) for d in ref.diffs]):
                errs.append(f"{where}: diff pairs or scales differ")


def check_index(root: Path, manifests: dict[tuple[str, float], EpochsResponse],
                presets: tuple, fovs: tuple[float, ...], public: Path,
                errs: list[str],
                stacks: dict[tuple[str, float], Stacks] | None = None) -> None:
    try:
        doc = json.loads((root / "index.json").read_text(encoding="utf-8"))
        entries = doc["presets"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errs.append(f"index.json: cannot load ({exc})")
        return
    if doc.get("credit") != CREDIT:
        errs.append("index.json: credit line missing or changed")
    got_ids = [p.get("id") for p in entries]
    want_ids = [p[0] for p in presets]
    if got_ids != want_ids:
        errs.append(f"index.json: preset ids {got_ids} != {want_ids}")
    by_id = {p.get("id"): p for p in entries}
    for preset in presets:
        pid, name, ra, dec, band, _simbad = preset
        entry = by_id.get(pid)
        if entry is None:
            continue
        where = f"index.json {pid}"
        if entry.get("name") != name or entry.get("band") != band:
            errs.append(f"{where}: name or band differs from the preset")
        if (not isinstance(entry.get("ra"), int | float)
                or not isinstance(entry.get("dec"), int | float)
                or abs(entry["ra"] - ra) > POSITION_TOLERANCE_DEG
                or abs(entry["dec"] - dec) > POSITION_TOLERANCE_DEG):
            errs.append(f"{where}: ra/dec differ from the preset")
        if not entry.get("look_for"):
            errs.append(f"{where}: look_for is empty")
        if entry.get("default_fov_arcmin") not in fovs:
            errs.append(f"{where}: default_fov_arcmin is not one of {list(fovs)}")
        listed = {m.get("fov_arcmin"): m for m in entry.get("manifests", [])}
        if sorted(listed) != sorted(fovs):
            errs.append(f"{where}: manifests for fovs {sorted(listed)}, want {sorted(fovs)}")
        for fov in fovs:
            m = listed.get(fov)
            if m is None:
                continue
            url = m.get("url")
            if url != manifest_url(pid, fov, fovs) or url_to_file(url, public) is None:
                errs.append(f"{where}: fov {fov:g} url {url!r} is wrong or missing")
            man = manifests.get((pid, fov))
            if man is None:
                continue
            if man.target.name != name:
                errs.append(f"{where}: manifest target.name {man.target.name!r} != {name!r}")
            if (abs(man.target.ra - ra) > POSITION_TOLERANCE_DEG
                    or abs(man.target.dec - dec) > POSITION_TOLERANCE_DEG):
                errs.append(f"{where}: fov {fov:g} manifest target differs from the preset")
            if m.get("count") != man.count:
                errs.append(f"{where}: fov {fov:g} count {m.get('count')} != {man.count}")
            if m.get("visits") != len({e.visit for e in man.epochs}):
                errs.append(f"{where}: fov {fov:g} visits do not match the manifest")
            st = (stacks or {}).get((pid, fov))
            if st is not None and m.get("stacks") != len(st.visits):
                errs.append(f"{where}: fov {fov:g} stacks {m.get('stacks')!r} != "
                            f"{len(st.visits)} visit stacks in the manifest")


# ------------------------------------------------------------------ photometry


def _check_points(points, where: str, errs: list[str]) -> None:
    for p in points:
        if (p.quality == "bad") != (p.flux is None) or (p.flux is None) != (p.flux_err is None):
            errs.append(f"{where}: {p.epoch_id} flux, flux_err and quality disagree "
                        "(null exactly when bad)")
        elif p.flux_err is not None and p.flux_err <= 0:
            errs.append(f"{where}: {p.epoch_id} flux_err is not positive")


def _check_motion(pid: str, entry: dict, man: EpochsResponse, errs: list[str],
                  require: bool) -> None:
    where = f"index.json {pid} motion"
    raw = entry.get("motion")
    if pid not in PROPER_MOTION:
        if raw is not None:
            errs.append(f"{where}: only proper-motion movers have motion")
        return
    if raw is None:
        if require:
            errs.append(f"{where}: missing")
        return
    try:
        motion = PresetMotion.model_validate(raw)
    except ValueError as exc:
        errs.append(f"{where}: not valid: {str(exc)[:200]}")
        return
    visits = sorted({e.visit for e in man.epochs})
    if [v.visit for v in motion.visits] != visits:
        errs.append(f"{where}: visits {[v.visit for v in motion.visits]} != {visits}")
    elif (motion.first_visit, motion.last_visit) != (visits[0], visits[-1]):
        errs.append(f"{where}: first and last visit do not match the manifest")
    if tuple(motion.proper_motion_mas_yr) != tuple(PROPER_MOTION[pid]):
        errs.append(f"{where}: proper motion differs from preset_science")


def _load(path: Path | None, model, where: str, errs: list[str]):
    if path is None or not path.is_file():
        errs.append(f"{where}: file missing")
        return None
    try:
        return model.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:  # ValidationError is a ValueError
        errs.append(f"{where}: not valid: {str(exc)[:200]}")
        return None


def check_photometry(root: Path, manifests: dict[tuple[str, float], EpochsResponse],
                     presets: tuple, fovs: tuple[float, ...], public: Path,
                     errs: list[str], require: bool = False) -> dict[str, int]:
    """Light curves and spectra listed in index.json (see the module docstring);
    returns the number of light-curve and spectrum points. ``require``: every
    preset must have a light curve (the committed presets do; a fresh
    build_presets.py output has none until build_preset_photometry.py runs)."""
    counts = {"lightcurve_points": 0, "spectra": 0, "spectrum_points": 0}
    try:
        entries = json.loads((root / "index.json").read_text(encoding="utf-8"))["presets"]
    except (OSError, ValueError, KeyError, TypeError):
        return counts  # check_index reports it
    by_id = {p.get("id"): p for p in entries}
    for preset in presets:
        pid = preset[0]
        entry = by_id.get(pid)
        man = manifests.get((pid, max(fovs)))
        if entry is None or man is None:
            continue
        url = entry.get("lightcurve_url")
        where = f"{pid} lightcurve"
        if url is None and not require:
            pass
        elif url != f"/presets/{pid}/{LIGHTCURVE_FILE}":
            errs.append(f"index.json {pid}: lightcurve_url {url!r} is wrong or missing")
        else:
            lc = _load(url_to_file(url, public), PresetLightcurve, where, errs)
            if lc is not None:
                counts["lightcurve_points"] += len(lc.points)
                if lc.synthetic or lc.target != man.target or lc.band != man.band:
                    errs.append(f"{where}: synthetic, target or band differs from the manifest")
                want = [(e.id, e.visit) for e in man.epochs]
                got = [(p.epoch_id, p.visit) for p in lc.points]
                if sorted(got) != sorted(want):
                    errs.append(f"{where}: points are not one per manifest epoch (ids, visits)")
                if [p.mjd for p in lc.points] != sorted(p.mjd for p in lc.points):
                    errs.append(f"{where}: points are not in time order")
                if (lc.tracking is not None) != (pid in PROPER_MOTION):
                    errs.append(f"{where}: tracking must be set exactly for proper-motion movers")
                _check_points(lc.points, where, errs)
        _check_motion(pid, entry, man, errs, require)
        url = entry.get("spectrum_url")
        if url is None:
            continue
        where = f"{pid} spectrum"
        if url != f"/presets/{pid}/{SPECTRUM_FILE}":
            errs.append(f"index.json {pid}: spectrum_url {url!r} is wrong")
            continue
        sp = _load(url_to_file(url, public), PresetSpectrum, where, errs)
        if sp is None:
            continue
        counts["spectra"] += 1
        counts["spectrum_points"] += len(sp.points)
        if sp.synthetic or sp.target != man.target:
            errs.append(f"{where}: synthetic or target differs from the manifest")
        waves = [p.wavelength_um for p in sp.points]
        if not sp.points or waves != sorted(waves):
            errs.append(f"{where}: points are empty or not in wavelength order")
        if len({p.epoch_id for p in sp.points}) != len(sp.points):
            errs.append(f"{where}: epoch ids repeat")
        if [b.id for b in sp.bands] != sorted({p.band for p in sp.points}):
            errs.append(f"{where}: bands do not list exactly the arrays of the points")
        _check_points(sp.points, where, errs)
    return counts


# ------------------------------------------------------------------ baseline


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(obj: object) -> str:
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _index_without_additions(doc: dict) -> dict:
    """index.json minus the additive fields (T-016 ``stacks`` and ``look_for``; the
    ``lightcurve_url``, ``spectrum_url`` and ``motion`` of the preset photometry)."""
    out = json.loads(json.dumps(doc))
    for p in out.get("presets", []):
        p.pop("look_for", None)
        p.pop("lightcurve_url", None)
        p.pop("spectrum_url", None)
        p.pop("motion", None)
        for m in p.get("manifests", []):
            m.pop("stacks", None)
    return out


def baseline_document(public: Path, root: Path, presets: tuple = PRESETS,
                      fovs: tuple[float, ...] = PRESET_FOVS) -> dict:
    """SHA-256 of every manifest's fields other than ``stacks``, its stretch, every
    frame and diff PNG it references, and index.json without the additive fields."""
    manifests: dict[str, dict] = {}
    files: dict[str, str] = {}
    for preset in presets:
        for fov in fovs:
            url = manifest_url(preset[0], fov, fovs)
            raw = json.loads((public / url.lstrip("/")).read_text(encoding="utf-8"))
            raw.pop("stacks", None)
            manifests[url] = {"fields_sha256": _canonical_sha256(raw),
                              "stretch": [raw["stretch"]["vmin"], raw["stretch"]["vmax"]]}
            urls = [e["frame_url"] for e in raw["epochs"]] + [d["url"] for d in raw["diffs"]]
            for u in urls:
                files[u] = _sha256(public / u.lstrip("/"))
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    return {"index_sha256": _canonical_sha256(_index_without_additions(index)),
            "manifests": manifests, "files": dict(sorted(files.items()))}


def check_baseline(baseline: Path, public: Path, root: Path, presets: tuple,
                   fovs: tuple[float, ...], errs: list[str]) -> int:
    """Compare with the recorded baseline; returns the number of files compared."""
    try:
        want = json.loads(baseline.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errs.append(f"baseline {baseline.name}: cannot load ({exc})")
        return 0
    try:
        got = baseline_document(public, root, presets, fovs)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errs.append(f"baseline: cannot read the current presets ({exc})")
        return 0
    if got["index_sha256"] != want.get("index_sha256"):
        errs.append("baseline: index.json fields (other than stacks and look_for) changed")
    for url, rec in want.get("manifests", {}).items():
        now = got["manifests"].get(url)
        if now is None:
            errs.append(f"baseline: {url} missing")
            continue
        if now["stretch"] != rec["stretch"]:
            errs.append(f"baseline: {url} stretch {now['stretch']} != {rec['stretch']}")
        if now["fields_sha256"] != rec["fields_sha256"]:
            errs.append(f"baseline: {url} fields other than stacks changed")
    for url in sorted(set(got["manifests"]) - set(want.get("manifests", {}))):
        errs.append(f"baseline: {url} is not in the baseline")
    want_files = want.get("files", {})
    for url, sha in want_files.items():
        if got["files"].get(url) != sha:
            errs.append(f"baseline: {url} changed or missing")
    for url in sorted(set(got["files"]) - set(want_files)):
        errs.append(f"baseline: {url} is not in the baseline")
    return len(want_files)


def check(root: Path | None = None, public: Path | None = None, presets: tuple = PRESETS,
          fovs: tuple[float, ...] = PRESET_FOVS,
          max_bytes: int = MAX_FOLDER_BYTES,
          baseline: Path | None = None,
          require_photometry: bool = False) -> tuple[list[str], dict]:
    """(errors, summary). ``root`` is the presets folder inside ``public``.
    ``baseline``: also compare the frames and manifest fields with that record."""
    public = public or PUBLIC
    root = root or public / "presets"
    errs: list[str] = []
    manifests: dict[tuple[str, float], EpochsResponse] = {}
    stacks: dict[tuple[str, float], Stacks] = {}
    for preset in presets:
        for fov in fovs:
            path = public / manifest_url(preset[0], fov, fovs).lstrip("/")
            doc = check_manifest(path, preset, fov, public, errs)
            if doc is not None:
                manifests[(preset[0], fov)] = doc
                st = check_stacks(path, doc, public, errs)
                if st is not None:
                    stacks[(preset[0], fov)] = st
    check_fovs(manifests, presets, fovs, errs, stacks)
    check_index(root, manifests, presets, fovs, public, errs, stacks)
    photometry = check_photometry(root, manifests, presets, fovs, public, errs,
                                  require_photometry)
    compared = 0
    if baseline is not None:
        compared = check_baseline(baseline, public, root, presets, fovs, errs)
    if not (root / "README.md").is_file():
        errs.append("README.md missing")
    total = sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) if root.is_dir() else 0
    if total >= max_bytes:
        errs.append(f"presets folder is {total} bytes, limit {max_bytes}")
    frames = sum(m.count for m in manifests.values())
    n_stacks = sum(len(s.visits) for s in stacks.values())
    return errs, {"manifests": len(manifests), "frames": frames, "stacks": n_stacks,
                  "baseline_files": compared, "bytes": total, **photometry}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", type=Path, default=BASELINE,
                        help="baseline of the existing frames (default: %(default)s)")
    parser.add_argument("--write-baseline", action="store_true",
                        help="record the current frames and manifest fields, then exit")
    args = parser.parse_args(argv)
    if args.write_baseline:
        doc = baseline_document(PUBLIC, PUBLIC / "presets")
        args.baseline.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n",
                                 encoding="utf-8", newline="\n")
        print(f"wrote {args.baseline} ({len(doc['files'])} files, "
              f"{len(doc['manifests'])} manifests)")
        return 0
    errs, summary = check(baseline=args.baseline, require_photometry=True)
    if errs:
        print("PRESET CHECK FAILED:")
        for e in errs:
            print(f"  - {e}")
        return 1
    print(f"PRESET CHECK OK: {summary['manifests']} manifests "
          f"({len(PRESETS)} presets x {len(PRESET_FOVS)} fovs), {summary['frames']} frames, "
          f"{summary['stacks']} visit stacks, {summary['lightcurve_points']} light-curve "
          f"points, {summary['spectra']} spectra ({summary['spectrum_points']} points), "
          f"{summary['baseline_files']} frame and diff "
          f"PNGs and every manifest field unchanged from the baseline, "
          f"folder {summary['bytes']} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
