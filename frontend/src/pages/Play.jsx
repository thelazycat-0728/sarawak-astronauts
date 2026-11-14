import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { Play as PlayIcon, Pause, Lightbulb, Eye, ArrowRight, RotateCcw } from 'lucide-react';
import { Button, Segmented, Kicker, Panel, Chip, SkyCanvas } from '../components/ui';
import { useProgress, useAuth, useUi } from '../store';
import { LEVELS, PRESET_BY_ID, SURVEYS, ARRAYS } from '../lib/data';
import { simulate, paintImage, paintDiff, lutForArray, drawCompass, PX } from '../lib/sim';
import { cn, formatDate } from '../lib/util';

const reduced = () => { try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; } };

function levelObject(level) {
  return level.preset ? PRESET_BY_ID[level.preset] : level.object;
}
function levelEpochs(level) {
  return level.preset ? PRESET_BY_ID[level.preset].epochs : SURVEYS;
}

export default function Play() {
  const [params, setParams] = useSearchParams();
  const startId = LEVELS.some((l) => l.id === params.get('level')) ? params.get('level') : LEVELS[0].id;
  const [levelId, setLevelId] = useState(startId);
  const level = LEVELS.find((l) => l.id === levelId);
  const [arrayIdx, setArrayIdx] = useState(level.defaultArray ?? level.arrays[0]);
  const [view, setView] = useState('flip');
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(!reduced());
  const [blinkB, setBlinkB] = useState(false);
  const [guesses, setGuesses] = useState([]);
  const [hint, setHint] = useState(false);
  const [result, setResult] = useState(null); // { won, points, stars, dist }
  const [feedback, setFeedback] = useState(null);

  const games = useProgress((s) => s.games);
  const recordGame = useProgress((s) => s.recordGame);
  const user = useAuth((s) => s.user);
  const addPoints = useAuth((s) => s.addPoints);
  const toast = useUi((s) => s.toast);

  const object = levelObject(level);
  const epochs = levelEpochs(level);
  const field = useMemo(() => simulate({
    key: `game-${level.id}-${arrayIdx}`, seed: level.seed, fov: 5, arrayIdx, object, epochs, exps: 3, cosmic: level.cosmic, place: 'random',
  }), [level, arrayIdx, object, epochs]);

  const loadLevel = (id) => {
    const l = LEVELS.find((x) => x.id === id);
    setLevelId(id);
    setParams({ level: id }, { replace: true });
    setArrayIdx(l.defaultArray ?? l.arrays[0]);
    setView('flip'); setFrame(0); setPlaying(!reduced()); setBlinkB(false);
    setGuesses([]); setHint(false); setResult(null); setFeedback(null);
  };

  useEffect(() => {
    if (!playing || view === 'diff') return undefined;
    const id = setInterval(() => {
      if (view === 'flip') setFrame((f) => (f + 1) % field.frames.length);
      else setBlinkB((b) => !b);
    }, view === 'flip' ? 230 : 700);
    return () => clearInterval(id);
  }, [playing, view, field.frames.length]);

  const track = useMemo(() => {
    if (object.kind === 'asteroid') return [0, 1, 2].map((x) => field.posAt(object.astEpoch, x));
    return epochs.map((_, e) => field.posAt(e));
  }, [field, object, epochs]);

  const misses = guesses.filter((g) => !g.ok).length;
  const lastE = epochs.length - 1;

  const finish = (won, dist, missCount) => {
    let points = 0, stars = 0;
    if (won) {
      points = Math.max(10, Math.round(100 - 40 * (dist / level.tol)) - (hint ? 25 : 0) - 15 * missCount);
      stars = points >= 85 ? 3 : points >= 60 ? 2 : 1;
    }
    setResult({ won, points, stars });
    setPlaying(false);
    const prev = useProgress.getState().games[level.id];
    recordGame(level.id, { points, stars });
    if (won && user && (!prev || prev.stars < stars)) {
      const gain = (stars - (prev?.stars ?? 0)) * 2;
      addPoints(gain, `Training: ${level.preset ? PRESET_BY_ID[level.preset].name : 'bonus'} level`);
      toast(`+${gain} points for training.`, 'good');
    }
  };

  const onPick = (x, y) => {
    if (result) return;
    const d = Math.min(...track.map(([px, py]) => Math.hypot(px - x, py - y)));
    const invisible = object.flux[arrayIdx - 1] < 2;
    if (d <= level.tol && !invisible) {
      setGuesses((g) => [...g, { x, y, ok: true }]);
      setFeedback({ tone: 'good', text: 'Found it. The rings show where it was in each snapshot.' });
      finish(true, d, misses);
      return;
    }
    const ray = field.cosmicHits.find((c) => Math.hypot(c.x + 0.5 - x, c.y + 0.5 - y) < 2.2);
    const next = [...guesses, { x, y, ok: false }];
    setGuesses(next);
    if (d <= level.tol && invisible) setFeedback({ tone: 'warn', text: 'There’s nothing visible there in this array. Something might glow at a longer wavelength…' });
    else if (ray) setFeedback({ tone: 'info', text: 'That flash appears in one exposure only: a cosmic-ray hit on the detector, not a real object.' });
    else if (level.change) setFeedback({ tone: 'warn', text: 'That star holds steady. Look for one that brightens or fades.' });
    else setFeedback({ tone: 'warn', text: 'Nothing is moving there. Try Blink or Difference.' });
    if (next.filter((g) => !g.ok).length >= 3) finish(false, null, 3);
  };

  const draw = useCallback((ctx, W, H) => {
    const { nx, ny } = field;
    const sx = W / nx, sy = H / ny;
    const opts = { vmin: field.vmin, vmax: field.vmax, lut: lutForArray(arrayIdx), smooth: false };
    if (view === 'flip') paintImage(ctx, W, H, field.frames[frame].data, nx, ny, opts);
    else if (view === 'blink') paintImage(ctx, W, H, field.stacks[blinkB ? lastE : 0], nx, ny, opts);
    else paintDiff(ctx, W, H, field.stacks[0], field.stacks[lastE], nx, ny, { smooth: false });

    if (hint && !result) {
      const mx = track.reduce((s, p) => s + p[0], 0) / track.length, my = track.reduce((s, p) => s + p[1], 0) / track.length;
      ctx.save(); ctx.setLineDash([6, 5]); ctx.strokeStyle = 'rgba(69,211,184,.85)'; ctx.lineWidth = 1.5;
      ctx.strokeRect((mx - 8 + (level.seed % 3)) * sx, (my - 6) * sy, 16 * sx, 12 * sy); ctx.restore();
    }
    for (const g of guesses) {
      ctx.save(); ctx.lineWidth = 2.5;
      if (g.ok) { ctx.strokeStyle = '#4ade80'; ctx.beginPath(); ctx.arc(g.x * sx, g.y * sy, 18, 0, Math.PI * 2); ctx.stroke(); }
      else {
        ctx.strokeStyle = '#f87171'; const r = 8;
        ctx.beginPath(); ctx.moveTo(g.x * sx - r, g.y * sy - r); ctx.lineTo(g.x * sx + r, g.y * sy + r);
        ctx.moveTo(g.x * sx + r, g.y * sy - r); ctx.lineTo(g.x * sx - r, g.y * sy + r); ctx.stroke();
      }
      ctx.restore();
    }
    if (result) {
      ctx.save(); ctx.lineWidth = 2;
      track.forEach(([x, y], i) => {
        const f = track.length > 1 ? i / (track.length - 1) : 1;
        ctx.strokeStyle = `rgb(${Math.round(69 + 186 * f)},${Math.round(211 - 34 * f)},${Math.round(184 - 92 * f)})`;
        ctx.beginPath(); ctx.arc(x * sx, y * sy, 12, 0, Math.PI * 2); ctx.stroke();
      });
      ctx.restore();
    }
    drawCompass(ctx, W, H, 5, nx);
  }, [field, view, frame, blinkB, lastE, arrayIdx, hint, result, guesses, track, level.seed]);

  const f = field.frames[frame];
  const hud = view === 'flip'
    ? `${epochs[f.epoch].label} · ${formatDate(epochs[f.epoch].date)} · exposure ${f.exp + 1}/3`
    : view === 'blink' ? `${epochs[blinkB ? lastE : 0].label} · ${formatDate(epochs[blinkB ? lastE : 0].date)}`
      : `${epochs[lastE].label} − ${epochs[0].label}`;
  const preset = level.preset ? PRESET_BY_ID[level.preset] : null;
  const facts = preset ? preset.facts : level.facts;
  const shift = object.kind === 'motion' ? Math.hypot(track[track.length - 1][0] - track[0][0], track[track.length - 1][1] - track[0][1]) * PX : null;

  return (
    <div className="mx-auto max-w-[1440px] px-[max(16px,env(safe-area-inset-left,0px))] py-10 lg:px-10">
      <Kicker>Training game</Kicker>
      <h1 className="mt-4 text-4xl font-medium tracking-tight">{level.change ? 'Did it change?' : 'Spot the mover'}</h1>
      <p className="mt-2 max-w-2xl text-muted">Simulated SPHEREx fields built around each real object’s motion and timing. Three tries per level.</p>

      <div className="mt-6 flex gap-2 overflow-x-auto pb-1">
        {LEVELS.map((l, i) => (
          <button key={l.id} type="button" onClick={() => loadLevel(l.id)} aria-current={l.id === levelId}
            className={cn('min-w-40 rounded-lg border px-4 py-3 text-left transition-colors', l.id === levelId ? 'border-accent bg-ink-800' : 'border-line bg-ink-900 hover:border-line-strong')}>
            <span className="label-mono block text-faint">{String(i + 1).padStart(2, '0')} · {l.change ? 'variable' : l.preset ? 'mover' : 'bonus'}</span>
            <span className="mt-1 block font-medium">{l.preset ? PRESET_BY_ID[l.preset].name : 'Unknown asteroid'}</span>
            <span className="text-sm text-amber">{'★'.repeat(games[l.id]?.stars ?? 0)}<span className="text-line-strong">{'★'.repeat(3 - (games[l.id]?.stars ?? 0))}</span></span>
          </button>
        ))}
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0">
          <div className="overflow-hidden rounded-xl border border-line bg-ink-950">
            <SkyCanvas nx={field.nx} ny={field.ny} draw={draw} onPick={onPick} label="Simulated SPHEREx field. Click the object you think is moving or changing.">
              <p className="label-mono absolute top-3 left-4 text-fg/90">{hud}</p>
              {feedback && (
                <p className={cn('absolute inset-x-4 bottom-4 rounded-md border bg-ink-900/95 px-4 py-2.5 text-sm',
                  feedback.tone === 'good' ? 'border-good/50' : feedback.tone === 'warn' ? 'border-amber/50' : 'border-teal/50')}>{feedback.text}</p>
              )}
            </SkyCanvas>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button variant="outline" size="icon" aria-label={playing ? 'Pause' : 'Play'} disabled={view === 'diff'} onClick={() => setPlaying(!playing)}>
              {playing ? <Pause className="size-4" /> : <PlayIcon className="size-4" />}
            </Button>
            {view === 'flip' && (
              <input type="range" min={0} max={field.frames.length - 1} value={frame} aria-label="Exposure"
                onChange={(e) => { setPlaying(false); setFrame(Number(e.target.value)); }} className="min-w-32 flex-1" />
            )}
            <Segmented value={view} onChange={(x) => { setView(x); if (x === 'diff') setPlaying(false); }} label="View" options={[
              { value: 'flip', label: 'Flipbook' }, { value: 'blink', label: 'Blink' }, { value: 'diff', label: 'Difference' },
            ]} />
            {level.arrays.length > 1 && (
              <Segmented value={arrayIdx} onChange={setArrayIdx} label="Detector array" options={level.arrays.map((a) => ({
                value: a, label: `Array ${a} · ${ARRAYS[a - 1].lo}–${ARRAYS[a - 1].hi} µm`,
              }))} />
            )}
          </div>
          {view === 'diff' && <p className="mt-3 text-sm text-muted"><span className="text-teal">Teal</span> = was here, <span className="text-amber">amber</span> = now here. Fixed stars cancel out.</p>}
        </div>

        <Panel className="flex flex-col gap-4 self-start p-6" aria-live="polite">
          <div className="flex flex-wrap gap-2">
            <Chip tone={level.change ? 'teal' : 'accent'}>{level.change ? 'Changing brightness' : level.preset ? 'Moving object' : 'Bonus · fast mover'}</Chip>
            <Chip>{'●'.repeat(level.difficulty)}{'○'.repeat(3 - level.difficulty)}</Chip>
          </div>
          <h2 className="text-2xl font-medium">{preset ? preset.name : 'Unknown asteroid'}</h2>
          <p className="text-sm">{level.mission}</p>
          <div className="flex items-center gap-2 text-sm text-muted">
            Tries left
            <span className="flex gap-1.5">{[0, 1, 2].map((i) => <span key={i} className={cn('size-3 rounded-full border-2', i < misses ? 'border-line-strong bg-line-strong' : 'border-accent')} />)}</span>
          </div>
          {hint && !result && <p className="border-l-2 border-teal pl-3 text-sm">{level.hint}</p>}
          {!result && (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" disabled={hint} onClick={() => setHint(true)}><Lightbulb className="size-4" />Hint (−25)</Button>
              <Button size="sm" variant="ghost" onClick={() => { setFeedback(null); finish(false, null, misses); }}><Eye className="size-4" />Show me</Button>
            </div>
          )}
          {result && (
            <div className="grid gap-3 border-t border-line pt-4">
              <p className={cn('text-2xl font-medium', result.won ? 'text-good' : 'text-bad')}>{result.won ? `Found it · +${result.points}` : 'Here’s where it was'}</p>
              <p className="text-xl tracking-widest text-amber">{'★'.repeat(result.stars)}<span className="text-line-strong">{'★'.repeat(3 - result.stars)}</span></p>
              {shift != null && <p className="label-mono text-muted normal-case tracking-normal">Moved {shift.toFixed(1)}″ ({(shift / PX).toFixed(1)} px) from {epochs[0].label} to {epochs[lastE].label}</p>}
              <ul className="grid gap-1.5 text-sm">{facts.map((x) => <li key={x} className="flex gap-2"><span className="mt-2 size-1.5 shrink-0 rounded-sm bg-accent" />{x}</li>)}</ul>
              <div className="flex flex-wrap gap-2">
                <Button variant="primary" onClick={() => loadLevel(LEVELS[(LEVELS.indexOf(level) + 1) % LEVELS.length].id)}>Next level<ArrowRight className="size-4" /></Button>
                <Button variant="outline" onClick={() => loadLevel(level.id)}><RotateCcw className="size-4" />Play again</Button>
              </div>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
