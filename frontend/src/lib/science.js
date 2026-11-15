// Real science products from the science backend (docs/API.md): spectra, light curves
// and Gaia DR3 stars. The five presets ship them as static files next to their frames
// (section 4); a typed-in position gets a live light curve from GET /lightcurve.
import { useEffect, useState } from 'react';
import { API_URL, request, withRetry, liveErrorText, loadPresetIndex } from './api';
import { PRESET_BY_ID } from './data';
import { drawReticle } from './sim';

const files = new Map();
function loadJson(url) {
  if (!files.has(url)) {
    files.set(url, request(url).then((r) => r.json()).catch((e) => { files.delete(url); throw e; }));
  }
  return files.get(url);
}

const NONE = { entry: null, spectrum: null, lightcurve: null, gaia: null };

/**
 * A preset's entry in presets/index.json and its spectrum, light curve and Gaia files.
 * Each is null when the preset has none. `ready` is false while they load.
 */
export function usePresetScience(targetId) {
  const slug = targetId ? PRESET_BY_ID[targetId]?.slug ?? null : null;
  const [state, setState] = useState({ slug: null, ...NONE });
  useEffect(() => {
    if (!slug) return undefined;
    let current = true;
    const get = (url) => (url ? loadJson(url).catch(() => null) : Promise.resolve(null));
    loadPresetIndex()
      .then(async (index) => {
        const entry = index.presets.find((x) => x.id === slug) ?? null;
        const [spectrum, lightcurve, gaia] = await Promise.all([get(entry?.spectrum_url), get(entry?.lightcurve_url), get(entry?.gaia_url)]);
        if (current) setState({ slug, entry, spectrum, lightcurve, gaia });
      })
      .catch(() => { if (current) setState({ slug, ...NONE }); });
    return () => { current = false; };
  }, [slug]);
  if (!slug) return { ready: true, ...NONE };
  return state.slug === slug ? { ready: true, ...state } : { ready: false, ...NONE };
}

const liveCurves = new Map();

/**
 * GET /lightcurve for a position and detector array (docs/API.md section 3), with the
 * same retries as /epochs. Returns { data, loading, error }; idle when `enabled` is false.
 */
