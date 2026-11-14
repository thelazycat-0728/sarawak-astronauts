"""Science endpoints from docs/API.md section 3: /epochs, /frame/{id}.png, /diff, /lightcurve.

Routes validate input, call the configured data provider and add absolute URLs
built from the incoming request, so responses work behind any host.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from app.models import (
    EPOCH_ID_PATTERN,
    DiffPair,
    DiffQuery,
    Epoch,
    EpochsResponse,
    ErrorResponse,
    FrameQuery,
    LightcurveQuery,
    LightcurveResponse,
    ViewQuery,
)
from app.providers import DataProvider

router = APIRouter(tags=["science"])

_ERRORS: dict[int | str, dict] = {
    400: {"model": ErrorResponse, "description": "Invalid parameter"},
    502: {"model": ErrorResponse, "description": "Archive error"},
    504: {"model": ErrorResponse, "description": "Archive timeout"},
}
_PNG: dict[int | str, dict] = {
    200: {"content": {"image/png": {}}, "description": "8-bit greyscale PNG"},
    404: {"model": ErrorResponse, "description": "Unknown epoch id"},
    **_ERRORS,
}
# /diff and /lightcurve are not available yet with DATA_MODE=irsa.
_UNAVAILABLE: dict[int | str, dict] = {
    503: {"model": ErrorResponse, "description": "Not available for live data yet"},
}


def get_provider(request: Request) -> DataProvider:
    return request.app.state.provider


Provider = Annotated[DataProvider, Depends(get_provider)]


def _num(x: float) -> str:
    """Compact, exact number text for URLs: 10.0 -> '10', 269.4464 -> '269.4464'."""
    return str(int(x)) if float(x).is_integer() else repr(float(x))


def _view_params(q: ViewQuery) -> dict[str, str]:
    return {"ra": _num(q.ra), "dec": _num(q.dec), "fov": _num(q.fov), "band": q.band}


def _png(data: bytes) -> Response:
    return Response(content=data, media_type="image/png")


@router.get("/epochs", response_model=EpochsResponse, responses=_ERRORS)
def get_epochs(
    request: Request, q: Annotated[ViewQuery, Query()], provider: Provider
) -> EpochsResponse:
    seq = provider.epochs(q)
    view = _view_params(q)
    frame_qs = urlencode(
        view | {"vmin": _num(seq.stretch.vmin), "vmax": _num(seq.stretch.vmax)}
    )
    diff_base = str(request.url_for("get_diff"))
    epochs = [
        Epoch(
            **e.model_dump(),
            frame_url=f"{request.url_for('get_frame', id=e.id)}?{frame_qs}",
        )
        for e in seq.epochs
    ]
    diffs = [
        DiffPair(
            a=d.a,
            b=d.b,
            scale=d.scale,
            url=f"{diff_base}?{urlencode({'a': d.a, 'b': d.b} | view | {'scale': _num(d.scale)})}",
        )
        for d in seq.prerendered_diffs
    ]
    template = (
        f"{diff_base}?a={{a}}&b={{b}}&{urlencode(view)}" if seq.any_pair_diff else None
    )
    return EpochsResponse(
        synthetic=seq.synthetic,
        target=seq.target,
        fov_arcmin=seq.fov_arcmin,
        band=seq.band,
        image=seq.image,
        stretch=seq.stretch,
        releases=seq.releases,
        duplicates_removed=seq.duplicates_removed,
        count=len(epochs),
        epochs=epochs,
        diff_url_template=template,
        diffs=diffs,
    )


@router.get(
    "/frame/{id}.png", response_class=Response, responses=_PNG, name="get_frame"
)
def get_frame(
    id: Annotated[str, Path(pattern=EPOCH_ID_PATTERN, description="Epoch id")],
    q: Annotated[FrameQuery, Query()],
    provider: Provider,
) -> Response:
    return _png(provider.frame_png(id, q))


@router.get("/diff", response_class=Response, responses=_PNG | _UNAVAILABLE,
            name="get_diff")
def get_diff(q: Annotated[DiffQuery, Query()], provider: Provider) -> Response:
    return _png(provider.diff_png(q))


@router.get("/lightcurve", response_model=LightcurveResponse,
            responses=_ERRORS | _UNAVAILABLE)
def get_lightcurve(
    q: Annotated[LightcurveQuery, Query()], provider: Provider
) -> LightcurveResponse:
    return provider.lightcurve(q)
