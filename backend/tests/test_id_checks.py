"""Id and name checks reject a trailing newline (T-005 review: ``$`` matches
before a final ``\\n``, so every check uses ``fullmatch``)."""

from __future__ import annotations

import pytest

from app.cutouts.cache import CutoutCache, cutout_key
from app.errors import NotFound
from app.irsa.epochs import build_epoch_id, parse_epoch_id
from app.models import FrameQuery, check_band
from app.pipeline.render import render_frame
from app.providers.mock import MockProvider
from tests.conftest import BARNARD, assert_error
from tests.cutout_helpers import make_settings

GOOD_ID = "sx_dr1_2025W35_1A_0146_4_D2"


@pytest.mark.parametrize("tail", ["\n", "\r\n", " ", "\x00"])
def test_epoch_id_parse_rejects_a_trailing_character(tail):
    assert parse_epoch_id(GOOD_ID) == ("dr1", "2025W35_1A_0146_4", "D2")
    with pytest.raises(ValueError):
        parse_epoch_id(GOOD_ID + tail)


def test_epoch_id_parts_reject_a_trailing_newline():
    with pytest.raises(ValueError):
        build_epoch_id("DR1\n", "2025W35_1A_0146_4", "D2")
    with pytest.raises(ValueError):
        build_epoch_id("DR1", "2025W35_1A_0146_4\n", "D2")


def test_cache_keys_reject_a_trailing_newline(tmp_path):
    with pytest.raises(ValueError):
        cutout_key("dr1\n", "obs", "D2", 1.0, 2.0, 10.0)
    with pytest.raises(ValueError):
        cutout_key("dr1", "obs\n", "D2", 1.0, 2.0, 10.0)
    key = cutout_key("dr1", "obs", "D2", 1.0, 2.0, 10.0)
    with pytest.raises(ValueError):
        CutoutCache(tmp_path, 1000).path_for(key + "\n")


def test_render_frame_treats_a_newline_id_as_unknown_without_any_request(tmp_path):
    with pytest.raises(NotFound):
        render_frame(GOOD_ID + "\n", BARNARD["ra"], BARNARD["dec"], 10, "D2",
                     settings=make_settings(tmp_path))
    assert not (tmp_path / "cache").exists()


def test_mock_ids_and_reserved_channels_reject_a_trailing_newline(provider: MockProvider):
    e = provider._doc.epochs[0]
    q = FrameQuery(**BARNARD, fov=10, band="D2")
    assert provider.frame_png(e.id, q)
    with pytest.raises(NotFound):
        provider.frame_png(e.id + "\n", q)
    # "D2-07\n" is not a reserved channel name, so it gets the plain message.
    with pytest.raises(ValueError, match="must be one of"):
        check_band("D2-07\n")


def test_routes_reject_a_trailing_newline(client):
    params = {**BARNARD, "fov": 10, "band": "D2"}
    resp = client.get(f"/frame/{GOOD_ID}%0A.png", params=params)
    assert_error(resp, 400, "invalid_parameter")
    assert_error(client.get("/epochs", params={**params, "band": "D2\n"}), 400,
                 "invalid_parameter")
