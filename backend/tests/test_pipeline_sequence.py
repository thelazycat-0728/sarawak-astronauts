"""Sequence builder: /epochs shape, shared stretch and round trips (offline)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from app.errors import InvalidParameter
from app.models import EpochsResponse
from app.pipeline import build_sequence, render_frame
from tests.cutout_helpers import FOV10, StreamResponse, StreamSession, make_settings, ok
from tests.irsa_helpers import FakeResponse, FakeSession, fixture_bytes
from tests.pipeline_helpers import DEC, RA, epoch, query_of, square

SAFE = square(0.5)


def frame_url(epoch_id: str, vmin: float, vmax: float) -> str:
    return f"/presets/test/frames/{epoch_id}.png"


def api_url(epoch_id: str, vmin: float, vmax: float) -> str:
    return (f"http://localhost:8000/frame/{epoch_id}.png?ra={RA}&dec={DEC}&fov=10&band=D2"
            f"&vmin={vmin!r}&vmax={vmax!r}")


def epochs_of_three_visits():
    out = []
    for visit, (week, base) in enumerate([("2025W34_2B_0090", 60908.0),
                                          ("2026W12_1A_0311", 61116.0),
                                          ("2026W35_1A_0092", 61276.0)], start=1):
        for i in (1, 2):
            release = "QR3" if visit == 3 else "DR1"
            out.append(epoch(f"{week}_{i}", visit, base + i * 0.1, SAFE, release=release))
    return out


@pytest.mark.parametrize("mode,count", [("per_visit", 3), ("all", 6)])
def test_sequence_validates_as_epochs_response(tmp_path, mode, count):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV10)])
    seq = build_sequence(RA, DEC, "D2", 10, frame_url=frame_url, mode=mode,
                         name="Barnard's Star", settings=settings, session=session,
                         query=query_of(epochs_of_three_visits(), duplicates_removed=2))
    resp = seq.response
    again = EpochsResponse.model_validate_json(resp.model_dump_json())
    assert again == resp
    assert resp.synthetic is False and resp.count == count == len(seq.frames)
    assert resp.diffs == [] and resp.diff_url_template is None
    assert resp.duplicates_removed == 2
    assert resp.releases == ["DR1", "QR3"]
    assert resp.target.name == "Barnard's Star" and resp.target.ra == RA
    assert (resp.image.width_px, resp.image.height_px) == (97, 97)
    assert resp.image.pixel_scale_arcsec == 6.2
    assert resp.stretch.shared and resp.stretch.method == "asinh"
    assert resp.stretch.asinh_a == 0.1 and resp.image.nodata_gray == 0
    assert resp.stretch.vmax > resp.stretch.vmin
    assert [e.mjd for e in resp.epochs] == sorted(e.mjd for e in resp.epochs)
    assert len(session.calls) == count  # per_visit fetches one per visit only
    for e in resp.epochs:
        assert e.frame_url == f"/presets/test/frames/{e.id}.png"
        assert e.wavelength_um == FOV10["wavelength_um"]  # real, from the spectral WCS
        assert e.time_utc.endswith("Z") and e.obs_id in e.id
        assert "SOURCE" not in e.quality.flags
    assert list(seq.frames) == [e.id for e in resp.epochs]
    assert set(seq.timings_s) >= {"stretch_and_render", "total"}


def test_wavelength_is_not_clamped_to_the_band(tmp_path):
    # The fixture's target wavelength (1.101 um) is below the SIA em_min (1.1033)
    # of the exposure; it is reported as measured.
    settings = make_settings(tmp_path)
    seq = build_sequence(RA, DEC, "D2", 10, frame_url=frame_url, mode="all",
                         settings=settings, session=StreamSession(replies=[ok(FOV10)]),
                         query=query_of(epochs_of_three_visits()[:1]))
    assert seq.response.epochs[0].wavelength_um == 1.101


def test_stretch_is_shared_and_round_trips_through_json(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV10)])
    seq = build_sequence(RA, DEC, "D2", 10, frame_url=api_url, mode="all",
                         settings=settings, session=session,
                         query=query_of(epochs_of_three_visits()))
    manifest = json.loads(json.dumps(seq.response.model_dump(mode="json")))
    vmin, vmax = manifest["stretch"]["vmin"], manifest["stretch"]["vmax"]
    assert (vmin, vmax) == (seq.response.stretch.vmin, seq.response.stretch.vmax)
    for e in manifest["epochs"]:
        assert f"&vmin={vmin!r}&vmax={vmax!r}" in e["frame_url"]
    # All frames hold the same pixels here, so one shared stretch gives one image.
    assert len(set(seq.frames.values())) == 1
    # The frame endpoint, given the manifest's stretch, reproduces each PNG exactly.
    offline = StreamSession(replies=[])
    no_sia = FakeSession(error=AssertionError("no SIA call expected"))
    for e in manifest["epochs"]:
        png = render_frame(e["id"], RA, DEC, 10, "D2", vmin, vmax, settings=settings,
                           session=offline, sia_session=no_sia)
        assert png == seq.frames[e["id"]]
    assert offline.calls == [] and no_sia.calls == []


def test_stretch_covers_every_frame(tmp_path):
    settings = make_settings(tmp_path)
    seq = build_sequence(RA, DEC, "D2", 10, frame_url=frame_url, mode="all",
                         settings=settings, session=StreamSession(replies=[ok(FOV10)]),
                         query=query_of(epochs_of_three_visits()))
    pixels = np.concatenate([f.image[f.valid] for f in seq.cleaned.values()])
    lo, hi = np.percentile(pixels, settings.stretch_percentiles)
    assert seq.response.stretch.vmin == pytest.approx(lo, rel=1e-4)
    assert seq.response.stretch.vmax == pytest.approx(hi, rel=1e-4)


def test_all_mode_leaves_out_failed_downloads(tmp_path):
    settings = make_settings(tmp_path)

    def reply(url):
        return StreamResponse(b"", status_code=404) if "0311_1" in url else ok(FOV10)

    seq = build_sequence(RA, DEC, "D2", 10, frame_url=frame_url, mode="all",
                         settings=settings, session=StreamSession(replies=[reply]),
                         query=query_of(epochs_of_three_visits()))
    assert seq.response.count == 5
    assert [f.epoch_id for f in seq.failures] == ["sx_dr1_2026W12_1A_0311_1_D2"]
    assert seq.downloads == 5 and seq.cache_hits == 0


def test_empty_query_gives_an_empty_valid_response(tmp_path):
    seq = build_sequence(RA, DEC, "D2", 10, frame_url=frame_url,
                         settings=make_settings(tmp_path),
                         session=StreamSession(replies=[]), query=query_of([]))
    assert seq.response.count == 0 and seq.response.epochs == []
    assert (seq.response.stretch.vmin, seq.response.stretch.vmax) == (0.0, 1.0)
    assert seq.response.releases == []


def test_sequence_from_the_recorded_sia_query(tmp_path):
    """The query step with the recorded Barnard's Star SIA response: per_visit
    downloads three cutouts (one per visit) out of 61 exposures."""
    settings = make_settings(tmp_path)
    sia = FakeSession(response=FakeResponse(fixture_bytes("barnard_d2.xml")))
    session = StreamSession(replies=[ok(FOV10)])
    seq = build_sequence(RA, DEC, "D2", 10, frame_url=frame_url, settings=settings,
                         session=session, sia_session=sia)
    assert len(sia.calls) == 1
    assert len(session.calls) == 3 and seq.response.count == 3
    assert [e.visit for e in seq.response.epochs] == [1, 2, 3]
    assert "query" in seq.timings_s


def test_bad_mode_and_view(tmp_path):
    with pytest.raises(InvalidParameter):
        build_sequence(RA, DEC, "D2", 10, frame_url=frame_url, mode="some",
                       settings=make_settings(tmp_path), query=query_of([]))
    with pytest.raises(InvalidParameter):
        build_sequence(RA, 91.0, "D2", 10, frame_url=frame_url,
                       settings=make_settings(tmp_path), query=query_of([]))
