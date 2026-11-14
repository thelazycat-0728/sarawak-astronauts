"""One bad cutout never aborts a sequence (T-005 review, MED 2): cleaning
failures are per-cutout outcomes, and unreadable cached files are evicted."""

from __future__ import annotations

import errno
import io
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from app.cutouts import fetch_cutouts, get_cutout
from app.errors import UpstreamError
from app.pipeline import build_sequence, select_per_visit
from app.pipeline.render import clean_path
from tests.cutout_helpers import (
    FOV10,
    StreamResponse,
    StreamSession,
    fixture_epoch,
    fixture_path,
    make_settings,
    ok,
)
from tests.pipeline_helpers import DEC, RA, epoch, query_of, square

SAFE = square(0.5)


def with_image_unit(unit: str) -> bytes:
    """The fov=10 fixture as IRSA would serve it, with another IMAGE BUNIT."""
    with fits.open(fixture_path(FOV10)) as hdul:
        hdul = fits.HDUList([h.copy() for h in hdul])
    hdul["IMAGE"].header["BUNIT"] = unit
    buf = io.BytesIO()
    hdul.writeto(buf)
    return buf.getvalue()


def visits():
    v1 = [epoch(f"2025W34_2B_0090_{i}", 1, 60908.0 + i * 0.1, SAFE) for i in (1, 2, 3)]
    v2 = [epoch(f"2026W12_1A_0311_{i}", 2, 61116.0 + i * 0.1, SAFE) for i in (1, 2, 3)]
    return v1 + v2


def run(epochs, session, settings, before_clean=None):
    def fetch(items):
        return fetch_cutouts(items, RA, DEC, 10, settings=settings, session=session).results

    def clean(cutout):
        if before_clean is not None:
            before_clean(cutout)
        return clean_path(cutout.path, RA, DEC, 10, settings)

    return select_per_visit(epochs, RA, DEC, 10, fetch=fetch, clean=clean, settings=settings)


def test_unexpected_unit_is_a_clean_failure_and_the_visit_falls_back(tmp_path):
    settings = make_settings(tmp_path)
    adu = StreamResponse(with_image_unit("adu"))

    def reply(url):
        return adu if "0090_2" in url else ok(FOV10)

    result = run(visits(), StreamSession(replies=[reply]), settings)
    failed = [a for a in result.attempts if a.outcome == "clean_failed"]
    assert [a.epoch_id for a in failed] == ["sx_dr1_2025W34_2B_0090_2_D2"]
    assert failed[0].detail.startswith("upstream_error: IMAGE unit 'adu'")
    assert [c.epoch.id for c in result.chosen] == ["sx_dr1_2025W34_2B_0090_1_D2",
                                                   "sx_dr1_2026W12_1A_0311_2_D2"]
    assert result.skipped_visits == []


def test_a_file_evicted_before_cleaning_is_a_clean_failure(tmp_path):
    settings = make_settings(tmp_path, per_visit_max_candidates=1)

    def evict_visit_1(cutout):
        if cutout.exposure.obs_id.startswith("2025W34"):
            Path(cutout.path).unlink()

    result = run(visits(), StreamSession(replies=[ok(FOV10)]), settings, evict_visit_1)
    (failed,) = [a for a in result.attempts if a.outcome == "clean_failed"]
    assert failed.visit == 1
    assert failed.detail == "upstream_error: cached cutout could not be read"
    assert result.skipped_visits == [1] and [c.epoch.visit for c in result.chosen] == [2]


def test_an_unexpected_error_in_cleaning_is_recorded_not_raised(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV10)])

    def fetch(items):
        return fetch_cutouts(items, RA, DEC, 10, settings=settings, session=session).results

    def clean(cutout):
        raise ZeroDivisionError("bug")

    result = select_per_visit(visits(), RA, DEC, 10, fetch=fetch, clean=clean,
                              settings=settings)
    assert {a.outcome for a in result.attempts} == {"clean_failed"}
    assert all(a.detail == "internal_error: ZeroDivisionError" for a in result.attempts)
    assert result.chosen == [] and result.skipped_visits == [1, 2]


def test_all_mode_leaves_out_cutouts_that_cannot_be_cleaned(tmp_path):
    settings = make_settings(tmp_path)
    adu = StreamResponse(with_image_unit("adu"))
    epochs = visits()

    def reply(url):
        return adu if "0311_1" in url else ok(FOV10)

    seq = build_sequence(RA, DEC, "D2", 10, frame_url=lambda i, lo, hi: f"/f/{i}.png",
                         mode="all", settings=settings, session=StreamSession(replies=[reply]),
                         query=query_of(epochs))
    assert seq.response.count == 5
    (failure,) = seq.failures
    assert failure.epoch_id == "sx_dr1_2026W12_1A_0311_1_D2"
    assert failure.code == "upstream_error"
    assert failure.reason.startswith("clean_failed: IMAGE unit 'adu'")


