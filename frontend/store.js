import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { safeStorage, uid } from './lib/util';
import { PRESET_BY_ID } from './lib/data';

const reducedMotion = () => {
  try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; }
};

/** What the sky viewer is showing. Not persisted: shared links carry it instead. */
export const useViewer = create((set) => ({
  targetId: 'barnard',
  ra: PRESET_BY_ID.barnard.raStr,
  dec: PRESET_BY_ID.barnard.decStr,
  fov: 6,
  arrayIdx: 3,
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
    { name: 'skyshift-ui', storage: safeStorage, partialize: (s) => ({ expert: s.expert }) },
  ),
);

/** Demo accounts. With Supabase configured, setFromSupabase() fills this from the real session. */
export const useAuth = create(
  persist(
    (set, get) => ({
      user: null,
      signIn: ({ name, email }) => set({
        user: { id: uid(), name, email, role: 'volunteer', points: 0, events: [], joined: new Date().toISOString() },
      }),
      setFromSupabase: (u) => set((s) => ({
        user: u ? { ...(s.user ?? { points: 0, events: [], role: 'volunteer' }), id: u.id, email: u.email, name: u.user_metadata?.name ?? u.email.split('@')[0] } : null,
      })),
      signOut: () => set({ user: null }),
      setRole: (role) => set((s) => (s.user ? { user: { ...s.user, role } } : s)),
      addPoints: (n, reason) => {
        const u = get().user;
        if (!u) return;
        set({ user: { ...u, points: u.points + n, events: [{ n, reason, at: new Date().toISOString() }, ...u.events].slice(0, 50) } });
      },
    }),
    { name: 'skyshift-auth', storage: safeStorage },
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
    { name: 'skyshift-progress', storage: safeStorage },
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
    { name: 'skyshift-submissions', storage: safeStorage },
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
