// React hooks for the reputation UI (components/Reputation.jsx and the Play page).
import { useEffect, useMemo, useState } from 'react';
import { useAuth, useSubmissions } from '../store';
import { isAuthor } from './reputation';

/** The signed-in user's own submissions. */
export function useMySubmissions() {
  const user = useAuth((s) => s.user);
  const items = useSubmissions((s) => s.items);
  return useMemo(() => items.filter((it) => isAuthor(it, user)), [items, user]);
}

/** Current time, ticking every second while `active`. */
export function useNow(active) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}
