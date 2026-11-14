"""Ask IRSA which SPHEREx spectral images cover a sky position.

Follows IRSA's ``spherex_intro`` tutorial: a Simple Image Access v2 (SIA) query on
the ``spherex_dr1`` and ``spherex_qr3`` collections. The position, band and
collections are sent as SIA service parameters (no ADQL), built only from values
that were converted to ``float`` and range-checked first.

One SIA request covers every configured release. ``BAND`` is set to the centre
wavelength of the detector array, so IRSA returns only that array's images; the
parser checks ``energy_bandpassname`` again and drops anything else.
"""

from __future__ import annotations

import io
import logging
import math
import numbers
import re
import warnings
from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np
import requests
from astropy.io.votable import parse as parse_votable
from pyvo.dal import DALResults
from pyvo.dal.exceptions import DALQueryError

from app.config import Settings
from app.errors import InvalidParameter, UpstreamError, UpstreamTimeout
from app.irsa.epochs import build_epochs
from app.irsa.records import EpochQueryResult, Exposure, parse_s_region
from app.models import BAND_IDS, BAND_RANGES_UM

log = logging.getLogger("app.irsa")

# Point query: any image whose footprint touches a 1 arcsec circle at the target.
SEARCH_RADIUS_DEG = 1.0 / 3600.0
USER_AGENT = "sarawak-astronauts-science-api (SPHEREx time-lapse viewer)"
REQUIRED_COLUMNS = (
    "obs_id",
    "obs_collection",
    "energy_bandpassname",
    "t_min",
    "t_max",
    "access_url",
)
_OBS_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,56}$")


class HttpSession(Protocol):
    def get(self, url: str, *, params: Any, timeout: Any, headers: Any) -> Any: ...


# ------------------------------------------------------------------ validation


