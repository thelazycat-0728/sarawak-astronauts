"""Parsing recorded IRSA SIA responses (backend/tests/fixtures/irsa/)."""

from __future__ import annotations

import re

import pytest

from app.config import Settings
from app.errors import UpstreamError
from app.irsa.sia import band_centre_m, build_sia_params, parse_sia_votable
from app.models import BAND_IDS, BAND_RANGES_UM
from tests.irsa_helpers import FIXTURES, fixture_bytes

UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@pytest.fixture(scope="module")
def barnard():
    return parse_sia_votable(fixture_bytes("barnard_d2.xml"), "D2", Settings())


def test_fixture_folder_is_small():
    total = sum(p.stat().st_size for p in FIXTURES.iterdir() if p.is_file())
    assert total < 500 * 1024


def test_barnard_d2_records(barnard):
    assert len(barnard) == 61
    by_release = {r: sum(e.release == r for e in barnard) for r in ("DR1", "QR3")}
    assert by_release == {"DR1": 36, "QR3": 25}
    for e in barnard:
        assert e.band == "D2"
        assert e.collection == {"DR1": "spherex_dr1", "QR3": "spherex_qr3"}[e.release]
        assert re.match(r"^\d{4}W\d{2}_\d[A-Z]_\d{4}_\d$", e.obs_id)
        assert e.publisher_did == f"ivo://irsa.ipac/{e.collection}?{e.obs_id}/D2"
        assert e.access_url.startswith("https://irsa.ipac.caltech.edu/ibe/data/spherex/")
        assert f"{e.obs_id}D2_" in e.access_url
        assert e.t_start_mjd < e.t_mid_mjd < e.t_end_mjd
        assert 100 < (e.t_end_mjd - e.t_start_mjd) * 86400 < 130
        assert UTC_RE.match(e.time_start_utc) and UTC_RE.match(e.time_mid_utc)
        assert e.wavelength_min_um == pytest.approx(1.10, abs=0.02)
        assert e.wavelength_max_um == pytest.approx(1.65, abs=0.04)


def test_known_first_exposure(barnard):
    first = min(barnard, key=lambda e: e.t_start_mjd)
    assert first.obs_id == "2025W34_2B_0090_4"
    assert first.release == "DR1"
    assert first.time_start_utc == "2025-08-21T23:33:59Z"  # t_min 60908.981929, rounded


@pytest.mark.parametrize("band", ["D2", "D3"])
def test_band_filter_drops_other_arrays(band):
    # IRSA returned D2 and D3 rows for this fixture; only the asked band survives.
    exposures = parse_sia_votable(fixture_bytes("barnard_d2_d3.xml"), band, Settings())
    assert len(exposures) == 6
    assert {e.band for e in exposures} == {band}
    assert all(f"{e.obs_id}{band}_" in e.access_url for e in exposures)


def test_band_filter_no_match():
    assert parse_sia_votable(fixture_bytes("barnard_d2_d3.xml"), "D5", Settings()) == []


@pytest.mark.parametrize("band", BAND_IDS)
def test_band_parameter_sits_inside_its_array(band):
    centre_um = band_centre_m(band) * 1e6
    lo, hi = BAND_RANGES_UM[band]
    assert lo + 0.05 < centre_um < hi - 0.05
    params = dict(build_sia_params(1.0, 2.0, band, ["spherex_dr1"]))
    assert params["BAND"] == f"{band_centre_m(band):.6e} {band_centre_m(band):.6e}"


def test_unconfigured_collection_rows_are_dropped():
    only_dr1 = Settings(spherex_releases=("DR1",), spherex_collections=(("DR1", "spherex_dr1"),))
    exposures = parse_sia_votable(fixture_bytes("barnard_d2.xml"), "D2", only_dr1)
    assert len(exposures) == 36
    assert {e.release for e in exposures} == {"DR1"}


def test_empty_response():
    assert parse_sia_votable(fixture_bytes("empty.xml"), "D2", Settings()) == []


def test_error_document_raises_upstream_error():
    with pytest.raises(UpstreamError, match="IRSA SIA query failed"):
        parse_sia_votable(fixture_bytes("sia_error.xml"), "D2", Settings())


@pytest.mark.parametrize("content", [b"", b"<html>Service down</html>", b"\x00\xff"])
def test_unreadable_response_raises_upstream_error(content):
    with pytest.raises(UpstreamError):
        parse_sia_votable(content, "D2", Settings())
