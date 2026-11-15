// Hard-coded account system: no server or database. Two fixed demo accounts let the
// judging panel in straight away; anyone can also create an account, which is kept in
// this browser only (store.js, useAuth).
//
// The demo credentials are public on purpose: they are shown in the sign-in window and
// end up in the built JavaScript. Never reuse them for anything real.

export const DEMO_ACCOUNTS = [
  {
    id: 'demo-volunteer', username: 'volunteer', password: 'starxplorer', role: 'volunteer',
    name: 'Demo Volunteer', points: 24, trained: true, joined: '2026-11-01T00:00:00.000Z',
    blurb: 'Training already finished, so you can submit discoveries in Discover right away.',
  },
  {
    id: 'demo-scientist', username: 'scientist', password: 'starxplorer', role: 'scientist',
    name: 'Demo Scientist', points: 0, trained: true, joined: '2026-11-01T00:00:00.000Z',
    blurb: 'Opens the scientists’ review queue to confirm or reject candidates.',
  },
];

export const DEMO_BY_USERNAME = Object.fromEntries(DEMO_ACCOUNTS.map((a) => [a.username, a]));

/** A fresh stored account for a demo login: everything except the password. */
export function demoRecord(demo) {
  const { id, username, role, name, points, trained, joined } = demo;
  return { id, username, role, name, points, trained, joined, events: [] };
}

export const USERNAME_RE = /^[a-z0-9_.]{3,20}$/;
export const MIN_PASSWORD = 6;

/**
 * Password hash for accounts created in the browser, so the password itself is never
 * stored. SHA-256 where the browser offers it (https or localhost); a simple FNV-1a
 * hash otherwise. Salted with the username.
 */
export async function hashPassword(username, password) {
  const text = `starxplorer:${username}:${password}`;
  if (globalThis.crypto?.subtle) {
    const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    return `sha256:${Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, '0')).join('')}`;
  }
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return `fnv:${(h >>> 0).toString(16)}`;
}
