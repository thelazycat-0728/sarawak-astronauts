import { useRef } from 'react';
import { formatDate, cn } from '../lib/util';

/**
 * Timeline of a field's snapshots with a sliding comparison window.
 *
 * win = [a, b]: the first and last snapshot compared (indexes into epochs, a < b).
 * Drag either handle to resize the window, or drag the shaded window itself to slide it
 * along the timeline. Arrow keys do the same on a focused handle or window. Clicking a
 * snapshot inside the window shows it (cur).
 */
export function WindowTimeline({ epochs, win, onWin, cur, onCur }) {
  const track = useRef(null);
  const drag = useRef(null);
  const n = epochs.length;
  const [a, b] = win;
  const t0 = epochs[0].t, span = epochs[n - 1].t - t0 || 1;
  const pos = (i) => ((epochs[i].t - t0) / span) * 100;

  /** Snapshot nearest to a pointer x position. */
  const nearest = (clientX) => {
    const r = track.current.getBoundingClientRect();
    const t = t0 + Math.min(1, Math.max(0, (clientX - r.left) / r.width)) * span;
    let best = 0;
    epochs.forEach((e, i) => { if (Math.abs(e.t - t) < Math.abs(epochs[best].t - t)) best = i; });
    return best;
  };

  const set = (na, nb) => {
    const lo = Math.max(0, Math.min(na, n - 2)), hi = Math.min(n - 1, Math.max(nb, lo + 1));
    if (lo !== a || hi !== b) onWin([lo, hi]);
  };
  const slide = (d) => {
    const w = b - a;
    const lo = Math.min(n - 1 - w, Math.max(0, a + d));
    if (lo !== a) onWin([lo, lo + w]);
  };

  const start = (e) => {
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { kind: e.currentTarget.dataset.kind, from: nearest(e.clientX), a, b };
  };
  const move = (e) => {
    const d = drag.current;
    if (!d) return;
    const i = nearest(e.clientX);
    if (d.kind === 'a') set(Math.min(i, b - 1), b);
    else if (d.kind === 'b') set(a, Math.max(i, a + 1));
    else {
      const w = d.b - d.a;
      const lo = Math.min(n - 1 - w, Math.max(0, d.a + i - d.from));
      if (lo !== a) onWin([lo, lo + w]);
    }
  };
  const end = () => { drag.current = null; };

  const keys = (kind) => (e) => {
    const d = e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? -1 : e.key === 'ArrowRight' || e.key === 'ArrowUp' ? 1 : 0;
    if (!d) return;
    e.preventDefault();
    if (kind === 'a') set(Math.min(a + d, b - 1), b);
    else if (kind === 'b') set(a, Math.max(b + d, a + 1));
    else slide(d);
  };

  const months = ((epochs[b].t - epochs[a].t) * 12).toFixed(0);
  const every = n > 8 ? 3 : 1;
  const fmt = n > 8 ? { month: 'short', year: '2-digit' } : undefined;

  return (
    <div className="min-w-0">
      <p className="label-mono mb-3 flex flex-wrap justify-between gap-2 text-muted">
        <span>Comparison window <span className="normal-case tracking-normal text-faint">(drag the ends, or slide the window)</span></span>
        <span className="text-fg">{epochs[a].label} → {epochs[b].label} · {months} months</span>
      </p>
      <div ref={track} className="relative mx-2 h-16 touch-none select-none" onPointerMove={move} onPointerUp={end} onPointerCancel={end}>
        <div className="absolute top-3 right-0 left-0 h-px bg-line-strong" />
        {/* The window */}
        <div role="slider" tabIndex={0} aria-label="Comparison window" aria-valuemin={0} aria-valuemax={n - 1} aria-valuenow={a}
          aria-valuetext={`${epochs[a].label} to ${epochs[b].label}`}
          data-kind="win" onPointerDown={start} onKeyDown={keys('win')}
          className="absolute top-0 h-6 cursor-grab rounded-md border border-accent/60 bg-accent-soft outline-none focus-visible:ring-2 focus-visible:ring-accent active:cursor-grabbing"
          style={{ left: `${pos(a)}%`, width: `${pos(b) - pos(a)}%` }} />
        {/* Snapshots */}
        {epochs.map((e, i) => {
          const inside = i >= a && i <= b;
          return (
            <div key={e.date} className="absolute top-3 -translate-x-1/2 -translate-y-1/2" style={{ left: `${pos(i)}%` }}>
              <button type="button" disabled={!inside} onClick={() => onCur(i)} aria-label={`Show ${e.label}, ${formatDate(e.date)}`}
                className={cn('relative block rounded-full transition-all',
                  i === cur ? 'size-3.5 bg-fg ring-4 ring-fg/20' : inside ? 'size-2.5 bg-accent-fg hover:bg-fg' : 'size-2 bg-faint')} />
            </div>
          );
        })}
        {/* Handles */}
        {[['a', a], ['b', b]].map(([kind, i]) => (
          <div key={kind} role="slider" tabIndex={0} aria-label={kind === 'a' ? 'Window start' : 'Window end'}
            aria-valuemin={0} aria-valuemax={n - 1} aria-valuenow={i} aria-valuetext={`${epochs[i].label}, ${formatDate(epochs[i].date)}`}
            data-kind={kind} onPointerDown={start} onKeyDown={keys(kind)}
            className="absolute top-[-3px] z-10 h-[30px] w-3 -translate-x-1/2 cursor-ew-resize rounded-sm border border-accent bg-accent outline-none focus-visible:ring-2 focus-visible:ring-fg"
            style={{ left: `${pos(i)}%` }} />
        ))}
        {/* Dates */}
        {epochs.map((e, i) => ((i % every === 0 || i === a || i === b) && (
          <span key={e.date} className={cn('label-mono absolute top-9 whitespace-nowrap normal-case tracking-[0.04em]',
            i === 0 ? '-left-1' : i === n - 1 ? '-right-1' : '-translate-x-1/2', i === a || i === b ? 'text-fg' : 'text-faint')}
            style={i === 0 || i === n - 1 ? undefined : { left: `${pos(i)}%` }}>
            {formatDate(e.date, fmt)}
          </span>
        )))}
      </div>
    </div>
  );
}
