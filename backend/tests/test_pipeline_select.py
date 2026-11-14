"""Per-visit selection: footprint ranking and download fallback (offline)."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.cutouts import fetch_cutouts
from app.irsa.records import parse_s_region
from app.irsa.sia import parse_sia_votable
from app.pipeline.render import clean_path
from app.pipeline.select import edge_distance_arcmin, rank_visit, select_per_visit
from tests.cutout_helpers import FOV10, StreamResponse, StreamSession, make_settings, ok
from tests.irsa_helpers import fixture_bytes
from tests.pipeline_helpers import DEC, RA, bit, epoch, fixture_with, square

SAFE = square(0.5)  # target 30' inside every edge
EDGE = square(0.5, ddec=0.45)  # target 3' from the south edge


# ------------------------------------------------------------------ footprints


def test_parse_s_region():
    text = "POLYGON ICRS 1 2 3 2 3 4 1 4 1 2"
    assert parse_s_region(text) == ((1.0, 2.0), (3.0, 2.0), (3.0, 4.0), (1.0, 4.0))
    assert parse_s_region("POLYGON 359.9 0 0.1 0 0.1 1") == ((359.9, 0.0), (0.1, 0.0),
                                                             (0.1, 1.0))
    for bad in [None, "", "CIRCLE ICRS 1 2 3", "POLYGON ICRS 1 2 3", "POLYGON GALACTIC 1 2 3 4 5 6",
                "POLYGON ICRS 1 2 3 4", "POLYGON ICRS 1 95 2 95 3 95", "POLYGON ICRS a b c d e f"]:
        assert parse_s_region(bad) is None


def test_sia_parser_keeps_the_footprint():
    exposures = parse_sia_votable(fixture_bytes("barnard_d2.xml"), "D2", Settings())
    assert all(e.footprint is not None and len(e.footprint) == 4 for e in exposures)
    first = min(exposures, key=lambda e: e.t_start_mjd)
    assert first.footprint[0] == pytest.approx((267.5218890263455, 8.161020395014317))
    # Barnard's Star is inside every footprint IRSA returned for it.
    assert all(edge_distance_arcmin(e.footprint, RA, DEC) > 0 for e in exposures)


def test_edge_distance():
    assert edge_distance_arcmin(SAFE, RA, DEC) == pytest.approx(30.0, rel=0.01)
    assert edge_distance_arcmin(tuple(reversed(SAFE)), RA, DEC) == pytest.approx(30.0,
                                                                                 rel=0.01)
    assert edge_distance_arcmin(EDGE, RA, DEC) == pytest.approx(3.0, abs=0.1)
    outside = square(0.5, ddec=0.6)
    assert edge_distance_arcmin(outside, RA, DEC) == pytest.approx(-6.0, abs=0.1)
    assert edge_distance_arcmin(None, RA, DEC) is None
    assert edge_distance_arcmin(((RA, DEC), (RA, DEC), (RA, DEC)), RA, DEC) is None


def test_rank_visit_prefers_safe_then_unknown_then_deepest():
    middle = 61000.0
    safe_late = epoch("2025W01_1A_0001_1", 1, middle + 0.3, SAFE)
    safe_mid = epoch("2025W01_1A_0002_1", 1, middle + 0.01, SAFE)
    unknown_mid = epoch("2025W01_1A_0003_1", 1, middle, None)
    edge_mid = epoch("2025W01_1A_0004_1", 1, middle, EDGE)
    deeper_edge = epoch("2025W01_1A_0005_1", 1, middle - 0.3, square(0.5, ddec=0.4))
    ranked = rank_visit([edge_mid, unknown_mid, safe_late, deeper_edge, safe_mid], RA, DEC, 10)
    assert [e.id for e in ranked] == [safe_mid.id, safe_late.id, unknown_mid.id,
                                      deeper_edge.id, edge_mid.id]
    assert rank_visit([], RA, DEC, 10) == []


# ------------------------------------------------------------------ selection


def run(epochs, session, settings):
    def fetch(items):
        return fetch_cutouts(items, RA, DEC, 10, settings=settings, session=session).results

    def clean(cutout):
        return clean_path(cutout.path, RA, DEC, 10, settings)

    return select_per_visit(epochs, RA, DEC, 10, fetch=fetch, clean=clean, settings=settings)


def visits():
    """Two visits of three exposures; the *_2 exposure of each is the best ranked."""
    v1 = [epoch(f"2025W34_2B_0090_{i}", 1, 60908.0 + i * 0.1, SAFE) for i in (1, 2, 3)]
    v2 = [epoch(f"2026W12_1A_0311_{i}", 2, 61116.0 + i * 0.1, SAFE) for i in (1, 2, 3)]
    return v1 + v2


def obs_of(call) -> str:
    return call["url"].split("/x/")[1].split("D2.fits")[0]


def test_only_the_chosen_candidates_are_downloaded(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV10)])
    result = run(visits(), session, settings)
    assert sorted(obs_of(c) for c in session.calls) == ["2025W34_2B_0090_2",
                                                        "2026W12_1A_0311_2"]
    assert [c.epoch.id for c in result.chosen] == ["sx_dr1_2025W34_2B_0090_2_D2",
                                                   "sx_dr1_2026W12_1A_0311_2_D2"]
    assert all(c.accepted for c in result.chosen)
    assert [a.outcome for a in result.attempts] == ["accepted", "accepted"]
    assert result.skipped_visits == []


def test_failed_download_falls_back_to_the_next_candidate(tmp_path):
    settings = make_settings(tmp_path)

    def reply(url):
        return StreamResponse(b"", status_code=404) if "0090_2" in url else ok(FOV10)

    session = StreamSession(replies=[reply])
    result = run(visits(), session, settings)
    called = [obs_of(c) for c in session.calls]
    assert sorted(called) == ["2025W34_2B_0090_1", "2025W34_2B_0090_2", "2026W12_1A_0311_2"]
    assert called[-1] == "2025W34_2B_0090_1"  # tried in the second round
    assert [c.epoch.id for c in result.chosen] == ["sx_dr1_2025W34_2B_0090_1_D2",
                                                   "sx_dr1_2026W12_1A_0311_2_D2"]
    outcomes = {(a.epoch_id, a.outcome) for a in result.attempts}
    assert ("sx_dr1_2025W34_2B_0090_2_D2", "download_failed") in outcomes


def test_bad_quality_falls_back_and_stops_after_three(tmp_path):
    settings = make_settings(tmp_path)
    hot = StreamResponse(fixture_with(flags_or=bit("HOT")))  # every pixel masked

    def reply(url):
        return hot if "0090_" in url else ok(FOV10)

    epochs = visits() + [epoch("2025W34_2B_0090_4", 1, 60908.45, SAFE)]
    session = StreamSession(replies=[reply])
    result = run(epochs, session, settings)
    visit1 = [obs_of(c) for c in session.calls if "0090_" in c["url"]]
    assert len(visit1) == 3  # per_visit_max_candidates; the 4th is never fetched
    assert "2025W34_2B_0090_4" not in visit1
    # Every candidate was bad, so the visit keeps the least flagged one, marked.
    v1 = [c for c in result.chosen if c.epoch.visit == 1]
    assert len(v1) == 1 and not v1[0].accepted and v1[0].frame.status == "bad"
    assert sum(a.outcome == "bad_quality" for a in result.attempts) == 3


def test_visit_is_skipped_when_every_download_fails(tmp_path):
    settings = make_settings(tmp_path, per_visit_max_candidates=2)

    def reply(url):
        return StreamResponse(b"", status_code=404) if "0090_" in url else ok(FOV10)

    session = StreamSession(replies=[reply])
    result = run(visits(), session, settings)
    assert result.skipped_visits == [1]
    assert [c.epoch.visit for c in result.chosen] == [2]
    assert sum("0090_" in c["url"] for c in session.calls) == 2


def test_low_coverage_is_rejected(tmp_path):
    settings = make_settings(tmp_path, per_visit_min_coverage=1.01)  # nothing passes
    session = StreamSession(replies=[ok(FOV10)])
    result = run(visits(), session, settings)
    assert {a.outcome for a in result.attempts} == {"low_coverage"}
    assert len(session.calls) == 6
    assert all(not c.accepted for c in result.chosen) and len(result.chosen) == 2
