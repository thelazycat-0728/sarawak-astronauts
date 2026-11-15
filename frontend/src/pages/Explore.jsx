import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import {
  Sparkles, Play, Pause, SkipBack, SkipForward, CircleHelp,
  Layers, ScanLine, Gauge, Flag, Check, GraduationCap, RotateCcw,
} from 'lucide-react';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip as RTooltip, CartesianGrid,
  ComposedChart, ReferenceLine, ReferenceArea,
} from 'recharts';
import { Button, Segmented, Term, Tip, StepBadge, Kicker, Chip, Switch, SkyCanvas, Panel } from '../components/ui';
import { useViewer, useUi, useProgress } from '../store';
import { useField } from '../lib/api';
import { PRESETS, PRESET_BY_ID, ARRAYS, LESSONS } from '../lib/data';
import {
  paintImage, paintDiff, LUTS, lutForArray, meanOf, drawReticle, drawCompass, aperture, centroid,
  spectrum102, MOLECULES, bandLambda,
} from '../lib/sim';
import { parseRA, parseDec, formatRA, formatDec, formatDate, cn, mulberry32, hashString } from '../lib/util';
import { startTour } from '../lib/tour';
import { TargetPanel } from '../components/TargetPanel';

const MODE_HELP = {
  blink: 'Flips between snapshots. Anything that jumps while the background stays still is moving.',
  diff: 'Subtracts the first snapshot. Fixed stars vanish; a mover leaves a teal (was here) and amber (now here) pair.',
  stack: 'Averages all ticked snapshots into one deeper image. Faint still objects stand out; movers smear.',
};

export default function Explore() {
  const v = useViewer();
  const expert = useUi((s) => s.expert);
  const preset = v.targetId ? PRESET_BY_ID[v.targetId] : null;
  const raDeg = parseRA(v.ra), decDeg = parseDec(v.dec);
  const { data: field, loading, source, error, attempt, tryAgain } = useField({
    targetId: v.targetId, raDeg, decDeg, fov: v.fov, arrayIdx: v.arrayIdx, channel: v.channel,
  });

  // Read a shared link once (?target=… or ?ra=…&dec=…, plus fov, array, mode).
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const s = useViewer.getState();
    const t = q.get('target');
    if (t && PRESET_BY_ID[t]) s.loadPreset(t);
    else if (q.get('ra') && q.get('dec') && parseRA(q.get('ra')) != null && parseDec(q.get('dec')) != null) s.setField(q.get('ra'), q.get('dec'));
    const fov = Number(q.get('fov')); if ([3, 6, 12].includes(fov)) s.setFov(fov);
    const arr = Number(q.get('array')); if (arr >= 1 && arr <= 6) s.setArray(arr);
    const mode = q.get('mode'); if (['blink', 'diff', 'stack'].includes(mode)) s.setMode(mode);
  }, []);

  const n = field?.epochs.length ?? 0;
  const included = useMemo(() => (v.included && v.included.length === n ? v.included : Array(n).fill(true)), [v.included, n]);
  const incIdx = useMemo(() => included.map((on, i) => (on ? i : -1)).filter((i) => i >= 0), [included]);
  const cur = included[v.epochIdx] ? v.epochIdx : incIdx[0] ?? 0;
  const base = incIdx[0] ?? 0;

  // Playback through the ticked snapshots.
  useEffect(() => {
    if (!v.playing || v.mode === 'stack' || incIdx.length < 2) return undefined;
    const id = setInterval(() => {
      const st = useViewer.getState();
      const list = st.mode === 'diff' ? incIdx.slice(1) : incIdx;
      if (!list.length) return;
      const pos = list.indexOf(st.epochIdx);
      st.setEpoch(list[(pos + 1) % list.length]);
    }, 1100 / v.speed);
    return () => clearInterval(id);
  }, [v.playing, v.mode, v.speed, incIdx]);

  // Expert: the source whose spectrum and brightness are shown.
  // Cleared whenever the view moves to another field.
  const fieldKey = `${v.targetId}|${v.ra}|${v.dec}|${v.fov}`;
  const [picked, setPicked] = useState({ key: fieldKey, pick: null });
  const pick = picked.key === fieldKey ? picked.pick : null;
  const setPick = useCallback((p) => setPicked({ key: fieldKey, pick: p }), [fieldKey]);
  const selection = useMemo(() => {
    if (!field) return null;
    if (pick) return pick;
    if (field.object && field.object.kind !== 'asteroid' && !field.object.mystery) return { type: 'object' };
    if (field.live) return centrePoint(field);
    if (!field.stars.length) return null;
    let best = 0;
    field.stars.forEach((s, i) => { if (s.peak > field.stars[best].peak) best = i; });
    return { type: 'star', i: best };
  }, [field, pick]);

  const onPick = useCallback((x, y) => {
    if (!field) return;
    const op = field.object && field.visibleAt(cur) ? field.posAt(cur) : null;
    if (op && Math.hypot(op[0] - x, op[1] - y) < 3) { setPick({ type: 'object' }); return; }
    if (field.live) {
      // Live data has no star list: snap to the light centre near the click.
      const [cx, cy] = centroid(field.stacks[cur], field.nx, field.ny, x, y);
      setPick({ type: 'point', x: cx, y: cy });
      return;
    }
    let bi = -1, bd = 3;
    field.stars.forEach((s, i) => { const d = Math.hypot(s.x - x, s.y - y); if (d < bd) { bd = d; bi = i; } });
    if (bi >= 0) setPick({ type: 'star', i: bi });
  }, [field, cur, setPick]);

  return (
    <div className="mx-auto max-w-[1680px] px-[max(16px,env(safe-area-inset-left,0px))] pb-6 lg:px-10">
      <Hero />
      <div className="grid overflow-hidden rounded-xl border border-line bg-ink-900 lg:grid-cols-[360px_minmax(0,1fr)]">
        <TargetPanel />
        <div className="min-w-0 border-t border-line lg:border-t-0 lg:border-l">
          <ViewerHead preset={preset} raDeg={raDeg} decDeg={decDeg} expert={expert} field={field} />
          <Stage
            field={field} loading={loading} attempt={attempt} error={error} tryAgain={tryAgain} preset={preset} cur={cur} base={base} incIdx={incIdx}
            expert={expert} selection={selection} onPick={expert ? onPick : undefined}
          />
          {field && <TimelineBar field={field} included={included} cur={cur} />}
          <ModeBar expert={expert} hasField={Boolean(field)} />
        </div>
        <StatusBar field={field} incIdx={incIdx} source={source} error={error} />
      </div>
      {field && (expert
        ? <Analysis field={field} preset={preset} incIdx={incIdx} cur={cur} selection={selection} />
        : <BeginnerGuide />)}
    </div>
  );
}

