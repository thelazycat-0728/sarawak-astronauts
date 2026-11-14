"""Error handling per docs/API.md section 1: one JSON shape for every error."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import BARNARD, assert_error

FRAME = "/frame/mock_20250514_01_D2.png"
PAIR = {"a": "mock_20250514_01_D2", "b": "mock_20260514_09_D2"}

BAD_VIEW_PARAMS = [
    pytest.param({"ra": -0.1}, id="ra-negative"),
    pytest.param({"ra": 360}, id="ra-360"),
    pytest.param({"ra": "abc"}, id="ra-not-a-number"),
    pytest.param({"ra": "nan"}, id="ra-nan"),
    pytest.param({"dec": -90.5}, id="dec-below"),
    pytest.param({"dec": 91}, id="dec-above"),
    pytest.param({"fov": 0.5}, id="fov-below"),
    pytest.param({"fov": 31}, id="fov-above"),
    pytest.param({"band": "D7"}, id="band-unknown"),
    pytest.param({"band": "d2"}, id="band-lowercase"),
    pytest.param({"band": "D2-07"}, id="band-reserved-channel"),
]


@pytest.mark.parametrize("bad", BAD_VIEW_PARAMS)
@pytest.mark.parametrize(
    "path, extra",
    [("/epochs", {}), (FRAME, {}), ("/diff", PAIR)],
    ids=["epochs", "frame", "diff"],
)
def test_bad_view_params_return_400(client, path, extra, bad):
    resp = client.get(path, params={**BARNARD, **extra, **bad})
    assert_error(resp, 400, "invalid_parameter")


@pytest.mark.parametrize(
    "bad",
    [
        {"ra": 400},
        {"dec": -95},
        {"band": "D9"},
        {"band": "D2-07"},
        {"aperture": 2},
        {"aperture": 61},
    ],
)
def test_bad_lightcurve_params_return_400(client, bad):
    assert_error(
        client.get("/lightcurve", params={**BARNARD, **bad}), 400, "invalid_parameter"
    )


def test_messages_name_the_parameter(client):
    msg = client.get("/epochs", params={"ra": 1, "dec": 91}).json()["error"]["message"]
    assert msg.startswith("dec:")
    msg = client.get("/epochs", params={**BARNARD, "band": "D2-07"}).json()["error"][
        "message"
    ]
    assert "D2-07" in msg and "D1 to D6" in msg


def test_missing_required_params_return_400(client):
    assert_error(client.get("/epochs"), 400, "invalid_parameter")
    assert_error(client.get("/epochs", params={"ra": 1}), 400, "invalid_parameter")
    assert_error(client.get("/lightcurve", params={"dec": 1}), 400, "invalid_parameter")
    assert_error(client.get(FRAME), 400, "invalid_parameter")
    assert_error(client.get("/diff", params=BARNARD), 400, "invalid_parameter")


@pytest.mark.parametrize(
    "extra",
    [{"vmin": 0.1}, {"vmax": 1.0}, {"vmin": 1.0, "vmax": 0.5}],
    ids=["vmin-only", "vmax-only", "vmax-not-greater"],
)
def test_bad_frame_stretch_returns_400(client, extra):
    assert_error(
        client.get(FRAME, params={**BARNARD, **extra}), 400, "invalid_parameter"
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"a": PAIR["a"], "b": PAIR["a"]},
        {**PAIR, "scale": 0},
        {**PAIR, "scale": -1},
        {"a": "bad.id", "b": PAIR["b"]},
    ],
    ids=["a-equals-b", "scale-zero", "scale-negative", "a-malformed"],
)
def test_bad_diff_params_return_400(client, extra):
    assert_error(
        client.get("/diff", params={**BARNARD, **extra}), 400, "invalid_parameter"
    )


def test_malformed_frame_id_returns_400(client):
    assert_error(
        client.get("/frame/" + "x" * 81 + ".png", params=BARNARD),
        400,
        "invalid_parameter",
    )


@pytest.mark.parametrize(
    "path, params",
    [
        ("/frame/unknown_epoch.png", BARNARD),
        ("/frame/mock_20990101_01_D2.png", BARNARD),
        (FRAME, {**BARNARD, "band": "D3"}),  # id belongs to another band
        ("/diff", {**BARNARD, "a": "unknown_epoch", "b": PAIR["b"]}),
        ("/diff", {**BARNARD, "a": PAIR["a"], "b": "unknown_epoch"}),
        ("/no-such-route", {}),
        ("/frame/unknown_epoch.jpg", BARNARD),
    ],
)
def test_unknown_ids_and_routes_return_404(client, path, params):
    assert_error(client.get(path, params=params), 404, "not_found")


def test_unhandled_error_returns_500_without_traceback(provider):
    app = create_app(settings=Settings(), provider=provider)

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internal detail")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/boom", headers={"Origin": "http://localhost:5173"})
    assert_error(resp, 500, "internal_error")
    assert "secret" not in resp.text and "Traceback" not in resp.text
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_other_http_errors_use_the_error_shape(client):
    resp = client.post("/epochs", params=BARNARD)
    assert resp.status_code == 405
    body = resp.json()
    assert set(body["error"]) == {"code", "message"}
