"""Pure epoch functions: sorting, de-duplication, visit grouping and epoch ids."""

from __future__ import annotations

import random
import re

import pytest

from app.config import Settings
from app.irsa.epochs import (
    EPOCH_ID_RE,
    build_epoch_id,
    build_epochs,
    deduplicate,
    group_visits,
    parse_epoch_id,
    sort_exposures,
)
from app.irsa.records import mjd_to_utc_iso
from app.irsa.sia import parse_sia_votable
from tests.irsa_helpers import fixture_bytes, make_exposure

ORDER = ("DR1", "QR3")


@pytest.fixture(scope="module")
def barnard():
    return parse_sia_votable(fixture_bytes("barnard_d2.xml"), "D2", Settings())


# ------------------------------------------------------------------- times


def test_mjd_to_utc():
    assert mjd_to_utc_iso(60809.0) == "2025-05-14T00:00:00Z"
    assert mjd_to_utc_iso(60809.133796) == "2025-05-14T03:12:40Z"
    assert mjd_to_utc_iso(60809.9999999) == "2025-05-15T00:00:00Z"  # rounds up


# -------------------------------------------------------------------- sort


def test_sort_by_mid_time_with_stable_ties():
    a = make_exposure("2025W20_1A_0001_1", mjd=60810.0)
    b = make_exposure("2025W20_1A_0002_1", mjd=60800.0)
    c = make_exposure("2025W20_1A_0000_1", mjd=60810.0)  # same time as a
    for _ in range(5):
        shuffled = [a, b, c]
        random.shuffle(shuffled)
        assert sort_exposures(shuffled) == [b, c, a]


# ----------------------------------------------------------------- de-dup


def test_dedup_prefers_first_configured_release():
    qr3 = make_exposure("2025W34_2B_0090_4", release="QR3")
    dr1 = make_exposure("2025W34_2B_0090_4", release="DR1")
    kept, removed = deduplicate([qr3, dr1], ORDER)
    assert kept == [dr1] and removed == 1
    kept, removed = deduplicate([qr3, dr1], ("QR3", "DR1"))
    assert kept == [qr3] and removed == 1


def test_dedup_key_is_obs_id_and_band():
    d2 = make_exposure("2025W34_2B_0090_4", band="D2")
    d3 = make_exposure("2025W34_2B_0090_4", band="D3")
    other = make_exposure("2025W34_2B_0090_3", band="D2", release="QR3")
    kept, removed = deduplicate([d2, d3, other], ORDER)
    assert kept == [d2, d3, other] and removed == 0


def test_dedup_counts_every_drop():
    copies = [make_exposure(release=r) for r in ("QR3", "QR3", "DR1", "QR2")]
    kept, removed = deduplicate(copies, ORDER)
    assert [e.release for e in kept] == ["DR1"] and removed == 3


def test_dedup_on_real_fixture_has_no_duplicates(barnard):
    # DR1 ends in March 2026 and QR3 starts in August 2026 at this position.
    kept, removed = deduplicate(barnard, ORDER)
    assert removed == 0 and len(kept) == 61
    kept, removed = deduplicate(barnard + barnard[:5], ORDER)
    assert removed == 5 and len(kept) == 61


# ------------------------------------------------------------------ visits


def test_visits_on_real_fixture(barnard):
    ordered = sort_exposures(barnard)
    visits = group_visits(ordered)
    assert visits[0] == 1 and visits == sorted(visits)
    spans = {}
    for exp, v in zip(ordered, visits, strict=True):
        spans.setdefault(v, []).append(exp.time_mid_utc[:10])
    assert {v: (d[0], d[-1], len(d)) for v, d in spans.items()} == {
        1: ("2025-08-21", "2025-08-30", 18),
        2: ("2026-03-15", "2026-03-22", 18),
        # The single 2026-09-22 exposure is 25 days after the previous one: same pass.
        3: ("2026-08-18", "2026-09-22", 25),
    }


def test_gap_rule():
    days = [0, 1, 2, 47.5, 48, 92.9, 300]
    exps = [make_exposure(f"2025W20_1A_{i:04d}_1", mjd=60800 + d) for i, d in enumerate(days)]
    assert group_visits(exps) == [1, 1, 1, 2, 2, 2, 3]


def test_span_cap_splits_continuous_coverage():
    # Near the ecliptic poles a position is seen every few days all year.
    exps = [make_exposure(f"2025W20_1A_{i:04d}_1", mjd=60800 + 3 * i) for i in range(140)]
    visits = group_visits(exps)
    assert visits[0] == 1 and visits[-1] == 3
    assert visits.index(2) == 61  # day 183, the first exposure >= 182.6 days in


def test_group_visits_rejects_unsorted_and_handles_empty():
    late, early = make_exposure(mjd=60900), make_exposure(mjd=60800)
    with pytest.raises(ValueError, match="sorted"):
        group_visits([late, early])
    assert group_visits([]) == []


# --------------------------------------------------------------------- ids


def test_epoch_id_format_and_round_trip():
    epoch_id = build_epoch_id("DR1", "2025W34_2B_0090_4", "D2")
    assert epoch_id == "sx_dr1_2025W34_2B_0090_4_D2"
    assert EPOCH_ID_RE.match(epoch_id) and "." not in epoch_id
    assert epoch_id.endswith("_D2")
    assert parse_epoch_id(epoch_id) == ("dr1", "2025W34_2B_0090_4", "D2")


def test_epoch_id_longest_parts_fit():
    epoch_id = build_epoch_id("A" * 16, "x" * 56, "D6")
    assert len(epoch_id) <= 80 and EPOCH_ID_RE.match(epoch_id)


@pytest.mark.parametrize(
    "release, obs_id, band",
    [
        ("DR1", "2025W34/../etc", "D2"),
        ("DR1", "2025.W34", "D2"),
        ("DR1", "", "D2"),
        ("DR1", "x" * 57, "D2"),
        ("DR_1", "2025W34_2B_0090_4", "D2"),
        ("DR1", "2025W34_2B_0090_4", "D7"),
    ],
)
def test_epoch_id_rejects_unsafe_parts(release, obs_id, band):
    with pytest.raises(ValueError):
        build_epoch_id(release, obs_id, band)


@pytest.mark.parametrize("bad", ["sx_dr1_2025W34_D9", "foo", "sx__x_D2", "sx_dr1_a.b_D2"])
def test_parse_epoch_id_rejects_foreign_ids(bad):
    with pytest.raises(ValueError):
        parse_epoch_id(bad)


def test_build_epochs_ids_are_unique_and_stable(barnard):
    epochs, removed = build_epochs(barnard, ORDER)
    shuffled = list(barnard)
    random.Random(7).shuffle(shuffled)
    again, _ = build_epochs(shuffled, ORDER)
    assert removed == 0
    assert [e.id for e in epochs] == [e.id for e in again]
    assert len({e.id for e in epochs}) == len(epochs) == 61
    assert all(re.match(r"^sx_(dr1|qr3)_\w+_D2$", e.id) for e in epochs)
    assert epochs[0].id == "sx_dr1_2025W34_2B_0090_4_D2"
    assert [e.exposure.t_mid_mjd for e in epochs] == sorted(e.exposure.t_mid_mjd for e in epochs)