export function useLiveLightcurve({ raDeg, decDeg, arrayIdx, enabled }) {
  const key = enabled && raDeg != null && decDeg != null ? `${raDeg.toFixed(5)},${decDeg.toFixed(5)},${arrayIdx}` : null;
  const cached = key ? liveCurves.get(key) : undefined;
  const [state, setState] = useState({ key: null, data: null, error: null });
  useEffect(() => {
    if (!key || cached) return undefined;
    const ctrl = new AbortController();
    const q = new URLSearchParams({ ra: raDeg, dec: decDeg, band: `D${arrayIdx}` });
    withRetry(() => request(`${API_URL}/lightcurve?${q}`, ctrl.signal), ctrl.signal)
      .then((r) => r.json())
      .then((data) => {
        liveCurves.set(key, data);
        if (liveCurves.size > 20) liveCurves.delete(liveCurves.keys().next().value);
        setState({ key, data, error: null });
      })
      .catch((e) => { if (!ctrl.signal.aborted) setState({ key, data: null, error: liveErrorText(e) }); });
    return () => ctrl.abort();
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!key) return { data: null, loading: false, error: null };
  if (cached) return { data: cached, loading: false, error: null };
  if (state.key !== key) return { data: null, loading: true, error: null };
  return { data: state.data, loading: false, error: state.error };
}

// ---------------------------------------------------------------- Gaia overlay
const D2R = Math.PI / 180;

/**
 * Image position (pixels; the image centre is nx/2, ny/2) of a sky position in a
 * north-up, east-left field with a gnomonic projection, like the backend's frames.
 */
export function skyToPixel(ra, dec, field) {
  const { centre, nx, ny, pxScale } = field;
  const da = (ra - centre.ra) * D2R, d = dec * D2R, d0 = centre.dec * D2R;
  const cosc = Math.sin(d0) * Math.sin(d) + Math.cos(d0) * Math.cos(d) * Math.cos(da);
  const xi = (Math.cos(d) * Math.sin(da)) / cosc;
  const eta = (Math.cos(d0) * Math.sin(d) - Math.sin(d0) * Math.cos(d) * Math.cos(da)) / cosc;
  const s = (pxScale / 3600) * D2R;
  return [nx / 2 - xi / s, ny / 2 - eta / s];
}

/** Gaia DR3 stars at their positions on the SPHEREx date, with arrows for one year of motion. */
export function drawGaia(ctx, W, H, field, gaia) {
  const sx = W / field.nx, sy = H / field.ny;
  ctx.save();
  ctx.font = '11px Geist Mono, monospace';
  for (const s of gaia.stars) {
    const [x, y] = skyToPixel(s.ra_epoch, s.dec_epoch, field);
    if (x < 0 || y < 0 || x > field.nx || y > field.ny) continue;
    const cx = x * sx, cy = y * sy;
    const r = Math.max(4, Math.min(11, 12 - (s.g_mag - 6) * 0.7)); // brighter stars get bigger rings
    ctx.strokeStyle = 'rgba(69,211,184,.8)';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
    if (s.pmra == null || s.pmdec == null) continue;
    // One year of motion: east is left and north is up on the image.
    const ex = cx - (s.pmra / 1000 / field.pxScale) * sx, ey = cy - (s.pmdec / 1000 / field.pxScale) * sy;
    if (Math.hypot(ex - cx, ey - cy) < 3) continue;
    const ang = Math.atan2(ey - cy, ex - cx);
    ctx.strokeStyle = '#45d3b8';
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(ex, ey);
    ctx.lineTo(ex - 6 * Math.cos(ang - 0.45), ey - 6 * Math.sin(ang - 0.45));
    ctx.moveTo(ex, ey);
    ctx.lineTo(ex - 6 * Math.cos(ang + 0.45), ey - 6 * Math.sin(ang + 0.45));
    ctx.stroke();
    const pm = Math.hypot(s.pmra, s.pmdec) / 1000;
    if (pm >= 1) {
      ctx.fillStyle = '#45d3b8';
      ctx.fillText(`Gaia · ${pm.toFixed(1)}″/yr`, cx + r + 4, cy - r);
    }
  }
  ctx.restore();
}

// ---------------------------------------------------------------- predicted track
// Preset centres are the targets' positions at CENTRE_EPOCH (backend
// scripts/preset_photometry.py), so the catalogue proper motion gives the expected
// position on any snapshot date.
const CENTRE_EPOCH = 2025.5;
const J2000_MS = Date.UTC(2000, 0, 1, 12);
const YEAR_MS = 365.25 * 864e5;
const MAS_PER_DEG = 3.6e6;

const julianYear = (epoch) => 2000 + (Date.parse(`${epoch.date}T00:00:00Z`) - J2000_MS) / YEAR_MS;

/** Expected sky position on a snapshot's date. track = { ra, dec, pm: [pmra·cos dec, pmdec] mas/yr }. */
function trackPosition(track, epoch) {
  const dt = julianYear(epoch) - CENTRE_EPOCH;
  return [track.ra + (track.pm[0] * dt) / MAS_PER_DEG / Math.cos(track.dec * D2R), track.dec + (track.pm[1] * dt) / MAS_PER_DEG];
}

/** The catalogue track through the ticked snapshots, and a reticle at the current one. */
export function drawTrack(ctx, W, H, field, track, incIdx, cur) {
  const sx = W / field.nx, sy = H / field.ny;
  const at = (e) => skyToPixel(...trackPosition(track, field.epochs[e]), field);
  if (incIdx.length > 1) {
    ctx.save();
    ctx.strokeStyle = 'rgba(184,175,255,.55)';
    ctx.setLineDash([3, 4]);
    ctx.beginPath();
    incIdx.forEach((e, k) => { const [x, y] = at(e); if (k) ctx.lineTo(x * sx, y * sy); else ctx.moveTo(x * sx, y * sy); });
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = 'rgba(184,175,255,.8)';
    incIdx.forEach((e) => { const [x, y] = at(e); ctx.beginPath(); ctx.arc(x * sx, y * sy, 2, 0, Math.PI * 2); ctx.fill(); });
    ctx.restore();
  }
  const [x, y] = at(cur);
  drawReticle(ctx, x * sx, y * sy, { label: track.name, sub: 'expected from catalogue motion', r: 13 });
}

// ---------------------------------------------------------------- text per preset
/** What each preset's co-adds show (docs/API.md section 4). */
export const COADD_NOTES = {
  'eps-indi-b': 'Epsilon Indi B (in the ring) is no brighter than its neighbour star in array 1 (about 1 µm), but outshines everything around it in arrays 5–6 (4–5 µm): a cold brown dwarf’s colour.',
  'rw-aur': 'RW Aurigae (in the ring) stays bright in every array while its neighbours fade at 4–5 µm: the infrared glow of its dusty disk.',
  'wise-0855': 'In arrays 1–4 only a normal star 10.6″ (1.7 pixels) west of the centre shows, just right of the ring’s centre. WISE 0855 itself appears at the centre only in arrays 5–6 (3.8–5 µm).',
};

/** What the Gaia overlay shows at each target (checked against each preset's gaia.json). */
export const GAIA_NOTES = {
  'barnards-star': 'Barnard’s Star is the G = 8.2 source near the centre, with the longest arrow: 10.4″ a year.',
  'eps-indi-b': 'Epsilon Indi B is the faint G = 18.0 source at the centre, moving 4.7″ a year like its star Epsilon Indi A, which is just outside the view.',
  'wise-0855': 'WISE 0855 itself is far too faint for Gaia, which sees visible light: it only shows up in the infrared.',
};

/** What each preset's real light curve shows (docs/API.md section 4). */
export const LIGHTCURVE_NOTES = {
  'rw-aur': 'Real variability: at about 1.2 µm, visit 3 is about 30–40 % brighter than visit 1.',
  'eps-indi-b': 'The spread within a visit is mostly colour, not time: water vapour absorbs at 1.37–1.40 µm. The same colour repeats within about 5 % between visits.',
  'wise-0855': 'Very faint (signal-to-noise 1–4 per point), and partly light from a normal star 10.6″ away.',
};

/** Why a preset has no spectrum file. */
export const NO_SPECTRUM = {
  'wise-0855': 'No spectrum for WISE 0855: below about 3.5 µm a normal star 10.6″ away outshines it inside the measuring circle, so the spectrum would mostly be that star. Compare array 2 with array 6 instead: WISE 0855 only appears at 4–5 µm.',
};

/** What each real spectrum shows, plus the absorption features to mark on the chart. */
export const SPECTRUM_INFO = {
  'eps-indi-b': {
    text: 'A cold brown dwarf’s fingerprints: water, methane and carbon monoxide absorb at the dashed lines, between peaks at 1.27, 1.58 and 2.11 µm and a bright window at 4 µm.',
    features: [
      { lam: 1.4, label: 'H₂O' }, { lam: 1.9, label: 'H₂O' }, { lam: 2.3, label: 'CH₄' },
      { lam: 3.3, label: 'CH₄' }, { lam: 4.5, label: 'CO' },
    ],
  },
  'rw-aur': {
    text: 'It brightens towards 5 µm: infrared glow from the young star’s warm, dusty disk (a bare star would fade beyond about 1.5 µm). Arrays observed days apart can step, because RW Aur varies within days.',
    features: [],
  },
};
