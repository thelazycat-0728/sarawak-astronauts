import { useCallback, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import { Flag, LogIn, Lock, CircleCheck, CircleX, CircleHelp, Send, RotateCcw, ArrowRight } from 'lucide-react';
import { Button, Kicker, Panel, Chip, SkyCanvas, Term } from '../components/ui';
import { useViewer, useAuth, useUi, useProgress, useSubmissions } from '../store';
import { useField, vet } from '../lib/api';
import { PRESET_BY_ID, LESSONS, LEVELS } from '../lib/data';
import { paintImage, lutForArray } from '../lib/sim';
import { parseRA, parseDec, formatRA, formatDec, formatDate, cn, uid } from '../lib/util';

const CHECKS = [
  'It appears in at least two snapshots.',
  'It isn’t a single bright pixel in one snapshot (a cosmic ray).',
  'I compared it with the catalogue overlays where I could.',
];

export default function Submit() {
  const user = useAuth((s) => s.user);
  const setSignIn = useUi((s) => s.setSignIn);
  const lessons = useProgress((s) => s.lessons);
  const games = useProgress((s) => s.games);
  const navigate = useNavigate();
  const trained = LESSONS.filter((l) => lessons[l.id]).length >= 3 && LEVELS.filter((l) => games[l.id]?.stars > 0).length >= 2;

  if (!user) {
    return (
      <Gate icon={LogIn} title="Sign in to submit findings" text="Exploring, lessons and games are open to everyone. Submitting to scientists needs an account, so your finds are credited to you and scientists can follow up.">
        <Button variant="primary" onClick={() => setSignIn(true)}><LogIn className="size-4" />Sign in</Button>
      </Gate>
    );
  }
  if (!trained) {
    return (
      <Gate icon={Lock} title="Finish your training first" text="Complete three lessons and two training-game levels. It takes about 15 minutes and teaches you to tell real movers from glitches, which keeps the review queue useful for scientists.">
        <Button variant="primary" onClick={() => navigate('/learn')}>Go to training<ArrowRight className="size-4" /></Button>
        <Button variant="ghost" onClick={() => {
          const p = useProgress.getState();
          LESSONS.slice(0, 3).forEach((l) => p.completeLesson(l.id));
          ['barnard', 'rwaur'].forEach((id) => p.recordGame(id, { points: 80, stars: 2 }));
        }}>Skip for this demo</Button>
      </Gate>
    );
  }
  return <SubmitForm />;
}

function Gate({ icon: Icon, title, text, children }) {
  return (
    <div className="mx-auto max-w-xl px-[max(16px,env(safe-area-inset-left,0px))] py-20 text-center">
      <span className="mx-auto grid size-12 place-items-center rounded-full border border-line-strong bg-ink-800"><Icon className="size-5 text-accent-fg" /></span>
      <h1 className="mt-5 text-3xl font-medium tracking-tight">{title}</h1>
      <p className="mt-3 text-muted">{text}</p>
      <div className="mt-6 flex flex-wrap justify-center gap-2">{children}</div>
    </div>
  );
}

function SubmitForm() {
  const v = useViewer();
  const user = useAuth((s) => s.user);
  const addPoints = useAuth((s) => s.addPoints);
  const add = useSubmissions((s) => s.add);
  const toast = useUi((s) => s.toast);
  const navigate = useNavigate();
  const preset = v.targetId ? PRESET_BY_ID[v.targetId] : null;
  const raDeg = parseRA(v.ra), decDeg = parseDec(v.dec);
  const { data: field } = useField({ targetId: v.targetId, raDeg, decDeg, fov: v.fov, arrayIdx: v.arrayIdx, channel: v.channel });

  const included = useMemo(() => {
    if (!field) return [];
    const inc = v.included && v.included.length === field.epochs.length ? v.included : field.epochs.map(() => true);
    return inc.map((on, i) => (on ? i : -1)).filter((i) => i >= 0);
  }, [field, v.included]);
  const ea = included[0], eb = included[included.length - 1];

  const [marks, setMarks] = useState({});
  const [checked, setChecked] = useState([false, false, false]);
  const [note, setNote] = useState('');
  const [result, setResult] = useState(null);

  if (!field || included.length < 2) {
    return <Gate icon={Flag} title="Pick a field first" text="Open a field in Explore with at least two snapshots ticked, then come back to flag what you found."><Button variant="primary" onClick={() => navigate('/')}>Open Explore</Button></Gate>;
  }

  const ready = marks[ea] && marks[eb] && checked.every(Boolean);
  const submit = () => {
    const r = vet({ marks: [{ epoch: ea, ...marks[ea] }, { epoch: eb, ...marks[eb] }], field, preset, note });
    const item = {
      id: uid(), code: `SX-${String(Math.floor(Math.random() * 9000) + 1000)}`, at: new Date().toISOString(),
      by: user.name, fieldName: preset?.name ?? 'Custom field', targetId: v.targetId, ra: v.ra, dec: v.dec, fov: v.fov,
      arrayIdx: v.arrayIdx, epochs: [ea, eb], marks: [{ epoch: ea, ...marks[ea] }, { epoch: eb, ...marks[eb] }],
      note, ...r, status: r.outcome === 'candidate' ? 'in-review' : 'auto-resolved',
    };
    add(item);
    addPoints(2, `Submission ${item.code}`);
    setResult(item);
    toast('+2 points for your submission.', 'good');
  };

  if (result) return <SubmitResult item={result} onAgain={() => { setResult(null); setMarks({}); setChecked([false, false, false]); setNote(''); }} />;

  return (
    <div className="mx-auto max-w-[1280px] px-[max(16px,env(safe-area-inset-left,0px))] py-10 lg:px-10">
      <Kicker>Submit a finding</Kicker>
      <h1 className="mt-4 text-4xl font-medium tracking-tight">Flag a candidate</h1>
      <p className="mt-2 max-w-2xl text-muted">Mark the object in the first and last snapshots. Automatic checks run straight away; promising <Term id="candidate">candidates</Term> go to scientists.</p>

      <Panel className="mt-6 flex flex-wrap gap-x-8 gap-y-2 p-5 text-sm">
        <span><span className="label-mono text-faint">Field </span>{preset?.name ?? 'Custom field'}</span>
        <span className="tabular"><span className="label-mono text-faint">Position </span>{formatRA(raDeg)} {formatDec(decDeg)}</span>
        <span><span className="label-mono text-faint">Array </span>{v.arrayIdx}</span>
        <span><span className="label-mono text-faint">Snapshots </span>{included.length} ticked</span>
        <button type="button" onClick={() => navigate('/')} className="ml-auto text-accent-fg hover:text-fg">Change field</button>
      </Panel>

      <h2 className="label-mono mt-8 mb-3 text-muted">1 · Click the object in each snapshot</h2>
      <div className="grid gap-4 md:grid-cols-2">
        {[ea, eb].map((e, k) => (
          <MarkCanvas key={e} field={field} epoch={e} mark={marks[e]} other={marks[k ? ea : eb]}
            onMark={(m) => setMarks((s) => ({ ...s, [e]: m }))} title={`${field.epochs[e].label} · ${formatDate(field.epochs[e].date)}`} />
        ))}
      </div>
      {(marks[ea] || marks[eb]) && <button type="button" onClick={() => setMarks({})} className="mt-2 text-sm text-muted hover:text-fg">Clear marks</button>}

      <div className="mt-8 grid gap-6 lg:grid-cols-2">
        <div>
          <h2 className="label-mono mb-3 text-muted">2 · Quick self-check</h2>
          <Panel className="divide-y divide-line">
            {CHECKS.map((c, i) => (
              <label key={c} htmlFor={`chk-${i}`} className="flex cursor-pointer items-start gap-3 px-5 py-4 text-sm">
                <input id={`chk-${i}`} type="checkbox" checked={checked[i]} onChange={() => setChecked((s) => s.map((x, k) => (k === i ? !x : x)))} className="mt-0.5 size-4 accent-[var(--color-accent)]" />
                {c}
              </label>
            ))}
          </Panel>
        </div>
        <div>
          <h2 className="label-mono mb-3 text-muted">3 · Note for scientists (optional)</h2>
          <textarea id="sub-note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={280} rows={5}
            placeholder="What did you notice? e.g. faint, moves south-west, brighter in array 6"
            className="w-full rounded-lg border border-line-strong bg-ink-900 p-4 text-sm outline-none focus:border-accent" />
          <p className="mt-1 flex justify-between text-xs text-faint"><span>Notes are checked automatically. Don’t include contact details.</span><span className="tabular">{note.length}/280</span></p>
        </div>
      </div>

      <div className="mt-8 flex flex-wrap items-center gap-4">
        <Button variant="primary" size="lg" disabled={!ready} onClick={submit}><Send className="size-4" />Submit for checks</Button>
        {!ready && <p className="text-sm text-muted">Mark both snapshots and tick all three checks to submit.</p>}
      </div>
    </div>
  );
}

function MarkCanvas({ field, epoch, mark, other, onMark, title }) {
  const draw = useCallback((ctx, W, H) => {
    const { nx, ny } = field;
    paintImage(ctx, W, H, field.stacks[epoch], nx, ny, { vmin: field.vmin, vmax: field.vmax, lut: lutForArray(field.arrayIdx), smooth: false });
    const sx = W / nx, sy = H / ny;
    if (other) {
      ctx.save(); ctx.strokeStyle = 'rgba(140,147,168,.6)'; ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.arc(other.x * sx, other.y * sy, 12, 0, Math.PI * 2); ctx.stroke(); ctx.restore();
    }
    if (mark) {
      ctx.save(); ctx.strokeStyle = '#ffb15c'; ctx.lineWidth = 2;
      const r = 14, g = 5, x = mark.x * sx, y = mark.y * sy;
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.moveTo(x - r - 6, y); ctx.lineTo(x - g, y); ctx.moveTo(x + g, y); ctx.lineTo(x + r + 6, y);
      ctx.stroke(); ctx.restore();
    }
  }, [field, epoch, mark, other]);
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-ink-950">
      <p className="label-mono flex justify-between border-b border-line px-4 py-2.5 text-muted"><span>{title}</span><span className={mark ? 'text-amber' : ''}>{mark ? 'Marked' : 'Click to mark'}</span></p>
      <SkyCanvas nx={field.nx} ny={field.ny} draw={draw} onPick={(x, y) => onMark({ x, y })} label={`${title}: click the object to mark it`} />
    </div>
  );
}

