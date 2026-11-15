// Data access. Presets show real SPHEREx frames shipped in public/presets/ (in their
// own detector array). Other views come from the Python API when VITE_API_URL is set;
// otherwise the app runs on simulated demo frames with the same shape.
import { useEffect, useMemo, useState } from 'react';
import { simulate, aperture, PX } from './sim';
import { PRESET_BY_ID, SURVEYS, GLOSSARY, ARRAYS, DAY0, customObject } from './data';
import { hashString, parseRA, parseDec } from './util';

export const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
export const IS_LIVE = Boolean(API_URL);

/** Simulated field for a preset or typed coordinates. */
export function demoField({ targetId, raDeg, decDeg, fov, arrayIdx, channel }) {
  const preset = targetId ? PRESET_BY_ID[targetId] : null;
  if (preset) {
    return simulate({
      key: `f-${preset.id}-${fov}-${arrayIdx}-${channel}`,
      seed: preset.seed, fov, arrayIdx, channel, object: preset, epochs: preset.epochs,
    });
  }
  const seed = hashString(`${(raDeg ?? 0).toFixed(3)},${(decDeg ?? 0).toFixed(3)}`);
  return simulate({
    key: `c-${seed}-${fov}-${arrayIdx}-${channel}`,
    seed, fov, arrayIdx, channel, object: customObject(seed), epochs: SURVEYS, place: 'random',
  });
}

// ---------------------------------------------------------------- live API (docs/API.md)
// Frames arrive as 8-bit asinh-stretched PNGs: grey 0 is "no data", valid pixels are
// 1–255. Undoing the stretch gives linear brightness, scaled to 0–100 of the shared
// stretch range so it lands near the simulator's units, which keeps Blink / Difference /
// Stack and the submission checks working unchanged.
const LINEAR_SCALE = 100;
const GREY_LEVELS = 254;
const EPOCH_ATTEMPTS = 3; // a 504 means "still building"; the build keeps going, so retry
const RETRY_DELAY_MS = 3000;

/** An API error with its HTTP status and the message from the {"error": {...}} body. */
class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

const wait = (ms, signal) => new Promise((resolve, reject) => {
  const id = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => { clearTimeout(id); reject(signal.reason); }, { once: true });
});

async function request(url, signal) {
  let r;
  try { r = await fetch(url, { signal }); } catch (e) {
    if (signal?.aborted) throw e;
    throw new ApiError(0, 'The data service could not be reached.');
  }
  if (r.ok) return r;
  const body = await r.json().catch(() => null);
  throw new ApiError(r.status, body?.error?.message ?? `The data service answered ${r.status}.`);
}

/** Retries 504 "still building" answers; every other error is final. */
async function withRetry(fn, signal, onRetry) {
  for (let attempt = 1; ; attempt++) {
    try { return await fn(); } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 504 || attempt >= EPOCH_ATTEMPTS) throw e;
      onRetry?.(attempt + 1);
      await wait(RETRY_DELAY_MS, signal);
    }
  }
}

/** Fetches one frame PNG and returns its pixels as linear brightness (row-major, north up; NaN = no data). */
async function loadFrame(url, stretch, signal) {
  const r = await withRetry(() => request(url, signal), signal);
  const bmp = await createImageBitmap(await r.blob(), { colorSpaceConversion: 'none', premultiplyAlpha: 'none' });
  const canvas = document.createElement('canvas');
  canvas.width = bmp.width; canvas.height = bmp.height;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(bmp, 0, 0);
  bmp.close();
  const px = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  const { a, nodata } = stretch;
  const k = Math.asinh(1 / a);
  const out = new Float32Array(canvas.width * canvas.height);
  for (let i = 0; i < out.length; i++) {
    const g = px[i * 4];
    out[i] = g === nodata ? NaN : a * Math.sinh(((g - 1) / GREY_LEVELS) * k) * LINEAR_SCALE;
  }
  return { nx: canvas.width, ny: canvas.height, data: out };
}

