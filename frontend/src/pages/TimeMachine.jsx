import { useCallback, useEffect, useMemo, useState } from 'react';
import { Play, Pause, SkipBack, SkipForward } from 'lucide-react';
import { Button, Segmented, Kicker, Panel, Chip, Switch, SkyCanvas } from '../components/ui';
import { PRESET_BY_ID, TM_TARGETS, TM_SURVEYS, timeMachineEpochs } from '../lib/data';
import { simulate, paintImage, paintComposite, LUTS, drawReticle, drawCompass, PX } from '../lib/sim';
import { cn } from '../lib/util';

const reduced = () => { try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; } };

export default function TimeMachine() {
  const [targetId, setTargetId] = useState('barnard');
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(!reduced());
  const [thenNow, setThenNow] = useState(false);
  const [showTrack, setShowTrack] = useState(true);

  const cfg = TM_TARGETS.find((t) => t.id === targetId);
  const preset = PRESET_BY_ID[targetId];
  const epochs = useMemo(() => timeMachineEpochs(), []);
  const field = useMemo(() => simulate({
    key: `tm-${targetId}`, seed: preset.seed + 77, fov: cfg.fov, arrayIdx: 3, epochs, place: 'centre', density: 0.9,
    object: {
      ...preset, kind: 'motion', flux: Array(6).fill(cfg.brightness),
      epochMult: (e) => (cfg.coldBefore && e.year < cfg.coldBefore ? 0 : 1),
    },
  }), [targetId, preset, cfg, epochs]);

  // Restart from the first frame when the target changes (adjusting state during render).
  const [shownTarget, setShownTarget] = useState(targetId);
  if (shownTarget !== targetId) { setShownTarget(targetId); setI(0); setThenNow(false); }
  useEffect(() => {
    if (!playing || thenNow) return undefined;
    const id = setTimeout(() => setI((k) => (k + 1) % epochs.length), i === epochs.length - 1 ? 2200 : 750);
    return () => clearTimeout(id);
  }, [playing, thenNow, i, epochs.length]);

  const first = field.posAt(0);
  const now = field.posAt(i);
  const moved = Math.hypot(now[0] - first[0], now[1] - first[1]) * PX;
  const total = Math.hypot(field.posAt(epochs.length - 1)[0] - first[0], field.posAt(epochs.length - 1)[1] - first[1]) * PX;
  const ep = epochs[i];
  const survey = TM_SURVEYS[ep.survey];

  const draw = useCallback((ctx, W, H) => {
    const { nx, ny, stacks } = field;
    const sx = W / nx, sy = H / ny;
    if (thenNow) paintComposite(ctx, W, H, stacks[0], stacks[epochs.length - 1], nx, ny, { vmin: field.vmin, vmax: field.vmax });
    else paintImage(ctx, W, H, stacks[i], nx, ny, { vmin: field.vmin, vmax: field.vmax, lut: LUTS.sky });
    if (showTrack && !thenNow) {
      ctx.save();
      ctx.strokeStyle = 'rgba(184,175,255,.4)';
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      epochs.forEach((_, k) => { const [x, y] = field.posAt(k); if (k) ctx.lineTo(x * sx, y * sy); else ctx.moveTo(x * sx, y * sy); });
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.strokeStyle = 'rgba(69,211,184,.85)';
      ctx.beginPath(); ctx.arc(first[0] * sx, first[1] * sy, 10, 0, Math.PI * 2); ctx.stroke();
      ctx.restore();
      drawReticle(ctx, now[0] * sx, now[1] * sy, { label: preset.name, sub: `${ep.date} · ${moved.toFixed(0)}″ since 1998`, r: 12 });
    }
    drawCompass(ctx, W, H, cfg.fov, nx);
  }, [field, i, thenNow, showTrack, epochs, first, now, preset.name, ep.date, moved, cfg.fov]);

  const yMin = 1997, yMax = 2028;
  const X = (y) => ((y - yMin) / (yMax - yMin)) * 100;

  return (
    <div className="mx-auto max-w-[1440px] px-[max(16px,env(safe-area-inset-left,0px))] py-10 lg:px-10">
      <Kicker>25-year time machine</Kicker>
      <div className="mt-4 flex flex-wrap items-end justify-between gap-6">
        <div className="max-w-2xl">
          <h1 className="text-4xl font-medium tracking-tight text-balance">
            27 years of infrared sky, <span className="font-serif font-normal text-accent-fg italic">in seconds.</span>
          </h1>
          <p className="mt-3 text-muted">SPHEREx is the latest chapter. Older infrared surveys stretch the timeline back to 1998, so slow motion becomes a march across the screen.</p>
        </div>
        <Segmented value={targetId} onChange={setTargetId} label="Target"
          options={TM_TARGETS.map((t) => ({ value: t.id, label: PRESET_BY_ID[t.id].name, title: t.note }))} />
      </div>

      <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0">
          <div className="overflow-hidden rounded-xl border border-line bg-ink-950">
            <SkyCanvas nx={field.nx} ny={field.ny} draw={draw} label={`${preset.name} as seen by ${ep.survey} in ${ep.date}`} />
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button variant="outline" size="icon" aria-label="Previous epoch" onClick={() => { setPlaying(false); setI((k) => (k - 1 + epochs.length) % epochs.length); }}><SkipBack className="size-4" /></Button>
            <Button variant="outline" size="icon" aria-label={playing ? 'Pause' : 'Play'} disabled={thenNow} onClick={() => setPlaying(!playing)}>{playing ? <Pause className="size-4" /> : <Play className="size-4" />}</Button>
            <Button variant="outline" size="icon" aria-label="Next epoch" onClick={() => { setPlaying(false); setI((k) => (k + 1) % epochs.length); }}><SkipForward className="size-4" /></Button>
            <div className="ml-auto flex flex-wrap gap-5">
              <Switch id="tm-track" label="Predicted track" checked={showTrack} onChange={setShowTrack} />
              <Switch id="tm-thennow" label="Then & now" checked={thenNow} onChange={setThenNow} />
            </div>
          </div>
        </div>

        <Panel className="flex flex-col gap-5 self-start p-6" aria-live="polite">
          {thenNow ? (
            <div>
              <p className="label-mono text-muted">Then & now</p>
              <p className="mt-1 text-5xl font-medium tabular">1998 → 2026</p>
              <p className="mt-2 text-sm text-muted"><span className="text-[#7fb2ff]">Blue</span> is the first image, <span className="text-amber">amber</span> the latest. Fixed stars come out white; the mover leaves a blue and amber pair.</p>
            </div>
          ) : (
            <>
              <div>
                <p className="label-mono text-muted">Year</p>
                <p className="mt-1 text-6xl font-medium tabular">{ep.date}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Chip className="border-current" tone="default"><span style={{ color: survey.color }}>{ep.survey}</span></Chip>
                <span className="label-mono text-muted normal-case tracking-normal">{survey.band} · {survey.wave} µm · {ep.n} image{ep.n > 1 ? 's' : ''} stacked</span>
              </div>
              <div>
                <p className="label-mono text-muted">Moved since 1998</p>
                <p className="mt-1 text-4xl font-medium tabular">{moved.toFixed(0)}″</p>
                <p className="mt-1 text-sm text-muted">of {total.toFixed(0)}″ in total, about {(total / 60).toFixed(1)} arcminutes.</p>
              </div>
            </>
          )}
          {cfg.coldBefore && <p className="rounded-md border border-amber/30 bg-amber/10 p-3 text-[13px]">Too cold for 2MASS: it appears only from {cfg.coldBefore}, when WISE could see it.</p>}
          <p className="text-[13px] text-muted">Brightness isn’t comparable between telescopes. Watch position, not brightness.</p>
        </Panel>
      </div>

      <Panel className="mt-6 p-5">
        <div className="relative h-16" role="group" aria-label="Observations timeline">
          <div className="absolute top-4 right-0 left-0 h-px bg-line-strong" />
          {[2000, 2005, 2010, 2015, 2020, 2025].map((y) => (
            <span key={y} className="label-mono absolute top-9 -translate-x-1/2 text-faint" style={{ left: `${X(y)}%` }}>{y}</span>
          ))}
          {epochs.map((e, k) => (
            <button key={`${e.survey}-${e.year}`} type="button" onClick={() => { setPlaying(false); setThenNow(false); setI(k); }}
              aria-label={`${e.survey} ${e.date}`}
              className={cn('absolute top-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 transition-all', k === i ? 'size-4' : 'size-2.5')}
              style={{ left: `${X(e.year)}%`, borderColor: TM_SURVEYS[e.survey].color, background: k <= i ? TM_SURVEYS[e.survey].color : 'var(--color-ink-950)' }} />
          ))}
        </div>
        <div className="mt-2 flex flex-wrap gap-5 text-[13px] text-muted">
          {['2MASS', 'WISE', 'SPHEREx'].map((s) => (
            <span key={s} className="flex items-center gap-2"><span className="size-2.5 rounded-full" style={{ background: TM_SURVEYS[s].color }} />{s === 'WISE' ? 'WISE / NEOWISE' : s}</span>
          ))}
          <span className="ml-auto text-faint">Demo frames. The real pipeline aligns 2MASS, WISE/NEOWISE and SPHEREx cutouts from IRSA.</span>
        </div>
      </Panel>
    </div>
  );
}
