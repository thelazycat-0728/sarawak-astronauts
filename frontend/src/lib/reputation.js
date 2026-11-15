// Submitter reputation, worked out from a user's own submission history.
//
// Relevant finds raise it; submissions that fail the automatic checks or that a
// scientist rejects lower it. The tier it lands in sets how long a user waits between
// submissions and how far up the scientists' review queue their candidates rank.
//
// Everything here runs in the browser for now (submissions live in localStorage), so
// it guides honest users but can be reset by clearing site data. To enforce it, the
// same rules belong in the database alongside the submissions.

export const REP_START = 50;
const REP_MAX = 100;

/** Change from the automatic checks, by outcome. */
const AUTO_DELTA = { candidate: 3, known: 1, artefact: -8 };
/** Change from a scientist's verdict, on top of the automatic one. */
const VERDICT_DELTA = { confirmed: 15, known: 2, artefact: -6, rejected: -10 };

/** Highest tier first. weight scales the AI priority score in the review queue. */
export const TIERS = [
  { id: 'trusted', name: 'Trusted', min: 75, cooldownMin: 1, weight: 1.15, tone: 'good',
    text: 'Your finds are consistently relevant. Your candidates rank higher in the review queue.' },
  { id: 'good', name: 'Good standing', min: 45, cooldownMin: 5, weight: 1, tone: 'accent',
    text: 'Your candidates are reviewed at their normal priority.' },
  { id: 'probation', name: 'On probation', min: 20, cooldownMin: 30, weight: 0.6, tone: 'warn',
    text: 'Several recent submissions weren’t relevant. Your candidates rank lower and you wait longer between submissions.' },
  { id: 'low', name: 'Low', min: 0, cooldownMin: 120, weight: 0.3, tone: 'bad',
    text: 'Most submissions weren’t relevant. Your candidates are rarely reviewed until your reputation recovers. Revisit the lessons in Learn.' },
];

export const tierFor = (score) => TIERS.find((t) => score >= t.min) ?? TIERS[TIERS.length - 1];

const clamp = (x) => Math.max(0, Math.min(REP_MAX, x));

/** True when a submission was made by this user (older items carry only the display name). */
export function isAuthor(item, user) {
  if (!user) return false;
  return item.userId ? item.userId === user.id : item.by === user.name;
}

/** Key that groups submissions by author, for the review queue. */
export const authorKey = (item) => item.userId ?? `name:${item.by}`;

/**
 * Reputation from a user's submissions: { score, tier, history }. history lists each
 * change, newest first, as { code, at, delta, reason }.
 */
export function reputationFor(items) {
  const ordered = [...items].sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  let score = REP_START;
  const history = [];
  for (const it of ordered) {
    const auto = AUTO_DELTA[it.outcome] ?? 0;
    if (auto) {
      score = clamp(score + auto);
      history.push({ code: it.code, at: it.at, delta: auto, reason: AUTO_REASON[it.outcome] });
    }
    const verdict = VERDICT_DELTA[it.verdict] ?? 0;
    if (verdict) {
      score = clamp(score + verdict);
      history.push({ code: it.code, at: it.at, delta: verdict, reason: VERDICT_REASON[it.verdict] });
    }
  }
  return { score, tier: tierFor(score), history: history.reverse() };
}

const AUTO_REASON = {
  candidate: 'Passed the automatic checks',
  known: 'Matched a known object',
  artefact: 'Failed the automatic checks',
};
const VERDICT_REASON = {
  confirmed: 'Confirmed by a scientist',
  known: 'Scientist: known object',
  artefact: 'Scientist: artefact',
  rejected: 'Rejected by a scientist',
};

/**
 * Wait before the next submission: { tier, until, remainingMs }. until is null when
 * the user has never submitted.
 */
export function cooldownFor(items, now = Date.now()) {
  const { tier } = reputationFor(items);
  const last = items.reduce((t, it) => Math.max(t, Date.parse(it.at) || 0), 0);
  if (!last) return { tier, until: null, remainingMs: 0 };
  const until = last + tier.cooldownMin * 60e3;
  return { tier, until, remainingMs: Math.max(0, until - now) };
}

/** Review-queue priority: the AI score scaled by the submitter's reputation tier. */
export function reviewPriority(ai, score) {
  return Math.max(1, Math.min(99, Math.round(ai * tierFor(score).weight)));
}

/** "4 min", "1 h 30 min", "45 s". */
export function formatWait(ms) {
  const s = Math.ceil(ms / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.ceil(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60), r = m % 60;
  return r ? `${h} h ${r} min` : `${h} h`;
}