@pytest.mark.filterwarnings("ignore:File may have been truncated")
@pytest.mark.parametrize("damage", ["truncate", "garbage"])
def test_an_unreadable_cached_file_is_evicted_and_downloaded_again(tmp_path, damage):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV10)])
    first = get_cutout(fixture_epoch(FOV10), RA, DEC, 10, settings=settings, session=session)
    data = first.path.read_bytes()
    if damage == "truncate":
        first.path.write_bytes(data[: len(data) // 3])
    else:
        first.path.write_bytes(b"SIMPLE  = not a fits file" + b" " * 3000)
    with pytest.raises(UpstreamError, match="could not be read"):
        clean_path(first.path, RA, DEC, 10, settings)
    assert not first.path.exists()  # evicted
    again = get_cutout(fixture_epoch(FOV10), RA, DEC, 10, settings=settings, session=session)
    assert not again.from_cache and len(session.calls) == 2
    frame = clean_path(again.path, RA, DEC, 10, settings)
    assert frame.image.shape == (97, 97) and np.isfinite(frame.image).any()


def test_a_unit_error_keeps_the_cached_file(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[StreamResponse(with_image_unit("adu"))])
    cutout = get_cutout(fixture_epoch(FOV10), RA, DEC, 10, settings=settings, session=session)
    with pytest.raises(UpstreamError, match="surface brightness"):
        clean_path(cutout.path, RA, DEC, 10, settings)
    assert cutout.path.exists()  # readable, so re-downloading would not help


# ------------------------------------------------- T-015 fix-1: narrow eviction


def _cached(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV10)])
    cutout = get_cutout(fixture_epoch(FOV10), RA, DEC, 10, settings=settings, session=session)
    return settings, session, cutout


@pytest.mark.parametrize("error", [MemoryError(), OSError(errno.EMFILE, "Too many open files"),
                                   OSError(errno.ENFILE, "File table overflow")],
                         ids=["MemoryError", "EMFILE", "ENFILE"])
def test_a_resource_error_while_reading_keeps_the_file_and_surfaces(tmp_path, monkeypatch,
                                                                     error):
    from app.cutouts import cache as cache_mod

    settings, _session, cutout = _cached(tmp_path)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(cache_mod.fits, "open", fail)
    with pytest.raises(type(error)):
        clean_path(cutout.path, RA, DEC, 10, settings)
    assert cutout.path.exists()  # not evicted


def test_a_memory_error_in_cleaning_is_not_a_clean_failure(tmp_path, monkeypatch):
    import app.pipeline.sequence as seq_mod

    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV10)])

    def fetch(items):
        return fetch_cutouts(items, RA, DEC, 10, settings=settings, session=session).results

    def clean(*args, **kwargs):
        raise MemoryError()

    with pytest.raises(MemoryError):
        select_per_visit(visits(), RA, DEC, 10, fetch=fetch, clean=clean, settings=settings)
    monkeypatch.setattr(seq_mod, "clean_path", clean)
    with pytest.raises(MemoryError):  # all mode: the same
        build_sequence(RA, DEC, "D2", 10, frame_url=lambda i, lo, hi: i, mode="all",
                       settings=settings, session=session, query=query_of(visits()))
    files = list((tmp_path / "cache").glob("*.fits"))
    assert files and all(p.exists() for p in files)


def test_a_memory_error_in_a_download_batch_surfaces(tmp_path, monkeypatch):
    import app.cutouts.service as service

    def fail(*args, **kwargs):
        raise MemoryError()

    monkeypatch.setattr(service, "download_cutout", fail)
    with pytest.raises(MemoryError):
        fetch_cutouts(visits(), RA, DEC, 10, settings=make_settings(tmp_path),
                      session=StreamSession(replies=[ok(FOV10)]))


def test_a_geometry_error_does_not_evict_the_cached_file(tmp_path, monkeypatch):
    import app.cutouts.service as service

    settings, session, first = _cached(tmp_path)

    def bad_geometry(*args, **kwargs):
        raise ValueError("WCS quirk")

    monkeypatch.setattr(service, "measure", bad_geometry)
    with pytest.raises(UpstreamError, match="no usable sky geometry"):
        get_cutout(fixture_epoch(FOV10), RA, DEC, 10, settings=settings, session=session)
    assert first.path.exists()
    monkeypatch.undo()
    again = get_cutout(fixture_epoch(FOV10), RA, DEC, 10, settings=settings, session=session)
    assert again.from_cache and len(session.calls) == 1  # never downloaded again


def test_a_read_error_in_get_cutout_still_evicts(tmp_path):
    settings, session, first = _cached(tmp_path)
    first.path.write_bytes(b"SIMPLE  = not a fits file" + b" " * 3000)
    with pytest.raises(UpstreamError, match="could not be read"):
        get_cutout(fixture_epoch(FOV10), RA, DEC, 10, settings=settings, session=session)
    assert not first.path.exists()
    again = get_cutout(fixture_epoch(FOV10), RA, DEC, 10, settings=settings, session=session)
    assert not again.from_cache and len(session.calls) == 2
