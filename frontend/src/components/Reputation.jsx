import { useMemo, useState } from 'react';
import { Gauge, Timer } from 'lucide-react';
import { Panel, Chip } from './ui';
import { reputationFor, cooldownFor, formatWait, TIERS } from '../lib/reputation';
import { useNow } from '../lib/reputationHooks';
import { cn } from '../lib/util';

/** Reputation score, tier, next-submission wait and recent changes. */
export function ReputationCard({ mine, className, showHistory = true }) {
  const { score, tier, history } = useMemo(() => reputationFor(mine), [mine]);
  const [pending, setPending] = useState(() => cooldownFor(mine).remainingMs > 0);
  const now = useNow(pending);
  const cool = cooldownFor(mine, now);
  if (pending !== cool.remainingMs > 0) setPending(cool.remainingMs > 0); // adjust during render

  return (
    <Panel className={cn('p-6', className)}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="label-mono text-muted">Reputation</p>
          <p className="mt-1 flex items-center gap-2 text-3xl font-medium"><Gauge className="size-6 text-accent-fg" />{tier.name}</p>
        </div>
        <p className="tabular text-right"><span className="text-3xl font-medium">{score}</span> <span className="text-muted">/ 100</span></p>
      </div>
      <div className="relative mt-4 h-2 overflow-hidden rounded-full bg-ink-700">
        <div className={cn('h-full rounded-full', { trusted: 'bg-good', good: 'bg-accent', probation: 'bg-amber', low: 'bg-bad' }[tier.id])} style={{ width: `${score}%` }} />
        {TIERS.filter((t) => t.min > 0).map((t) => <span key={t.id} className="absolute inset-y-0 w-px bg-ink-950" style={{ left: `${t.min}%` }} aria-hidden="true" />)}
      </div>
      <p className="mt-3 text-sm text-muted">{tier.text}</p>
      <div className="mt-4 flex flex-wrap gap-2">
        <Chip tone={tier.tone}>Wait between submissions: {formatWait(tier.cooldownMin * 60e3)}</Chip>
        <Chip tone={cool.remainingMs > 0 ? 'warn' : 'good'}>
          <Timer className="size-3" />{cool.remainingMs > 0 ? `Next submission in ${formatWait(cool.remainingMs)}` : 'You can submit now'}
        </Chip>
      </div>
      {showHistory && history.length > 0 && (
        <ul className="mt-4 grid gap-1.5 border-t border-line pt-4 text-sm">
          {history.slice(0, 4).map((h, i) => (
            <li key={i} className="flex justify-between gap-3">
              <span className="truncate text-muted"><span className="font-mono text-faint">{h.code}</span> · {h.reason}</span>
              <span className={cn('tabular', h.delta > 0 ? 'text-good' : 'text-bad')}>{h.delta > 0 ? '+' : ''}{h.delta}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