const OUTCOME = {
  candidate: { tone: 'good', title: 'Sent to the review queue', text: 'It passed the automatic checks. A scientist will review it; you’ll see their verdict on your profile.' },
  known: { tone: 'accent', title: 'Already known', text: 'Nice spotting: this matches a catalogued object, so it doesn’t need review. That’s exactly what a real mover looks like.' },
  artefact: { tone: 'warn', title: 'Probably not a moving object', text: 'The checks didn’t find a consistent moving source at your marks. Look at the details below and try again.' },
};

function SubmitResult({ item, onAgain }) {
  const navigate = useNavigate();
  const o = OUTCOME[item.outcome];
  return (
    <div className="mx-auto max-w-3xl px-[max(16px,env(safe-area-inset-left,0px))] py-12">
      <Kicker>{item.code}</Kicker>
      <h1 className="mt-4 text-4xl font-medium tracking-tight">{o.title}</h1>
      <p className="mt-2 text-muted">{o.text}</p>
      <Panel className="mt-8 divide-y divide-line">
        {item.checks.map((c) => {
          const Icon = c.status === 'new' || c.status === 'known' || c.status === 'fixed' ? CircleHelp : c.ok ? CircleCheck : CircleX;
          return (
            <div key={c.id} className="flex gap-4 px-5 py-4">
              <Icon className={cn('mt-0.5 size-5 shrink-0', c.status ? 'text-accent-fg' : c.ok ? 'text-good' : 'text-bad')} />
              <div><p className="font-medium">{c.label}</p><p className="text-sm text-muted">{c.detail}</p></div>
            </div>
          );
        })}
        <div className="px-5 py-4">
          <div className="flex items-center justify-between text-sm"><span>AI priority score</span><span className="tabular font-medium">{item.score}/100</span></div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-700"><div className="h-full rounded-full bg-accent" style={{ width: `${item.score}%` }} /></div>
          <p className="mt-2 text-xs text-muted">Ranks the review queue. Scientists can always view the queue unranked too.</p>
        </div>
      </Panel>
      <div className="mt-6 flex flex-wrap gap-2">
        <Chip tone={o.tone}>{item.status === 'in-review' ? 'In review' : 'Resolved automatically'}</Chip>
        <Chip tone="accent">+2 points</Chip>
      </div>
      <div className="mt-8 flex flex-wrap gap-2">
        <Button variant="primary" onClick={() => navigate('/profile')}>View my submissions<ArrowRight className="size-4" /></Button>
        <Button variant="outline" onClick={onAgain}><RotateCcw className="size-4" />Submit another</Button>
      </div>
    </div>
  );
}
