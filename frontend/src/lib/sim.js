// Simulated SPHEREx-like fields (used until the Python API is connected),
// plus spectra, canvas rendering and simple photometry used for real data too.
import { mulberry32, gauss } from './util';
import { ARRAYS, CHANNELS_PER_ARRAY } from './data';

export const PX = 6.15; // arcsec per SPHEREx pixel
const PSF = [0.55, 0.6, 0.65, 0.72, 0.8, 0.85]; // Gaussian sigma (pixels) per array

export function fieldSize(fov) {
  const nx = Math.max(24, Math.round((fov * 60) / PX));
  return { nx, ny: Math.round(nx * 0.6) };
}
export function bandLambda(arrayIdx, channel) {
  const a = ARRAYS[arrayIdx - 1];
  return channel == null ? (a.lo + a.hi) / 2 : a.lo + ((channel + 0.5) * (a.hi - a.lo)) / CHANNELS_PER_ARRAY;
}
/** Screen velocity in pixels/year. East is left, north is up. */
export function velocity(obj) {
  if (!obj?.pm) return [0, 0];
  return [-obj.pm[0] / 1000 / PX, -obj.pm[1] / 1000 / PX];
}

// ---------------------------------------------------------------- spectra
const dip = (lam, c, w, d) => 1 - d * Math.exp(-0.5 * ((lam - c) / w) ** 2);
const bump = (lam, c, w) => Math.exp(-0.5 * ((lam - c) / w) ** 2);

export function spectrumModel(kind, lam, colour = 0.5) {
  switch (kind) {
    case 'm-dwarf':
      return (0.15 + bump(lam, 1.15, 0.9)) * dip(lam, 1.4, 0.06, 0.15) * dip(lam, 1.9, 0.08, 0.18) * dip(lam, 2.7, 0.12, 0.12);
    case 't-dwarf':
      return (0.05 + 0.55 * bump(lam, 1.25, 0.1) + 0.35 * bump(lam, 1.6, 0.08) + 0.22 * bump(lam, 2.1, 0.1) + 0.75 * bump(lam, 4.5, 0.4))
        * dip(lam, 3.3, 0.15, 0.75);
    case 'y-dwarf':
      return (0.02 + 0.05 * bump(lam, 1.25, 0.08) + bump(lam, 4.55, 0.38)) * dip(lam, 3.3, 0.14, 0.85) * dip(lam, 4.67, 0.05, 0.3);
    case 'mira':
      return (0.25 + bump(lam, 1.8, 0.75)) * dip(lam, 1.4, 0.07, 0.3) * dip(lam, 1.9, 0.08, 0.3) * dip(lam, 2.7, 0.15, 0.25);
    case 'yso':
      return (0.35 * bump(lam, 1.1, 0.5) + 0.22 * lam ** 0.9) * dip(lam, 3.05, 0.15, 0.35) * dip(lam, 4.27, 0.03, 0.3) * dip(lam, 4.67, 0.04, 0.18);
    default: // ordinary star; redder stars fall off more slowly
      return lam ** (-2.2 + 1.6 * colour);
  }
}
export const MOLECULES = [
  { lam: 3.0, label: 'H₂O ice' },
  { lam: 3.3, label: 'CH₄' },
  { lam: 4.27, label: 'CO₂ ice' },
  { lam: 4.67, label: 'CO' },
];
/** 102-point spectrum (6 arrays × 17 channels), scaled so the array-3 mean equals `scale`. */
export function spectrum102(kind, scale, seed = 1, colour = 0.5) {
  const rng = mulberry32(seed);
  const norm = spectrumModel(kind, bandLambda(3, null), colour) || 1;
  const out = [];
  for (const a of ARRAYS) {
    for (let c = 0; c < CHANNELS_PER_ARRAY; c++) {
      const lam = a.lo + ((c + 0.5) * (a.hi - a.lo)) / CHANNELS_PER_ARRAY;
      const f = (spectrumModel(kind, lam, colour) / norm) * scale;
      out.push({ lam: +lam.toFixed(3), flux: +(f * (1 + 0.035 * gauss(rng)) + 0.25 * gauss(rng)).toFixed(2), array: a.n });
    }
  }
  return out;
}

