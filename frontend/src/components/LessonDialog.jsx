import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react';
import { useNavigate } from 'react-router';
import { Play as PlayIcon, Pause, Check, ArrowRight, Telescope, RotateCcw } from 'lucide-react';
import { Button, Segmented, Modal, Chip, SkyCanvas } from './ui';
import { useProgress, useUi, useViewer } from '../store';
import { PRESET_BY_ID, ARRAYS, LESSONS } from '../lib/data';
import { simulate, paintImage, paintDiff, lutForArray, drawReticle, drawCompass, aperture } from '../lib/sim';
import { cn, formatDate } from '../lib/util';

const FOV = 5; // arcmin: a small, uncluttered practice field
const HIT_PX = 3; // how close a click must be to the target, in SPHEREx pixels

// ---------------------------------------------------------------- lesson state
// Tasks run in order; after every action, `advance` ticks each task whose
// condition now holds (see the task types in lib/data.js).

function satisfied(task, s) {
  switch (task.type) {
    case 'watch': {
      const a = task.array ?? s.arrayIdx;
      for (let e = 0; e < s.nEpochs; e++) if (!s.seen[`${a}:${e}`]) return false;
      return true;
    }
    case 'mode': return s.mode === task.mode;
    case 'array': return s.arrayIdx === task.array;
    case 'find': return s.found;
    // Only a deliberate stop counts, not wherever playback happened to be paused before.
    case 'peak': return !s.playing && s.stoppedAt === s.epoch && s.bright.includes(s.epoch);
    default: return false;
  }
}

function advance(s, tasks) {
  let n = s;
  // Clear the stop position as each step ticks, so it can't also tick the next one.
  while (n.step < tasks.length && satisfied(tasks[n.step], n)) n = { ...n, step: n.step + 1, stoppedAt: null };
  if (n.step === s.step) return s;
  return { ...n, feedback: null }; // the ticked step shows what was learned
}

const see = (s, epoch, arrayIdx = s.arrayIdx) => ({ ...s, epoch, seen: { ...s.seen, [`${arrayIdx}:${epoch}`]: true } });

function reducer(tasks) {
  return (s, a) => {
    let n = s;
    switch (a.type) {
      case 'tick': n = see(s, (s.epoch + 1) % s.nEpochs); break;
      case 'epoch': n = see({ ...s, playing: a.pause ? false : s.playing, stoppedAt: a.pause ? a.epoch : s.stoppedAt }, a.epoch); break;
      case 'play': n = { ...s, playing: a.playing, stoppedAt: a.playing ? null : s.epoch }; break;
      case 'mode':
        n = { ...s, mode: a.mode };
        if (a.mode === 'diff' && s.epoch === 0) n = see(n, s.nEpochs - 1); // first − first would be blank
        break;
      case 'array': n = see({ ...s, arrayIdx: a.arrayIdx }, s.epoch, a.arrayIdx); break;
      case 'pick': {
        const task = tasks[s.step];
        if (task?.type === 'find') {
          n = a.hit ? { ...s, found: true }
            : { ...s, feedback: { tone: 'warn', text: a.invisible ? 'Nothing shows there in this array. Try a different one.' : `Not that one: it stays put. ${task.hint}` } };
        } else if (task) {
          n = { ...s, feedback: { tone: 'info', text: 'Follow the highlighted step first.' } };
        }
        break;
      }
      case 'reset': return a.state;
      default: return s;
    }
    return advance(n, tasks);
  };
}

function initialState(lesson, preset) {
  const epochs = preset.epochs;
  // Snapshots near maximum brightness, for 'peak' tasks. Same pulsation as lib/sim.js;
  // a pulsing star peaks more than once, so any near-peak snapshot counts.
  const bright = preset.period
    ? epochs.map((ep, e) => (Math.sin((2 * Math.PI * ep.t * 365.25) / preset.period + 0.9) >= 0.75 ? e : -1)).filter((e) => e >= 0)
    : [];
  return see({
    arrayIdx: lesson.array, mode: 'blink', playing: false, nEpochs: epochs.length,
    seen: {}, step: 0, found: false, bright, stoppedAt: null, feedback: null,
  }, 0);
}

// ---------------------------------------------------------------- dialog
/** Interactive practice window for one lesson. */
export function LessonDialog({ lessonId, onClose, onOpenLesson }) {
  const lesson = LESSONS.find((l) => l.id === lessonId);
  return (
    <Modal open={Boolean(lesson)} onOpenChange={(o) => { if (!o) onClose(); }}
      title={lesson ? lesson.title : ''} description={lesson ? `Lesson · ${lesson.skill} · practice on a simulated SPHEREx field` : ''}
      className="max-h-[calc(100dvh-32px)] max-w-5xl overflow-y-auto">
      {lesson && <LessonBody key={lesson.id} lesson={lesson} onClose={onClose} onOpenLesson={onOpenLesson} />}
    </Modal>
  );
}

