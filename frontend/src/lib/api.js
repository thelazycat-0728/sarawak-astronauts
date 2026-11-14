// Data access. With VITE_API_URL set, frames come from the Python API;
// otherwise the app runs on simulated demo frames with the same shape.
import { useEffect, useMemo, useState } from 'react';
import { simulate, aperture, PX } from './sim';
import { PRESET_BY_ID, SURVEYS, GLOSSARY, ARRAYS, DAY0, customObject } from './data';
import { hashString, parseRA, parseDec } from './util';

export const API_URL = import.meta.env.VITE_API_URL || '';
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
// Frames arrive as 8-bit asinh-stretched PNGs. Undoing the stretch gives linear
// brightness, scaled so it lands in the same units as the simulator (noise ≈ 0.5),
// which keeps Blink / Difference / Stack and the submission checks working unchanged.
const STRETCH_ASINH_A = 0.03; // softening of the backend's asinh stretch (backend/app/providers/mock.py)
const LINEAR_SCALE = 100; // linear stretch fraction 0–1 → 0–100

async function getJson(url) {
  let r;
  try { r = await fetch(url); } catch { throw new Error('The data service could not be reached.'); }
  const body = await r.json().catch(() => null);
  if (!r.ok) throw new Error(body?.error?.message ? `The data service said: ${body.error.message}.` : `The data service answered ${r.status}.`);
  return body;
}

/** Fetches one frame PNG and returns its pixels as linear brightness (row-major, north up). */
async function loadFrame(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`A snapshot failed to load (${r.status}).`);
  const bmp = await createImageBitmap(await r.blob(), { colorSpaceConversion: 'none', premultiplyAlpha: 'none' });
  const canvas = document.createElement('canvas');
  canvas.width = bmp.width; canvas.height = bmp.height;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(bmp, 0, 0);
  bmp.close();
  const px = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  const k = Math.asinh(1 / STRETCH_ASINH_A);
  const out = new Float32Array(canvas.width * canvas.height);
  for (let i = 0; i < out.length; i++) out[i] = STRETCH_ASINH_A * Math.sinh((px[i * 4] / 255) * k) * LINEAR_SCALE;
  return { nx: canvas.width, ny: canvas.height, data: out };
}

/** Display range for the whole field, matching the simulator's choice. */
function displayRange(stacks) {
  const sample = [];
  for (const s of stacks) for (let i = 0; i < s.length; i += 2) sample.push(s[i]);
  sample.sort((a, b) => a - b);
  const q = (p) => sample[Math.min(sample.length - 1, Math.floor(p * sample.length))];
  const vmin = q(0.04);
  return { vmin, vmax: Math.max(q(0.997), vmin + 6) };
}

/**
 * GET /epochs, then every frame_url. Exposures from the same survey visit are
 * averaged into one snapshot, like the demo frames; the single exposures stay in `frames`.
 */
async function fetchField(p) {
  const q = new URLSearchParams({ ra: p.raDeg, dec: p.decDeg, fov: p.fov, band: `D${p.arrayIdx}` });
  const j = await getJson(`${API_URL}/epochs?${q}`);
  if (!j.count) throw new Error('No SPHEREx coverage at this position yet.');

  const shots = await Promise.all(j.epochs.map((e) => loadFrame(e.frame_url)));
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
    nx, ny, ...displayRange(stacks), fov: p.fov, arrayIdx: p.arrayIdx, channel: p.channel,
    epochs, stacks, frames, stars: [], object: null, posAt: () => null, visibleAt: () => false,
    live: true, synthetic: Boolean(j.synthetic),
  };
}

export function useField(params) {
  const key = JSON.stringify(params);
  const valid = params.raDeg != null && params.decDeg != null;
  const demo = useMemo(() => (valid && (!IS_LIVE || params.targetId) ? demoField(params) : null), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const [remote, setRemote] = useState({ key: null, data: null, error: null });

  useEffect(() => {
    if (!IS_LIVE || !valid || params.targetId) return undefined;
    let cancelled = false;
    fetchField(params)
      .then((data) => !cancelled && setRemote({ key, data, error: null }))
      .catch((e) => !cancelled && setRemote({ key, data: null, error: e.message }));
    return () => { cancelled = true; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!valid) return { data: null, loading: false, source: 'none' };
  // Presets use pre-computed frames so the demo never depends on a live query.
  if (!IS_LIVE || params.targetId) return { data: demo, loading: false, source: IS_LIVE ? 'precomputed' : 'demo' };
  if (remote.key !== key) return { data: null, loading: true, source: 'live' };
  if (remote.error) return { data: demoField(params), loading: false, source: 'demo', error: remote.error };
  return { data: remote.data, loading: false, source: 'live' };
}

// ---------------------------------------------------------------- AI explainer
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
          },
        }),
      });
      if (r.ok) return (await r.json()).answer;
    } catch { /* fall back to local answers */ }
  }
  await new Promise((res) => setTimeout(res, 450));
  return localExplain(question, context);
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
  if (/submit|discover|report|found/.test(s)) return 'Finish the training lessons, then use “Flag a candidate”. Mark the object in two snapshots, add a note, and the automatic checks run straight away. Promising candidates go to scientists for review.';
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

  const shift = Math.hypot(b.x - a.x, b.y - a.y) * PX;
  const moved = shift > 3;
  checks.push({ id: 'motion', label: 'Position changed', ok: moved,
    detail: moved ? `${shift.toFixed(1)}″ between ${epochs[a.epoch].label} and ${epochs[b.epoch].label}.` : `Only ${shift.toFixed(1)}″: within measurement error.` });

  let known = null;
  if (field.object && field.posAt) {
    const pa = field.posAt(a.epoch), pb = field.posAt(b.epoch);
    const near = pa && pb && Math.hypot(pa[0] - a.x, pa[1] - a.y) < 2.6 && Math.hypot(pb[0] - b.x, pb[1] - b.y) < 2.6;
    if (near && !field.object.mystery) known = preset?.name ?? field.object.name;
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
