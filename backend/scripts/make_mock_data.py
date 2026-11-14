"""Generate (or check) the synthetic mock data in frontend/public/mock/.

Usage, from the repo root:

    python backend/scripts/make_mock_data.py           # regenerate every mock file
    python backend/scripts/make_mock_data.py --check   # validate mocks and docs/API.md

Everything this script writes is synthetic. It imitates the shapes in
docs/API.md so that the frontend can be built before the backend exists.
Output is deterministic (fixed seed, pinned numpy and Pillow versions in
backend/scripts/requirements-mock.txt).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError:  # pragma: no cover - friendly message only
    sys.exit(
        "make_mock_data.py needs numpy and Pillow: "
        "pip install -r backend/scripts/requirements-mock.txt"
    )

REPO = Path(__file__).resolve().parents[2]
PUBLIC = REPO / "frontend" / "public"
MOCK = PUBLIC / "mock"
API_DOC = REPO / "docs" / "API.md"
# Byte-identical copies served by the FastAPI mock provider (the app never reads frontend/).
BACKEND_ASSETS = REPO / "backend" / "app" / "providers" / "mock_assets"
BACKEND_ASSET_FILES = ("epochs.json", "lightcurve.json")
MAX_MOCK_BYTES = 1_000_000

SEED = 20250501

# Field geometry: 96 x 96 px at 6.25 arcsec/px = 10 arcmin (SPHEREx native is about 6.2"/px).
WIDTH = HEIGHT = 96
PIXEL_SCALE_ARCSEC = 6.25
FOV_ARCMIN = WIDTH * PIXEL_SCALE_ARCSEC / 60.0
CX = (WIDTH - 1) / 2.0
CY = (HEIGHT - 1) / 2.0

# Barnard's Star view centre: SIMBAD J2000 position propagated to about 2025.5.
TARGET = {"name": "Barnard's Star", "ra": 269.4464, "dec": 4.7668, "frame": "ICRS"}
BAND = {"id": "D2", "wavelength_min_um": 1.10, "wavelength_max_um": 1.62}

SKY_MJYSR = 0.02
NOISE_MJYSR = 0.01
PSF_SIGMA_PX = 1.0
N_STATIC_STARS = 45
STRETCH_ASINH_A = 0.03

# (time_utc, release, visit, flagged_fraction, flags). Visits are about 6 months apart.
EXPOSURES = [
    ("2025-05-14T03:12:40Z", "DR1", 1, 0.004, []),
    ("2025-05-14T16:47:05Z", "DR1", 1, 0.006, []),
    ("2025-05-15T06:20:52Z", "DR1", 1, 0.071, ["TRANSIENT"]),
    ("2025-11-11T22:05:18Z", "DR1", 2, 0.003, []),
    ("2025-11-12T11:39:44Z", "DR1", 2, 0.005, []),
    ("2025-11-13T01:14:09Z", "DR1", 2, 0.009, ["PERSIST"]),
    ("2026-05-13T08:31:27Z", "QR3", 3, 0.004, []),
    ("2026-05-13T22:05:51Z", "QR3", 3, 0.012, []),
    ("2026-05-14T11:40:16Z", "QR3", 3, 0.006, []),
]

# Moving source (stands in for Barnard's Star): steady motion, mostly north (up).
MOVER_AMP = 2.5
MOVER_START = (CX - 1.0, CY + 12.0)  # (x, y) at the first exposure
MOVER_END = (CX + 1.0, CY - 12.0)  # (x, y) at the last exposure

# Variable source: fixed position, brightness factor per exposure.
VARIABLE_XY = (CX + 22.0, CY + 18.0)
VARIABLE_AMP = 1.2
VARIABLE_FACTORS = [1.0, 0.8, 1.15, 0.35, 0.45, 0.3, 1.9, 1.6, 2.0]

LIGHTCURVE_APERTURE_ARCSEC = 12.0
ANNULUS_PX = (4.0, 7.0)
ARCSEC_PER_RAD = 206264.806
UJY_PER_MJYSR_PIXEL = 1e12 * (PIXEL_SCALE_ARCSEC / ARCSEC_PER_RAD) ** 2

STRETCH_NOTE = (
    "One stretch is shared by every frame in this sequence, "
    "so brightness changes between frames are real."
)
EPOCH_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
BANDS = {"D1", "D2", "D3", "D4", "D5", "D6"}
RELEASES = {"DR1", "QR3"}
QUALITY = {"good", "warn", "bad"}


# ---------------------------------------------------------------- helpers


def iso_to_mjd(iso: str) -> float:
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return round(dt.timestamp() / 86400.0 + 40587.0, 6)


def pixel_to_radec(x: float, y: float) -> tuple[float, float]:
    """Small-field approximation for a north-up, east-left image centred on TARGET."""
    dec = TARGET["dec"] - (y - CY) * PIXEL_SCALE_ARCSEC / 3600.0
    cos_dec = np.cos(np.radians(TARGET["dec"]))
    ra = TARGET["ra"] - (x - CX) * PIXEL_SCALE_ARCSEC / 3600.0 / cos_dec
    return round(float(ra), 6), round(float(dec), 6)


def quality_status(flagged_fraction: float) -> str:
    if flagged_fraction < 0.05:
        return "good"
    if flagged_fraction < 0.25:
        return "warn"
    return "bad"


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def save_png(path: Path, data: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    assert data.dtype == np.uint8 and data.ndim == 2  # 2-D uint8 -> 8-bit grey ("L")
    Image.fromarray(data).save(path, format="PNG", optimize=False, compress_level=9)


def add_star(img: np.ndarray, x: float, y: float, amp: float) -> None:
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    img += amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * PSF_SIGMA_PX**2))


def asinh_stretch(img: np.ndarray, vmin: float, vmax: float) -> np.ndarray:
    x = np.clip((img - vmin) / (vmax - vmin), 0.0, 1.0)
    y = np.arcsinh(x / STRETCH_ASINH_A) / np.arcsinh(1.0 / STRETCH_ASINH_A)
    return np.round(y * 255.0).astype(np.uint8)


def diff_to_png(d: np.ndarray, scale: float) -> np.ndarray:
    return np.round(128.0 + 127.0 * np.clip(d / scale, -1.0, 1.0)).astype(np.uint8)


def static_stars(rng: np.random.Generator) -> list[tuple[float, float, float]]:
    """Random static field stars, kept clear of the mover track and the variable."""
    avoid = [VARIABLE_XY] + [
        (
            MOVER_START[0] + f * (MOVER_END[0] - MOVER_START[0]),
            MOVER_START[1] + f * (MOVER_END[1] - MOVER_START[1]),
        )
        for f in np.linspace(0.0, 1.0, 25)
    ]
    stars = []
    while len(stars) < N_STATIC_STARS:
        x, y = rng.uniform(3.0, WIDTH - 4.0), rng.uniform(3.0, HEIGHT - 4.0)
        if min((x - ax) ** 2 + (y - ay) ** 2 for ax, ay in avoid) < 5.0**2:
            continue
        amp = min(0.04 * (rng.pareto(1.5) + 1.0), 3.0)
        stars.append((float(x), float(y), float(amp)))
    return stars


def aperture_flux(img: np.ndarray, x: float, y: float) -> tuple[float, float]:
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    r2 = (xx - x) ** 2 + (yy - y) ** 2
    r_ap = LIGHTCURVE_APERTURE_ARCSEC / PIXEL_SCALE_ARCSEC
    ap = r2 <= r_ap**2
    ann = (r2 >= ANNULUS_PX[0] ** 2) & (r2 <= ANNULUS_PX[1] ** 2)
    bg = float(np.median(img[ann]))
    flux = float(img[ap].sum() - bg * ap.sum()) * UJY_PER_MJYSR_PIXEL
    err = NOISE_MJYSR * float(np.sqrt(ap.sum())) * UJY_PER_MJYSR_PIXEL
    return round(flux, 1), round(err, 1)


# ---------------------------------------------------------------- generate


def generate() -> None:
    rng = np.random.default_rng(SEED)
    stars = static_stars(rng)
    mjds = [iso_to_mjd(t) for t, *_ in EXPOSURES]
    t0, t1 = mjds[0], mjds[-1]

    frames = []
    for i, mjd in enumerate(mjds):
        img = np.full((HEIGHT, WIDTH), SKY_MJYSR, dtype=np.float64)
        for x, y, amp in stars:
            add_star(img, x, y, amp)
        f = (mjd - t0) / (t1 - t0)
        mx = MOVER_START[0] + f * (MOVER_END[0] - MOVER_START[0])
        my = MOVER_START[1] + f * (MOVER_END[1] - MOVER_START[1])
        add_star(img, mx, my, MOVER_AMP)
        add_star(img, *VARIABLE_XY, VARIABLE_AMP * VARIABLE_FACTORS[i])
        img += rng.normal(0.0, NOISE_MJYSR, size=img.shape)
        frames.append(img)

    pooled = np.stack(frames)
    vmin = round(float(np.percentile(pooled, 10.0)), 4)
    vmax = round(float(np.percentile(pooled, 99.98)), 4)

    for sub in ("frames", "diff"):
        (MOCK / sub).mkdir(parents=True, exist_ok=True)
        for old in (MOCK / sub).glob("*.png"):
            old.unlink()

    epochs = []
    for i, ((time_utc, release, visit, flagged, flags), mjd) in enumerate(
        zip(EXPOSURES, mjds)
    ):
        epoch_id = f"mock_{time_utc[:10].replace('-', '')}_{i + 1:02d}_{BAND['id']}"
        save_png(
            MOCK / "frames" / f"{epoch_id}.png", asinh_stretch(frames[i], vmin, vmax)
        )
        wavelength = rng.uniform(BAND["wavelength_min_um"], BAND["wavelength_max_um"])
        epochs.append(
            {
                "id": epoch_id,
                "time_utc": time_utc,
                "mjd": mjd,
                "visit": visit,
                "release": release,
                "obs_id": f"synthetic-{i + 1:04d}",
                "band": BAND["id"],
                "wavelength_um": round(float(wavelength), 3),
                "quality": {
                    "status": quality_status(flagged),
                    "flagged_fraction": flagged,
                    "flags": flags,
                },
                "frame_url": f"/mock/frames/{epoch_id}.png",
            }
        )

    a, b = epochs[0]["id"], epochs[-1]["id"]
    d = frames[-1] - frames[0]
    scale = round(float(np.percentile(np.abs(d), 99.5)), 4)
    diff_url = f"/mock/diff/{a}__{b}.png"
    save_png(MOCK / "diff" / f"{a}__{b}.png", diff_to_png(d, scale))

    write_json(
        MOCK / "epochs.json",
        {
            "synthetic": True,
            "target": TARGET,
            "fov_arcmin": FOV_ARCMIN,
            "band": BAND,
            "image": {
                "width_px": WIDTH,
                "height_px": HEIGHT,
                "pixel_scale_arcsec": PIXEL_SCALE_ARCSEC,
                "orientation": "north-up-east-left",
                "format": "png-gray8",
            },
            "stretch": {
                "shared": True,
                "method": "asinh",
                "vmin": vmin,
                "vmax": vmax,
                "unit": "MJy/sr",
                "note": STRETCH_NOTE,
            },
            "releases": sorted({e["release"] for e in epochs}),
            "duplicates_removed": 0,
            "count": len(epochs),
            "epochs": epochs,
            "diff_url_template": None,
            "diffs": [{"a": a, "b": b, "url": diff_url, "scale": scale}],
        },
    )

    var_ra, var_dec = pixel_to_radec(*VARIABLE_XY)
    points = []
    for e, img in zip(epochs, frames):
        flux, err = aperture_flux(img, *VARIABLE_XY)
        points.append(
            {
                "epoch_id": e["id"],
                "time_utc": e["time_utc"],
                "mjd": e["mjd"],
                "release": e["release"],
                "wavelength_um": e["wavelength_um"],
                "flux": flux,
                "flux_err": err,
                "quality": e["quality"]["status"],
            }
        )
    write_json(
        MOCK / "lightcurve.json",
        {
            "synthetic": True,
            "target": {"ra": var_ra, "dec": var_dec, "name": None, "frame": "ICRS"},
            "band": BAND,
            "aperture_arcsec": LIGHTCURVE_APERTURE_ARCSEC,
            "unit": "uJy",
            "method": "aperture photometry, median sky annulus, no aperture correction",
            "points": points,
        },
    )

    write_json(
        MOCK / "explain.json",
        {
            "synthetic": True,
            "answer": (
                "This is a mock answer. The bright dot near the centre is Barnard's Star. "
                "It is one of the closest stars to the Sun, so it moves across the sky fast "
                "enough to see between snapshots taken months apart. The other stars are much "
                "farther away and look fixed."
            ),
            "ai_generated": True,
            "model": "mock",
            "glossary_terms": ["proper-motion", "epoch"],
        },
    )

    (MOCK / "README.md").write_text(README, encoding="utf-8", newline="\n")
    copy_to_backend()
    print(
        f"Wrote {len(epochs)} frames, 1 diff and 3 JSON files to {MOCK.relative_to(REPO)}"
        f" (copied to {BACKEND_ASSETS.relative_to(REPO)})"
    )


def copy_to_backend() -> None:
    """Copy the mock files the API serves into backend/, byte for byte."""
    for sub in ("frames", "diff"):
        dest = BACKEND_ASSETS / sub
        dest.mkdir(parents=True, exist_ok=True)
        for old in dest.glob("*.png"):
            old.unlink()
        for png in sorted((MOCK / sub).glob("*.png")):
            (dest / png.name).write_bytes(png.read_bytes())
    for name in BACKEND_ASSET_FILES:
        (BACKEND_ASSETS / name).write_bytes((MOCK / name).read_bytes())


README = """# Mock data (synthetic)

