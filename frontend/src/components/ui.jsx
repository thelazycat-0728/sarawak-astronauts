import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import * as RTooltip from '@radix-ui/react-tooltip';
import * as RDialog from '@radix-ui/react-dialog';
import { X, CircleCheck, Info, TriangleAlert } from 'lucide-react';
import { cn } from '../lib/util';
import { GLOSSARY } from '../lib/data';
import { useUi } from '../store';

// ---------------------------------------------------------------- Button
const VARIANTS = {
  primary: 'bg-accent text-white hover:bg-accent-strong',
  outline: 'border border-line-strong bg-ink-900 text-fg hover:border-faint',
  subtle: 'border border-line bg-ink-800 text-fg hover:bg-ink-700',
  ghost: 'text-muted hover:bg-ink-800 hover:text-fg',
  danger: 'border border-bad/40 bg-bad/10 text-bad hover:bg-bad/20',
};
const SIZES = { sm: 'h-8 px-3 text-[13px]', md: 'h-10 px-4 text-sm', lg: 'h-12 px-5 text-[15px]', icon: 'size-10', iconSm: 'size-8' };

export function Button({ variant = 'outline', size = 'md', className, type = 'button', ...props }) {
  return (
    <button
      type={type}
      className={cn(
        'inline-flex shrink-0 items-center justify-center gap-2 rounded-md font-medium transition-colors disabled:pointer-events-none disabled:opacity-40',
        VARIANTS[variant], SIZES[size], className,
      )}
      {...props}
    />
  );
}