// ---------------------------------------------------------------- hero
function Hero() {
  return (
    <section className="flex flex-wrap items-end justify-between gap-8 py-10 lg:py-14">
      <div className="max-w-3xl">
        <Kicker>The sky never stands still</Kicker>
        <h1 className="mt-5 text-[clamp(40px,7vw,84px)] leading-[0.95] font-medium tracking-[-0.04em] text-balance">
          See the universe <span className="font-serif font-normal tracking-normal text-accent-fg italic">move.</span>
        </h1>
        <p className="mt-5 max-w-[62ch] text-[17px] text-muted">
          Compare near-infrared views of the same sky, captured months apart by NASA’s SPHEREx mission.
        </p>
        <button type="button" onClick={startTour} className="mt-5 inline-flex items-center gap-2 text-sm font-medium text-accent-fg hover:text-fg">
          <CircleHelp className="size-4" />New here? Take the 60-second tour
        </button>
      </div>
      <dl className="flex gap-10">
        <div className="flex items-center gap-3 border-l border-line-strong pl-4">
          <dt className="sr-only">Infrared bands</dt>
          <dd className="text-3xl font-medium tabular">102</dd>
          <span className="label-mono leading-relaxed text-muted">Infrared<br />bands</span>
        </div>
        <div className="flex items-center gap-3 border-l border-line-strong pl-4">
          <dt className="sr-only">Coverage</dt>
          <dd className="text-3xl font-medium">All sky</dd>
          <span className="label-mono leading-relaxed text-muted">Every 6<br />months</span>
        </div>
      </dl>
    </section>
  );
}

// ---------------------------------------------------------------- viewer
function ViewerHead({ preset, raDeg, decDeg, expert, field }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-line px-6 py-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <div>
          <p className="label-mono text-muted">Now viewing</p>
          <p className="mt-0.5 flex items-center gap-2 text-lg font-medium">
            <span className="size-1.5 rounded-full bg-accent" aria-hidden="true" />
            {preset ? preset.name : 'Custom field'}
          </p>
        </div>
        <span className="hidden h-8 w-px bg-line-strong sm:block" />
        <p className="label-mono text-fg/80">{preset ? preset.type : 'Unexplored sky · look for anything that moves'}</p>
      </div>
      {raDeg != null && (
        <p className="label-mono flex flex-wrap gap-x-5 text-muted tabular">
          <span>RA <span className="text-fg/80">{formatRA(raDeg)}</span></span>
          <span>Dec <span className="text-fg/80">{formatDec(decDeg)}</span></span>
          {expert && field && <span>{raDeg.toFixed(4)}°, {decDeg.toFixed(4)}° · {field.nx}×{field.ny} px</span>}
        </p>
      )}
    </div>
  );
}

