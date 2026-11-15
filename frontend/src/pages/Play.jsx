import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router';
import { LogIn, Lock, ShieldCheck, Timer, CircleCheck, CircleX, CircleHelp, Send, RotateCcw, ArrowRight, Play as PlayIcon, Pause, SkipBack, SkipForward } from 'lucide-react';
import { Button, Kicker, Panel, Chip, SkyCanvas, Term, Segmented, StepBadge } from '../components/ui';
import { TargetPanel } from '../components/TargetPanel';
import { WindowTimeline } from '../components/WindowTimeline';
import { useViewer, useAuth, useUi, useProgress, useSubmissions } from '../store';
import { useField, vet } from '../lib/api';
import { PRESET_BY_ID, LESSONS, LEVELS } from '../lib/data';
import { paintImage, paintDiff, lutForArray, crop, centroid, drawCompass, PX } from '../lib/sim';
import { reputationFor, cooldownFor, formatWait } from '../lib/reputation';
import { ReputationCard } from '../components/Reputation';
import { useMySubmissions, useNow } from '../lib/reputationHooks';
import { parseRA, parseDec, formatRA, formatDec, formatDate, cn, uid } from '../lib/util';

const CHECKS = [
  'It appears in at least two snapshots.',
  'It isn’t a single bright pixel in one snapshot (a cosmic ray).',
  'I compared it with the catalogue overlays where I could.',
];

/**
 * Play: the public submits discoveries found in Explore. Needs an account (to stop spam
 * and credit contributions) and finished training; how often a user can submit and how
 * high their candidates rank for review depend on their reputation (lib/reputation.js).
 */