// ---------------------------------------------------------------- Segmented control
export function Segmented({ value, onChange, options, size = 'md', className, label }) {
  return (
    <div role="group" aria-label={label} className={cn('inline-flex rounded-md border border-line bg-ink-900 p-0.5', className)}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          aria-pressed={value === o.value}
          title={o.title}
          onClick={() => onChange(o.value)}
          className={cn(
            'rounded-[5px] px-3 font-medium whitespace-nowrap transition-colors',
            size === 'sm' ? 'h-7 text-xs' : 'h-9 text-sm',
            value === o.value ? 'bg-ink-700 text-fg shadow-[inset_0_0_0_1px_var(--color-line-strong)]' : 'text-muted hover:text-fg',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- Tooltip + glossary term
export const TipProvider = RTooltip.Provider;
export function Tip({ content, children, side = 'top' }) {
  return (
    <RTooltip.Root delayDuration={150}>
      <RTooltip.Trigger asChild>{children}</RTooltip.Trigger>
      <RTooltip.Portal>
        <RTooltip.Content side={side} sideOffset={6} className="z-[60] max-w-72 rounded-md border border-line-strong bg-ink-800 px-3 py-2 text-[13px] leading-snug text-fg shadow-xl">
          {content}
          <RTooltip.Arrow className="fill-ink-800" />
        </RTooltip.Content>
      </RTooltip.Portal>
    </RTooltip.Root>
  );
}

/** Plain-language label with a "?" that explains the technical term. */
export function Term({ id, children, className }) {
  const g = GLOSSARY[id];
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      {children}
      {g && (
        <Tip content={<><span className="font-semibold">{g.term}</span><br />{g.short}</>}>
          <button type="button" aria-label={`What is ${g.term}?`} className="inline-grid size-4 shrink-0 place-items-center rounded-full border border-faint text-[10px] leading-none text-muted normal-case tracking-normal hover:border-muted hover:text-fg">?</button>
        </Tip>
      )}
    </span>
  );
}

// ---------------------------------------------------------------- Dialogs
export function Modal({ open, onOpenChange, title, description, children, className }) {
  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <RDialog.Portal>
        <RDialog.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" />
        <RDialog.Content className={cn('fixed top-1/2 left-1/2 z-50 w-[calc(100%-32px)] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-xl border border-line-strong bg-ink-850 p-6 shadow-2xl', className)}>
          <RDialog.Title className="text-lg font-semibold">{title}</RDialog.Title>
          {description && <RDialog.Description className="mt-1 text-sm text-muted">{description}</RDialog.Description>}
          <div className="mt-5">{children}</div>
          <RDialog.Close asChild>
            <button type="button" aria-label="Close" className="absolute top-4 right-4 rounded-md p-1 text-muted hover:bg-ink-700 hover:text-fg"><X className="size-4" /></button>
          </RDialog.Close>
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  );
}

export function Sheet({ open, onOpenChange, title, description, children }) {
  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <RDialog.Portal>
        <RDialog.Overlay className="fixed inset-0 z-50 bg-black/40" />
        <RDialog.Content className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-line-strong bg-ink-850 shadow-2xl" style={{ paddingTop: 'env(safe-area-inset-top, 0px)', paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}>
          <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
            <div>
              <RDialog.Title className="text-base font-semibold">{title}</RDialog.Title>
              {description && <RDialog.Description className="mt-0.5 text-[13px] text-muted">{description}</RDialog.Description>}
            </div>
            <RDialog.Close asChild>
              <button type="button" aria-label="Close" className="rounded-md p-1 text-muted hover:bg-ink-700 hover:text-fg"><X className="size-4" /></button>
            </RDialog.Close>
          </div>
          {children}
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  );
}

// ---------------------------------------------------------------- Toasts
export function Toaster() {
  const toasts = useUi((s) => s.toasts);
  return (
    <div aria-live="polite" className="pointer-events-none fixed right-4 bottom-4 z-[70] flex w-[min(380px,calc(100%-32px))] flex-col gap-2" style={{ marginBottom: 'env(safe-area-inset-bottom, 0px)' }}>
      {toasts.map((t) => {
        const Icon = t.kind === 'good' ? CircleCheck : t.kind === 'warn' ? TriangleAlert : Info;
        return (
          <div key={t.id} className="pointer-events-auto flex items-start gap-3 rounded-lg border border-line-strong bg-ink-800 px-4 py-3 text-sm shadow-xl">
            <Icon className={cn('mt-0.5 size-4 shrink-0', t.kind === 'good' ? 'text-good' : t.kind === 'warn' ? 'text-amber' : 'text-accent-fg')} />
            <span className="min-w-0 break-words">{t.msg}</span>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- Layout bits
export function Kicker({ children, className }) {
  return (
    <p className={cn('label-mono flex items-center gap-3 text-muted', className)}>
      <span className="h-px w-6 bg-faint" aria-hidden="true" />
      {children}
    </p>
  );
}
export function StepBadge({ n }) {
  return <span className="grid size-7 shrink-0 place-items-center rounded border border-accent/50 bg-accent-soft font-mono text-[11px] text-accent-fg">{String(n).padStart(2, '0')}</span>;
}
export function Panel({ className, children, ...props }) {
  return <section className={cn('rounded-xl border border-line bg-ink-900', className)} {...props}>{children}</section>;
}
export function Chip({ children, className, tone = 'default' }) {
  const tones = {
    default: 'border-line-strong text-muted',
    accent: 'border-accent/50 text-accent-fg bg-accent-soft',
    good: 'border-good/40 text-good bg-good/10',
    warn: 'border-amber/40 text-amber bg-amber/10',
    bad: 'border-bad/40 text-bad bg-bad/10',
    teal: 'border-teal/40 text-teal bg-teal/10',
  };
  return <span className={cn('inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium whitespace-nowrap', tones[tone], className)}>{children}</span>;
}
export function Switch({ checked, onChange, label, id }) {
  return (
    <label htmlFor={id} className="flex cursor-pointer items-center justify-between gap-3 text-sm">
      <span>{label}</span>
      <button
        id={id} type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
        className={cn('relative h-5 w-9 shrink-0 rounded-full border transition-colors', checked ? 'border-accent bg-accent' : 'border-line-strong bg-ink-700')}
      >
        <span className={cn('absolute top-0.5 size-3.5 rounded-full bg-white transition-all', checked ? 'left-[18px]' : 'left-0.5')} />
      </button>
    </label>
  );
}

export function Logo({ className }) {
  return (
    <svg viewBox="0 0 32 32" className={className} fill="none" aria-hidden="true">
      <circle cx="16" cy="16" r="13" stroke="currentColor" strokeWidth="2" />
      {[0, 60, 120, 180, 240, 300].map((a) => (
        <path key={a} d="M16 3.5 L22.5 15" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" transform={`rotate(${a} 16 16)`} />
      ))}
    </svg>
  );
}

// ---------------------------------------------------------------- Sky canvas
/** Canvas sized to its container. draw(ctx, W, H) paints in CSS pixels; onPick gets image pixel coords. */
export function SkyCanvas({ nx, ny, draw, onPick, className, label, children }) {
  const wrap = useRef(null);
  const cv = useRef(null);
  const [w, setW] = useState(0);

  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return undefined;
    setW(el.clientWidth);
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const c = cv.current;
    if (!c || !w || !draw) return;
    const h = (w * ny) / nx;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    c.width = Math.round(w * dpr);
    c.height = Math.round(h * dpr);
    const ctx = c.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw(ctx, w, h);
  }, [draw, w, nx, ny]);

  const pick = (e) => {
    if (!onPick) return;
    const r = cv.current.getBoundingClientRect();
    onPick(((e.clientX - r.left) / r.width) * nx, ((e.clientY - r.top) / r.height) * ny);
  };

  return (
    <div ref={wrap} className={cn('relative w-full overflow-hidden', className)} style={{ aspectRatio: `${nx} / ${ny}` }}>
      <canvas ref={cv} role="img" aria-label={label} onClick={pick} className={cn('absolute inset-0 size-full', onPick ? 'cursor-crosshair' : '')} />
      {children}
    </div>
  );
}
