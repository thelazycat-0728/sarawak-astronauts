"""Resolving an epoch id by a cached position query (offline, recorded SIA fixture)."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.cutouts import QueryCache, resolve_epoch
from app.errors import InvalidParameter, NotFound, UpstreamError
from tests.irsa_helpers import FakeResponse, FakeSession, fixture_bytes

BARNARD = (269.4464, 4.7668)
FIRST_ID = "sx_dr1_2025W34_2B_0090_4_D2"


def barnard_session() -> FakeSession:
    return FakeSession(response=FakeResponse(fixture_bytes("barnard_d2.xml")))


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_epoch_id_resolved():
    session = barnard_session()
    epoch = resolve_epoch(FIRST_ID, *BARNARD, session=session, query_cache=QueryCache())
    assert epoch.id == FIRST_ID
    assert epoch.exposure.obs_id == "2025W34_2B_0090_4"
    assert epoch.exposure.release == "DR1" and epoch.exposure.band == "D2"
    assert epoch.exposure.access_url.startswith("https://irsa.ipac.caltech.edu/")
    assert len(session.calls) == 1


def test_query_reused_within_ttl_and_repeated_after():
    clock = Clock()
    cache = QueryCache(clock=clock)
    settings = Settings(epoch_query_ttl_s=60)
    session = barnard_session()
    first = resolve_epoch(FIRST_ID, *BARNARD, settings=settings, session=session,
                          query_cache=cache)
    last_id = "sx_qr3_2026W12_1A_0311_3_D2"
    with pytest.raises(NotFound):  # not in the fixture: proves a lookup, not a guess
        resolve_epoch(last_id, *BARNARD, settings=settings, session=session, query_cache=cache)
    clock.now += 59
    again = resolve_epoch(FIRST_ID, 269.44641, 4.76679, settings=settings, session=session,
                          query_cache=cache)
    assert again == first and len(session.calls) == 1
    clock.now += 2
    resolve_epoch(FIRST_ID, *BARNARD, settings=settings, session=session, query_cache=cache)
    assert len(session.calls) == 2


def test_every_fixture_epoch_resolves_with_one_query():
    from app.irsa.sia import find_epochs

    ids = [e.id for e in find_epochs(*BARNARD, "D2", session=barnard_session()).epochs]
    session = barnard_session()
    cache = QueryCache()
    resolved = [resolve_epoch(i, *BARNARD, session=session, query_cache=cache) for i in ids]
    assert [e.id for e in resolved] == ids and len(session.calls) == 1


def test_epoch_id_not_found():
    session = barnard_session()
    with pytest.raises(NotFound) as info:
        resolve_epoch("sx_dr1_2099W01_1A_0001_1_D2", *BARNARD, session=session,
                      query_cache=QueryCache())
    assert info.value.status_code == 404 and "not found" in info.value.message
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "epoch_id",
    ["", "abc", "sx_dr1_2025W34_2B_0090_4_D7", "sx_DR1_x_D2", "sx_dr1_a/b_D2", None, 5],
)
def test_malformed_epoch_id_is_not_found_without_a_query(epoch_id):
    session = barnard_session()
    with pytest.raises(NotFound):
        resolve_epoch(epoch_id, *BARNARD, session=session, query_cache=QueryCache())
    assert session.calls == []


def test_band_comes_from_the_id():
    session = FakeSession(response=FakeResponse(fixture_bytes("empty.xml")))
    with pytest.raises(NotFound):
        resolve_epoch("sx_dr1_2025W34_2B_0090_4_D3", *BARNARD, session=session,
                      query_cache=QueryCache())
    band = dict(session.calls[0]["params"])["BAND"]
    assert band.startswith("2.02")  # D3 centre, 2.02 um


def test_bad_position_rejected_before_query():
    session = barnard_session()
    with pytest.raises(InvalidParameter):
        resolve_epoch(FIRST_ID, 360.0, 4.0, session=session, query_cache=QueryCache())
    assert session.calls == []


def test_upstream_errors_are_not_cached():
    cache = QueryCache()
    failing = FakeSession(response=FakeResponse(b"oops", status_code=503))
    with pytest.raises(UpstreamError):
        resolve_epoch(FIRST_ID, *BARNARD, session=failing, query_cache=cache)
    epoch = resolve_epoch(FIRST_ID, *BARNARD, session=barnard_session(), query_cache=cache)
    assert epoch.id == FIRST_ID


def test_query_cache_is_bounded():
    cache = QueryCache(max_entries=3)
    for i in range(5):
        cache.get_or_load(i, 60, lambda i=i: i)
    assert len(cache._items) == 3
    assert cache.get_or_load(4, 60, lambda: "reloaded") == 4
    assert cache.get_or_load(0, 60, lambda: "reloaded") == "reloaded"