function Stage({ field, loading, attempt, error, tryAgain, preset, cur, base, incIdx, expert, selection, onPick }) {
  const v = useViewer();
  const lessonDone = useProgress((s) => s.lessons);
  const lesson = v.lessonId ? LESSONS.find((l) => l.id === v.lessonId) : null;

  const draw = useCallback((ctx, W, H) => {
    if (!field) return;
    const { nx, ny, stacks } = field;
    const sx = W / nx, sy = H / ny;
    const smooth = !expert;
    // Expert shows the measured brightness in greyscale; the guided view adds false colour.
    const lut = expert ? LUTS.grey : lutForArray(field.arrayIdx);
    const range = { vmin: field.vmin, vmax: field.vmax, lut, smooth };
    if (v.mode === 'diff') paintDiff(ctx, W, H, stacks[base], stacks[cur], nx, ny, { smooth });
    else if (v.mode === 'stack') paintImage(ctx, W, H, meanOf(stacks, incIdx.length ? incIdx : [0]), nx, ny, range);
    else paintImage(ctx, W, H, stacks[cur], nx, ny, range);

    // Catalogue overlays (Expert).
    if (expert && v.overlays.gaia) {
      ctx.save();
      ctx.strokeStyle = 'rgba(69,211,184,.75)';
      ctx.lineWidth = 1;
      for (const s of field.stars) {
        if (!s.gaia) continue;
        ctx.beginPath(); ctx.arc(s.x * sx, s.y * sy, 6, 0, Math.PI * 2); ctx.stroke();
      }
      const o = field.object;
      if (o && o.pm && o.inGaia !== false && !o.mystery && ['barnard', 'rwaur', 'rdra'].includes(o.id)) {
        const p = field.posAt(cur);
        const len = Math.hypot(...o.pm) / 1000 / field.pxScale; // 1 year, in pixels
        const ang = Math.atan2(o.pm[1], -o.pm[0]);
        const ex = p[0] * sx + Math.cos(ang) * len * sx, ey = p[1] * sy - Math.sin(ang) * len * sy;
        ctx.strokeStyle = '#45d3b8'; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(p[0] * sx, p[1] * sy); ctx.lineTo(ex, ey); ctx.stroke();
      }
      ctx.restore();
    }
    if (expert && v.overlays.wise) {
      ctx.save();
      ctx.strokeStyle = 'rgba(255,177,92,.7)';
      ctx.lineWidth = 1;
      for (const s of field.stars) if (s.wise) ctx.strokeRect(s.x * sx - 7, s.y * sy - 7, 14, 14);
      if (field.object && !field.object.mystery && field.visibleAt(cur)) {
        const p = field.posAt(cur);
        ctx.strokeRect(p[0] * sx - 9, p[1] * sy - 9, 18, 18);
      }
      ctx.restore();
    }

    // Predicted track and the target reticle (presets only; mystery movers are for users to find).
    const o = field.object;
    if (o && !o.mystery && preset) {
      if (v.overlays.track && o.kind === 'motion' && incIdx.length > 1) {
        ctx.save();
        ctx.strokeStyle = 'rgba(184,175,255,.45)';
        ctx.setLineDash([3, 4]);
        ctx.beginPath();
        incIdx.forEach((e, k) => { const [x, y] = field.posAt(e); if (k) ctx.lineTo(x * sx, y * sy); else ctx.moveTo(x * sx, y * sy); });
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = 'rgba(184,175,255,.7)';
        incIdx.forEach((e) => { const [x, y] = field.posAt(e); ctx.beginPath(); ctx.arc(x * sx, y * sy, 1.8, 0, Math.PI * 2); ctx.fill(); });
        ctx.restore();
      }
      const [x, y] = field.posAt(cur);
      let sub;
      if (o.kind === 'motion') {
        const [bx, by] = field.posAt(base);
        sub = `motion detected · ${(Math.hypot(x - bx, y - by) * field.pxScale).toFixed(1)}″`;
      } else {
        const ref = aperture(field.stacks[base], field.nx, field.ny, x, y);
        const now = aperture(field.stacks[cur], field.nx, field.ny, x, y);
        sub = `brightness · ${ref > 0 ? Math.round((now / ref) * 100) : 100}% of first`;
      }
      drawReticle(ctx, x * sx, y * sy, { label: preset.name, sub, r: 13 });
    }

    // Selected source (Expert).
    if (expert && selection) {
      const p = selectionAt(field, selection, cur);
      if (p) {
        ctx.save();
        ctx.strokeStyle = '#ffb15c';
        ctx.setLineDash([2, 3]);
        ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(p[0] * sx, p[1] * sy, 20, 0, Math.PI * 2); ctx.stroke();
        ctx.restore();
      }
    }
    drawCompass(ctx, W, H, field.fov, nx, field.pxScale);
  }, [field, v.mode, v.overlays, cur, base, incIdx, expert, selection, preset]);

  const arr = ARRAYS[v.arrayIdx - 1];
  const lam = v.channel != null ? `λ ${bandLambda(v.arrayIdx, v.channel).toFixed(2)} µm` : `λ ${arr.lo.toFixed(2)}–${arr.hi.toFixed(2)} µm`;
  const ep = field?.epochs[cur];

  return (
    <div id="tour-stage" className="relative bg-ink-950">
      {field ? (
        <SkyCanvas nx={field.nx} ny={field.ny} draw={draw} onPick={onPick} label={`SPHEREx image of ${preset?.name ?? 'the selected field'}, ${ep?.label}`}>
          {!expert && preset && (
            <div className="absolute top-4 left-4 max-w-[min(360px,calc(100%-90px))] rounded-md border-l-2 border-accent bg-ink-900/90 px-4 py-3 shadow-lg backdrop-blur">
              <p className="label-mono text-accent-fg">{lesson ? `Lesson · ${lesson.skill}` : preset.lesson.kicker}</p>
              <p className="mt-1 text-[15px] font-medium">{lesson ? lesson.title : preset.lesson.title}</p>
              {lesson ? (
                <>
                  <ol className="mt-2 list-decimal space-y-0.5 pl-4 text-[13px] text-muted">{lesson.tasks.map((t) => <li key={t.text}>{t.text}</li>)}</ol>
                  <p className={cn('mt-3 flex items-center gap-1.5 text-[13px]', lessonDone[lesson.id] ? 'text-good' : 'text-muted')}>
                    {lessonDone[lesson.id] ? <><Check className="size-4" />Lesson complete. Now try it on the real images.</> : 'Finish this lesson’s practice window in Learn to tick it off.'}
                  </p>
                </>
              ) : <p className="mt-1 text-[13px] text-muted">{preset.lesson.text}</p>}
            </div>
          )}
          {v.mode === 'diff' && cur === base && incIdx.length > 1 && (
            <p className="absolute inset-x-0 bottom-12 mx-auto w-fit rounded-md bg-ink-900/90 px-3 py-2 text-[13px] text-muted">Pick a later snapshot to compare with the first one.</p>
          )}
        </SkyCanvas>
      ) : (
        <div className="grid aspect-[5/3] place-items-center p-6 text-sm text-muted">
          {loading ? <LiveLoading attempt={attempt} />
            : error ? <LiveError message={error} onRetry={tryAgain} />
              : 'Enter a sky position to begin.'}
        </div>
      )}
      <div className="label-mono flex flex-wrap items-center justify-between gap-2 border-t border-line px-6 py-2.5 text-muted">
        <span>SPHEREx · <Term id="array">Array {v.arrayIdx}</Term></span>
        <span className="text-fg tabular">{v.mode === 'stack' ? `${incIdx.length} snapshots stacked` : ep ? formatDate(ep.date) : ''}{v.mode === 'diff' && field ? ` − ${formatDate(field.epochs[base].date)}` : ''}</span>
        <span className="tabular">{lam}</span>
      </div>
    </div>
  );
}