// ---------------------------------------------------------------- simulation
function addStar(img, nx, ny, x, y, peak, s) {
  if (!(peak > 0)) return;
  const r = Math.ceil(s * 4);
  const x0 = Math.max(0, Math.floor(x - r)), x1 = Math.min(nx - 1, Math.ceil(x + r));
  const y0 = Math.max(0, Math.floor(y - r)), y1 = Math.min(ny - 1, Math.ceil(y + r));
  const k = 1 / (2 * s * s);
  for (let j = y0; j <= y1; j++) {
    for (let i = x0; i <= x1; i++) {
      const dx = i + 0.5 - x, dy = j + 0.5 - y;
      img[j * nx + i] += peak * Math.exp(-(dx * dx + dy * dy) * k);
    }
  }
}

const cache = new Map();

/**
 * Simulate a field.
 * object: { kind: 'motion'|'variable'|'asteroid', pm, flux[6], lc, period, amp, astEpoch, astStep, spectrum, epochMult(epoch) }
 * Returns { nx, ny, epochs, stacks, frames, vmin, vmax, stars, posAt(e, exp), object, cosmicHits }.
 */
export function simulate(opts) {
  const key = opts.key;
  if (key && cache.has(key)) return cache.get(key);
  const { seed, fov, arrayIdx = 3, channel = null, object = null, epochs, exps = 1, cosmic = [], place = 'centre', density = 1 } = opts;
  const { nx, ny } = fieldSize(fov);
  const rng = mulberry32(seed);
  const sigma = PSF[arrayIdx - 1];
  const lam = bandLambda(arrayIdx, channel);
  const noise = channel == null ? 1 : 2.4;
  const [vx, vy] = velocity(object);
  const t0 = epochs[0].t, t1 = epochs[epochs.length - 1].t;

  let pos0 = [nx / 2, ny / 2];
  if (object) {
    if (place === 'centre') {
      const tm = (t0 + t1) / 2;
      pos0 = [nx / 2 - vx * tm, ny / 2 - vy * tm];
    } else {
      do {
        pos0 = [8 + rng() * (nx - 16), 6 + rng() * (ny - 12)];
      } while (Math.hypot(pos0[0] - nx / 2, pos0[1] - ny / 2) < Math.min(nx, ny) * 0.15);
      // Keep the whole path inside the frame.
      const xs = [pos0[0] + vx * t0, pos0[0] + vx * t1], ys = [pos0[1] + vy * t0, pos0[1] + vy * t1];
      if (Math.min(...xs) < 6) pos0[0] += 6 - Math.min(...xs);
      if (Math.max(...xs) > nx - 6) pos0[0] -= Math.max(...xs) - (nx - 6);
      if (Math.min(...ys) < 5) pos0[1] += 5 - Math.min(...ys);
      if (Math.max(...ys) > ny - 5) pos0[1] -= Math.max(...ys) - (ny - 5);
    }
  }
  const posAt = (e, x = 0) => {
    if (!object) return null;
    if (object.kind === 'asteroid') {
      const k = x - (exps - 1) / 2;
      return [pos0[0] + object.astStep[0] * k, pos0[1] + object.astStep[1] * k];
    }
    const t = epochs[e].t;
    return [pos0[0] + vx * t, pos0[1] + vy * t];
  };
  const visibleAt = (e) => object && (object.kind !== 'asteroid' || e === object.astEpoch);
  const objFlux = (e) => {
    let f = object.flux[arrayIdx - 1];
    if (channel != null && object.spectrum) {
      f *= spectrumModel(object.spectrum, lam) / (spectrumModel(object.spectrum, bandLambda(arrayIdx, null)) || 1);
    }
    if (object.lc) f *= object.lc[e] ?? 1;
    if (object.period) f *= 1 + object.amp * Math.sin((2 * Math.PI * epochs[e].t * 365.25) / object.period + 0.9);
    if (object.epochMult) f *= object.epochMult(epochs[e]);
    return f;
  };

  // Field stars, kept clear of the object's path.
  const path = object ? epochs.map((_, e) => posAt(e, 0)) : [];
  if (object?.kind === 'asteroid') for (let x = 0; x < exps; x++) path.push(posAt(object.astEpoch, x));
  const stars = [];
  const want = Math.round(((nx * ny) / 95) * density);
  let guard = 0;
  while (stars.length < want && guard++ < want * 40) {
    const s = { x: 1 + rng() * (nx - 2), y: 1 + rng() * (ny - 2) };
    if (path.some(([px, py]) => Math.hypot(s.x - px, s.y - py) < 3.5)) continue;
    s.peak = 3 + 45 * rng() ** 3.4;
    s.colour = rng();
    s.mult = ARRAYS.map((a) => Math.max(0.15, 1 + (s.colour - 0.5) * (a.n - 3.5) * 0.28));
    s.pm = [gauss(rng) * 12, gauss(rng) * 12]; // mas/yr, for the Gaia overlay
    s.gaia = s.peak * s.mult[0] > 5;
    s.wise = s.peak * s.mult[3] > 6;
    stars.push(s);
  }

  // Cosmic-ray hits (single-exposure flashes).
  const cosmicHits = cosmic.map((frame) => {
    let x, y, tries = 0;
    do { x = Math.floor(4 + rng() * (nx - 8)); y = Math.floor(4 + rng() * (ny - 8)); }
    while (tries++ < 50 && path.some(([px, py]) => Math.hypot(x + 0.5 - px, y + 0.5 - py) < 5));
    return { frame, x, y };
  });

  const frames = [];
  const stacks = [];
  let fi = 0;
  for (let e = 0; e < epochs.length; e++) {
    const acc = new Float32Array(nx * ny);
    for (let x = 0; x < exps; x++, fi++) {
      const img = new Float32Array(nx * ny);
      const jx = (rng() - 0.5) * 0.15, jy = (rng() - 0.5) * 0.15;
      for (const s of stars) addStar(img, nx, ny, s.x + jx, s.y + jy, s.peak * s.mult[arrayIdx - 1], sigma);
      if (visibleAt(e)) {
        const [px, py] = posAt(e, x);
        addStar(img, nx, ny, px + jx, py + jy, objFlux(e), sigma);
      }
      const sd = noise * (exps > 1 ? 1 : 0.6);
      for (let i = 0; i < img.length; i++) img[i] += gauss(rng) * sd;
      for (const c of cosmicHits) {
        if (c.frame === fi) {
          img[c.y * nx + c.x] += 70;
          if (c.x + 1 < nx) img[c.y * nx + c.x + 1] += 14;
        }
      }
      if (exps > 1) frames.push({ epoch: e, exp: x, data: img });
      for (let i = 0; i < img.length; i++) acc[i] += img[i] / exps;
    }
    stacks.push(acc);
  }

  // One display range for the whole field, so brightness changes are real.
  const sample = [];
  for (const s of stacks) for (let i = 0; i < s.length; i += 2) sample.push(s[i]);
  sample.sort((a, b) => a - b);
  const q = (p) => sample[Math.min(sample.length - 1, Math.floor(p * sample.length))];
  const vmin = q(0.04);
  const vmax = Math.max(q(0.997), vmin + 6);

  const result = { nx, ny, pxScale: PX, epochs, stacks, frames, vmin, vmax, stars, posAt, visibleAt, object, cosmicHits, lam, arrayIdx, channel, fov, objFlux: object ? objFlux : null };
  if (key) {
    if (cache.size > 40) cache.delete(cache.keys().next().value);
    cache.set(key, result);
  }
  return result;
}

