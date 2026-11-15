import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { safeStorage, uid } from './lib/util';
import { PRESET_BY_ID } from './lib/data';
import { DEMO_BY_USERNAME, demoRecord, hashPassword, USERNAME_RE, MIN_PASSWORD } from './lib/accounts';

const reducedMotion = () => {
  try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; }
};

/** What the sky viewer is showing. Not persisted: shared links carry it instead. */
export const useViewer = create((set) => ({
  targetId: 'barnard',
  ra: PRESET_BY_ID.barnard.raStr,
  dec: PRESET_BY_ID.barnard.decStr,
  fov: 6,
  arrayIdx: PRESET_BY_ID.barnard.defaultArray,
  channel: null,
  mode: 'blink',
  epochIdx: 0,
  included: null, // null = every snapshot included
  playing: !reducedMotion(),
  speed: 1,
  overlays: { gaia: false, wise: false, track: true },
  lessonId: null,

  loadPreset: (id, extra = {}) => {
    const p = PRESET_BY_ID[id];
    set({
      targetId: id, ra: p.raStr, dec: p.decStr, arrayIdx: p.defaultArray, channel: null,
      epochIdx: 0, included: null, mode: 'blink', playing: !reducedMotion(), lessonId: null, ...extra,
    });
  },
  setField: (ra, dec) => set({ targetId: null, ra, dec, epochIdx: 0, included: null, playing: !reducedMotion(), lessonId: null }),
  setFov: (fov) => set({ fov }),
  setArray: (arrayIdx) => set({ arrayIdx, channel: null }),
  setChannel: (channel) => set({ channel }),
  setMode: (mode) => set({ mode }),
  setEpoch: (epochIdx) => set({ epochIdx }),
  setIncluded: (included) => set({ included }),
  setPlaying: (playing) => set({ playing }),
  setSpeed: (speed) => set({ speed }),
  toggleOverlay: (k) => set((s) => ({ overlays: { ...s.overlays, [k]: !s.overlays[k] } })),
  setLesson: (lessonId) => set({ lessonId }),
}));

export const useUi = create(
  persist(
    (set) => ({
      expert: false,
      setExpert: (expert) => set({ expert }),
      assistantOpen: false,
      setAssistant: (assistantOpen) => set({ assistantOpen }),
      signInOpen: false,
      setSignIn: (signInOpen) => set({ signInOpen }),
      toasts: [],
      toast: (msg, kind = 'info') => {
        const id = uid();
        set((s) => ({ toasts: [...s.toasts, { id, msg, kind }] }));
        setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 3600);
      },
    }),
    { name: 'starxplorer-ui', storage: safeStorage, partialize: (s) => ({ expert: s.expert }) },
  ),
);

/**
 * Accounts, hard-coded (lib/accounts.js): no server. `accounts` holds every account used
 * in this browser (the two demo accounts once signed into, plus any created here), keyed
 * by id; `user` is the signed-in one. Each keeps its own points.
 */
export const useAuth = create(
  persist(
    (set, get) => {
      const login = (account) => set((s) => ({ accounts: { ...s.accounts, [account.id]: account }, user: account }));
      return {
        accounts: {},
        user: null,

        /** Signs in with a username and password. Returns an error message, or null. */
        signIn: async (username, password) => {
          const name = username.trim().toLowerCase();
          const demo = DEMO_BY_USERNAME[name];
          if (demo) {
            if (password !== demo.password) return 'Wrong password for this demo account.';
            login(get().accounts[demo.id] ?? demoRecord(demo));
            return null;
          }
          const account = Object.values(get().accounts).find((a) => a.username === name && a.passwordHash);
          if (!account || account.passwordHash !== await hashPassword(name, password)) return 'Wrong username or password.';
          login(account);
          return null;
        },
        /** One-click sign-in to a fixed demo account ('volunteer' or 'scientist'). */
        signInDemo: (username) => {
          const demo = DEMO_BY_USERNAME[username];
          login(get().accounts[demo.id] ?? demoRecord(demo));
        },
        /** Creates a volunteer account in this browser. Returns an error message, or null. */
        signUp: async ({ username, name, password }) => {
          const u = username.trim().toLowerCase();
          if (!USERNAME_RE.test(u)) return 'Usernames are 3–20 characters: letters, numbers, dots and underscores.';
          if (DEMO_BY_USERNAME[u] || Object.values(get().accounts).some((a) => a.username === u)) return 'That username is taken.';
          if (password.length < MIN_PASSWORD) return `Passwords need at least ${MIN_PASSWORD} characters.`;
          login({
            id: uid(), username: u, name: name.trim() || u, role: 'volunteer', points: 0, events: [],
            joined: new Date().toISOString(), passwordHash: await hashPassword(u, password),
          });
          return null;
        },
        signOut: () => set({ user: null }),
        /** Adds points to an account: the signed-in one unless `userId` is given. */
        addPoints: (n, reason, userId = get().user?.id) => {
          const a = get().accounts[userId];
          if (!a) return;
          const next = { ...a, points: a.points + n, events: [{ n, reason, at: new Date().toISOString() }, ...a.events].slice(0, 50) };
          set((s) => ({ accounts: { ...s.accounts, [a.id]: next }, user: s.user?.id === a.id ? next : s.user }));
        },
      };
    },
    {
      name: 'starxplorer-auth', storage: safeStorage, version: 2,
      // Version 1 kept a single email-based account; it can't sign in any more.
      migrate: () => ({ accounts: {}, user: null }),
      partialize: (s) => ({ accounts: s.accounts, user: s.user }),
    },
  ),
);

/** Training progress and saved views. Guests keep this in their browser only. */
export const useProgress = create(
  persist(
    (set) => ({
      lessons: {},
      games: {},
      savedViews: [],
      completeLesson: (id) => set((s) => ({ lessons: { ...s.lessons, [id]: true } })),
      recordGame: (id, result) => set((s) => {
        const prev = s.games[id];
        if (prev && prev.points >= result.points) return s;
        return { games: { ...s.games, [id]: result } };
      }),
      saveView: (view) => set((s) => ({ savedViews: [{ ...view, id: uid(), at: new Date().toISOString() }, ...s.savedViews].slice(0, 20) })),
      removeView: (id) => set((s) => ({ savedViews: s.savedViews.filter((v) => v.id !== id) })),
    }),
    { name: 'starxplorer-progress', storage: safeStorage },
  ),
);

export const useSubmissions = create(
  persist(
    (set) => ({
      items: [],
      demoVerdicts: {},
      add: (item) => set((s) => ({ items: [item, ...s.items] })),
      review: (id, verdict) => set((s) => ({ items: s.items.map((it) => (it.id === id ? { ...it, verdict, status: 'reviewed' } : it)) })),
      reviewDemo: (id, verdict) => set((s) => ({ demoVerdicts: { ...s.demoVerdicts, [id]: verdict } })),
    }),
    { name: 'starxplorer-submissions', storage: safeStorage },
  ),
);

export function shareUrl(v) {
  const q = new URLSearchParams();
  if (v.targetId) q.set('target', v.targetId);
  else { q.set('ra', v.ra); q.set('dec', v.dec); }
  q.set('fov', v.fov);
  q.set('array', v.arrayIdx);
  q.set('mode', v.mode);
  return `${window.location.origin}/?${q.toString()}`;
}
