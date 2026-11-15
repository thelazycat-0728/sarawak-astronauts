import { useEffect, useRef, useState } from 'react';
import { Sparkles, Send, BookOpen, LogIn, ShieldCheck, UserRound } from 'lucide-react';
import { Modal, Sheet, Button, Chip, Segmented } from './ui';
import { useUi, useAuth, useViewer } from '../store';
import { explain } from '../lib/api';
import { PRESET_BY_ID, ARRAYS } from '../lib/data';
import { cn } from '../lib/util';
import { DEMO_ACCOUNTS } from '../lib/accounts';

const FIELD = 'h-10 rounded-md border border-line-strong bg-ink-900 px-3 outline-none focus:border-accent';

function Field({ id, label, ...props }) {
  return (
    <label htmlFor={id} className="grid gap-1.5 text-sm">
      <span className="label-mono text-muted">{label}</span>
      <input id={id} className={FIELD} {...props} />
    </label>
  );
}

/** Sign in, create an account, or use a fixed demo account (lib/accounts.js). */
export function SignInDialog() {
  const open = useUi((s) => s.signInOpen);
  const setOpen = useUi((s) => s.setSignIn);
  const toast = useUi((s) => s.toast);
  const { signIn, signUp, signInDemo } = useAuth();
  const [tab, setTab] = useState('signin');
  const [form, setForm] = useState({ username: '', name: '', password: '' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const change = (k) => (e) => { setForm((f) => ({ ...f, [k]: e.target.value })); setError(''); };
  const done = (msg) => { toast(msg, 'good'); setOpen(false); setForm({ username: '', name: '', password: '' }); setError(''); };

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    const err = tab === 'signin' ? await signIn(form.username, form.password) : await signUp(form);
    setBusy(false);
    if (err) setError(err);
    else done(tab === 'signin' ? 'Signed in.' : 'Account created. You’re signed in.');
  };
  const demo = (a) => { signInDemo(a.username); done(`Signed in as ${a.name}.`); };

  return (
    <Modal open={open} onOpenChange={setOpen} title="Sign in to StarXplorer" className="max-w-lg"
      description="An account lets you submit discoveries, earn points and build your reputation. Exploring and Learn work without one.">
      <div className="grid gap-5">
        <section aria-labelledby="demo-accounts" className="rounded-lg border border-accent/40 bg-accent-soft/40 p-4">
          <h3 id="demo-accounts" className="label-mono text-accent-fg">Demo accounts</h3>
          <div className="mt-3 grid gap-2">
            {DEMO_ACCOUNTS.map((a) => (
              <div key={a.id} className="flex flex-wrap items-center gap-3 rounded-md border border-line bg-ink-900 p-3">
                <div className="min-w-0 flex-1 text-sm">
                  <p className="flex flex-wrap items-center gap-2 font-medium">
                    {a.role === 'scientist' ? <ShieldCheck className="size-4 text-teal" /> : <UserRound className="size-4 text-accent-fg" />}
                    {a.role === 'scientist' ? 'Scientist' : 'Volunteer'}
                    <span className="font-mono text-xs font-normal text-muted">{a.username} / {a.password}</span>
                  </p>
                  <p className="mt-0.5 text-xs text-muted">{a.blurb}</p>
                </div>
                <Button size="sm" variant="primary" onClick={() => demo(a)}>Use this account</Button>
              </div>
            ))}
          </div>
        </section>

        <Segmented value={tab} onChange={(t) => { setTab(t); setError(''); }} label="Account" className="justify-self-start"
          options={[{ value: 'signin', label: 'Sign in' }, { value: 'signup', label: 'Create account' }]} />
        <form onSubmit={submit} className="grid gap-4">
          {tab === 'signup' && <Field id="signin-name" label="Display name" value={form.name} onChange={change('name')} placeholder="e.g. Stargazer from Kuching" autoComplete="nickname" />}
          <Field id="signin-username" label="Username" required value={form.username} onChange={change('username')} placeholder={tab === 'signup' ? 'e.g. stargazer_kch' : 'Your username'} autoComplete="username" autoCapitalize="none" />
          <Field id="signin-password" label="Password" type="password" required value={form.password} onChange={change('password')} autoComplete={tab === 'signup' ? 'new-password' : 'current-password'} />
          {error && <p className="text-sm text-bad">{error}</p>}
          <Button type="submit" variant="primary" disabled={busy}><LogIn className="size-4" />{tab === 'signin' ? 'Sign in' : 'Create account'}</Button>
          <p className="text-xs leading-relaxed text-faint">Accounts are kept in this browser only; there is no server. Don’t reuse a password from another site.</p>
        </form>
      </div>
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
    const answer = await explain(question, { preset, mode: v.mode, arrayIdx: v.arrayIdx, fov: v.fov, ra: v.ra, dec: v.dec });
    setMessages((m) => [...m, { role: 'ai', text: answer.text, ai: answer.ai }]);
    setBusy(false);
  };

  const arr = ARRAYS[v.arrayIdx - 1];
  return (
    <Sheet open={open} onOpenChange={setOpen} title="Ask StarXplorer" description="Plain-language answers about what’s on screen.">
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
            {m.role === 'ai' && (m.ai
              ? <Chip tone="accent" className="mb-2"><Sparkles className="size-3" />AI-generated</Chip>
              : <Chip className="mb-2"><BookOpen className="size-3" />Offline answer</Chip>)}
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
      <p className="px-5 pb-4 text-[11.5px] text-faint">AI answers come from Google Gemini, using our glossary and this view’s details, and can be wrong: check important facts. Questions are sent to Google and may be used to improve its products, so don’t include personal details.</p>
    </Sheet>
  );
}