Everything in this folder is **synthetic**. It is not SPHEREx data. It exists so the
frontend can be built against the shapes in `docs/API.md` before the backend is ready.
Every JSON file carries `"synthetic": true`.

| File | Mimics |
| --- | --- |
| `epochs.json` | `GET /epochs?ra=269.4464&dec=4.7668&fov=10&band=D2` (Barnard's Star) |
| `frames/*.png` | `GET /frame/{id}.png`: 9 frames, 96 x 96 px, 8-bit grey, one shared stretch |
| `diff/*.png` | `GET /diff`: last frame minus first frame (mid-grey = no change) |
| `lightcurve.json` | `GET /lightcurve` at the variable source's position |
| `explain.json` | `POST /explain` response |

The field has three visits (May 2025, Nov 2025, May 2026) of three exposures each.

- **Moving source:** starts just below the centre and moves steadily north (up),
  about 24 px over the year. The motion is exaggerated (about 14 times faster than
  Barnard's Star really moves) so it is easy to see. Frames in the same visit are
  hours apart, so the source only jumps between visits.
- **Varying source:** about 22 px right and 18 px below the centre (south-west). It
  fades in Nov 2025 and is brightest in May 2026. `lightcurve.json` measures it.
- About 45 static field stars, Gaussian PSF, Gaussian noise.

All URLs in the JSON are root-relative (`/mock/...`), as Vite serves `public/` at `/`.
`diff_url_template` is `null` because static files cannot build arbitrary pairs; use
the pre-rendered pair in `diffs`.

## Regenerate

From the repo root (output is deterministic with the pinned versions):

```
python -m venv backend/.venv
backend/.venv/Scripts/python -m pip install -r backend/scripts/requirements-mock.txt
backend/.venv/Scripts/python backend/scripts/make_mock_data.py
backend/.venv/Scripts/python backend/scripts/make_mock_data.py --check
```

On Linux or macOS use `backend/.venv/bin/python` instead.
"""


# ---------------------------------------------------------------- check

NUM = (int, float)
NULLABLE_STR = (str, type(None))

EPOCH_SPEC = {
    "id": str,
    "time_utc": str,
    "mjd": NUM,
    "visit": int,
    "release": str,
    "obs_id": NULLABLE_STR,
    "band": str,
    "wavelength_um": NUM,
    "quality": {"status": str, "flagged_fraction": NUM, "flags": list},
    "frame_url": str,
}
TARGET_SPEC = {"ra": NUM, "dec": NUM, "name": NULLABLE_STR, "frame": str}
BAND_SPEC = {"id": str, "wavelength_min_um": NUM, "wavelength_max_um": NUM}
EPOCHS_SPEC = {
    "synthetic": bool,
    "target": TARGET_SPEC,
    "fov_arcmin": NUM,
    "band": BAND_SPEC,
    "image": {
        "width_px": int,
        "height_px": int,
        "pixel_scale_arcsec": NUM,
        "orientation": str,
        "format": str,
    },
    "stretch": {
        "shared": bool,
        "method": str,
        "vmin": NUM,
        "vmax": NUM,
        "unit": str,
        "note": str,
    },
    "releases": list,
    "duplicates_removed": int,
    "count": int,
    "epochs": [EPOCH_SPEC],
    "diff_url_template": NULLABLE_STR,
    "diffs": [{"a": str, "b": str, "url": str, "scale": NUM}],
}
LIGHTCURVE_SPEC = {
    "synthetic": bool,
    "target": TARGET_SPEC,
    "band": BAND_SPEC,
    "aperture_arcsec": NUM,
    "unit": str,
    "method": str,
    "points": [
        {
            "epoch_id": str,
            "time_utc": str,
            "mjd": NUM,
            "release": str,
            "wavelength_um": NUM,
            "flux": (int, float, type(None)),
            "flux_err": (int, float, type(None)),
            "quality": str,
        }
    ],
}
EXPLAIN_SPEC = {
    "answer": str,
    "ai_generated": bool,
    "model": str,
    "glossary_terms": list,
}


def validate(obj: object, spec: object, path: str = "$") -> list[str]:
    """Check required keys and types. Extra keys are allowed."""
    if isinstance(spec, dict):
        if not isinstance(obj, dict):
            return [f"{path}: expected object"]
        errs = []
        for key, sub in spec.items():
            if key not in obj:
                errs.append(f"{path}.{key}: missing")
            else:
                errs.extend(validate(obj[key], sub, f"{path}.{key}"))
        return errs
    if isinstance(spec, list):
        if not isinstance(obj, list):
            return [f"{path}: expected array"]
        return [
            e
            for i, item in enumerate(obj)
            for e in validate(item, spec[0], f"{path}[{i}]")
        ]
    types = spec if isinstance(spec, tuple) else (spec,)
    if isinstance(obj, bool) and bool not in types:
        return [f"{path}: unexpected boolean"]
    if not isinstance(obj, types):
        return [f"{path}: expected {'/'.join(t.__name__ for t in types)}"]
    return []


def url_to_file(url: str) -> Path | None:
    if not url.startswith("/") or ".." in url or "?" in url:
        return None
    return PUBLIC / url.lstrip("/")


def check_url(url: str, errs: list[str], where: str) -> Path | None:
    path = url_to_file(url)
    if path is None or not path.is_file():
        errs.append(
            f"{where}: URL {url!r} does not point to a file under frontend/public/"
        )
        return None
    return path


def load(name: str, errs: list[str]) -> dict | None:
    try:
        return json.loads((MOCK / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errs.append(f"{name}: cannot load ({exc})")
        return None


def check_epochs(doc: dict, errs: list[str]) -> set[str]:
    shape_errs = validate(doc, EPOCHS_SPEC)
    if shape_errs:
        errs.extend(f"epochs.json {e}" for e in shape_errs)
        return set()
    if doc["synthetic"] is not True:
        errs.append("epochs.json: mock must set synthetic=true")
    ids = [e["id"] for e in doc["epochs"]]
    if len(set(ids)) != len(ids):
        errs.append("epochs.json: duplicate epoch ids")
    if doc["count"] != len(ids):
        errs.append("epochs.json: count does not match len(epochs)")
    mjds = [e["mjd"] for e in doc["epochs"]]
    if mjds != sorted(mjds):
        errs.append("epochs.json: epochs are not sorted by time")
    size = (doc["image"]["width_px"], doc["image"]["height_px"])
    for i, e in enumerate(doc["epochs"]):
        where = f"epochs.json $.epochs[{i}]"
        if not EPOCH_ID_RE.match(e["id"]):
            errs.append(f"{where}: id {e['id']!r} does not match {EPOCH_ID_RE.pattern}")
        if abs(iso_to_mjd(e["time_utc"]) - e["mjd"]) > 1e-5:
            errs.append(f"{where}: mjd does not match time_utc")
        if e["band"] not in BANDS or e["release"] not in RELEASES:
            errs.append(f"{where}: bad band or release")
        if e["quality"]["status"] not in QUALITY:
            errs.append(f"{where}: bad quality.status")
        png = check_url(e["frame_url"], errs, where)
        if png is not None:
            with Image.open(png) as im:
                if im.size != size or im.mode != "L":
                    errs.append(
                        f"{where}: PNG is {im.size} {im.mode}, expected {size} L"
                    )
    for i, dpair in enumerate(doc["diffs"]):
        where = f"epochs.json $.diffs[{i}]"
        if dpair["a"] not in ids or dpair["b"] not in ids:
            errs.append(f"{where}: a/b are not epoch ids")
        png = check_url(dpair["url"], errs, where)
        if png is not None:
            with Image.open(png) as im:
                if im.size != size:
                    errs.append(f"{where}: diff PNG is {im.size}, expected {size}")
    tmpl = doc["diff_url_template"]
    if tmpl is not None and ("{a}" not in tmpl or "{b}" not in tmpl):
        errs.append("epochs.json: diff_url_template must contain {a} and {b}")
    if len(ids) < 6:
        errs.append("epochs.json: need at least 6 frames")
    return set(ids)


def check_api_doc(errs: list[str]) -> int:
    text = API_DOC.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", text, flags=re.DOTALL)
    for i, block in enumerate(blocks):
        try:
            json.loads(block)
        except json.JSONDecodeError as exc:
            errs.append(f"docs/API.md json block {i + 1}: {exc}")
    if not blocks:
        errs.append("docs/API.md: no ```json blocks found")
    return len(blocks)


def check_backend_copies() -> list[str]:
    """The API's mock assets must equal the frontend mocks byte for byte."""
    names = list(BACKEND_ASSET_FILES)
    for sub in ("frames", "diff"):
        names += [f"{sub}/{p.name}" for p in sorted((MOCK / sub).glob("*.png"))]
        names += [
            f"{sub}/{p.name}"
            for p in sorted((BACKEND_ASSETS / sub).glob("*.png"))
            if not (MOCK / sub / p.name).is_file()
        ]
    errs = []
    for name in names:
        src, dest = MOCK / name, BACKEND_ASSETS / name
        if not (src.is_file() and dest.is_file()) or src.read_bytes() != dest.read_bytes():
            errs.append(f"backend mock asset {name} differs from frontend/public/mock")
    return errs


def check() -> int:
    errs: list[str] = []
    ids: set[str] = set()
    epochs = load("epochs.json", errs)
    if epochs is not None:
        ids = check_epochs(epochs, errs)
    lc = load("lightcurve.json", errs)
    if lc is not None:
        lc_errs = validate(lc, LIGHTCURVE_SPEC)
        errs.extend(f"lightcurve.json {e}" for e in lc_errs)
        if not lc_errs and any(p["epoch_id"] not in ids for p in lc["points"]):
            errs.append("lightcurve.json: epoch_id not found in epochs.json")
    ex = load("explain.json", errs)
    if ex is not None:
        errs.extend(f"explain.json {e}" for e in validate(ex, EXPLAIN_SPEC))
        if ex.get("ai_generated") is not True:
            errs.append("explain.json: ai_generated must be true")
    if not (MOCK / "README.md").is_file():
        errs.append("README.md missing")
    errs.extend(check_backend_copies())
    total = sum(p.stat().st_size for p in MOCK.rglob("*") if p.is_file())
    if total >= MAX_MOCK_BYTES:
        errs.append(f"frontend/public/mock is {total} bytes, limit {MAX_MOCK_BYTES}")
    n_blocks = check_api_doc(errs)

    if errs:
        print("CHECK FAILED:")
        for e in errs:
            print(f"  - {e}")
        return 1
    print(
        f"CHECK OK: {len(ids)} frames + diff valid, 3 mock JSON files valid, "
        f"{n_blocks} JSON blocks in docs/API.md parse, mock folder {total} bytes"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="validate instead of generating"
    )
    args = parser.parse_args()
    if args.check:
        return check()
    generate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