// ---------------------------------------------------------------- photometry
export function aperture(img, nx, ny, x, y, r = 2.2) {
  let s = 0;
  for (let j = Math.floor(y - r); j <= Math.ceil(y + r); j++) {
    for (let i = Math.floor(x - r); i <= Math.ceil(x + r); i++) {
      if (i < 0 || j < 0 || i >= nx || j >= ny) continue;
      const v = img[j * nx + i];
      if (Math.hypot(i + 0.5 - x, j + 0.5 - y) <= r && !Number.isNaN(v)) s += v; // NaN = no data (live frames)
    }
  }
  return s;
}
export function centroid(img, nx, ny, x, y, r = 3) {
  let sw = 0, sx = 0, sy = 0;
  for (let j = Math.floor(y - r); j <= Math.ceil(y + r); j++) {
    for (let i = Math.floor(x - r); i <= Math.ceil(x + r); i++) {
      if (i < 0 || j < 0 || i >= nx || j >= ny) continue;
      const w = Math.max(0, img[j * nx + i]) || 0; // NaN (no data) weighs nothing
      sw += w; sx += w * (i + 0.5); sy += w * (j + 0.5);
    }
  }
  return sw > 0 ? [sx / sw, sy / sw] : [x, y];
}
/** Mean of the chosen snapshots; no-data pixels (NaN) are skipped, and stay NaN only if no snapshot has data. */
export function meanOf(stacks, idx) {
  const n = stacks[0].length;
  const out = new Float32Array(n), cnt = new Uint8Array(n);
  for (const i of idx) {
    const s = stacks[i];
    for (let k = 0; k < n; k++) { const v = s[k]; if (!Number.isNaN(v)) { out[k] += v; cnt[k]++; } }
  }
  for (let k = 0; k < n; k++) out[k] = cnt[k] ? out[k] / cnt[k] : NaN;
  return out;
}
export function crop(img, nx, ny, cx, cy, half = 10) {
  const n = half * 2 + 1;
  const out = new Float32Array(n * n);
  const x0 = Math.round(cx) - half, y0 = Math.round(cy) - half;
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      const x = x0 + i, y = y0 + j;
      out[j * n + i] = x >= 0 && y >= 0 && x < nx && y < ny ? img[y * nx + x] : NaN;
    }
  }
  return { data: out, n };
}