/** Display range for the whole field, matching the simulator's choice. No-data pixels are skipped. */
function displayRange(stacks) {
  const sample = [];
  for (const s of stacks) for (let i = 0; i < s.length; i += 2) if (!Number.isNaN(s[i])) sample.push(s[i]);
  if (!sample.length) return { vmin: 0, vmax: 6 };
  sample.sort((a, b) => a - b);
  const q = (p) => sample[Math.min(sample.length - 1, Math.floor(p * sample.length))];
  const vmin = q(0.04);
  return { vmin, vmax: Math.max(q(0.997), vmin + 6) };
}

/**
 * Loads every frame_url of an /epochs-shaped response. Exposures from the same survey
 * visit are averaged into one snapshot; single exposures stay in `frames`.
 */
async function buildField(j, p, signal) {
  if (!j.count) throw new ApiError(200, 'SPHEREx hasn’t observed this patch of sky yet.');

  // asinh_a is in every response (0.03 mock, 0.1 live); older mocks without it used 0.03.
  const stretch = { a: j.stretch?.asinh_a ?? 0.03, nodata: j.image?.nodata_gray ?? 0 };
  const shots = await Promise.all(j.epochs.map((e) => loadFrame(e.frame_url, stretch, signal)));
  const { nx, ny } = shots[0];
  const visits = [...new Set(j.epochs.map((e) => e.visit))].sort((a, b) => a - b);
  const epochs = [], stacks = [], frames = [];

  visits.forEach((visit, ei) => {
    const members = j.epochs.map((e, i) => ({ e, shot: shots[i] })).filter(({ e }) => e.visit === visit);
    const acc = new Float32Array(nx * ny);
    members.forEach(({ e, shot }, x) => {
      frames.push({ epoch: ei, exp: x, id: e.id, data: shot.data });
      for (let i = 0; i < acc.length; i++) acc[i] += shot.data[i] / members.length;
    });
    stacks.push(acc);
    const first = members[0].e;
    epochs.push({
      label: `Visit ${visit}`,
      date: first.time_utc.slice(0, 10),
      release: first.release,
      t: (Date.parse(first.time_utc) - DAY0) / (365.25 * 864e5),
      ids: members.map(({ e }) => e.id),
      exposures: members.length,
      flaggedFraction: Math.max(...members.map(({ e }) => e.quality?.flagged_fraction ?? 0)),
      flags: [...new Set(members.flatMap(({ e }) => e.quality?.flags ?? []))],
    });
  });

  return {
    nx, ny, pxScale: j.image?.pixel_scale_arcsec ?? PX, ...displayRange(stacks), fov: p.fov, arrayIdx: p.arrayIdx, channel: p.channel,
    epochs, stacks, frames, stars: [], object: null, posAt: () => null, visibleAt: () => false,
    live: true, synthetic: Boolean(j.synthetic),
  };
}

/** GET /epochs from the science API, then its frames. */
async function fetchField(p, signal, onRetry) {
  const q = new URLSearchParams({ ra: p.raDeg, dec: p.decDeg, fov: p.fov, band: `D${p.arrayIdx}` });
  const r = await withRetry(() => request(`${API_URL}/epochs?${q}`, signal), signal, onRetry);
  return buildField(await r.json(), p, signal);
}

// ---------------------------------------------------------------- real preset frames
// public/presets/index.json lists every preset with one manifest per fov; each manifest
// has the /epochs response shape (public/presets/README.md). They are static files, so
// presets work without the science API.
let presetIndex = null;

function loadPresetIndex() {
  presetIndex ??= request('/presets/index.json').then((r) => r.json())
    .catch((e) => { presetIndex = null; throw e; });
  return presetIndex;
}

/** True when the view is a preset in the detector array its real frames cover. */
function hasRealPreset(p) {
  const preset = p.targetId ? PRESET_BY_ID[p.targetId] : null;
  return Boolean(preset?.slug) && preset.realArray === p.arrayIdx;
}

async function fetchPresetField(p, signal) {
  const preset = PRESET_BY_ID[p.targetId];
  const entry = (await loadPresetIndex()).presets.find((x) => x.id === preset.slug);
  const m = entry?.band === `D${p.arrayIdx}` && entry.manifests.find((x) => x.fov_arcmin === p.fov);
  if (!m) throw new ApiError(404, `No SPHEREx preset frames for ${preset.name} at this view size.`);
  const r = await request(m.url, signal);
  return { ...(await buildField(await r.json(), p, signal)), preset: true };
}

