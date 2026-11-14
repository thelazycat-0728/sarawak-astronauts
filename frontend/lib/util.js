import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { createJSONStorage } from 'zustand/middleware';

export const cn = (...args) => twMerge(clsx(args));

// localStorage that never throws (private windows, blocked storage).
const memory = new Map();
const safe = {
  getItem: (k) => { try { return window.localStorage.getItem(k); } catch { return memory.get(k) ?? null; } },
  setItem: (k, v) => { try { window.localStorage.setItem(k, v); } catch { memory.set(k, v); } },
  removeItem: (k) => { try { window.localStorage.removeItem(k); } catch { memory.delete(k); } },
};
export const safeStorage = createJSONStorage(() => safe);

// ---------------------------------------------------------------- random
export function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
export function gauss(rng) {
  let u = 0, v = 0;
  while (u === 0) u = rng();
  while (v === 0) v = rng();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}
export function hashString(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}

// ---------------------------------------------------------------- coordinates
const pad = (n) => String(n).padStart(2, '0');
const clean = (s) => String(s ?? '').trim().replace(/[−–—]/g, '-');

/** Right ascension from decimal degrees or h m s ("17h57m48.5s", "17 57 48.5", "17:57:48.5"). */
export function parseRA(input) {
  const s = clean(input);
  if (!s) return null;
  if (/^\+?\d+(\.\d+)?$/.test(s)) { const v = parseFloat(s); return v >= 0 && v < 360 ? v : null; }
  const parts = s.replace(/[hHmMsS:°′″'"]/g, ' ').trim().split(/\s+/).map(Number);
  if (parts.length < 2 || parts.length > 3 || parts.some((x) => Number.isNaN(x))) return null;
  const [h, m, sec = 0] = parts;
  if (h < 0 || h >= 24 || m < 0 || m >= 60 || sec < 0 || sec >= 60) return null;
  return (h + m / 60 + sec / 3600) * 15;
}

/** Declination from decimal degrees or d m s ("+04°41′36.2″", "-7d14m42.53s", "-07 14 42.5"). */
export function parseDec(input) {
  const s = clean(input);
  if (!s) return null;
  if (/^[+-]?\d+(\.\d+)?$/.test(s)) { const v = parseFloat(s); return v >= -90 && v <= 90 ? v : null; }
  const sign = s.startsWith('-') ? -1 : 1;
  const parts = s.replace(/^[+-]/, '').replace(/[dD:°′″'"mMsS]/g, ' ').trim().split(/\s+/).map(Number);
  if (parts.length < 2 || parts.length > 3 || parts.some((x) => Number.isNaN(x))) return null;
  const [d, m, sec = 0] = parts;
  if (d < 0 || d > 90 || m < 0 || m >= 60 || sec < 0 || sec >= 60) return null;
  const v = sign * (d + m / 60 + sec / 3600);
  return Math.abs(v) <= 90 ? v : null;
}

export function formatRA(deg) {
  let t = Math.round((deg / 15) * 3600 * 100);
  const h = Math.floor(t / 360000); t -= h * 360000;
  const m = Math.floor(t / 6000); t -= m * 6000;
  return `${pad(h)}h${pad(m)}m${(t / 100).toFixed(2).padStart(5, '0')}s`;
}
export function formatDec(deg) {
  const sign = deg < 0 ? '−' : '+';
  let t = Math.round(Math.abs(deg) * 3600 * 10);
  const d = Math.floor(t / 36000); t -= d * 36000;
  const m = Math.floor(t / 600); t -= m * 600;
  return `${sign}${pad(d)}°${pad(m)}′${(t / 10).toFixed(1).padStart(4, '0')}″`;
}

export function formatDate(iso, opts = { day: 'numeric', month: 'short', year: 'numeric' }) {
  return new Date(iso + 'T00:00:00Z').toLocaleDateString('en-GB', { ...opts, timeZone: 'UTC' });
}

export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
export const uid = () => Math.random().toString(36).slice(2, 10);