function TimelineBar({ field, included, cur }) {
  const v = useViewer();
  const { epochs } = field;
  const t0 = epochs[0].t, t1 = epochs[epochs.length - 1].t || 1;
  const pos = (e) => (epochs.length === 1 ? 50 : ((e.t - t0) / (t1 - t0)) * 100);
  const every = epochs.length > 8 ? 3 : 1;
  const step = (dir) => {
    const list = included.map((on, i) => (on ? i : -1)).filter((i) => i >= 0);
    const k = list.indexOf(cur);
    v.setPlaying(false);
    v.setEpoch(list[(k + dir + list.length) % list.length]);
  };
  const toggle = (i) => {
    const next = included.slice();
    next[i] = !next[i];
    if (next.filter(Boolean).length < 1) return;
    v.setIncluded(next);
    if (!next[cur]) v.setEpoch(next.findIndex(Boolean));
  };
  return (
    <div id="tour-timeline" className="grid gap-4 border-t border-line px-6 pt-5 pb-4 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-center">
      <div className="flex items-center gap-3">
        <Button variant="outline" size="icon" className="rounded-full" aria-label="Previous snapshot" onClick={() => step(-1)}><SkipBack className="size-4" /></Button>
        <Button variant="outline" size="lg" className="size-14 rounded-full border-accent/60 bg-accent-soft" aria-label={v.playing ? 'Pause' : 'Play'}
          disabled={v.mode === 'stack'} onClick={() => v.setPlaying(!v.playing)}>
          {v.playing ? <Pause className="size-5" /> : <Play className="size-5" />}
        </Button>
        <Button variant="outline" size="icon" className="rounded-full" aria-label="Next snapshot" onClick={() => step(1)}><SkipForward className="size-4" /></Button>
      </div>
      <div className="min-w-0 px-2">
        <p className="label-mono mb-3 text-muted"><Term id="epoch">Snapshots over time</Term> <span className="normal-case tracking-normal text-faint">(tick to include)</span></p>
        <div className="relative h-16">
          <div className="absolute top-2 right-0 left-0 h-px bg-line-strong" />
          <div className="absolute top-2 left-0 h-px bg-accent" style={{ width: `${pos(epochs[cur])}%` }} />
          {epochs.map((e, i) => (
            <div key={e.date} className="absolute top-0 -translate-x-1/2" style={{ left: `${pos(e)}%` }}>
              <button type="button" onClick={() => { v.setPlaying(false); if (included[i]) v.setEpoch(i); }} aria-label={`${e.label}, ${formatDate(e.date)}`}
                className={cn('block rounded-full transition-all', i === cur ? 'size-4 -translate-y-[3px] bg-accent ring-4 ring-accent/25' : included[i] ? 'size-2.5 bg-faint hover:bg-muted' : 'size-2.5 border border-faint bg-ink-950')} />
              {(i % every === 0 || i === cur) && (
                <span className={cn('label-mono absolute top-5 whitespace-nowrap normal-case tracking-[0.04em]', i === 0 ? '-left-1' : i === epochs.length - 1 ? '-right-1' : 'left-1/2 -translate-x-1/2', i === cur ? 'text-fg' : 'text-faint')}>
                  {formatDate(e.date, epochs.length > 8 ? { month: 'short', year: '2-digit' } : undefined)}
                </span>
              )}
              <input type="checkbox" checked={included[i]} onChange={() => toggle(i)} aria-label={`Include ${e.label}`}
                className="absolute top-11 left-1/2 size-3.5 -translate-x-1/2 accent-[var(--color-accent)]" />
            </div>
          ))}
        </div>
      </div>
      <label className="flex items-center gap-2 text-sm text-muted">
        <span className="sr-only">Playback speed</span>
        <select value={v.speed} onChange={(e) => v.setSpeed(Number(e.target.value))} className="h-10 rounded-md border border-line-strong bg-ink-900 px-3 text-fg">
          <option value={0.5}>0.5×</option><option value={1}>1×</option><option value={2}>2×</option><option value={4}>4×</option>
        </select>
      </label>
    </div>
  );
}

