"""Add per-visit stacks to the committed presets, offline, from the cutout cache.

Run from the repo root or backend/ after build_presets.py (no network: every
cutout must be in ``backend/.cache/cutouts/``):

    python backend/scripts/build_stacks.py                   # all five presets
    python backend/scripts/build_stacks.py --preset wise-0855
    python backend/scripts/check_presets.py                  # then validate

For each preset and fov (largest first, it sets the stack diff scale), every
exposure in the manifest is cleaned again from its cached fov-12 cutout and
rendered with the manifest's stored stretch. The result must equal the committed
frame PNG byte for byte, which proves the stacks are built from the same cleaned
images as the frames; otherwise the preset is left untouched and the run fails.
Then the ``stacks`` section (preset_stacks.py) is added to the manifest (no other
field changes), the stack PNGs are written under ``<folder>/stacks/``, and
``index.json`` gets ``stacks`` (the number of visit stacks) per manifest entry,
and the preset's ``look_for`` from build_presets.LOOK_FOR.

Exit code 1 if a cutout is missing from the cache or a frame does not reproduce.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from build_presets import (
    LOOK_FOR,
    PRESET_FOVS,
    PRESETS_DIR,
    URL_ROOT,
    _write_json,
    fov_subdir,
    manifest_url,
)
from irsa_coverage import PRESETS
from preset_stacks import stack_section, write_stack_files

from app.config import Settings
from app.cutouts import cutout_key, default_cache, make_view
from app.irsa.epochs import parse_epoch_id
from app.models import EpochsResponse
from app.pipeline.clean import CleanFrame
from app.pipeline.render import clean_path, frame_png


class StackBuildError(Exception):
    pass


def reclean(doc: EpochsResponse, settings: Settings, cutout_fov: float,
            cache=None) -> dict[str, CleanFrame]:
    """Epoch id -> the frame cleaned again at the manifest's view, from the cached
    cutout fetched for ``cutout_fov``."""
    fetch_view = make_view(doc.target.ra, doc.target.dec, cutout_fov)
    view = make_view(doc.target.ra, doc.target.dec, doc.fov_arcmin)
    cache = cache or default_cache(settings)
    out = {}
    for e in doc.epochs:
        release, obs_id, band = parse_epoch_id(e.id)
        key = cutout_key(release, obs_id, band, fetch_view.request_ra,
                         fetch_view.request_dec, fetch_view.size_arcmin)
        path = cache.lookup(key)
        if path is None:
            raise StackBuildError(f"{e.id}: cutout not cached; run build_presets.py first")
        out[e.id] = clean_path(path, view.ra, view.dec, view.fov_arcmin, settings)
    return out


def check_frames_reproduce(doc: EpochsResponse, cleaned: dict[str, CleanFrame],
                           public: Path, settings: Settings) -> None:
    vmin, vmax = doc.stretch.vmin, doc.stretch.vmax
    for e in doc.epochs:
        committed = (public / e.frame_url.lstrip("/")).read_bytes()
        if frame_png(cleaned[e.id], vmin, vmax, settings) != committed:
            raise StackBuildError(f"{e.frame_url}: re-cleaned frame differs from the "
                                  "committed PNG")


def add_stacks(preset: tuple, root: Path = PRESETS_DIR, public: Path | None = None,
               fovs: tuple[float, ...] = PRESET_FOVS, settings: Settings | None = None,
               cache=None) -> dict[float, int]:
    """Add ``stacks`` to every fov's manifest of one preset; fov -> visit stacks.
    Nothing is written unless every fov reproduces its frames."""
    pid = preset[0]
    public = public or root.parent
    settings = settings or Settings.from_env()
    biggest = max(fovs)
    planned = []
    scale = None
    for fov in sorted(fovs, reverse=True):
        path = public / manifest_url(pid, fov, fovs).lstrip("/")
        raw = json.loads(path.read_text(encoding="utf-8"))
        doc = EpochsResponse.model_validate(raw)
        cleaned = reclean(doc, settings, biggest, cache)
        check_frames_reproduce(doc, cleaned, public, settings)
        url_base = f"{URL_ROOT}/{pid}/{fov_subdir(fov, fovs)}"
        section, files, _stacks = stack_section(
            doc.epochs, cleaned, (doc.stretch.vmin, doc.stretch.vmax), url_base,
            settings, scale)
        if scale is None and section.diff is not None:
            scale = section.diff.scale
        raw["stacks"] = section.model_dump(mode="json")
        planned.append((fov, path, raw, files, len(section.visits)))
    for _fov, path, raw, files, _n in planned:
        write_stack_files(path.parent, files)
        _write_json(path, raw)
    return {fov: n for fov, _p, _r, _f, n in planned}


def update_index(root: Path, counts: dict[str, dict[float, int]]) -> None:
    """Set ``stacks`` on each listed manifest and ``look_for`` from build_presets;
    every other field is kept."""
    path = root / "index.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    for entry in doc["presets"]:
        if entry["id"] in counts and entry["id"] in LOOK_FOR:
            entry["look_for"] = LOOK_FOR[entry["id"]]
        for m in entry["manifests"]:
            n = counts.get(entry["id"], {}).get(m["fov_arcmin"])
            if n is not None:
                m["stacks"] = n
    _write_json(path, doc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preset", action="append", choices=[p[0] for p in PRESETS],
                        help="only this preset (repeatable); default all")
    args = parser.parse_args(argv)
    wanted = args.preset or [p[0] for p in PRESETS]
    settings = Settings.from_env()
    counts: dict[str, dict[float, int]] = {}
    code = 0
    for preset in PRESETS:
        if preset[0] not in wanted:
            continue
        try:
            counts[preset[0]] = add_stacks(preset, settings=settings)
        except StackBuildError as exc:
            print(f"{preset[0]}: FAILED: {exc}")
            code = 1
            continue
        print(f"{preset[0]}: stacks per fov {counts[preset[0]]}")
    if counts:
        update_index(PRESETS_DIR, counts)
    return code


if __name__ == "__main__":
    sys.exit(main())