function LessonBody({ lesson, onClose, onOpenLesson }) {
  const preset = PRESET_BY_ID[lesson.preset];
  const tasks = lesson.tasks;
  const navigate = useNavigate();
  const completeLesson = useProgress((s) => s.completeLesson);
  const toast = useUi((s) => s.toast);
  const setExpert = useUi((s) => s.setExpert);
  const red = useMemo(() => reducer(tasks), [tasks]);
  const [s, dispatch] = useReducer(red, null, () => initialState(lesson, preset));
  const finished = s.step === tasks.length;

  const field = useMemo(() => simulate({
    key: `lesson-${lesson.id}-${s.arrayIdx}`, seed: preset.seed + 17, fov: FOV, arrayIdx: s.arrayIdx,
    object: preset, epochs: preset.epochs, place: 'random',
  }), [lesson.id, preset, s.arrayIdx]);
  const visible = preset.flux[s.arrayIdx - 1] >= 2;

  // Playback.
  const many = s.nEpochs > 6;
  useEffect(() => {
    if (!s.playing) return undefined;
    const id = setInterval(() => dispatch({ type: 'tick' }), many ? 450 : 800);
    return () => clearInterval(id);
  }, [s.playing, many]);

  // Record completion once.
  const recorded = useRef(false);
  useEffect(() => {
    if (!finished || recorded.current) return;
    recorded.current = true;
    completeLesson(lesson.id);
    toast(`Lesson complete: ${lesson.title}`, 'good');
  }, [finished, completeLesson, lesson.id, lesson.title, toast]);

  const onPick = (x, y) => {
    const d = Math.min(...preset.epochs.map((_, e) => { const [px, py] = field.posAt(e); return Math.hypot(px - x, py - y); }));
    dispatch({ type: 'pick', hit: d <= HIT_PX && visible, invisible: d <= HIT_PX && !visible });
  };

  const draw = useCallback((ctx, W, H) => {
    const { nx, ny, stacks } = field;
    if (s.mode === 'diff') paintDiff(ctx, W, H, stacks[0], stacks[s.epoch], nx, ny, { smooth: false });
    else paintImage(ctx, W, H, stacks[s.epoch], nx, ny, { vmin: field.vmin, vmax: field.vmax, lut: lutForArray(s.arrayIdx), smooth: false });
    if (s.found && visible) {
      const [x, y] = field.posAt(s.epoch);
      drawReticle(ctx, x * (W / nx), y * (H / ny), { label: preset.name, r: 13 });
    }
    drawCompass(ctx, W, H, FOV, nx);
  }, [field, s.mode, s.epoch, s.arrayIdx, s.found, visible, preset.name]);

  const ep = preset.epochs[s.epoch];
  const hud = s.mode === 'diff' ? `${ep.label} − ${preset.epochs[0].label}` : `${ep.label} · ${formatDate(ep.date)}`;
  const idx = LESSONS.indexOf(lesson);
  const next = LESSONS[idx + 1];

  const tryReal = () => {
    const v = useViewer.getState();
    v.loadPreset(lesson.preset, { mode: tasks.some((t) => t.mode === 'diff') ? 'diff' : 'blink' });
    v.setArray(lesson.array);
    v.setLesson(lesson.id);
    setExpert(false);
    onClose();
    navigate('/');
  };

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      {/* Mock viewer */}
      <div className="min-w-0">
        <div className="overflow-hidden rounded-lg border border-line bg-ink-950">
          <p className="label-mono flex justify-between gap-3 border-b border-line px-3 py-2 text-muted">
            <span>Practice viewer · {preset.name}</span><span>Array {s.arrayIdx} · {ARRAYS[s.arrayIdx - 1].lo}–{ARRAYS[s.arrayIdx - 1].hi} µm</span>
          </p>
          <SkyCanvas nx={field.nx} ny={field.ny} draw={draw} onPick={onPick} label={`Simulated SPHEREx field around ${preset.name}. Click an object to select it.`}>
            <p className="label-mono pointer-events-none absolute top-2.5 left-3 text-fg/90">{hud}</p>
            {s.mode === 'diff' && s.epoch === 0 && (
              <p className="pointer-events-none absolute inset-x-0 bottom-3 mx-auto w-fit rounded-md bg-ink-900/90 px-3 py-1.5 text-[13px] text-muted">Pick a later snapshot to compare with the first.</p>
            )}
          </SkyCanvas>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button variant="outline" size="icon" aria-label={s.playing ? 'Pause' : 'Play'} onClick={() => dispatch({ type: 'play', playing: !s.playing })}>
            {s.playing ? <Pause className="size-4" /> : <PlayIcon className="size-4" />}
          </Button>
          <input type="range" min={0} max={s.nEpochs - 1} value={s.epoch} aria-label="Snapshot"
            onChange={(e) => dispatch({ type: 'epoch', epoch: Number(e.target.value), pause: true })} className="min-w-32 flex-1 accent-[var(--color-accent)]" />
          <Segmented size="sm" label="View mode" value={s.mode} onChange={(m) => dispatch({ type: 'mode', mode: m })}
            options={[{ value: 'blink', label: 'Blink' }, { value: 'diff', label: 'Difference' }]} />
          {lesson.arrays && (
            <Segmented size="sm" label="Detector array" value={s.arrayIdx} onChange={(a) => dispatch({ type: 'array', arrayIdx: a })}
              options={lesson.arrays.map((a) => ({ value: a, label: `Array ${a}` }))} />
          )}
        </div>
        {s.mode === 'diff' && <p className="mt-2 text-[13px] text-muted"><span className="text-teal">Teal</span> = was here, <span className="text-amber">amber</span> = now here. Fixed stars cancel out.</p>}
        {lesson.tasks.some((t) => t.type === 'peak') && s.found && (
          <BrightnessChart field={field} epoch={s.epoch} onPick={(e) => dispatch({ type: 'epoch', epoch: e, pause: true })} />
        )}
      </div>

      {/* Steps */}
      <div className="flex flex-col gap-4" aria-live="polite">
        <div className="flex items-center justify-between text-sm">
          <span className="label-mono text-muted">Steps</span>
          <span className="tabular text-muted">{s.step}/{tasks.length} done</span>
        </div>
        <ol className="grid gap-2">
          {tasks.map((t, i) => {
            const done = i < s.step, active = i === s.step;
            return (
              <li key={t.text} className={cn('flex gap-3 rounded-lg border p-3 transition-colors',
                done ? 'border-good/30 bg-good/5' : active ? 'border-accent bg-ink-800' : 'border-line opacity-60')}>
                <span className={cn('mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border text-[11px]',
                  done ? 'border-good bg-good text-ink-950' : active ? 'border-accent text-accent-fg' : 'border-line-strong text-faint')} aria-hidden="true">
                  {done ? <Check className="size-3.5" strokeWidth={3} /> : i + 1}
                </span>
                <div className="min-w-0 text-sm">
                  <p className={cn(done && 'text-muted')}>{t.text}<span className="sr-only">{done ? ' (done)' : active ? ' (current step)' : ''}</span></p>
                  {active && <p className="mt-1 text-[13px] text-muted">{t.hint}</p>}
                  {done && <p className="mt-1 text-[13px] text-good">{t.learned}</p>}
                </div>
              </li>
            );
          })}
        </ol>

        {s.feedback && !finished && (
          <p className={cn('rounded-md border px-3 py-2 text-[13px]',
            s.feedback.tone === 'good' ? 'border-good/40 text-good' : s.feedback.tone === 'warn' ? 'border-amber/40 text-amber' : 'border-line-strong text-muted')}>
            {s.feedback.text}
          </p>
        )}

        {finished && (
          <div className="grid gap-3 rounded-lg border border-good/40 bg-good/5 p-4">
            <p className="flex items-center gap-2 font-medium text-good"><Check className="size-4" />Lesson complete</p>
            <p className="text-[13px] text-muted">Now try the same thing on real SPHEREx images in Explore.</p>
            <div className="flex flex-wrap gap-2">
              {next ? <Button size="sm" variant="primary" onClick={() => onOpenLesson(next.id)}>Next lesson<ArrowRight className="size-4" /></Button>
                : <Chip tone="good">All lessons done</Chip>}
              <Button size="sm" variant="outline" onClick={tryReal}><Telescope className="size-4" />Try on real data</Button>
              <Button size="sm" variant="ghost" onClick={() => dispatch({ type: 'reset', state: initialState(lesson, preset) })}><RotateCcw className="size-4" />Again</Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- brightness chart
/** Bar per snapshot: the target's measured brightness. Click a bar to show that snapshot. */
function BrightnessChart({ field, epoch, onPick }) {
  const values = useMemo(() => field.epochs.map((_, e) => {
    const [x, y] = field.posAt(e);
    return Math.max(0, aperture(field.stacks[e], field.nx, field.ny, x, y));
  }), [field]);
  const max = Math.max(...values) || 1;
  const W = 100 / values.length;

  return (
    <figure className="mt-4 rounded-lg border border-line bg-ink-900 p-3">
      <figcaption className="label-mono mb-2 flex justify-between text-muted"><span>Brightness of R Draconis</span><span>monthly snapshots</span></figcaption>
      <svg viewBox="0 0 100 32" preserveAspectRatio="none" className="h-24 w-full" role="group" aria-label="Brightness per monthly snapshot">
        {values.map((v, e) => {
          const h = Math.max(1, (v / max) * 30);
          return (
            <g key={e} onClick={() => onPick(e)} className="cursor-pointer">
              <rect x={e * W} y={0} width={W} height={32} fill="transparent" />
              <rect x={e * W + 0.4} y={32 - h} width={W - 0.8} height={h} rx={0.6}
                className={e === epoch ? 'fill-accent' : 'fill-line-strong hover:fill-faint'} />
              <title>{`${field.epochs[e].label} · ${formatDate(field.epochs[e].date)}: ${Math.round((v / max) * 100)}% of peak`}</title>
            </g>
          );
        })}
      </svg>
      <p className="label-mono mt-1 flex justify-between text-faint"><span>{formatDate(field.epochs[0].date)}</span><span>{formatDate(field.epochs[field.epochs.length - 1].date)}</span></p>
    </figure>
  );
}