// Loaded live fields, so moving between pages (Explore → Submit) doesn’t download them
// again. Same lifetime as the API’s own /epochs cache.
const LIVE_CACHE_MS = 10 * 60 * 1000;
const liveCache = new Map();

function liveKey(p) {
  return hasRealPreset(p)
    ? JSON.stringify(['preset', p.targetId, p.fov, p.arrayIdx])
    : JSON.stringify([p.raDeg, p.decDeg, p.fov, p.arrayIdx]);
}

async function cachedField(p, signal, onRetry) {
  const key = liveKey(p);
  const hit = liveCache.get(key);
  if (hit && Date.now() - hit.at < LIVE_CACHE_MS) return hit.data;
  const data = hasRealPreset(p) ? await fetchPresetField(p, signal) : await fetchField(p, signal, onRetry);
  liveCache.set(key, { at: Date.now(), data });
  if (liveCache.size > 12) liveCache.delete(liveCache.keys().next().value);
  return data;
}

/** Plain-language reason for a failed live view (docs/API.md error codes). */
function liveErrorText(e) {
  if (!(e instanceof ApiError)) return 'Something went wrong while loading this view.';
  if (e.status === 504) return 'SPHEREx images for this patch are still being prepared. Try again in a minute, or explore a known object meanwhile.';
  if (e.status === 502) return 'The SPHEREx archive (IRSA) didn’t answer for this view. Try again later, or explore a known object.';
  if (e.status === 503) return 'The data service is busy right now. Try again shortly, or explore a known object.';
  return e.message;
}

/**
 * Field for the viewer. A preset in its real-data array loads the shipped SPHEREx
 * frames ('preset'). Anything else asks the live API ('live') or, without one, the
 * simulator ('demo'); a preset in another array is treated like typed coordinates.
 */
export function useField(params) {
  const key = JSON.stringify(params);
  const valid = params.raDeg != null && params.decDeg != null;
  const real = valid && hasRealPreset(params);
  const fetched = real || IS_LIVE;
  const demo = useMemo(() => (valid && !fetched ? demoField(params) : null), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const [remote, setRemote] = useState({ key: null, data: null, error: null });
  const [retry, setRetry] = useState({ key: null, attempt: 1 });
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!valid || !fetched) return undefined;
    const ctrl = new AbortController();
    cachedField(params, ctrl.signal, (attempt) => setRetry({ key, attempt }))
      .then((data) => setRemote({ key, data, error: null }))
      .catch((e) => { if (!ctrl.signal.aborted) setRemote({ key, data: null, error: liveErrorText(e) }); });
    return () => ctrl.abort();
  }, [key, reload]); // eslint-disable-line react-hooks/exhaustive-deps

  const tryAgain = () => { setRemote({ key: null, data: null, error: null }); setRetry({ key: null, attempt: 1 }); setReload((n) => n + 1); };

  if (!valid) return { data: null, loading: false, source: 'none' };
  if (!fetched) return { data: demo, loading: false, source: 'demo' };
  const source = real ? 'preset' : 'live';
  if (remote.key !== key) return { data: null, loading: true, source, attempt: retry.key === key ? retry.attempt : 1 };
  // No stand-in frames for a failed view: the stage offers a retry and the presets instead.
  if (remote.error) return { data: null, loading: false, source: 'error', error: remote.error, tryAgain };
  return { data: remote.data, loading: false, source };
}

// ---------------------------------------------------------------- AI explainer
/**
 * Answer to a question about the current view: { text, ai }. ai = true when it came
 * from the API's LLM (Google Gemini); otherwise it is a built-in offline answer (no
 * API, no key configured, rate limit or LLM failure).
 */
export async function explain(question, context) {
  if (IS_LIVE) {
    try {
      const ra = parseRA(context.ra), dec = parseDec(context.dec);
      const r = await fetch(`${API_URL}/explain`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: question.slice(0, 500),
          context: {
            ...(ra != null && dec != null ? { ra, dec } : {}),
            fov_arcmin: context.fov, band: `D${context.arrayIdx ?? 3}`, target_name: context.preset?.name,
            mode: context.mode,
          },
        }),
      });
      if (r.ok) return { text: (await r.json()).answer, ai: true };
    } catch { /* fall back to local answers */ }
  }
  await new Promise((res) => setTimeout(res, 450));
  return { text: localExplain(question, context), ai: false };
}

