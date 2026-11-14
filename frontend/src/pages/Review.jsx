import { useCallback, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import { ShieldCheck, LogIn, Eye, EyeOff, Users, RotateCcw } from 'lucide-react';
import { Button, Segmented, Kicker, Panel, Chip, SkyCanvas } from '../components/ui';
import { useAuth, useUi, useSubmissions } from '../store';
import { DEMO_QUEUE, SURVEYS, customObject } from '../lib/data';
import { simulate, crop, paintImage, paintDiff, LUTS } from '../lib/sim';
import { demoField } from '../lib/api';
import { parseRA, parseDec, cn } from '../lib/util';

const VERDICTS = [
  { id: 'confirmed', label: 'Confirm', tone: 'good', long: 'Confirmed candidate' },
  { id: 'known', label: 'Known', tone: 'accent', long: 'Known object' },
  { id: 'artefact', label: 'Artefact', tone: 'warn' },
  { id: 'rejected', label: 'Reject', tone: 'bad' },
];

/** Build a simulated field and the two marked positions for a demo queue item. */
function demoCandidate(d) {
  if (d.kind === 'flash') {
    const object = { id: 'flash', name: 'Flash', kind: 'asteroid', flux: Array(6).fill(26), astEpoch: 2, astStep: [0, 0], spectrum: 'star' };
    const field = simulate({ key: `rq-${d.id}`, seed: d.seed, fov: 5, arrayIdx: 3, object, epochs: SURVEYS, place: 'random' });
    const p = field.posAt(2, 0);
    return { field, a: 0, b: 2, pa: p, pb: p };
  }
  let object = null;
  for (let k = 0; !object && k < 20; k++) object = customObject(d.seed + k);
  const obj = { ...object, flux: object.flux.map((f) => f * (d.ai > 70 ? 1.4 : 0.9)) };
  const field = simulate({ key: `rq-${d.id}`, seed: d.seed, fov: 5, arrayIdx: 3, object: obj, epochs: SURVEYS, place: 'random' });
  return { field, a: 0, b: 3, pa: field.posAt(0), pb: field.posAt(3) };
}

/** Cut-outs saved with a live-data submission (null = no data). */
function storedStamps(s) {
  const read = ({ n, data }) => ({ n, data: Float32Array.from(data, (v) => (v == null ? NaN : v)) });
  return { stampA: read(s.a), stampB: read(s.b), vmin: s.vmin, vmax: s.vmax, labels: s.labels };
}

function userCandidate(it) {
  const field = demoField({ targetId: it.targetId, raDeg: parseRA(it.ra), decDeg: parseDec(it.dec), fov: it.fov, arrayIdx: it.arrayIdx, channel: null });
  const [ma, mb] = it.marks;
  return { field, a: ma.epoch, b: mb.epoch, pa: [ma.x, ma.y], pb: [mb.x, mb.y] };
}

export default function Review() {
  const user = useAuth((s) => s.user);
  const setRole = useAuth((s) => s.setRole);
  const setSignIn = useUi((s) => s.setSignIn);
  const items = useSubmissions((s) => s.items);
  const demoVerdicts = useSubmissions((s) => s.demoVerdicts);
  const [sort, setSort] = useState('ai');
  const [tab, setTab] = useState('pending');
  const [now] = useState(() => Date.now()); // fixed for this visit, so ages don't shift between renders

  const queue = useMemo(() => {
    const mine = items.filter((it) => it.outcome === 'candidate').map((it) => ({
      id: it.id, code: it.code, by: it.by, agreed: 1, ai: it.score, ageH: Math.max(0, Math.round((now - Date.parse(it.at)) / 36e5)),
      note: it.note, hiddenNote: it.noteHidden, user: true, verdict: it.verdict,
      signals: {
        motion: it.checks.find((c) => c.id === 'motion')?.detail, snr: +(it.snr ?? 0).toFixed(1), flags: 'Clear',
        catalogue: it.checks.find((c) => c.id === 'catalogue')?.detail, asteroid: 'Checked against SkyBoT',
      },
      build: () => userCandidate(it), stamps: it.stamps, fieldName: it.fieldName,
    }));
    const demo = DEMO_QUEUE.map((d) => ({ ...d, verdict: demoVerdicts[d.id], build: () => demoCandidate(d) }));
    const all = [...mine, ...demo].filter((q) => (tab === 'pending' ? !q.verdict : q.verdict));
    return all.sort((x, y) => (sort === 'ai' ? y.ai - x.ai : x.ageH - y.ageH));
  }, [items, demoVerdicts, sort, tab, now]);

  if (!user || user.role !== 'scientist') {
    return (
      <div className="mx-auto max-w-xl px-[max(16px,env(safe-area-inset-left,0px))] py-20 text-center">
        <span className="mx-auto grid size-12 place-items-center rounded-full border border-line-strong bg-ink-800"><ShieldCheck className="size-5 text-accent-fg" /></span>
        <h1 className="mt-5 text-3xl font-medium tracking-tight">Scientist review queue</h1>
        <p className="mt-3 text-muted">Volunteers’ candidates, ranked by the AI priority model, for scientists to confirm or reject. Only scientist accounts can open it; the role is enforced in the database.</p>
        <div className="mt-6 flex justify-center gap-2">
          {user ? <Button variant="primary" onClick={() => setRole('scientist')}>Switch this demo account to scientist</Button>
            : <Button variant="primary" onClick={() => setSignIn(true)}><LogIn className="size-4" />Sign in</Button>}
        </div>
      </div>
    );
  }

  const pending = [...items.filter((it) => it.outcome === 'candidate' && !it.verdict), ...DEMO_QUEUE.filter((d) => !demoVerdicts[d.id])].length;

  return (
    <div className="mx-auto max-w-[1280px] px-[max(16px,env(safe-area-inset-left,0px))] py-10 lg:px-10">
      <Kicker>Scientists</Kicker>
      <div className="mt-4 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-4xl font-medium tracking-tight">Review queue</h1>
          <p className="mt-2 text-muted">{pending} candidates waiting. Each has passed automatic quality, motion and catalogue checks.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Segmented value={tab} onChange={setTab} label="Queue" options={[{ value: 'pending', label: 'Pending' }, { value: 'done', label: 'Reviewed' }]} />
          <Segmented value={sort} onChange={setSort} label="Order" options={[{ value: 'ai', label: 'AI priority' }, { value: 'new', label: 'Newest (unranked)' }]} />
        </div>
      </div>
      <div className="mt-6 grid gap-4">
        {queue.length === 0 && <Panel className="p-10 text-center text-muted">{tab === 'pending' ? 'Queue clear. New candidates appear here as volunteers submit them.' : 'No reviewed candidates yet.'}</Panel>}
        {queue.map((q) => <Candidate key={q.id} q={q} />)}
      </div>
    </div>
  );
}

function Stamp({ data, n, kind, vmin, vmax, label }) {
  const draw = useCallback((ctx, W, H) => {
    if (kind === 'diff') paintDiff(ctx, W, H, data[0], data[1], n, n, { smooth: false });
    else paintImage(ctx, W, H, data, n, n, { vmin, vmax, lut: LUTS.sky, smooth: false });
    ctx.strokeStyle = 'rgba(255,177,92,.8)';
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(W / 2, H / 2, W * 0.16, 0, Math.PI * 2); ctx.stroke();
  }, [data, n, kind, vmin, vmax]);
  return (
    <figure className="min-w-0">
      <div className="overflow-hidden rounded-md border border-line"><SkyCanvas nx={n} ny={n} draw={draw} label={label} /></div>
      <figcaption className="label-mono mt-1.5 truncate text-faint">{label}</figcaption>
    </figure>
  );
}

function Candidate({ q }) {
  const [showNote, setShowNote] = useState(false);
  const review = useSubmissions((s) => s.review);
  const reviewDemo = useSubmissions((s) => s.reviewDemo);
  const addPoints = useAuth((s) => s.addPoints);
  const toast = useUi((s) => s.toast);
  const navigate = useNavigate();

  const { stampA, stampB, vmin, vmax, labels } = useMemo(() => {
    if (q.stamps) return storedStamps(q.stamps); // live-data submission
    const c = q.build();
    const cx = (c.pa[0] + c.pb[0]) / 2, cy = (c.pa[1] + c.pb[1]) / 2;
    const A = crop(c.field.stacks[c.a], c.field.nx, c.field.ny, cx, cy, 9);
    const B = crop(c.field.stacks[c.b], c.field.nx, c.field.ny, cx, cy, 9);
    return { stampA: A, stampB: B, vmin: c.field.vmin, vmax: c.field.vmax, labels: [c.field.epochs[c.a].label, c.field.epochs[c.b].label] };
  }, [q]);

  const decide = (v) => {
    if (q.user) {
      review(q.id, v);
      if (v === 'confirmed') { addPoints(50, `Verified finding ${q.code}`); toast('Confirmed. The volunteer earns +50 points.', 'good'); }
    } else reviewDemo(q.id, v);
  };
  const undo = () => (q.user ? review(q.id, undefined) : reviewDemo(q.id, undefined));
  const verdict = VERDICTS.find((x) => x.id === q.verdict);
  const scoreTone = q.ai >= 70 ? 'bg-good' : q.ai >= 45 ? 'bg-amber' : 'bg-faint';

  return (
    <Panel className="grid gap-6 p-5 lg:grid-cols-[360px_minmax(0,1fr)_220px]">
      <div className="grid grid-cols-3 gap-2">
        <Stamp data={stampA.data} n={stampA.n} vmin={vmin} vmax={vmax} label={labels[0]} />
        <Stamp data={stampB.data} n={stampB.n} vmin={vmin} vmax={vmax} label={labels[1]} />
        <Stamp data={[stampA.data, stampB.data]} n={stampA.n} kind="diff" label="Difference" />
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-mono text-lg font-medium">{q.code}</h2>
          {q.user && <Chip tone="accent">Your submission</Chip>}
          <span className="text-sm text-muted">by {q.by} · {q.ageH < 1 ? 'just now' : `${q.ageH} h ago`}{q.fieldName ? ` · ${q.fieldName}` : ''}</span>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Chip tone={/consistent/i.test(q.signals.motion ?? '') || /″ between/.test(q.signals.motion ?? '') ? 'good' : 'warn'}>{q.signals.motion}</Chip>
          <Chip>S/N {q.signals.snr}</Chip>
          <Chip tone={q.signals.flags === 'Clear' ? 'default' : 'warn'}>Flags: {q.signals.flags}</Chip>
          <Chip tone="teal">{q.signals.catalogue}</Chip>
          <Chip>{q.signals.asteroid}</Chip>
          <Chip><Users className="size-3" />{q.agreed} independent flag{q.agreed > 1 ? 's' : ''}</Chip>
        </div>
        <div className="mt-4 text-sm">
          {q.hiddenNote && !showNote ? (
            <p className="flex flex-wrap items-center gap-2 text-muted"><EyeOff className="size-4" />Note hidden by moderation (contact details).
              <button type="button" onClick={() => setShowNote(true)} className="text-accent-fg hover:text-fg">Show anyway</button></p>
          ) : q.note ? <p className="border-l-2 border-line-strong pl-3 text-muted">“{q.note}”{q.hiddenNote && <button type="button" onClick={() => setShowNote(false)} className="ml-2 text-accent-fg"><Eye className="inline size-3.5" /> Hide</button>}</p>
            : <p className="text-faint">No note.</p>}
        </div>
      </div>
      <div className="flex flex-col gap-3">
        <div>
          <div className="flex items-baseline justify-between text-sm"><span className="text-muted">AI priority</span><span className="text-xl font-medium tabular">{q.ai}</span></div>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-ink-700"><div className={cn('h-full rounded-full', scoreTone)} style={{ width: `${q.ai}%` }} /></div>
        </div>
        {verdict ? (
          <div className="flex flex-wrap items-center gap-2">
            <Chip tone={verdict.tone}>{verdict.long ?? verdict.label}</Chip>
            <Button size="sm" variant="ghost" onClick={undo}><RotateCcw className="size-3.5" />Undo</Button>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-1.5">
            {VERDICTS.map((x) => (
              <Button key={x.id} size="sm" variant={x.id === 'confirmed' ? 'primary' : x.id === 'rejected' ? 'danger' : 'outline'} className="text-xs" onClick={() => decide(x.id)}>{x.label}</Button>
            ))}
          </div>
        )}
        <Button size="sm" variant="ghost" onClick={() => navigate('/')}>Open field in Explore</Button>
      </div>
    </Panel>
  );
}