function ModeBar({ expert, hasField }) {
  const v = useViewer();
  const setAssistant = useUi((s) => s.setAssistant);
  const navigate = useNavigate();
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3 border-t border-line px-6 py-5">
      <div id="tour-modes" className="grid gap-2">
        <p className="label-mono text-muted">Compare images (view mode)</p>
        <Segmented value={v.mode} onChange={v.setMode} label="View mode" options={[
          { value: 'blink', label: 'Blink', title: MODE_HELP.blink },
          { value: 'diff', label: 'Difference', title: MODE_HELP.diff },
          { value: 'stack', label: 'Stack', title: MODE_HELP.stack },
        ]} />
      </div>
      {!expert && <p className="max-w-md text-[13.5px] text-muted">{MODE_HELP[v.mode]}</p>}
      {hasField && (
        <div className="ml-auto flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => setAssistant(true)}><Sparkles className="size-4 text-accent-fg" />Ask about this view</Button>
          {/* Discover takes new finds only, so known objects (presets) can't be flagged. */}
          {!v.targetId && <Button variant="subtle" size="sm" onClick={() => navigate('/discover')}><Flag className="size-4 text-amber" />Flag a candidate</Button>}
        </div>
      )}
    </div>
  );
}

// Live views can take a minute or two the first time: IRSA is slow, and the API
// builds the cutouts before answering (docs/API.md).
function LiveLoading({ attempt = 1 }) {
  const [start] = useState(() => Date.now());
  const [now, setNow] = useState(start);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const secs = Math.round((now - start) / 1000);
  return (
    <div className="max-w-sm text-center" role="status">
      <p className="label-mono animate-pulse">Fetching SPHEREx images… {secs}s</p>
      {secs >= 8 && (
        <p className="mt-3 text-[13px] leading-relaxed text-muted">
          The first look at a new patch of sky can take 1–2 minutes while the images download from the SPHEREx archive. Repeat visits are almost instant.
          {attempt > 1 && ` Still preparing (try ${attempt} of 3).`}
        </p>
      )}
    </div>
  );
}

function LiveError({ message, onRetry }) {
  const loadPreset = useViewer((s) => s.loadPreset);
  return (
    <div className="max-w-md text-center" role="alert">
      <p className="text-fg">This view couldn’t load</p>
      <p className="mt-2 text-[13px] leading-relaxed text-muted">{message}</p>
      <div className="mt-4 flex justify-center">
        <Button size="sm" variant="outline" onClick={onRetry}><RotateCcw className="size-4" />Try again</Button>
      </div>
      <p className="label-mono mt-5 text-faint">Or explore a known object</p>
      <div className="mt-2 flex flex-wrap justify-center gap-2">
        {PRESETS.map((p) => (
          <button key={p.id} type="button" onClick={() => loadPreset(p.id)}
            className="rounded-full border border-line-strong px-3 py-1.5 text-[13px] text-fg hover:border-accent">{p.name}</button>
        ))}
      </div>
    </div>
  );
}