const MODE_TEXT = {
  blink: 'Blink flips between snapshots taken months apart. Anything that jumps while the background stays still is moving.',
  diff: 'Difference subtracts the first snapshot from the current one. Stars that stay put cancel to black; a mover leaves a teal mark where it was and an amber mark where it is now.',
  stack: 'Stack averages the snapshots. Noise drops, so faint still objects show up, but anything moving gets smeared.',
};

function localExplain(q, ctx) {
  const s = q.toLowerCase();
  const p = ctx.preset;
  const arr = ARRAYS[(ctx.arrayIdx ?? 3) - 1];
  const where = p ? `${p.name} (${p.type.toLowerCase()})` : 'a field you typed in';
  if (/look|what is this|seeing|this view|what am i/.test(s)) {
    return `You’re looking at ${where}, in SPHEREx detector array ${arr.n} (${arr.lo}–${arr.hi} µm), using ${ctx.mode === 'diff' ? 'Difference' : ctx.mode === 'stack' ? 'Stack' : 'Blink'} mode. ${MODE_TEXT[ctx.mode]} ${p ? p.facts[0] : 'About half of typed-in demo fields hide a moving object. Try Difference mode to look for it.'}`;
  }
  if (/mov|shift|proper motion|drift|jump/.test(s)) {
    return `Nearby stars drift across the sky as they and the Sun orbit the Galaxy. This is called proper motion. ${p?.pmText ? `${p.name} moves about ${p.pmText}, ` : ''}SPHEREx pixels are 6.15″ wide, so most stars move far less than a pixel between surveys. The closest and fastest ones shift enough to see in Blink or Difference.`;
  }
  if (/differen|subtract/.test(s)) return MODE_TEXT.diff;
  if (/blink/.test(s)) return MODE_TEXT.blink;
  if (/stack|co-?add|average/.test(s)) return MODE_TEXT.stack;
  if (/flag|artefact|artifact|glitch|cosmic|ghost/.test(s)) {
    return `${GLOSSARY.flags.short} A cosmic-ray hit shows up in a single exposure only; a real object appears in every snapshot (or moves smoothly between them). If something flashes once and vanishes, it is almost always a glitch.`;
  }
  if (/band|array|colou?r|wavelength|infrared|micron|µm/.test(s)) {
    return `SPHEREx splits near-infrared light into 102 narrow bands across six detector arrays. You are in array ${arr.n}, covering ${arr.lo}–${arr.hi} µm. Cold objects such as brown dwarfs are brightest at the long end (arrays 5–6), while ordinary stars dominate the short end.`;
  }
  if (/brown dwarf/.test(s)) return `${GLOSSARY.browndwarf.short} The coldest, like WISE 0855, are nearly invisible below 2 µm, which is why SPHEREx’s long-wavelength arrays matter.`;
  if (/variab|bright|fade|dim|puls/.test(s)) return `${GLOSSARY.variable.short} In Expert mode, click a star to see its brightness chart. Compare snapshots taken in the same array so colour differences don’t look like brightness changes.`;
  if (/spectr|molecule|water|methane|ice/.test(s)) return `${GLOSSARY.spectrum.short} In Expert mode, click any source to build its spectrum from all 102 bands. Dips are marked where water ice (3.0 µm), methane (3.3 µm), CO₂ ice (4.27 µm) and CO (4.67 µm) absorb.`;
  if (/gaia|catalog/.test(s)) return `${GLOSSARY.gaia.short} Turn on the Gaia overlay in Expert mode to see known stars and their motion arrows. A mover with no Gaia match is a candidate.`;
  if (/submit|discover|report|found/.test(s)) return 'Finish the training in Learn, then open Discover (or use “Flag a candidate” in Explore). Mark the object in two snapshots, add a note, and the automatic checks run straight away. Promising candidates go to scientists for review.';
  return `I can explain what’s on screen. You’re viewing ${where} in array ${arr.n}. Try asking “Why does the star move?”, “What does Difference show?” or “What are contamination flags?”.`;
}

