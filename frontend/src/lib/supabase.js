// Optional Supabase auth. Without VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY the
// app uses a browser-only account instead.
import { useEffect } from 'react';
import { useAuth } from '../store';

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL;
const SUPABASE_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY;
export const HAS_SUPABASE = Boolean(SUPABASE_URL && SUPABASE_KEY);

let clientPromise = null;
export function supabase() {
  if (!HAS_SUPABASE) return null;
  clientPromise ??= import('@supabase/supabase-js').then(({ createClient }) => createClient(SUPABASE_URL, SUPABASE_KEY));
  return clientPromise;
}

/** Keeps the local user in step with a real Supabase session, when configured. */
export function useSupabaseSession() {
  useEffect(() => {
    const p = supabase();
    if (!p) return undefined;
    let sub;
    p.then((client) => {
      client.auth.getSession().then(({ data }) => useAuth.getState().setFromSupabase(data.session?.user ?? null));
      sub = client.auth.onAuthStateChange((_e, session) => useAuth.getState().setFromSupabase(session?.user ?? null)).data.subscription;
    });
    return () => sub?.unsubscribe();
  }, []);
}
