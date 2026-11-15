import { useState } from 'react';
import { Search, ChevronRight, Sparkles, Crosshair, Lightbulb } from 'lucide-react';
import { Button, Segmented, Term, StepBadge } from './ui';
import { useViewer } from '../store';
import { PRESETS } from '../lib/data';
import { parseRA, parseDec, cn } from '../lib/util';

const DEFAULT_TIP = 'Look for a point of light that “jumps” while the background stays still. A flash in one snapshot only is usually a glitch, not an object.';

/** Left panel shared by Explore and Play: typed coordinates, view size and the presets. */
export function TargetPanel({ tip = DEFAULT_TIP }) {
  const v = useViewer();
  const [ra, setRa] = useState(v.ra);
  const [dec, setDec] = useState(v.dec);
  const [err, setErr] = useState('');
  // Resync the inputs when the target changes elsewhere (adjusting state during render).
  const [synced, setSynced] = useState([v.ra, v.dec]);
  if (synced[0] !== v.ra || synced[1] !== v.dec) {
    setSynced([v.ra, v.dec]); setRa(v.ra); setDec(v.dec); setErr('');
  }

  const explore = (e) => {
    e.preventDefault();
    const r = parseRA(ra), d = parseDec(dec);
    if (r == null) { setErr('Right ascension should look like 17h57m48.5s or 269.45 (degrees).'); return; }
    if (d == null) { setErr('Declination should look like +04°41′36″ or 4.69 (degrees, −90 to +90).'); return; }
    const match = PRESETS.find((p) => Math.abs(p.ra - r) < 0.01 && Math.abs(p.dec - d) < 0.01);
    if (match) v.loadPreset(match.id); else v.setField(ra.trim(), dec.trim());
  };

  return (
    <aside className="flex flex-col gap-6 p-6">
      <form id="tour-coords" onSubmit={explore} className="grid gap-5">
        <div className="flex items-start gap-3">
          <StepBadge n={1} />
          <div>
            <p className="label-mono text-muted">Point the telescope</p>
            <h2 className="mt-1 text-xl font-medium">Choose a patch of sky</h2>
          </div>
        </div>
        <CoordInput id="ra" label={<Term id="ra">Sky position · right ascension</Term>} value={ra} onChange={setRa} placeholder="17h57m48.50s" />
        <CoordInput id="dec" label={<Term id="dec">Sky position · declination</Term>} value={dec} onChange={setDec} placeholder="+04°41′36.2″" />
        {err && <p role="alert" className="-mt-2 text-[13px] text-bad">{err}</p>}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="label-mono text-muted"><Term id="fov">View size (field of view)</Term></span>
          <Segmented size="sm" label="Field of view" value={v.fov} onChange={v.setFov}
            options={[{ value: 3, label: '3′', title: 'Fastest to load' }, { value: 6, label: '6′' }, { value: 12, label: '12′', title: 'Catches faster movers' }]} />
        </div>
        <Button type="submit" variant="primary" size="lg"><Search className="size-4" />Explore this field</Button>
      </form>

      <div id="tour-presets">
        <p className="label-mono mb-2 flex items-center gap-3 text-faint"><span className="h-px flex-1 bg-line" />or try a known object<span className="h-px flex-1 bg-line" /></p>
        <ul className="divide-y divide-line">
          {PRESETS.map((p) => {
            const active = v.targetId === p.id;
            return (
              <li key={p.id}>
                <button
                  type="button" onClick={() => v.loadPreset(p.id)} aria-current={active}
                  className={cn('group flex w-full items-center gap-3 border-l-2 px-3 py-3.5 text-left transition-colors',
                    active ? 'border-accent bg-ink-800' : 'border-transparent hover:bg-ink-850')}
                >
                  <Sparkles className={cn('size-4 shrink-0', active ? 'text-accent-fg' : 'text-faint')} />
                  <span className="min-w-0 flex-1">
                    <span className="block text-[15px] font-medium">{p.name}</span>
                    <span className="block truncate text-[12.5px] text-amber/80">{p.type}</span>
                  </span>
                  <ChevronRight className="size-4 text-faint transition-transform group-hover:translate-x-0.5" />
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="rounded-lg border border-line bg-ink-850 p-4 text-[13px] leading-relaxed">
        <p className="mb-1 flex items-center gap-2 font-medium text-accent-fg"><Lightbulb className="size-4" />Citizen science tip</p>
        <p className="text-muted">{tip}</p>
      </div>
    </aside>
  );
}

function CoordInput({ id, label, value, onChange, placeholder }) {
  return (
    <label htmlFor={`coord-${id}`} className="grid gap-2">
      <span className="label-mono text-muted">{label}</span>
      <span className="flex h-12 items-center gap-3 rounded-md border border-line-strong bg-ink-950 px-4 focus-within:border-accent">
        <Crosshair className="size-4 shrink-0 text-faint" />
        <input
          id={`coord-${id}`} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
          spellCheck={false} autoComplete="off" className="min-w-0 flex-1 bg-transparent font-mono text-[15px] outline-none placeholder:text-faint"
        />
      </span>
    </label>
  );
}
