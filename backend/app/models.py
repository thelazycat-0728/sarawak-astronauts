"""Pydantic models for every request and response in docs/API.md (contract v0.1)."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------- shared values

BandId = Literal["D1", "D2", "D3", "D4", "D5", "D6"]
BAND_IDS: tuple[str, ...] = ("D1", "D2", "D3", "D4", "D5", "D6")
# (wavelength_min_um, wavelength_max_um) per detector array, API.md section 2.
BAND_RANGES_UM: dict[str, tuple[float, float]] = {
    "D1": (0.75, 1.09),
    "D2": (1.10, 1.62),
    "D3": (1.63, 2.41),
    "D4": (2.42, 3.82),
    "D5": (3.83, 4.41),
    "D6": (4.42, 5.00),
}
DEFAULT_BAND = "D2"
# Reserved for the Stretch goal: single channels such as D2-07.
RESERVED_CHANNEL_RE = re.compile(r"^D[1-6]-(0[1-9]|1[0-7])$")
EPOCH_ID_PATTERN = r"^[A-Za-z0-9_-]{1,80}$"

QualityStatus = Literal["good", "warn", "bad"]


def check_band(value: str) -> str:
    if value in BAND_IDS:
        return value
    if RESERVED_CHANNEL_RE.fullmatch(value):
        raise ValueError(
            f"single channels such as {value} are not available yet; use D1 to D6"
        )
    raise ValueError("band must be one of D1, D2, D3, D4, D5, D6")


BandParam = Annotated[str, AfterValidator(check_band)]
EpochId = Annotated[str, Field(pattern=EPOCH_ID_PATTERN)]
Ra = Annotated[float, Field(ge=0, lt=360, allow_inf_nan=False)]
Dec = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]

# ---------------------------------------------------------------- query parameters


class PositionQuery(BaseModel):
    ra: Ra = Field(description="Right ascension, decimal degrees (ICRS), 0 <= ra < 360")
    dec: Dec = Field(description="Declination, decimal degrees (ICRS), -90 to 90")
    band: BandParam = Field(DEFAULT_BAND, description="Detector array D1 to D6")


class ViewQuery(PositionQuery):
    fov: float = Field(
        10.0, ge=1, le=30, allow_inf_nan=False, description="Cutout side, arcmin"
    )


class FrameQuery(ViewQuery):
    vmin: float | None = Field(None, allow_inf_nan=False, description="MJy/sr")
    vmax: float | None = Field(None, allow_inf_nan=False, description="MJy/sr")

    @model_validator(mode="after")
    def _stretch_pair(self) -> Self:
        if (self.vmin is None) != (self.vmax is None):
            raise ValueError("pass both vmin and vmax, or neither")
        if self.vmin is not None and self.vmax is not None and self.vmax <= self.vmin:
            raise ValueError("vmax must be greater than vmin")
        return self


class DiffQuery(ViewQuery):
    a: EpochId
    b: EpochId
    scale: float | None = Field(
        None, gt=0, allow_inf_nan=False, description="MJy/sr mapped to white/black"
    )

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if self.a == self.b:
            raise ValueError("a and b must be different epochs")
        return self


class LightcurveQuery(PositionQuery):
    aperture: float = Field(
        12.0, ge=3, le=60, allow_inf_nan=False, description="Aperture radius, arcsec"
    )


# ---------------------------------------------------------------- responses


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class Target(BaseModel):
    ra: float
    dec: float
    name: str | None
    frame: Literal["ICRS"] = "ICRS"


class BandInfo(BaseModel):
    id: BandId
    wavelength_min_um: float
    wavelength_max_um: float

    @classmethod
    def for_band(cls, band: str) -> BandInfo:
        lo, hi = BAND_RANGES_UM[band]
        return cls(id=band, wavelength_min_um=lo, wavelength_max_um=hi)


class ImageInfo(BaseModel):
    width_px: int
    height_px: int
    pixel_scale_arcsec: float
    orientation: Literal["north-up-east-left"] = "north-up-east-left"
    format: Literal["png-gray8"] = "png-gray8"
    # Grey level of pixels with no data (masked, off the detector or empty). Valid
    # pixels use 1-255 (see app.stretch and API.md section 3).
    nodata_gray: Literal[0]


class Stretch(BaseModel):
    shared: bool
    method: str
    vmin: float
    vmax: float
    # Softening of the asinh stretch (dimensionless), needed to invert grey levels.
    asinh_a: float = Field(gt=0, allow_inf_nan=False)
    unit: Literal["MJy/sr"] = "MJy/sr"
    note: str


class Quality(BaseModel):
    status: QualityStatus
    flagged_fraction: float = Field(ge=0, le=1)
    flags: list[str]


class EpochBase(BaseModel):
    """An epoch as a data provider reports it, before the API adds its URL."""

    id: EpochId
    time_utc: str
    mjd: float
    visit: int = Field(ge=1)
    release: str
    obs_id: str | None
    band: BandId
    wavelength_um: float
    quality: Quality


class Epoch(EpochBase):
    frame_url: str


class DiffPair(BaseModel):
    a: EpochId
    b: EpochId
    url: str
    scale: float = Field(gt=0)


class EpochsResponse(BaseModel):
    synthetic: bool
    target: Target
    fov_arcmin: float
    band: BandInfo
    image: ImageInfo
    stretch: Stretch
    releases: list[str]
    duplicates_removed: int = Field(ge=0)
    count: int = Field(ge=0)
    epochs: list[Epoch]
    diff_url_template: str | None
    diffs: list[DiffPair]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.count != len(self.epochs):
            raise ValueError("count must equal len(epochs)")
        tmpl = self.diff_url_template
        if tmpl is not None and ("{a}" not in tmpl or "{b}" not in tmpl):
            raise ValueError("diff_url_template must contain {a} and {b}")
        return self


class LightcurvePoint(BaseModel):
    epoch_id: EpochId
    time_utc: str
    mjd: float
    release: str
    wavelength_um: float
    flux: float | None
    flux_err: float | None
    quality: QualityStatus


class LightcurveResponse(BaseModel):
    synthetic: bool
    target: Target
    band: BandInfo
    aperture_arcsec: float
    unit: Literal["uJy"] = "uJy"
    method: str
    points: list[LightcurvePoint]


# ---------------------------------------------------------------- POST /explain (Track C)


class ExplainContext(BaseModel):
    ra: float | None = None
    dec: float | None = None
    fov_arcmin: float | None = None
    band: str | None = None
    target_name: str | None = None
    epoch_id: str | None = None


class ExplainRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    context: ExplainContext | None = None


class ExplainResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    answer: str
    ai_generated: Literal[True]
    model: str
    glossary_terms: list[str]