export default function Play() {
  const [params] = useSearchParams();
  const user = useAuth((s) => s.user);
  const setSignIn = useUi((s) => s.setSignIn);
  const signInDemo = useAuth((s) => s.signInDemo);
  const lessons = useProgress((s) => s.lessons);
  const games = useProgress((s) => s.games);
  const navigate = useNavigate();
  const trained = Boolean(user?.trained)
    || (LESSONS.filter((l) => lessons[l.id]).length >= 3 && LEVELS.filter((l) => games[l.id]?.stars > 0).length >= 2);

  // Old links to the training games (/play?level=…) now live under Learn.
  if (params.get('level')) return <Navigate to={`/learn/game?level=${encodeURIComponent(params.get('level'))}`} replace />;

  if (!user) {
    return (
      <Gate icon={LogIn} title="Sign in to submit discoveries" text="Exploring and the Learn section are open to everyone. Submitting a discovery needs an account: it stops spam, credits your finds to you and builds your reputation, so scientists can follow up. Short on time? Use the demo volunteer account.">
        <Button variant="primary" onClick={() => setSignIn(true)}><LogIn className="size-4" />Sign in</Button>
      </Gate>
    );
  }
  // Scientists review submissions; they don't make them, so no submit form for them.
  if (user.role === 'scientist') {
    return (
      <Gate icon={ShieldCheck} title="Scientists review, volunteers submit" text="Scientist accounts don’t submit discoveries. Open the Submissions tab to review what volunteers sent from this browser. To try submitting, switch to the demo volunteer account.">
        <Button variant="primary" onClick={() => navigate('/review')}><ShieldCheck className="size-4" />Open Submissions</Button>
        <Button variant="ghost" onClick={() => signInDemo('volunteer')}>Switch to the demo volunteer account</Button>
      </Gate>
    );
  }
  if (!trained) {
    return (
      <Gate icon={Lock} title="Finish your training first" text="Complete three lessons and two training-game levels. It takes about 15 minutes and teaches you to tell real movers from glitches, which keeps the review queue useful for scientists.">
        <Button variant="primary" onClick={() => navigate('/learn')}>Go to Learn<ArrowRight className="size-4" /></Button>
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

/** 19×19 cut-outs around the marks, as plain JSON (NaN, meaning no data, becomes null). */
function reviewStamps(field, ea, eb, marks) {
  const cx = (marks[ea].x + marks[eb].x) / 2, cy = (marks[ea].y + marks[eb].y) / 2;
  const cut = (e) => {
    const { data, n } = crop(field.stacks[e], field.nx, field.ny, cx, cy, 9);
    return { n, data: Array.from(data, (v) => (Number.isNaN(v) ? null : +v.toFixed(2))) };
  };
  return { a: cut(ea), b: cut(eb), vmin: field.vmin, vmax: field.vmax, labels: [field.epochs[ea].label, field.epochs[eb].label] };
}

function Gate({ icon: Icon, title, text, children, below }) {
  return (
    <div className="mx-auto max-w-xl px-[max(16px,env(safe-area-inset-left,0px))] py-20 text-center">
      <span className="mx-auto grid size-12 place-items-center rounded-full border border-line-strong bg-ink-800"><Icon className="size-5 text-accent-fg" /></span>
      <h1 className="mt-5 text-3xl font-medium tracking-tight">{title}</h1>
      <p className="mt-3 text-muted">{text}</p>
      <div className="mt-6 flex flex-wrap justify-center gap-2">{children}</div>
      {below && <div className="mt-10 text-left">{below}</div>}
    </div>
  );
}

/** Shown while the user's reputation-based wait between submissions runs. */
function CooldownGate({ mine, remainingMs, onSkip }) {
  const navigate = useNavigate();
  const { tier } = reputationFor(mine);
  return (
    <Gate icon={Timer} title={`Next submission in ${formatWait(remainingMs)}`}
      text={`At your reputation (${tier.name}) you wait ${formatWait(tier.cooldownMin * 60e3)} between submissions. Relevant finds shorten the wait; submissions that fail the checks make it longer.`}
      below={<ReputationCard mine={mine} />}>
      <Button variant="primary" onClick={() => navigate('/')}>Keep exploring<ArrowRight className="size-4" /></Button>
      <Button variant="ghost" onClick={onSkip}>Skip the wait for this demo</Button>
    </Gate>
  );
}

/**
 * Where the flagged area's light is in one snapshot: the brightest pixel inside the box,
 * refined by a centroid around it. null when the box has no data.
 */
function sourceIn(field, epoch, area) {
  const { nx, ny } = field, img = field.stacks[epoch];
  let best = -Infinity, bx = -1, by = -1;
  for (let y = Math.max(0, Math.floor(area.y0)); y < Math.min(ny, Math.ceil(area.y1)); y++) {
    for (let x = Math.max(0, Math.floor(area.x0)); x < Math.min(nx, Math.ceil(area.x1)); x++) {
      const v = img[y * nx + x];
      if (!Number.isNaN(v) && v > best) { best = v; bx = x; by = y; }
    }
  }
  if (bx < 0) return null;
  const [x, y] = centroid(img, nx, ny, bx + 0.5, by + 0.5, 1.5);
  return { epoch, x, y };
}

const MIN_BOX = 3; // pixels; a click flags an 8×8 area around it
const CLICK_BOX = 8;

function SubmitForm() {
  const v = useViewer();
  const user = useAuth((s) => s.user);
  const addPoints = useAuth((s) => s.addPoints);
  const add = useSubmissions((s) => s.add);
  const toast = useUi((s) => s.toast);
  const preset = v.targetId ? PRESET_BY_ID[v.targetId] : null;
  const raDeg = parseRA(v.ra), decDeg = parseDec(v.dec);
  const { data: field, loading, error, attempt, tryAgain, source } = useField({ targetId: v.targetId, raDeg, decDeg, fov: v.fov, arrayIdx: v.arrayIdx, channel: v.channel });

  // The comparison window and flagged area belong to one field; a new field starts fresh.
  const n = field?.epochs.length ?? 0;
  const fieldKey = `${v.targetId}|${v.ra}|${v.dec}|${v.fov}|${v.arrayIdx}|${n}`;
  const [selState, setSelState] = useState({ key: null });
  const sel = selState.key === fieldKey ? selState : { key: fieldKey, win: [0, Math.max(1, n - 1)], area: null };
  const setSel = (patch) => setSelState({ ...sel, ...patch, key: fieldKey });
  const [a, b] = sel.win;
  const marks = useMemo(() => {
    if (!field || !sel.area || n < 2) return null;
    const ma = sourceIn(field, a, sel.area), mb = sourceIn(field, b, sel.area);
    return ma && mb ? [ma, mb] : null;
  }, [field, sel.area, a, b, n]);

  const [checked, setChecked] = useState([false, false, false]);
  const [note, setNote] = useState('');
  const [result, setResult] = useState(null);

  const mine = useMySubmissions();
  const [skipUntil, setSkipUntil] = useState(0); // demo: ignore the wait after the latest submission
  const [waiting, setWaiting] = useState(() => cooldownFor(mine).remainingMs > 0);
  const now = useNow(waiting);
  const cool = cooldownFor(mine, now);
  const blocked = cool.remainingMs > 0 && cool.until !== skipUntil;
  if (waiting !== blocked) setWaiting(blocked); // adjust during render

  const reset = () => { setResult(null); setSel({ area: null }); setChecked([false, false, false]); setNote(''); };
  if (result) return <SubmitResult item={result} mine={mine} onAgain={reset} />;
  if (blocked) return <CooldownGate mine={mine} remainingMs={cool.remainingMs} onSkip={() => setSkipUntil(cool.until)} />;

  const ready = Boolean(marks) && checked.every(Boolean);
  const submit = () => {
    const r = vet({ marks, field, preset, note });
    const item = {
      id: uid(), code: `SX-${String(Math.floor(Math.random() * 9000) + 1000)}`, at: new Date().toISOString(),
      userId: user.id, by: user.name, fieldName: preset?.name ?? 'Custom field', targetId: v.targetId, ra: v.ra, dec: v.dec, fov: v.fov,
      arrayIdx: v.arrayIdx, epochs: [a, b], area: sel.area, marks,
      note, ...r, status: r.outcome === 'candidate' ? 'in-review' : 'auto-resolved',
      // Real data can’t be rebuilt later like demo fields, so keep the reviewer’s cut-outs.
      ...(field.live ? { live: true, stamps: reviewStamps(field, a, b, { [a]: marks[0], [b]: marks[1] }) } : {}),
    };
    add(item);
    setResult(item);
    // Points only for relevant finds, so submitting noise earns nothing.
    if (item.outcome === 'artefact') toast('This one didn’t pass the checks, so your reputation dropped.', 'info');
    else { addPoints(2, `Submission ${item.code}`); toast('+2 points for your submission.', 'good'); }
  };

  return (
    <div className="mx-auto max-w-[1680px] px-[max(16px,env(safe-area-inset-left,0px))] py-10 lg:px-10">
      <Kicker>Play · submit a discovery</Kicker>
      <div className="mt-4 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)] lg:items-start">
        <div>
          <h1 className="text-4xl font-medium tracking-tight">Flag a candidate</h1>
          <p className="mt-2 max-w-2xl text-muted">Help sift the SPHEREx data. Pick a patch of sky, choose which snapshots to compare, then drag a box around anything that moves or changes. Automatic checks run straight away; promising <Term id="candidate">candidates</Term> go to scientists.</p>
          <p className="mt-3 max-w-2xl text-sm text-muted">Relevant finds raise your reputation. Submissions that fail the checks or that scientists reject lower it, which pushes your candidates down the review queue and makes you wait longer between submissions.</p>
        </div>
        <ReputationCard mine={mine} showHistory={false} className="p-5" />
      </div>

      <div className="mt-6 grid overflow-hidden rounded-xl border border-line bg-ink-900 lg:grid-cols-[360px_minmax(0,1fr)]">
        <TargetPanel tip="Slide the window to compare snapshots months apart. A real mover shifts smoothly; a flash in one snapshot only is usually a glitch." />
        <div className="min-w-0 border-t border-line lg:border-t-0 lg:border-l">
          <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-line px-6 py-4">
            <div className="flex items-start gap-3">
              <StepBadge n={2} />
              <div>
                <p className="label-mono text-muted">Flag the area</p>
                <p className="mt-0.5 text-lg font-medium">{preset ? preset.name : 'Custom field'}</p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
              {raDeg != null && <p className="label-mono text-muted tabular">RA <span className="text-fg/80">{formatRA(raDeg)}</span> · Dec <span className="text-fg/80">{formatDec(decDeg)}</span></p>}
              {field && <Chip tone={field.live && !field.synthetic ? 'good' : 'warn'}>{field.live && !field.synthetic ? (source === 'preset' ? 'Real SPHEREx frames' : 'Live SPHEREx data') : 'Simulated frames'}</Chip>}
            </div>
          </div>
          {field && n >= 2 ? (
            <FlagWorkspace key={fieldKey} field={field} win={sel.win} onWin={(win) => setSel({ win })}
              area={sel.area} onArea={(area) => setSel({ area })} marks={marks} />
          ) : (
            <div className="grid aspect-[5/3] place-items-center p-6 text-center text-sm text-muted">
              {loading ? <p>Loading SPHEREx images{attempt > 1 ? ` (attempt ${attempt})` : ''}… The first look at a new patch of sky can take a minute or two.</p>
                : error ? <div className="grid justify-items-center gap-3"><p>{error}</p>{tryAgain && <Button variant="outline" onClick={tryAgain}>Try again</Button>}</div>
                  : field ? 'This field has only one snapshot so far. Pick another patch of sky.' : 'Enter a sky position to begin.'}
            </div>
          )}
        </div>
      </div>

      <div className="mt-8 grid gap-6 lg:grid-cols-2">
        <div>
          <h2 className="label-mono mb-3 text-muted">3 · Quick self-check</h2>
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
          <h2 className="label-mono mb-3 text-muted">4 · Note for scientists (optional)</h2>
          <textarea id="sub-note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={280} rows={5}
            placeholder="What did you notice? e.g. faint, moves south-west, brighter in array 6"
            className="w-full rounded-lg border border-line-strong bg-ink-900 p-4 text-sm outline-none focus:border-accent" />
          <p className="mt-1 flex justify-between text-xs text-faint"><span>Notes are checked automatically. Don’t include contact details.</span><span className="tabular">{note.length}/280</span></p>
        </div>
      </div>

      <div className="mt-8 flex flex-wrap items-center gap-4">
        <Button variant="primary" size="lg" disabled={!ready} onClick={submit}><Send className="size-4" />Submit for checks</Button>
        {!ready && <p className="text-sm text-muted">{marks ? 'Tick all three checks to submit.' : 'Flag an area on the image, then tick all three checks to submit.'}</p>}
      </div>
    </div>
  );
}

/** Viewer for one field: the image, the flag-an-area tool, view controls and the window timeline. */
function FlagWorkspace({ field, win, onWin, area, onArea, marks }) {
  const [a, b] = win;
  const [curRaw, setCur] = useState(b);
  const cur = Math.min(b, Math.max(a, curRaw));
  const [mode, setMode] = useState('blink');
  const [playing, setPlaying] = useState(false);
  const [box, setBox] = useState(null); // area being dragged
  const overlay = useRef(null);
  const start = useRef(null);

  // Playback through the snapshots inside the window.
  useEffect(() => {
    if (!playing) return undefined;
    const id = setInterval(() => setCur((c) => {
      const k = Math.min(b, Math.max(a, c));
      return k >= b ? (mode === 'diff' ? Math.min(a + 1, b) : a) : k + 1;
    }), 900);
    return () => clearInterval(id);
  }, [playing, a, b, mode]);

  const toImage = (e) => {
    const r = overlay.current.getBoundingClientRect();
    return [((e.clientX - r.left) / r.width) * field.nx, ((e.clientY - r.top) / r.height) * field.ny];
  };
  const down = (e) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    start.current = toImage(e);
    setBox(null);
  };
  const moveP = (e) => {
    if (!start.current) return;
    const [x, y] = toImage(e), [sx, sy] = start.current;
    setBox({ x0: Math.min(sx, x), y0: Math.min(sy, y), x1: Math.max(sx, x), y1: Math.max(sy, y) });
  };
  const up = (e) => {
    if (!start.current) return;
    const [x, y] = toImage(e), [sx, sy] = start.current;
    start.current = null;
    setBox(null);
    const clamp = (q) => ({ x0: Math.max(0, q.x0), y0: Math.max(0, q.y0), x1: Math.min(field.nx, q.x1), y1: Math.min(field.ny, q.y1) });
    if (Math.abs(x - sx) < MIN_BOX && Math.abs(y - sy) < MIN_BOX) {
      const h = CLICK_BOX / 2;
      onArea(clamp({ x0: x - h, y0: y - h, x1: x + h, y1: y + h }));
    } else {
      onArea(clamp({ x0: Math.min(sx, x), y0: Math.min(sy, y), x1: Math.max(sx, x), y1: Math.max(sy, y) }));
    }
  };

  const draw = useCallback((ctx, W, H) => {
    const { nx, ny, stacks } = field;
    const sx = W / nx, sy = H / ny;
    if (mode === 'diff') paintDiff(ctx, W, H, stacks[a], stacks[cur], nx, ny, { smooth: false });
    else paintImage(ctx, W, H, stacks[cur], nx, ny, { vmin: field.vmin, vmax: field.vmax, lut: lutForArray(field.arrayIdx), smooth: false });
    const rect = (q, dash, color) => {
      ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.setLineDash(dash);
      ctx.strokeRect(q.x0 * sx, q.y0 * sy, (q.x1 - q.x0) * sx, (q.y1 - q.y0) * sy); ctx.restore();
    };
    if (area) rect(area, [5, 4], '#ffb15c');
    if (box) rect(box, [2, 3], 'rgba(233,235,243,.8)');
    if (marks) {
      const [m0, m1] = marks;
      ctx.save(); ctx.lineWidth = 1.5;
      ctx.strokeStyle = 'rgba(184,175,255,.6)'; ctx.setLineDash([2, 3]);
      ctx.beginPath(); ctx.moveTo(m0.x * sx, m0.y * sy); ctx.lineTo(m1.x * sx, m1.y * sy); ctx.stroke();
      ctx.setLineDash([]);
      ctx.strokeStyle = '#45d3b8'; ctx.beginPath(); ctx.arc(m0.x * sx, m0.y * sy, 7, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = '#ffb15c'; ctx.beginPath(); ctx.arc(m1.x * sx, m1.y * sy, 7, 0, Math.PI * 2); ctx.stroke();
      ctx.restore();
    }
    drawCompass(ctx, W, H, field.fov, nx, field.pxScale);
  }, [field, mode, a, cur, area, box, marks]);

  const ep = field.epochs[cur];
  const px = field.pxScale ?? PX;
  const shift = marks ? Math.hypot(marks[1].x - marks[0].x, marks[1].y - marks[0].y) * px : null;
  const step = (d) => { setPlaying(false); setCur(Math.min(b, Math.max(a, cur + d))); };

  return (
    <>
      <div className="relative bg-ink-950">
        <SkyCanvas nx={field.nx} ny={field.ny} draw={draw} label={`SPHEREx image, ${ep.label}. Drag a box, or click, to flag an area.`}>
          <div ref={overlay} className="absolute inset-0 cursor-crosshair touch-none" onPointerDown={down} onPointerMove={moveP} onPointerUp={up}
            onPointerCancel={() => { start.current = null; setBox(null); }} />
          <p className="label-mono pointer-events-none absolute top-3 left-4 text-fg/90">
            {mode === 'diff' ? `${ep.label} − ${field.epochs[a].label}` : `${ep.label} · ${formatDate(ep.date)}`}
          </p>
          {!area && <p className="pointer-events-none absolute inset-x-0 bottom-4 mx-auto w-fit rounded-md bg-ink-900/90 px-3 py-2 text-[13px] text-muted">Drag a box around something that moves or changes (or click it).</p>}
        </SkyCanvas>
        <div className="label-mono flex flex-wrap items-center justify-between gap-2 border-t border-line px-6 py-2.5 text-muted">
          <span>SPHEREx · <Term id="array">Array {field.arrayIdx}</Term></span>
          {marks ? (
            <span className="normal-case tracking-normal text-fg">
              <span className="text-teal">●</span> {field.epochs[a].label} → <span className="text-amber">●</span> {field.epochs[b].label}: brightest source in your box moved {shift.toFixed(1)}″ ({(shift / px).toFixed(1)} px)
            </span>
          ) : <span>{area ? 'No data in the flagged area' : 'Nothing flagged yet'}</span>}
          {area && <button type="button" onClick={() => onArea(null)} className="normal-case tracking-normal text-accent-fg hover:text-fg">Clear flag</button>}
        </div>
      </div>

      <div className="grid gap-4 border-t border-line px-6 pt-5 pb-4 sm:grid-cols-[auto_minmax(0,1fr)] sm:items-center">
        <div className="flex items-center gap-2">
          <Button variant="outline" size="icon" className="rounded-full" aria-label="Previous snapshot" onClick={() => step(-1)}><SkipBack className="size-4" /></Button>
          <Button variant="outline" size="lg" className="size-12 rounded-full border-accent/60 bg-accent-soft px-0" aria-label={playing ? 'Pause' : 'Play'} onClick={() => setPlaying(!playing)}>
            {playing ? <Pause className="size-5" /> : <PlayIcon className="size-5" />}
          </Button>
          <Button variant="outline" size="icon" className="rounded-full" aria-label="Next snapshot" onClick={() => step(1)}><SkipForward className="size-4" /></Button>
        </div>
        <WindowTimeline epochs={field.epochs} win={win} onWin={onWin} cur={cur} onCur={(i) => { setPlaying(false); setCur(i); }} />
      </div>

      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 border-t border-line px-6 py-4">
        <Segmented value={mode} onChange={setMode} label="View mode" options={[
          { value: 'blink', label: 'Blink', title: 'Flip through the snapshots in the window' },
          { value: 'diff', label: 'Difference', title: 'Subtract the window’s first snapshot' },
        ]} />
        <p className="max-w-xl text-[13px] text-muted">
          {mode === 'diff' ? <><span className="text-teal">Teal</span> = was here, <span className="text-amber">amber</span> = now here, relative to the window’s first snapshot. Fixed stars cancel out.</>
            : 'The checks compare the brightest source in your box at the start and end of the window.'}
        </p>
      </div>
    </>
  );
}

const OUTCOME = {
  candidate: { tone: 'good', title: 'Sent to the review queue', text: 'It passed the automatic checks. A scientist will review it; you’ll see their verdict on your profile.' },
  known: { tone: 'accent', title: 'Already known', text: 'Nice spotting: this matches a catalogued object, so it doesn’t need review. That’s exactly what a real mover looks like.' },
  artefact: { tone: 'warn', title: 'Probably not a moving object', text: 'The checks didn’t find a consistent moving source at your marks. Look at the details below and try again.' },
};

function SubmitResult({ item, mine, onAgain }) {
  const navigate = useNavigate();
  const o = OUTCOME[item.outcome];
  const { score, tier } = reputationFor(mine);
  const before = reputationFor(mine.filter((m) => m.id !== item.id)).score;
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
        {item.outcome !== 'artefact' && <Chip tone="accent">+2 points</Chip>}
        <Chip tone={score >= before ? 'good' : 'bad'}>Reputation {score >= before ? '+' : ''}{score - before} · {tier.name}</Chip>
        <Chip>Next submission in {formatWait(tier.cooldownMin * 60e3)}</Chip>
      </div>
      <div className="mt-8 flex flex-wrap gap-2">
        <Button variant="primary" onClick={() => navigate('/profile')}>View my submissions<ArrowRight className="size-4" /></Button>
        <Button variant="outline" onClick={onAgain}><RotateCcw className="size-4" />Flag another</Button>
      </div>
    </div>
  );
}