function StatusBar({ field, incIdx, source, error }) {
  const live = field?.synthetic ? 'Live API · synthetic mock data' : 'Live from IRSA';
  const label = { demo: 'Demo frames', preset: 'Real SPHEREx frames (preset)', live, error: 'Live API', none: 'No field' }[source] ?? 'Demo frames';
  return (
    <div className="label-mono flex flex-wrap items-center justify-between gap-3 border-t border-line px-6 py-3 text-muted lg:col-span-2">
      <span className="flex items-center gap-2">
        <span className={cn('size-1.5 rounded-full', source === 'live' || source === 'preset' ? 'bg-good' : 'bg-amber')} />
        SPHEREx public data · <span className="text-fg">{label}</span>
        {error && <span className="normal-case tracking-normal text-amber">(Live view unavailable)</span>}
      </span>
      {field && (
        <span className="flex flex-wrap items-center gap-x-5 gap-y-1">
          <span className="flex items-center gap-1.5"><Layers className="size-3.5" />{incIdx.length} of {field.epochs.length} snapshots</span>
          <span className="flex items-center gap-1.5"><ScanLine className="size-3.5" /><Term id="flags">Flags</Term>: <span className="text-fg">Masked</span></span>
          <span className="flex items-center gap-1.5"><Gauge className="size-3.5" />Scale: <span className="text-fg">Asinh</span></span>
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- beginner guide
function BeginnerGuide() {
  const navigate = useNavigate();
  const setExpert = useUi((s) => s.setExpert);
  const steps = [
    { t: 'Pick a target', d: 'Start with Barnard’s Star: it moves more than any other star.' },
    { t: 'Press play', d: 'Each frame is the same patch of sky, months apart.' },
    { t: 'Spot what moves', d: 'Fixed stars stay put. Anything that jumps is worth a closer look.' },
  ];
  return (
    <section className="mt-8 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_300px]">
      {steps.map((s, i) => (
        <Panel key={s.t} className="p-5">
          <StepBadge n={i + 1} />
          <h3 className="mt-3 font-medium">{s.t}</h3>
          <p className="mt-1 text-sm text-muted">{s.d}</p>
        </Panel>
      ))}
      <Panel className="flex flex-col justify-between gap-4 p-5">
        <p className="text-sm text-muted">Ready to help scientists? Train on the five test targets, then submit real finds.</p>
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" size="sm" onClick={() => navigate('/learn')}><GraduationCap className="size-4" />Start training</Button>
          <Button variant="outline" size="sm" onClick={() => setExpert(true)}>Expert tools</Button>
        </div>
      </Panel>
    </section>
  );
}

// ---------------------------------------------------------------- expert analysis
const axis = { stroke: '#5b6378', fontSize: 11, fontFamily: 'Geist Mono, monospace' };
const tooltipStyle = { background: '#121827', border: '1px solid #2a3249', borderRadius: 8, fontSize: 12 };

/** Pixel position of the selected source in snapshot e (null if unknown). */
function selectionAt(field, selection, e) {
  if (!selection) return null;
  if (selection.type === 'object') return field.posAt(e);
  if (selection.type === 'point') return [selection.x, selection.y];
  const st = field.stars[selection.i];
  return st ? [st.x, st.y] : null;
}

/** Default selection on live data: the light centre nearest the middle, which is the position the user asked for. */
function centrePoint(field) {
  const { nx, ny } = field;
  const mean = meanOf(field.stacks, field.stacks.map((_, i) => i));
  const [x, y] = centroid(mean, nx, ny, nx / 2, ny / 2, 3);
  return { type: 'point', x, y };
}

function Analysis({ field, preset, incIdx, cur, selection }) {
  const v = useViewer();
  const selPos = (e) => selectionAt(field, selection, e);
  const selName = selection?.type === 'object' ? (preset?.name ?? field.object?.name ?? 'Source')
    : selection?.type === 'point' ? `Position ${Math.round(selection.x)}, ${Math.round(selection.y)}`
      : selection ? `Star #${selection.i + 1}` : '—';

  const brightness = useMemo(() => {
    if (!selection) return [];
    return incIdx.map((e) => {
      const p = selPos(e);
      return { date: formatDate(field.epochs[e].date, { month: 'short', year: '2-digit' }), flux: p ? +aperture(field.stacks[e], field.nx, field.ny, p[0], p[1]).toFixed(1) : null };
    });
  }, [field, incIdx, selection]); // eslint-disable-line react-hooks/exhaustive-deps

  const spectrum = useMemo(() => {
    if (!selection || field.live) return [];
    if (selection.type === 'object') {
      const o = field.object;
      return spectrum102(o.spectrum ?? 'star', o.flux?.[2] || 10, hashString(o.id ?? 'o'));
    }
    const s = field.stars[selection.i];
    return spectrum102('star', s.peak * s.mult[2], selection.i + 7, s.colour);
  }, [field, selection]);

  const arr = ARRAYS[v.arrayIdx - 1];
  return (
    <section className="mt-8">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <Kicker>Expert tools</Kicker>
          <h2 className="mt-2 text-2xl font-medium">Analyse this field</h2>
        </div>
        <p className="text-sm text-muted">Selected source: <span className="text-fg">{selName}</span>. Click any star in the image to inspect it.</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Panel className="p-5">
          <CardTitle term="band">Infrared colour</CardTitle>
          <div className="mt-4 grid grid-cols-6 gap-1.5">
            {ARRAYS.map((a) => (
              <Tip key={a.n} content={`Array ${a.n}: ${a.lo}–${a.hi} µm`}>
                <button type="button" aria-pressed={v.arrayIdx === a.n} onClick={() => v.setArray(a.n)}
                  className={cn('rounded-md border py-2 font-mono text-sm', v.arrayIdx === a.n ? 'border-accent bg-accent-soft text-fg' : 'border-line text-muted hover:text-fg')}>{a.n}</button>
              </Tip>
            ))}
          </div>
          <p className="mt-2 text-xs text-muted">Array {arr.n} covers {arr.lo}–{arr.hi} µm. All 17 bands stacked for the cleanest image.</p>
          <div className="mt-4 border-t border-line pt-4">
            {field.live ? (
              <p className="text-xs text-muted">Single bands aren’t available for live SPHEREx views yet; each array is shown stacked.</p>
            ) : <Switch id="single-band" label="Single band" checked={v.channel != null} onChange={(on) => v.setChannel(on ? 8 : null)} />}
            {!field.live && v.channel != null && (
              <div className="mt-3">
                <input type="range" min={0} max={16} value={v.channel} onChange={(e) => v.setChannel(Number(e.target.value))} className="w-full" aria-label="Band within array" />
                <p className="tabular mt-1 text-xs text-muted">Band {v.channel + 1} of 17 · λ = {bandLambda(v.arrayIdx, v.channel).toFixed(3)} µm. Narrower band: more colour detail, more noise.</p>
              </div>
            )}
          </div>
        </Panel>

        <Panel className="p-5">
          <CardTitle term="variable">Brightness over time</CardTitle>
          <div className="mt-3 h-44">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={brightness} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid stroke="#1b2233" vertical={false} />
                <XAxis dataKey="date" tick={axis} tickLine={false} axisLine={false} interval="preserveStartEnd" />
                <YAxis tick={axis} tickLine={false} axisLine={false} width={44} />
                <RTooltip contentStyle={tooltipStyle} labelStyle={{ color: '#8c93a8' }} />
                <Line type="monotone" dataKey="flux" name="Brightness" stroke="#7c6cf5" strokeWidth={2} dot={{ r: 3, fill: '#b8afff' }} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <p className="text-xs text-muted">Aperture brightness of {selName} in array {arr.n}, ticked snapshots only.</p>
        </Panel>

        <Panel className="p-5 md:col-span-2 xl:col-span-1">
          <CardTitle term="spectrum">Spectrum · 102 bands</CardTitle>
          <div className="mt-3 h-44">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={spectrum} margin={{ top: 14, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid stroke="#1b2233" vertical={false} />
                <ReferenceArea x1={arr.lo} x2={arr.hi} fill="#7c6cf5" fillOpacity={0.08} />
                {MOLECULES.map((m) => (
                  <ReferenceLine key={m.lam} x={m.lam} stroke="#ffb15c" strokeOpacity={0.5} strokeDasharray="2 3"
                    label={{ value: m.label, position: 'top', fill: '#ffb15c', fontSize: 9.5, fontFamily: 'Geist Mono, monospace' }} />
                ))}
                <XAxis dataKey="lam" type="number" domain={[0.7, 5.05]} ticks={[1, 2, 3, 4, 5]} tick={axis} tickLine={false} axisLine={false} unit=" µm" />
                <YAxis tick={axis} tickLine={false} axisLine={false} width={44} />
                <RTooltip contentStyle={tooltipStyle} labelStyle={{ color: '#8c93a8' }} labelFormatter={(l) => `${l} µm`} />
                <Line type="monotone" dataKey="flux" name="Brightness" stroke="#b8afff" strokeWidth={1.5} dot={false} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <p className="text-xs text-muted">{field.live
            ? 'Spectra need single-band data, which the live SPHEREx service doesn’t provide yet. Try a known object to see one.'
            : 'Built on the fly from all six arrays. Dashed lines mark where molecules absorb.'}</p>
        </Panel>

        <MotionCard field={field} incIdx={incIdx} />

        <Panel className="p-5">
          <CardTitle term="gaia">Catalogue overlays</CardTitle>
          <div className="mt-4 grid gap-3">
            <Switch id="ov-gaia" label={<span>Gaia DR3 <span className="text-muted">· circles, arrows = 1 yr motion</span></span>} checked={v.overlays.gaia} onChange={() => v.toggleOverlay('gaia')} />
            <Switch id="ov-wise" label={<span>CatWISE <span className="text-muted">· squares, sees cold objects</span></span>} checked={v.overlays.wise} onChange={() => v.toggleOverlay('wise')} />
            <Switch id="ov-track" label={<span>Predicted track <span className="text-muted">· from proper motion</span></span>} checked={v.overlays.track} onChange={() => v.toggleOverlay('track')} />
          </div>
          <p className="mt-4 text-xs text-muted">{field.live
            ? 'Gaia and CatWISE overlays aren’t connected to live SPHEREx views yet.'
            : 'A source moving with no Gaia or CatWISE match is a candidate worth flagging.'}</p>
        </Panel>

        <MetadataCard field={field} incIdx={incIdx} cur={cur} />
      </div>
    </section>
  );
}

function CardTitle({ term, children }) {
  return <h3 className="label-mono text-muted"><Term id={term}>{children}</Term></h3>;
}

function MotionCard({ field, incIdx }) {
  const o = field.object;
  const content = (() => {
    if (!o || o.kind !== 'motion' || incIdx.length < 2) {
      return <p className="mt-4 text-sm text-muted">{o?.kind === 'variable' ? 'This star changes brightness rather than position. See the brightness chart.' : 'No tracked mover in this field. Use Difference mode to hunt for one.'}</p>;
    }
    const a = incIdx[0], b = incIdx[incIdx.length - 1];
    const pa = centroid(field.stacks[a], field.nx, field.ny, ...field.posAt(a));
    const pb = centroid(field.stacks[b], field.nx, field.ny, ...field.posAt(b));
    const dx = pb[0] - pa[0], dy = pb[1] - pa[1];
    const shift = Math.hypot(dx, dy) * field.pxScale;
    const years = field.epochs[b].t - field.epochs[a].t;
    const rate = shift / years;
    const angle = ((Math.atan2(-dx, -dy) * 180) / Math.PI + 360) % 360;
    const catRate = Math.hypot(...o.pm) / 1000;
    const catAngle = ((Math.atan2(o.pm[0], o.pm[1]) * 180) / Math.PI + 360) % 360;
    const agree = Math.abs(rate - catRate) / catRate < 0.35;
    return (
      <>
        <dl className="mt-4 grid grid-cols-2 gap-4">
          <div><dt className="label-mono text-faint">Measured</dt><dd className="mt-1 text-2xl font-medium tabular">{rate.toFixed(1)}″<span className="text-base text-muted">/yr</span></dd></div>
          <div><dt className="label-mono text-faint">Direction</dt><dd className="mt-1 flex items-center gap-2 text-2xl font-medium tabular">
            <span aria-hidden="true" className="inline-block text-accent-fg" style={{ transform: `rotate(${-angle}deg)` }}>↑</span>{angle.toFixed(0)}°</dd></div>
        </dl>
        <p className="mt-3 text-xs text-muted">{shift.toFixed(1)}″ over {years.toFixed(2)} years, from centroids in the first and last ticked snapshots. Direction is measured east of north.</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {o.mystery ? <Chip tone="warn">No catalogue match: candidate</Chip>
            : <Chip tone={agree ? 'good' : 'warn'}>{agree ? 'Matches' : 'Differs from'} catalogue: {catRate.toFixed(1)}″/yr, {catAngle.toFixed(0)}°</Chip>}
        </div>
      </>
    );
  })();
  return (
    <Panel className="p-5">
      <CardTitle term="pm">Motion tracking</CardTitle>
      {content}
    </Panel>
  );
}

function MetadataCard({ field, incIdx, cur }) {
  const rows = field.epochs.map((e, i) => {
    if (field.live) {
      const pct = `${((e.flaggedFraction ?? 0) * 100).toFixed(1)}%`;
      return { ...e, i, exposures: e.exposures ?? 1, flagText: e.flags?.length ? `${pct} · ${e.flags.join(', ')}` : pct };
    }
    const rng = mulberry32(hashString(e.date + field.fov));
    const ghost = Math.floor(rng() * 4), persist = Math.floor(rng() * 6), halo = Math.floor(rng() * 3);
    return { ...e, i, exposures: 3, flagText: ghost + persist + halo === 0 ? 'none' : `ghost ${ghost} · persist ${persist} · halo ${halo}` };
  });
  return (
    <Panel className="p-5 md:col-span-2">
      <CardTitle term="flags">Snapshot metadata & contamination flags</CardTitle>
      <div className="mt-3 max-h-56 overflow-auto">
        <table className="w-full text-left text-[13px] tabular">
          <thead className="label-mono sticky top-0 bg-ink-900 text-faint">
            <tr><th className="py-2 pr-3 font-normal">Snapshot</th><th className="pr-3 font-normal">Date</th><th className="pr-3 font-normal"><Term id="release">Release</Term></th><th className="pr-3 font-normal">Exposures</th><th className="pr-3 font-normal">Flagged pixels</th><th className="font-normal">Used</th></tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((r) => (
              <tr key={r.date} className={r.i === cur ? 'text-fg' : 'text-muted'}>
                <td className="py-2 pr-3">{r.label}</td>
                <td className="pr-3">{formatDate(r.date)}</td>
                <td className="pr-3"><Chip tone={r.release === 'DR1' ? 'accent' : 'teal'}>{r.release}</Chip></td>
                <td className="pr-3">{r.exposures}</td>
                <td className="pr-3">{r.flagText}</td>
                <td>{incIdx.includes(r.i) ? <Check className="size-4 text-good" aria-label="Included" /> : <span className="text-faint">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-muted">Flagged pixels are masked before display. Zodiacal light is subtracted from every snapshot.</p>
    </Panel>
  );
}