// ---------------------------------------------------------------- rendering
function makeLut(stops) {
  const out = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = (i / 255) * (stops.length - 1);
    const k = Math.min(stops.length - 2, Math.floor(t)), f = t - k;
    for (let c = 0; c < 3; c++) out[i * 3 + c] = stops[k][c] + (stops[k + 1][c] - stops[k][c]) * f;
  }
  return out;
}
export const LUTS = {
  sky: makeLut([[7, 10, 18], [16, 21, 42], [56, 58, 126], [150, 140, 236], [236, 232, 255], [255, 255, 255]]),
  ember: makeLut([[8, 7, 12], [40, 15, 24], [118, 40, 42], [226, 118, 62], [255, 214, 162], [255, 255, 255]]),
  // Plain black-to-white brightness with no added colour (Expert view).
  grey: makeLut([[0, 0, 0], [255, 255, 255]]),
};
export const lutForArray = (a) => (a >= 4 ? LUTS.ember : LUTS.sky);

let off = null;
function offscreen(nx, ny) {
  if (!off) off = document.createElement('canvas');
  if (off.width !== nx || off.height !== ny) { off.width = nx; off.height = ny; }
  return off;
}
function blit(ctx, W, H, img, nx, ny, smooth) {
  const c = offscreen(nx, ny);
  c.getContext('2d').putImageData(img, 0, 0);
  ctx.imageSmoothingEnabled = smooth;
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(c, 0, 0, W, H);
}
const stretch = (v, vmin, span, k, soft) => {
  let x = (v - vmin) / span;
  x = x < 0 ? 0 : x > 1 ? 1 : x;
  return Math.asinh(soft * x) / k;
};