def _to_float(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise InvalidParameter(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidParameter(f"{name} must be finite")
    return number


def validate_position(ra: object, dec: object) -> tuple[float, float]:
    """Return (ra, dec) as floats in range, or raise InvalidParameter."""
    ra_f = _to_float("ra", ra)
    dec_f = _to_float("dec", dec)
    if not 0.0 <= ra_f < 360.0:
        raise InvalidParameter("ra must be in [0, 360)")
    if not -90.0 <= dec_f <= 90.0:
        raise InvalidParameter("dec must be between -90 and 90")
    return ra_f, dec_f


def validate_band(band: object) -> str:
    if not isinstance(band, str) or band not in BAND_IDS:
        raise InvalidParameter("band must be one of D1-D6")
    return band


def band_centre_m(band: str) -> float:
    """Centre wavelength of a detector array in metres (SIA BAND unit)."""
    lo, hi = BAND_RANGES_UM[band]
    return (lo + hi) / 2.0 * 1e-6


def build_sia_params(
    ra: float, dec: float, band: str, collections: Sequence[str]
) -> list[tuple[str, str]]:
    """SIA query parameters. Inputs must already be validated floats and a band id."""
    params = [
        ("POS", f"CIRCLE {ra:.8f} {dec:.8f} {SEARCH_RADIUS_DEG:.8f}"),
        ("BAND", f"{band_centre_m(band):.6e} {band_centre_m(band):.6e}"),
    ]
    params.extend(("COLLECTION", c) for c in collections)
    return params


# --------------------------------------------------------------------- parsing


def _cell(row: Any, name: str) -> Any:
    value = row[name]
    if np.ma.is_masked(value):
        return None
    return value


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def parse_sia_votable(content: bytes, band: str, settings: Settings) -> list[Exposure]:
    """Parse an IRSA SIA VOTable into exposures for one band.

    Rows from other bands, unknown collections, or with unusable ids, times or
    URLs are dropped (and logged). Raises UpstreamError for an error status or an
    unreadable document.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            votable = parse_votable(io.BytesIO(content), verify="ignore")
            results = DALResults(votable)
            table = results.to_table()
    except DALQueryError as exc:
        raise UpstreamError(f"IRSA SIA query failed: {str(exc)[:200]}") from exc
    except Exception as exc:
        raise UpstreamError("IRSA SIA returned an unreadable response") from exc
    if str(getattr(results, "query_status", "")).upper() == "OVERFLOW":
        log.warning("IRSA SIA result was truncated (OVERFLOW); %d rows", len(table))

    if len(table) == 0:
        return []
    missing = [c for c in REQUIRED_COLUMNS if c not in table.colnames]
    if missing:
        raise UpstreamError(f"IRSA SIA response lacks columns: {', '.join(missing)}")

    wanted_bandpass = f"SPHEREx-{band}"
    exposures: list[Exposure] = []
    skipped = 0
    for row in table:
        bandpass = _cell(row, "energy_bandpassname")
        if str(bandpass) != wanted_bandpass:
            skipped += 1
            continue
        collection = str(_cell(row, "obs_collection"))
        release = settings.release_for(collection)
        obs_id = str(_cell(row, "obs_id") or "")
        url = str(_cell(row, "access_url") or "")
        t_min = _float_or_none(_cell(row, "t_min"))
        t_max = _float_or_none(_cell(row, "t_max"))
        if (
            release is None
            or not _OBS_ID_RE.match(obs_id)
            or not url.startswith("https://")
            or t_min is None
            or t_max is None
        ):
            skipped += 1
            continue
        em_min = _float_or_none(_cell(row, "em_min")) if "em_min" in table.colnames else None
        em_max = _float_or_none(_cell(row, "em_max")) if "em_max" in table.colnames else None
        exptime = (
            _float_or_none(_cell(row, "t_exptime")) if "t_exptime" in table.colnames else None
        )
        did = _cell(row, "obs_publisher_did") if "obs_publisher_did" in table.colnames else None
        region = _cell(row, "s_region") if "s_region" in table.colnames else None
        exposures.append(
            Exposure(
                obs_id=obs_id,
                band=band,
                release=release,
                collection=collection,
                publisher_did=str(did) if did is not None else "",
                t_start_mjd=t_min,
                t_end_mjd=max(t_min, t_max),
                exptime_s=exptime,
                wavelength_min_um=round(em_min * 1e6, 4) if em_min is not None else None,
                wavelength_max_um=round(em_max * 1e6, 4) if em_max is not None else None,
                access_url=url,
                footprint=parse_s_region(str(region)) if region is not None else None,
            )
        )
    if skipped:
        log.info("IRSA SIA: kept %d rows, skipped %d (other band or unusable)",
                 len(exposures), skipped)
    return exposures


# ----------------------------------------------------------------------- query


def _make_session() -> requests.Session:
    return requests.Session()


def fetch_sia(
    params: Sequence[tuple[str, str]],
    settings: Settings,
    session: HttpSession | None = None,
) -> bytes:
    """GET the SIA endpoint with the configured timeout; map failures to API errors."""
    timeout = settings.irsa_timeout_s
    own_session = session is None
    sess: Any = _make_session() if own_session else session
    try:
        resp = sess.get(
            settings.irsa_sia_url,
            params=list(params),
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        )
    except requests.Timeout as exc:
        raise UpstreamTimeout(f"IRSA did not answer within {timeout:g} s") from exc
    except requests.RequestException as exc:
        raise UpstreamError(f"IRSA request failed ({type(exc).__name__})") from exc
    finally:
        if own_session:
            sess.close()
    status = int(getattr(resp, "status_code", 0))
    if status >= 400:
        raise UpstreamError(f"IRSA SIA returned HTTP {status}")
    return resp.content


def query_exposures(
    ra: object,
    dec: object,
    band: object,
    releases: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    session: HttpSession | None = None,
) -> list[Exposure]:
    """Every SPHEREx spectral image of ``band`` covering (ra, dec), all releases.

    The list is raw: unsorted and possibly holding the same exposure from more
    than one release. Validation happens before any request is made.
    """
    settings = settings or Settings()
    ra_f, dec_f = validate_position(ra, dec)
    band_s = validate_band(band)
    labels = tuple(releases) if releases is not None else settings.spherex_releases
    collections = [settings.collection_for(label) for label in labels]
    params = build_sia_params(ra_f, dec_f, band_s, collections)
    content = fetch_sia(params, settings, session)
    exposures = parse_sia_votable(content, band_s, settings)
    return [e for e in exposures if e.release in labels]


def find_epochs(
    ra: object,
    dec: object,
    band: object = "D2",
    *,
    settings: Settings | None = None,
    session: HttpSession | None = None,
) -> EpochQueryResult:
    """Query IRSA and return the de-duplicated, time-sorted epochs with visits."""
    settings = settings or Settings()
    ra_f, dec_f = validate_position(ra, dec)
    band_s = validate_band(band)
    raw = query_exposures(ra_f, dec_f, band_s, settings=settings, session=session)
    per_release = {label: 0 for label in settings.spherex_releases}
    for exp in raw:
        per_release[exp.release] = per_release.get(exp.release, 0) + 1
    epochs, removed = build_epochs(raw, settings.spherex_releases)
    return EpochQueryResult(
        ra=ra_f,
        dec=dec_f,
        band=band_s,
        epochs=epochs,
        duplicates_removed=removed,
        exposures_per_release=per_release,
    )
