"""The IRSA query path with a fake HTTP session: validation, params, error mapping."""

from __future__ import annotations

import pytest
import requests

from app.config import Settings
from app.errors import InvalidParameter, UpstreamError, UpstreamTimeout
from app.irsa import sia
from app.irsa.sia import find_epochs, query_exposures
from tests.irsa_helpers import FakeResponse, FakeSession, fixture_bytes

BARNARD = (269.4464, 4.7668)


def barnard_session() -> FakeSession:
    return FakeSession(response=FakeResponse(fixture_bytes("barnard_d2.xml")))


def test_find_epochs_from_fixture():
    session = barnard_session()
    result = find_epochs(*BARNARD, "D2", session=session)
    assert len(result.epochs) == 61
    assert result.duplicates_removed == 0
    assert result.exposures_per_release == {"DR1": 36, "QR3": 25}
    assert result.releases == ["DR1", "QR3"]
    assert result.visit_count == 3
    assert (result.ra, result.dec, result.band) == (*BARNARD, "D2")


def test_request_parameters_and_timeout():
    settings = Settings(irsa_timeout_s=12.5)
    session = barnard_session()
    query_exposures(*BARNARD, "D2", settings=settings, session=session)
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"] == "https://irsa.ipac.caltech.edu/SIA"
    assert call["timeout"] == 12.5
    assert call["params"] == [
        ("POS", "CIRCLE 269.44640000 4.76680000 0.00027778"),
        ("BAND", "1.360000e-06 1.360000e-06"),
        ("COLLECTION", "spherex_dr1"),
        ("COLLECTION", "spherex_qr3"),
    ]


def test_release_list_limits_collections_and_results():
    session = barnard_session()
    exposures = query_exposures(*BARNARD, "D2", ["QR3"], session=session)
    assert [p for p in session.calls[0]["params"] if p[0] == "COLLECTION"] == [
        ("COLLECTION", "spherex_qr3")
    ]
    assert len(exposures) == 25 and {e.release for e in exposures} == {"QR3"}


def test_unknown_release_is_a_config_error():
    with pytest.raises(ValueError, match="no IRSA collection"):
        query_exposures(*BARNARD, "D2", ["DR9"], session=barnard_session())


@pytest.mark.parametrize(
    "ra, dec, band",
    [
        ("269.4464", 4.7668, "D2"),
        ("269.4464) OR 1=1 --", 4.7668, "D2"),
        (269.4464, "4.7668; DROP TABLE x", "D2"),
        (None, 4.7668, "D2"),
        (True, 4.7668, "D2"),
        (float("nan"), 4.7668, "D2"),
        (float("inf"), 4.7668, "D2"),
        (360.0, 4.7668, "D2"),
        (-0.1, 4.7668, "D2"),
        (269.4464, 90.5, "D2"),
        (269.4464, 4.7668, "D7"),
        (269.4464, 4.7668, "d2"),
        (269.4464, 4.7668, None),
    ],
)
def test_bad_input_rejected_before_any_request(ra, dec, band):
    session = barnard_session()
    with pytest.raises(InvalidParameter):
        query_exposures(ra, dec, band, session=session)
    with pytest.raises(InvalidParameter):
        find_epochs(ra, dec, band, session=session)
    assert session.calls == []


def test_numpy_and_int_inputs_are_accepted():
    np = pytest.importorskip("numpy")
    session = barnard_session()
    find_epochs(np.float64(269.4464), 4, "D2", session=session)
    assert session.calls[0]["params"][0] == ("POS", "CIRCLE 269.44640000 4.00000000 0.00027778")


@pytest.mark.parametrize(
    "error", [requests.Timeout("slow"), requests.ReadTimeout(), requests.ConnectTimeout()]
)
def test_timeouts_map_to_upstream_timeout(error):
    session = FakeSession(error=error)
    with pytest.raises(UpstreamTimeout) as info:
        find_epochs(*BARNARD, "D2", settings=Settings(irsa_timeout_s=7), session=session)
    assert info.value.message == "IRSA did not answer within 7 s"
    assert info.value.status_code == 504


@pytest.mark.parametrize(
    "error",
    [requests.ConnectionError("refused"), requests.exceptions.ProxyError(), requests.TooManyRedirects()],
)
def test_transport_errors_map_to_upstream_error(error):
    with pytest.raises(UpstreamError) as info:
        find_epochs(*BARNARD, "D2", session=FakeSession(error=error))
    assert info.value.status_code == 502
    assert info.value.message.startswith("IRSA request failed (")


@pytest.mark.parametrize("status", [400, 404, 500, 502, 503])
def test_http_errors_map_to_upstream_error(status):
    session = FakeSession(response=FakeResponse(b"<html>oops</html>", status_code=status))
    with pytest.raises(UpstreamError, match=f"HTTP {status}") as info:
        find_epochs(*BARNARD, "D2", session=session)
    assert info.value.status_code == 502


def test_irsa_error_document_maps_to_upstream_error():
    session = FakeSession(response=FakeResponse(fixture_bytes("sia_error.xml")))
    with pytest.raises(UpstreamError, match="UsageFault"):
        find_epochs(*BARNARD, "D2", session=session)


def test_empty_coverage_is_not_an_error():
    session = FakeSession(response=FakeResponse(fixture_bytes("empty.xml")))
    result = find_epochs(*BARNARD, "D2", session=session)
    assert result.epochs == () and result.duplicates_removed == 0
    assert result.visit_count == 0 and result.releases == []


def test_default_session_is_created_and_closed(monkeypatch):
    created: list[FakeSession] = []

    class SessionFactory(FakeSession):
        closed = False

        def __init__(self):
            super().__init__(response=FakeResponse(fixture_bytes("empty.xml")))
            created.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(sia, "_make_session", SessionFactory)
    find_epochs(*BARNARD, "D2")
    assert len(created) == 1 and created[0].closed and len(created[0].calls) == 1