export function paintImage(ctx, W, H, data, nx, ny, { vmin, vmax, lut = LUTS.sky, smooth = true, soft = 9 }) {
  const img = new ImageData(nx, ny);
  const px = img.data, span = vmax - vmin || 1, k = Math.asinh(soft);
  for (let i = 0; i < nx * ny; i++) {
    const v = data[i];
    if (Number.isNaN(v)) { px[i * 4] = 10; px[i * 4 + 1] = 12; px[i * 4 + 2] = 20; px[i * 4 + 3] = 255; continue; }
    const idx = Math.round(stretch(v, vmin, span, k, soft) * 255) * 3;
    px[i * 4] = lut[idx]; px[i * 4 + 1] = lut[idx + 1]; px[i * 4 + 2] = lut[idx + 2]; px[i * 4 + 3] = 255;
  }
  blit(ctx, W, H, img, nx, ny, smooth);
}

const TEAL = [69, 211, 184], AMBER = [255, 177, 92], MID = [7, 10, 16];
export function paintDiff(ctx, W, H, a, b, nx, ny, { smooth = true } = {}) {
  const n = nx * ny, diff = new Float32Array(n), abs = new Float32Array(n);
  for (let i = 0; i < n; i++) { diff[i] = b[i] - a[i]; abs[i] = Math.abs(diff[i]) || 0; }
  const sorted = Array.from(abs).sort((x, y) => x - y);
  const lim = Math.max(sorted[Math.floor(n * 0.999)], 0.45 * sorted[n - 1], 4);
  const img = new ImageData(nx, ny), px = img.data;
  for (let i = 0; i < n; i++) {
    const t = Math.max(-1, Math.min(1, diff[i] / lim)) || 0;
    const end = t < 0 ? TEAL : AMBER, f = Math.abs(t) ** 1.4;
    for (let c = 0; c < 3; c++) px[i * 4 + c] = MID[c] + (end[c] - MID[c]) * f;
    px[i * 4 + 3] = 255;
  }
  blit(ctx, W, H, img, nx, ny, smooth);
}

// ---------------------------------------------------------------- canvas helpers
export function drawReticle(ctx, x, y, { color = '#b8afff', r = 14, label, sub } = {}) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x, y - r - 7); ctx.lineTo(x, y - r - 2);
  ctx.moveTo(x + r + 2, y); ctx.lineTo(x + r + 7, y);
  ctx.stroke();
  if (label) {
    ctx.font = '600 11px "Geist Mono", ui-monospace, monospace';
    ctx.fillStyle = '#e9ebf3';
    ctx.fillText(label.toUpperCase(), x + r + 12, y - 3);
    if (sub) {
      ctx.font = '400 10.5px "Geist Mono", ui-monospace, monospace';
      ctx.fillStyle = '#8c93a8';
      ctx.fillText(sub, x + r + 12, y + 12);
    }
  }
  ctx.restore();
}
export function drawCompass(ctx, W, H, fovArcmin, nx, pxScale = PX) {
  ctx.save();
  ctx.strokeStyle = 'rgba(233,235,243,.7)';
  ctx.fillStyle = 'rgba(233,235,243,.75)';
  ctx.lineWidth = 1.2;
  ctx.font = '500 10.5px "Geist Mono", ui-monospace, monospace';
  const cx = W - 24, cy = 40;
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx, cy - 20); ctx.moveTo(cx, cy); ctx.lineTo(cx - 20, cy); ctx.stroke();
  ctx.fillText('N', cx - 3.5, cy - 25);
  ctx.fillText('E', cx - 34, cy + 4);
  const barPx = (60 / pxScale) * (W / nx); // 1 arcminute
  ctx.beginPath(); ctx.moveTo(14, H - 16); ctx.lineTo(14 + barPx, H - 16); ctx.stroke();
  ctx.fillText('1′', 14, H - 22);
  ctx.restore();
}
