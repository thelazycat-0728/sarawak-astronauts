"""Settings for the science API.

These values come from the environment (names are provisional; Track C owns the
first two):

- ``DATA_MODE``: which data provider serves the endpoints: ``mock`` (default,
  synthetic frames) or ``irsa`` (live SPHEREx data, ``app.providers.irsa``).
- ``CORS_ORIGINS``: comma-separated list of allowed browser origins.
- ``CORS_ORIGIN_REGEX``: optional regular expression; any origin it fully matches
  is also allowed. Used for Netlify's per-PR deploy previews
  (``https://deploy-preview-N--<site>.netlify.app``). An invalid pattern fails at
  start-up.
- ``GEMINI_API_KEY``: Google Gemini API key for the AI explainer (POST /explain).
  Unset means /explain answers 503 and the frontend uses its offline answers.
  A secret: set it in the Render dashboard, never in the repo.
- ``GEMINI_MODEL``: Gemini model id (default ``gemini-3.8-flash``, free tier).
- ``GEMINI_FALLBACK_MODEL``: model asked once when the main one is overloaded, out
  of quota or unknown (default ``gemini-3.5-flash-lite``; set it to the main model
  to turn the fallback off).
- ``CUTOUT_CACHE_DIR``: where SPHEREx cutouts are cached on disk (default
  ``backend/.cache/cutouts/``, git-ignored). On Render's free plan the disk is wiped
  on every restart, so the cache is only a speed-up.

``DATA_MODE``, ``CORS_ORIGINS`` and ``CUTOUT_CACHE_DIR`` are read from the
environment whenever a ``Settings`` is created; an explicit argument wins over the
environment (``Settings(cutout_cache_dir=...)`` in tests). ``CORS_ORIGIN_REGEX``
and the ``GEMINI_*`` variables are read by ``Settings.from_env()`` only, so a plain
``Settings()`` never picks up an API key.

Everything else is a plain default that code can override (for example in tests).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CORS_ORIGINS = ("http://localhost:5173",)  # Vite dev server
BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CUTOUT_CACHE_DIR = str(BACKEND_DIR / ".cache" / "cutouts")


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _env_data_mode() -> str:
    return os.environ.get("DATA_MODE", "mock").strip().lower() or "mock"


def _env_cors_origins() -> tuple[str, ...]:
    return _split_csv(os.environ.get("CORS_ORIGINS", "")) or DEFAULT_CORS_ORIGINS


def _env_cutout_cache_dir() -> str:
    return os.environ.get("CUTOUT_CACHE_DIR", "").strip() or DEFAULT_CUTOUT_CACHE_DIR


@dataclass(frozen=True)
class Settings:
    # Environment-backed (see the module docstring).
    data_mode: str = field(default_factory=_env_data_mode)
    cors_origins: tuple[str, ...] = field(default_factory=_env_cors_origins)
    # Extra origins allowed by pattern (CORSMiddleware's allow_origin_regex).
    # Read from CORS_ORIGIN_REGEX by from_env only.
    cors_origin_regex: str | None = None
    # Always allowed on top of cors_origins: the Vite dev server, so a laptop demo
    # can use the deployed API. Safe because the API is read-only and sends no
    # credentials (CORS allow_credentials stays off).
    cors_local_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    # SPHEREx release labels used in responses. Keeping them here means a new
    # release needs no code change. The order matters: when the same exposure is
    # served by more than one release, the release listed first wins.
    spherex_releases: tuple[str, ...] = ("DR1", "QR3")
    # Release label -> IRSA SIA collection (Wide Survey spectral images). Names come
    # from IRSA's spherex_intro tutorial and Irsa.list_collections(filter='spherex').
    spherex_collections: tuple[tuple[str, str], ...] = (
        ("DR1", "spherex_dr1"),
        ("QR3", "spherex_qr3"),
    )
    # IRSA Simple Image Access v2 endpoint (the one astroquery's Irsa.query_sia uses).
    irsa_sia_url: str = "https://irsa.ipac.caltech.edu/SIA"
    # Seconds allowed for connecting to IRSA and for each read from it.
    irsa_timeout_s: float = 30.0
    # Transient SIA failures (HTTP 502/503/504, a dropped connection) are retried:
    # this many attempts in all, waiting the delay and then three times longer each
    # time (0.5 s, 1.5 s). IRSA had bursts of 502s on 2026-11-15.
    irsa_sia_attempts: int = 3
    irsa_sia_retry_delay_s: float = 0.5
    # Largest SIA response accepted (streamed; a bigger one is a 502). Barnard's
    # Star D2 (61 exposures) is 158 kB, about 2.1 kB per exposure, so 16 MB holds
    # about 7,500 exposures of one band at one position.
    irsa_sia_max_bytes: int = 16 * 1024 * 1024
    # SPHEREx cutouts (app.cutouts). The cap counts finished cutout files only.
    # The directory is environment-backed (CUTOUT_CACHE_DIR).
    cutout_cache_dir: str = field(default_factory=_env_cutout_cache_dir)
    cutout_cache_max_mb: float = 512.0
    cutout_workers: int = 4  # parallel downloads in a batch
    # Time budget for one download attempt: connecting, the response headers and
    # the whole body share it (app.cutouts.download). A cold 10' cutout took
    # 11-24 s in T-004's tests.
    cutout_timeout_s: float = 60.0
    cutout_retries: int = 2  # extra attempts after a timeout, 5xx or dropped connection
    # Wait before retry n is about backoff * 2**(n-1) (with +-50 % jitter). IRSA
    # answered "503 Server busy" to 14 of 61 cutouts with a 2 s base in T-004.
    cutout_retry_backoff_s: float = 5.0
    # How long a position query is reused when resolving an epoch id.
    epoch_query_ttl_s: float = 600.0

    # ------------------------------------------------ live API (DATA_MODE=irsa)
    # Most requests to IRSA (SIA queries and cutout downloads) in flight at once,
    # across the whole process (app.cutouts.limit).
    irsa_max_concurrency: int = 4
    # Whole-request limit for /epochs. When it passes the API answers 504
    # upstream_timeout; a build that has started keeps running in the background
    # and fills the /epochs cache, so a retry can be fast.
    epochs_deadline_s: float = 90.0
    # In-memory cache of /epochs results, keyed by the rounded position, fov and band.
    epochs_cache_ttl_s: float = 600.0
    epochs_cache_max_entries: int = 32
    # A sequence that lost a visit because its downloads failed is still returned,
    # but cached only this long, so it is rebuilt soon after IRSA recovers.
    epochs_cache_degraded_ttl_s: float = 30.0
    # Most /epochs requests waiting for a build at once. Each waiting request holds
    # a worker thread; more answer 503 unavailable at once.
    epochs_max_waiters: int = 16
    # Whole-request limit for /frame/{id}.png in irsa mode (504 upstream_timeout).
    frame_deadline_s: float = 60.0
    # Most /frame renders running at once in irsa mode. A request beyond that
    # waits up to frame_slot_wait_s for a free slot (the wait counts against
    # frame_deadline_s), then answers 503 unavailable. A render that passed its
    # deadline keeps its slot until it ends. A live view loads all its frames at
    # once (3 per view), so 8 slots and a short wait let a few users load together.
    frame_max_concurrent: int = 8
    frame_slot_wait_s: float = 3.0
    # How long a well-formed epoch id that resolved to nothing (404) at a position
    # is remembered, so repeating it costs no IRSA query and no render.
    frame_not_found_ttl_s: float = 60.0

    # ------------------------------------------------ AI explainer (POST /explain)
    # Google Gemini, free tier (docs/TECH_STACK.md). repr=False keeps the key out of logs.
    gemini_api_key: str | None = field(default=None, repr=False)
    gemini_model: str = "gemini-3.8-flash"
    # Asked once when the main model answers 429/5xx/404 or times out. Free-tier
    # models often answer 503 "high demand"; quotas are per model.
    gemini_fallback_model: str = "gemini-3.5-flash-lite"
    # Per Gemini call; a question may take two calls (main model, then fallback).
    explain_timeout_s: float = 15.0
    # Room for Gemini's thinking tokens plus a 2-4 sentence JSON reply.
    explain_max_output_tokens: int = 2048
    # Per client IP. Generous for a person, tight for a script; they also keep the
    # whole site inside Gemini's free-tier quota.
    explain_rate_per_minute: int = 6
    explain_rate_per_day: int = 60

    # ------------------------------------------------ cleaning pipeline (app.pipeline)
    # FLAGS bits that mask a pixel, by name. Bit numbers are read from each cutout's
    # FLAGS header (MSKNnnnn = name, MSKDnnnn = description), as IRSA's spherex_intro
    # tutorial shows; app.pipeline.flags has a fallback table for headers without
    # them. The set below marks pixels with no usable signal or a known artifact:
    #   NONFUNC "Permanently unusable", MISSING_DATA "Onboard data lost",
    #   HOT "Hot pixel", COLD "Anomalously low signal", NONLINEAR "Nonlinearity
    #   correction cannot be applied reliably", PERSIST "Persistent charge above
    #   threshold", OUTLIER "Pixel flagged by Detect Outliers", GHOST / GHOST_FPA /
    #   GHOST_EXT (ghosts of bright sources), STREAK, SNOWBALL and HALO (artifacts).
    # Deliberately NOT masked:
    #   SOURCE ("Pixel mapped to a known source"): about 98 % of pixels carry it, and
    #     masking it would remove every star. It is never masked, even if listed here.
    #   TRANSIENT, OVERFLOW, SUR_ERROR and BLOOM: in T-005's survey of the 61
    #     Barnard's Star D2 cutouts these four cover the whole core of the star in
    #     every exposure (onboard ramp-fit flags on bright, saturating sources).
    #     Masking them would cut every bright star out of the time-lapse.
    #   FULLSAMPLE (information only), PHANMISS, DICHROIC and CROSSTALK (the pixel
    #     still holds a calibrated, if less accurate, value).
    # IMAGE pixels that are not finite are always treated as masked, except
    # saturated cores (see pipeline_fill_saturated).
    pipeline_mask_flags: tuple[str, ...] = (
        "NONFUNC", "MISSING_DATA", "HOT", "COLD", "NONLINEAR", "PERSIST", "OUTLIER",
        "GHOST", "GHOST_FPA", "GHOST_EXT", "STREAK", "SNOWBALL", "HALO",
    )
    # Saturated star cores are NaN in IMAGE with OVERFLOW set. When True they are
    # filled, before reprojection, with the brightest finite pixel within 2 px, so a
    # bright star renders as a white disc instead of a hole. Display only: the
    # filled value is a lower bound, not a measurement. CleanFrame.filled marks the
    # affected view pixels; they are left out of the stretch percentiles and must
    # be left out of photometry (CleanFrame.measurable), but they are displayed and
    # so do not count as flagged in quality.flagged_fraction.
    pipeline_fill_saturated: bool = True
    # Output grid: north up, east left, centred on the target, side
    # ceil(fov * 60 / scale) pixels (fov=10 gives 97 px). 6.2" is about SPHEREx native.
    pipeline_pixel_scale_arcsec: float = 6.2
    # Shared stretch: asinh between robust percentiles of every valid pixel in the
    # sequence (MJy/sr, after ZODI subtraction). asinh softening as in astropy's
    # AsinhStretch (smaller = more contrast in faint parts); reported to clients as
    # stretch.asinh_a. Grey levels follow app.stretch: valid pixels 1-255, grey 0
    # (image.nodata_gray) for masked, off-detector or empty pixels.
    stretch_percentiles: tuple[float, float] = (1.0, 99.5)
    stretch_asinh_a: float = 0.1
    # Quality status from the flagged fraction of the view (API.md section 3).
    quality_warn_fraction: float = 0.05
    quality_bad_fraction: float = 0.25
    # Per-visit selection (live sequences): candidates tried per visit, and the
    # minimum view coverage a candidate needs to be accepted.
    per_visit_max_candidates: int = 3
    per_visit_min_coverage: float = 0.9

    def collection_for(self, release: str) -> str:
        """IRSA collection name for a release label; ValueError if unmapped."""
        for label, collection in self.spherex_collections:
            if label == release:
                return collection
        raise ValueError(f"no IRSA collection configured for release {release!r}")

    def release_for(self, collection: str) -> str | None:
        """Release label for an IRSA collection name, or None if unknown."""
        for label, name in self.spherex_collections:
            if name == collection:
                return label
        return None

    @classmethod
    def from_env(cls) -> Settings:
        """``Settings()`` (which reads DATA_MODE, CORS_ORIGINS and CUTOUT_CACHE_DIR)
        plus CORS_ORIGIN_REGEX (validated) and the GEMINI_* variables."""
        origin_regex = os.environ.get("CORS_ORIGIN_REGEX", "").strip() or None
        if origin_regex is not None:
            try:
                re.compile(origin_regex)
            except re.error as exc:
                raise ValueError(f"invalid CORS_ORIGIN_REGEX {origin_regex!r}: {exc}") from exc
        return cls(
            cors_origin_regex=origin_regex,
            gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip() or None,
            gemini_model=os.environ.get("GEMINI_MODEL", "").strip() or cls.gemini_model,
            gemini_fallback_model=os.environ.get("GEMINI_FALLBACK_MODEL", "").strip()
            or cls.gemini_fallback_model,
        )
