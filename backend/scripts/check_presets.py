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
- there are at least 2 visits, and ``diffs`` holds the first-versus-last pair;
- ``index.json`` lists exactly these presets with matching name, position, band
  and a manifest URL per fov, and the whole folder is under 10 MB.

Exit code 0 when everything passes, 1 otherwise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image
from pydantic import ValidationError

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from build_presets import CREDIT, PRESET_FOVS, manifest_url
from irsa_coverage import PRESETS

from app.models import EpochsResponse

PUBLIC = REPO / "frontend" / "public"
MAX_FOLDER_BYTES = 10_000_000
MIN_VISITS = 2
POSITION_TOLERANCE_DEG = 1e-9


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


def _check_png(path: Path, size: tuple[int, int], where: str, errs: list[str]) -> None:
    try:
        with Image.open(path) as im:
            if im.format != "PNG" or im.mode != "L" or im.size != size:
                errs.append(f"{where}: {path.name} is {im.format} {im.mode} {im.size}, "
                            f"expected PNG L {size}")
    except OSError as exc:
        errs.append(f"{where}: {path.name} cannot be read ({exc})")


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
        png = url_to_file(e.frame_url, public)
        if png is None:
            errs.append(f"{where}: frame_url {e.frame_url!r} does not resolve under "
                        "frontend/public/")
        else:
            _check_png(png, size, where, errs)
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
    return doc


def check_index(root: Path, manifests: dict[tuple[str, float], EpochsResponse],
                presets: tuple, fovs: tuple[float, ...], public: Path,
                errs: list[str]) -> None:
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


def check(root: Path | None = None, public: Path | None = None, presets: tuple = PRESETS,
          fovs: tuple[float, ...] = PRESET_FOVS,
          max_bytes: int = MAX_FOLDER_BYTES) -> tuple[list[str], dict]:
    """(errors, summary). ``root`` is the presets folder inside ``public``."""
    public = public or PUBLIC
    root = root or public / "presets"
    errs: list[str] = []
    manifests: dict[tuple[str, float], EpochsResponse] = {}
    for preset in presets:
        for fov in fovs:
            path = public / manifest_url(preset[0], fov, fovs).lstrip("/")
            doc = check_manifest(path, preset, fov, public, errs)
            if doc is not None:
                manifests[(preset[0], fov)] = doc
    check_index(root, manifests, presets, fovs, public, errs)
    if not (root / "README.md").is_file():
        errs.append("README.md missing")
    total = sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) if root.is_dir() else 0
    if total >= max_bytes:
        errs.append(f"presets folder is {total} bytes, limit {max_bytes}")
    frames = sum(m.count for m in manifests.values())
    return errs, {"manifests": len(manifests), "frames": frames, "bytes": total}


def main() -> int:
    errs, summary = check()
    if errs:
        print("PRESET CHECK FAILED:")
        for e in errs:
            print(f"  - {e}")
        return 1
    print(f"PRESET CHECK OK: {summary['manifests']} manifests "
          f"({len(PRESETS)} presets x {len(PRESET_FOVS)} fovs), {summary['frames']} frames, "
          f"folder {summary['bytes']} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
