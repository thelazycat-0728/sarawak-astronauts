import { useNavigate } from 'react-router';
import { LogIn, LogOut, Trophy, Bookmark, Trash2, ShieldCheck, Award } from 'lucide-react';
import { Button, Kicker, Panel, Chip } from '../components/ui';
import { useAuth, useUi, useProgress, useViewer } from '../store';
import { rankFor, RANKS, LESSONS, LEVELS } from '../lib/data';
import { ReputationCard } from '../components/Reputation';
import { useMySubmissions } from '../lib/reputationHooks';
import { cn } from '../lib/util';

const STATUS = {
  'in-review': { label: 'In review', tone: 'accent' },
  'auto-resolved': { label: 'Resolved automatically', tone: 'default' },
  reviewed: { label: 'Reviewed', tone: 'teal' },
};
const VERDICT = { confirmed: ['Confirmed by a scientist', 'good'], known: ['Known object', 'accent'], artefact: ['Artefact', 'warn'], rejected: ['Rejected', 'bad'] };

export default function Profile() {
  const user = useAuth((s) => s.user);
  const signOut = useAuth((s) => s.signOut);
  const setSignIn = useUi((s) => s.setSignIn);
  const { lessons, games, savedViews, removeView } = useProgress();
  const mine = useMySubmissions();
  const navigate = useNavigate();

  const lessonsDone = LESSONS.filter((l) => lessons[l.id]).length;
  const levelsDone = LEVELS.filter((l) => games[l.id]?.stars > 0).length;

  const badges = [
    { name: 'First steps', got: lessonsDone >= 1, text: 'Finished a lesson' },
    { name: 'Trained eye', got: lessonsDone === LESSONS.length, text: 'Finished every lesson' },
    { name: 'Speed spotter', got: (games.barnard?.stars ?? 0) === 3, text: '3 stars on Barnard’s Star' },
    { name: 'Cold finder', got: (games.wise0855?.stars ?? 0) > 0, text: 'Found WISE 0855' },
    { name: 'First report', got: mine.length > 0, text: 'Submitted a finding' },
    { name: 'Discoverer', got: mine.some((m) => m.verdict === 'confirmed'), text: 'A finding was confirmed' },
  ];

  const openView = (v) => {
    const s = useViewer.getState();
    if (v.targetId) s.loadPreset(v.targetId); else s.setField(v.ra, v.dec);
    s.setFov(v.fov); s.setArray(v.arrayIdx); s.setMode(v.mode);
    navigate('/');
  };

  if (!user) {
    return (
      <div className="mx-auto max-w-xl px-[max(16px,env(safe-area-inset-left,0px))] py-20 text-center">
        <h1 className="text-3xl font-medium tracking-tight">Your progress</h1>
        <p className="mt-3 text-muted">As a guest you’ve finished {lessonsDone} lessons and {levelsDone} game levels, saved in this browser only. Sign in to submit discoveries, earn points and build your reputation. The demo accounts in the sign-in window get you in straight away.</p>
        <Button variant="primary" className="mt-6" onClick={() => setSignIn(true)}><LogIn className="size-4" />Sign in</Button>
      </div>
    );
  }

  const rank = rankFor(user.points);
  const pct = rank.next ? ((user.points - rank.min) / (rank.next.min - rank.min)) * 100 : 100;

  return (
    <div className="mx-auto max-w-[1280px] px-[max(16px,env(safe-area-inset-left,0px))] py-10 lg:px-10">
      <Kicker>Profile</Kicker>
      <div className="mt-6 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <Panel className="flex flex-wrap items-center gap-5 p-6">
          <span className="grid size-16 place-items-center rounded-xl bg-accent-soft text-2xl font-semibold text-accent-fg">{user.name[0]?.toUpperCase()}</span>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-2xl font-medium">{user.name}</h1>
            <p className="truncate font-mono text-sm text-muted">@{user.username}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <Chip tone={user.role === 'scientist' ? 'teal' : 'accent'}>{user.role === 'scientist' ? <><ShieldCheck className="size-3" />Scientist</> : 'Volunteer'}</Chip>
              <Chip>Joined {new Date(user.joined).toLocaleDateString('en-GB', { month: 'short', year: 'numeric' })}</Chip>
            </div>
          </div>
        </Panel>
        <Panel className="p-6">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="label-mono text-muted">Rank</p>
              <p className="mt-1 flex items-center gap-2 text-3xl font-medium"><Trophy className="size-6 text-amber" />{rank.name}</p>
            </div>
            <p className="tabular text-right"><span className="text-3xl font-medium">{user.points}</span> <span className="text-muted">points</span></p>
          </div>
          <div className="mt-4 h-2 overflow-hidden rounded-full bg-ink-700"><div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} /></div>
          <div className="label-mono mt-2 flex justify-between text-faint">
            {RANKS.map((r) => <span key={r.name} className={cn(r.name === rank.name && 'text-fg')}>{r.name}</span>)}
          </div>
          <p className="mt-3 text-sm text-muted">{rank.next ? `${rank.next.min - user.points} points to ${rank.next.name}.` : 'Top rank reached.'} Relevant submissions earn 2 points; a confirmed finding earns 50.</p>
        </Panel>
      </div>

      <h2 className="label-mono mt-10 mb-3 text-muted">Reputation</h2>
      <ReputationCard mine={mine} />

      <h2 className="label-mono mt-10 mb-3 text-muted">Badges</h2>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {badges.map((b) => (
          <Panel key={b.name} className={cn('p-4', !b.got && 'opacity-45')}>
            <Award className={cn('size-6', b.got ? 'text-amber' : 'text-faint')} />
            <p className="mt-2 font-medium">{b.name}</p>
            <p className="text-xs text-muted">{b.text}</p>
          </Panel>
        ))}
      </div>

      <h2 className="label-mono mt-10 mb-3 text-muted">My submissions</h2>
      <Panel className="overflow-x-auto">
        {mine.length === 0 ? (
          <div className="flex flex-wrap items-center justify-between gap-3 p-6 text-sm text-muted">
            No submissions yet. Find something in Explore, then submit it in Discover.
            <Button size="sm" variant="primary" onClick={() => navigate('/discover')}>Open Discover</Button>
          </div>
        ) : (
          <table className="w-full min-w-[640px] text-left text-sm tabular">
            <thead className="label-mono text-faint"><tr><th className="px-5 py-3 font-normal">Code</th><th className="font-normal">Field</th><th className="font-normal">Submitted</th><th className="font-normal">AI score</th><th className="pr-5 font-normal">Status</th></tr></thead>
            <tbody className="divide-y divide-line">
              {mine.map((m) => {
                const [vl, vt] = m.verdict ? VERDICT[m.verdict] : [];
                const st = STATUS[m.status];
                return (
                  <tr key={m.id}>
                    <td className="px-5 py-3 font-mono">{m.code}</td>
                    <td>{m.fieldName}</td>
                    <td className="text-muted">{new Date(m.at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}</td>
                    <td>{m.score}</td>
                    <td className="pr-5"><Chip tone={vt ?? st.tone}>{vl ?? (m.outcome === 'known' ? 'Known object' : m.outcome === 'artefact' ? 'Not a mover' : st.label)}</Chip></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>

      <div className="mt-10 grid gap-6 lg:grid-cols-2">
        <div>
          <h2 className="label-mono mb-3 text-muted">Saved views</h2>
          <Panel className="divide-y divide-line">
            {savedViews.length === 0 && <p className="p-5 text-sm text-muted">Use the bookmark button in Explore to save a view.</p>}
            {savedViews.map((v) => (
              <div key={v.id} className="flex items-center gap-3 px-5 py-3">
                <Bookmark className="size-4 text-accent-fg" />
                <button type="button" onClick={() => openView(v)} className="min-w-0 flex-1 truncate text-left text-sm hover:text-accent-fg">{v.name} · array {v.arrayIdx} · {v.fov}′ · {v.mode}</button>
                <Button size="iconSm" variant="ghost" aria-label="Remove saved view" onClick={() => removeView(v.id)}><Trash2 className="size-4" /></Button>
              </div>
            ))}
          </Panel>
        </div>
        <div>
          <h2 className="label-mono mb-3 text-muted">Points history</h2>
          <Panel className="max-h-64 divide-y divide-line overflow-y-auto">
            {user.events.length === 0 && <p className="p-5 text-sm text-muted">Earn points through training games and submissions.</p>}
            {user.events.map((e, i) => (
              <div key={i} className="flex justify-between gap-3 px-5 py-3 text-sm"><span className="truncate">{e.reason}</span><span className="tabular text-good">+{e.n}</span></div>
            ))}
          </Panel>
        </div>
      </div>

      <Panel className="mt-10 flex flex-wrap items-center gap-4 p-5">
        <p className="text-sm text-muted">Signed in as <span className="font-mono text-fg">{user.username}</span>{user.id.startsWith('demo-') ? ' (demo account)' : ''}.</p>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={() => { signOut(); navigate('/'); }}><LogOut className="size-4" />Sign out</Button>
      </Panel>
    </div>
  );
}