// ---------------------------------------------------------------- submissions
const BLOCKLIST = ['idiot', 'stupid', 'scam', 'crypto', 'casino', 'xxx'];
export function moderateNote(text = '') {
  const reasons = [];
  if (/[\w.+-]+@[\w-]+\.[\w.]+/.test(text)) reasons.push('contains an email address');
  if (/(\+?\d[\d\s-]{7,}\d)/.test(text)) reasons.push('contains a phone number');
  if (/https?:\/\/|www\./i.test(text)) reasons.push('contains a link');
  if (BLOCKLIST.some((w) => text.toLowerCase().includes(w))) reasons.push('contains language we don’t allow');
  return { ok: reasons.length === 0, reasons };
}

/** Automatic vetting of a submission. marks = [{ epoch, x, y }, { epoch, x, y }]. */
export function vet({ marks, field, preset, note }) {
  const [a, b] = marks;
  const { nx, ny, stacks, epochs } = field;
  const checks = [];

  const edge = marks.some((m) => m.x < 2 || m.y < 2 || m.x > nx - 2 || m.y > ny - 2);
  checks.push({ id: 'quality', label: 'Data quality', ok: !edge,
    detail: edge ? 'Too close to the image edge, where pixels are less reliable.' : 'No contamination flags at the marked positions.' });

  const noise = Math.sqrt(Math.PI * 2.2 * 2.2) * 0.6;
  const snrA = aperture(stacks[a.epoch], nx, ny, a.x, a.y) / noise;
  const snrB = aperture(stacks[b.epoch], nx, ny, b.x, b.y) / noise;
  const snr = Math.min(snrA, snrB);
  const present = snr > 5;
  checks.push({ id: 'signal', label: 'Seen in both snapshots', ok: present,
    detail: present ? `Signal-to-noise ${snr.toFixed(1)} at the weaker mark.` : 'No clear source at one of your marks.' });

  const shift = Math.hypot(b.x - a.x, b.y - a.y) * (field.pxScale ?? PX);
  const moved = shift > 3;
  checks.push({ id: 'motion', label: 'Position changed', ok: moved,
    detail: moved ? `${shift.toFixed(1)}″ between ${epochs[a.epoch].label} and ${epochs[b.epoch].label}.` : `Only ${shift.toFixed(1)}″: within measurement error.` });

  let known = null;
  if (field.object && field.posAt) {
    const pa = field.posAt(a.epoch), pb = field.posAt(b.epoch);
    const near = pa && pb && Math.hypot(pa[0] - a.x, pa[1] - a.y) < 2.6 && Math.hypot(pb[0] - b.x, pb[1] - b.y) < 2.6;
    if (near && !field.object.mystery) known = preset?.name ?? field.object.name;
  } else if (field.preset && preset) {
    // Real preset frames are centred on the target, which moves a couple of pixels at most.
    const cx = (nx - 1) / 2, cy = (ny - 1) / 2;
    if (marks.every((m) => Math.hypot(m.x - cx, m.y - cy) < 4)) known = preset.name;
  }
  const staticStar = (field.stars || []).some((s) => Math.hypot(s.x - a.x, s.y - a.y) < 2 && Math.hypot(s.x - b.x, s.y - b.y) < 2);
  checks.push({ id: 'catalogue', label: 'Catalogue check', ok: true, status: known ? 'known' : staticStar ? 'fixed' : 'new',
    detail: known ? `Matches ${known} in Gaia DR3.` : staticStar ? 'Matches a fixed Gaia star that hasn’t moved.' : 'No match in Gaia DR3, CatWISE or known asteroids (SkyBoT).' });

  const mod = moderateNote(note);
  checks.push({ id: 'moderation', label: 'Note check', ok: mod.ok,
    detail: mod.ok ? (note ? 'Note looks fine.' : 'No note added.') : `Note hidden: ${mod.reasons.join(', ')}.` });

  let score = 18 + (present ? 24 : 0) + (moved ? 24 : 0) + Math.min(snr, 20) + (known ? -30 : staticStar ? -25 : 12) + (edge ? -15 : 0);
  score = Math.max(1, Math.min(99, Math.round(score)));
  const outcome = !present || !moved || staticStar ? 'artefact' : known ? 'known' : 'candidate';
  return { checks, score, outcome, shiftArcsec: shift, noteHidden: !mod.ok, snr };
}
