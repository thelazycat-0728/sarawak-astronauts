import { useEffect, useRef, useState } from 'react';
import { Sparkles, Send, Mail } from 'lucide-react';
import { Modal, Sheet, Button, Chip } from './ui';
import { useUi, useAuth, useViewer } from '../store';
import { explain } from '../lib/api';
import { PRESET_BY_ID, ARRAYS } from '../lib/data';
import { cn } from '../lib/util';

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

export function SignInDialog() {
  const open = useUi((s) => s.signInOpen);
  const setOpen = useUi((s) => s.setSignIn);
  const toast = useUi((s) => s.toast);
  const signIn = useAuth((s) => s.signIn);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [error, setError] = useState('');

  const submit = async (e) => {
    e.preventDefault();
    if (!/^[\w.+-]+@[\w-]+\.[\w.]+$/.test(email)) { setError('Enter a valid email address.'); return; }
    if (HAS_SUPABASE) {
      const client = await supabase();
      const { error: err } = await client.auth.signInWithOtp({ email, options: { data: { name: name || email.split('@')[0] } } });
      if (err) { setError(err.message); return; }
      toast('Check your inbox for a sign-in link.', 'good');
    } else {
      signIn({ name: name.trim() || email.split('@')[0], email });
      toast('Signed in. Your progress now follows your account.', 'good');
    }
    setOpen(false);
  };
  const google = async () => {
    const client = await supabase();
    await client?.auth.signInWithOAuth({ provider: 'google' });
  };

  return (
    <Modal open={open} onOpenChange={setOpen} title="Sign in to SkyShift" description="An account lets you submit findings to scientists, earn points and keep progress across devices. Everything else works without one.">
      <form onSubmit={submit} className="grid gap-4">
        <label className="grid gap-1.5 text-sm">
          <span className="label-mono text-muted">Display name</span>
          <input id="signin-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. stargazer_kch" className="h-10 rounded-md border border-line-strong bg-ink-900 px-3 outline-none focus:border-accent" />
        </label>
        <label className="grid gap-1.5 text-sm">
          <span className="label-mono text-muted">Email</span>
          <input id="signin-email" type="email" required value={email} onChange={(e) => { setEmail(e.target.value); setError(''); }} placeholder="you@example.com" className="h-10 rounded-md border border-line-strong bg-ink-900 px-3 outline-none focus:border-accent" />
        </label>
        {error && <p className="text-sm text-bad">{error}</p>}
        <Button type="submit" variant="primary"><Mail className="size-4" />{HAS_SUPABASE ? 'Email me a sign-in link' : 'Sign in'}</Button>
        {HAS_SUPABASE && <Button variant="outline" onClick={google}>Continue with Google</Button>}
        <p className="text-xs leading-relaxed text-faint">
          {HAS_SUPABASE ? 'We store only your display name and email.' : 'Demo mode: Supabase isn’t configured, so this account lives in this browser only.'} See our privacy notice before submitting findings.
        </p>
      </form>
    </Modal>
  );
}

const SUGGESTIONS = ['What am I looking at?', 'Why does the star move?', 'What does Difference show?', 'What are contamination flags?'];

export function Assistant() {
  const open = useUi((s) => s.assistantOpen);
  const setOpen = useUi((s) => s.setAssistant);
  const v = useViewer();
  const preset = v.targetId ? PRESET_BY_ID[v.targetId] : null;
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const end = useRef(null);

  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }); }, [messages, busy]);

  const ask = async (q) => {
    const question = q.trim();
    if (!question || busy) return;
    setInput('');
    setMessages((m) => [...m, { role: 'user', text: question }]);
    setBusy(true);
    const answer = await explain(question, { preset, mode: v.mode, arrayIdx: v.arrayIdx, ra: v.ra, dec: v.dec });
    setMessages((m) => [...m, { role: 'ai', text: answer }]);
    setBusy(false);
  };

  const arr = ARRAYS[v.arrayIdx - 1];
  return (
    <Sheet open={open} onOpenChange={setOpen} title="Ask SkyShift" description="Plain-language answers about what’s on screen.">
      <div className="flex items-center gap-2 border-b border-line px-5 py-3 text-xs text-muted">
        <span className="label-mono">Looking at</span>
        <span className="truncate text-fg">{preset ? preset.name : `${v.ra} ${v.dec}`} · array {arr.n} · {v.mode}</span>
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
        {messages.length === 0 && (
          <div className="space-y-3">
            <p className="text-sm text-muted">Ask anything about the current view, or start with one of these:</p>
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" onClick={() => ask(s)} className="rounded-full border border-line-strong px-3 py-1.5 text-[13px] text-fg hover:border-accent">{s}</button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={cn('max-w-[90%] rounded-lg px-3.5 py-2.5 text-sm leading-relaxed', m.role === 'user' ? 'ml-auto bg-accent-soft text-fg' : 'border border-line bg-ink-900')}>
            {m.role === 'ai' && <Chip tone="accent" className="mb-2"><Sparkles className="size-3" />AI-generated</Chip>}
            <p>{m.text}</p>
          </div>
        ))}
        {busy && <p className="label-mono animate-pulse text-muted">Thinking…</p>}
        <div ref={end} />
      </div>
      <form onSubmit={(e) => { e.preventDefault(); ask(input); }} className="flex gap-2 border-t border-line p-4">
        <input id="assistant-input" value={input} onChange={(e) => setInput(e.target.value)} maxLength={300} placeholder="Ask about this view…" className="h-10 min-w-0 flex-1 rounded-md border border-line-strong bg-ink-900 px-3 text-sm outline-none focus:border-accent" />
        <Button type="submit" variant="primary" size="icon" aria-label="Send" disabled={!input.trim() || busy}><Send className="size-4" /></Button>
      </form>
      <p className="px-5 pb-4 text-[11.5px] text-faint">Answers are AI-generated from the glossary and this view’s metadata. Check important facts.</p>
    </Sheet>
  );
}
