import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import { Check, Clock, Gamepad2, Lock, Search, ArrowRight } from 'lucide-react';
import { Button, Kicker, Panel, Chip, StepBadge } from '../components/ui';
import { useProgress, useAuth } from '../store';
import { LessonDialog } from '../components/LessonDialog';
import { LESSONS, LEVELS, GLOSSARY, PRESET_BY_ID } from '../lib/data';
import { cn } from '../lib/util';

export default function Learn() {
  const navigate = useNavigate();
  const lessons = useProgress((s) => s.lessons);
  const games = useProgress((s) => s.games);
  const [q, setQ] = useState('');
  const [openLesson, setOpenLesson] = useState(null);

  const done = LESSONS.filter((l) => lessons[l.id]).length;
  const gamesDone = LEVELS.filter((l) => games[l.id]?.stars > 0).length;
  const user = useAuth((s) => s.user);
  const trained = Boolean(user?.trained) || (done >= 3 && gamesDone >= 2);

  const terms = useMemo(() => Object.entries(GLOSSARY).filter(([, g]) => (g.term + g.short).toLowerCase().includes(q.toLowerCase())), [q]);

  return (
    <div className="mx-auto max-w-[1280px] px-[max(16px,env(safe-area-inset-left,0px))] py-10 lg:px-10">
      <Kicker>Training</Kicker>
      <div className="mt-4 flex flex-wrap items-end justify-between gap-6">
        <div className="max-w-2xl">
          <h1 className="text-4xl font-medium tracking-tight text-balance">Learn to spot what moves</h1>
          <p className="mt-3 text-muted">Short hands-on lessons on the real test targets, each in a practice window that ticks off every step as you do it, then two training games. Finish three lessons and two game levels to unlock submitting discoveries in Discover.</p>
        </div>
        <Panel className="w-full max-w-sm p-5">
          <div className="flex items-center justify-between text-sm"><span>Your training</span><span className="tabular text-muted">{done}/{LESSONS.length} lessons · {gamesDone}/{LEVELS.length} levels</span></div>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-ink-700">
            <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${((done + gamesDone) / (LESSONS.length + LEVELS.length)) * 100}%` }} />
          </div>
          <p className={cn('mt-3 flex items-center gap-2 text-[13px]', trained ? 'text-good' : 'text-muted')}>
            {trained ? <Check className="size-4" /> : <Lock className="size-4" />}
            {trained ? 'Submissions unlocked.' : 'Submissions unlock after 3 lessons and 2 game levels.'}
          </p>
        </Panel>
      </div>

      <h2 className="label-mono mt-12 mb-4 text-muted">Lessons</h2>
      <div className="grid gap-4 md:grid-cols-2">
        {LESSONS.map((l, i) => {
          const p = PRESET_BY_ID[l.preset];
          const complete = lessons[l.id];
          return (
            <Panel key={l.id} className="flex flex-col gap-4 p-6">
              <div className="flex items-start justify-between gap-3">
                <StepBadge n={i + 1} />
                {complete ? <Chip tone="good"><Check className="size-3" />Complete</Chip> : <Chip><Clock className="size-3" />{l.minutes} min</Chip>}
              </div>
              <div>
                <p className="label-mono text-accent-fg">{l.skill}</p>
                <h3 className="mt-1 text-xl font-medium">{l.title}</h3>
                <p className="mt-1 text-sm text-muted">Uses {p.name}, {p.type.toLowerCase()}.</p>
              </div>
              <ol className="grid gap-1.5 text-sm text-muted">
                {l.tasks.map((t, k) => <li key={t.text} className="flex gap-2"><span className="font-mono text-faint">{k + 1}.</span>{t.text}</li>)}
              </ol>
              <Button variant={complete ? 'outline' : 'primary'} className="mt-auto self-start" onClick={() => setOpenLesson(l.id)}>
                {complete ? 'Do it again' : 'Start lesson'}<ArrowRight className="size-4" />
              </Button>
            </Panel>
          );
        })}
      </div>

      <h2 className="label-mono mt-12 mb-4 text-muted">Training games</h2>
      <div className="grid gap-4 md:grid-cols-2">
        {[
          { id: 'mover', title: 'Spot the mover', text: 'Blink through SPHEREx frames and click the object that moves. Learn to tell real movers from cosmic rays and noise.', levels: LEVELS.filter((l) => !l.change) },
          { id: 'change', title: 'Did it change?', text: 'Find the star whose brightness changes, and learn what real variability looks like compared with noise.', levels: LEVELS.filter((l) => l.change) },
        ].map((g) => (
          <Panel key={g.id} className="flex flex-col gap-4 p-6">
            <Gamepad2 className="size-6 text-accent-fg" />
            <div>
              <h3 className="text-xl font-medium">{g.title}</h3>
              <p className="mt-1 text-sm text-muted">{g.text}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {g.levels.map((l) => {
                const best = games[l.id];
                return <Chip key={l.id} tone={best?.stars ? 'accent' : 'default'}>{l.preset ? PRESET_BY_ID[l.preset].name : 'Bonus'} {best?.stars ? '★'.repeat(best.stars) : ''}</Chip>;
              })}
            </div>
            <Button variant="primary" className="mt-auto self-start" onClick={() => navigate(`/learn/game?level=${g.levels[0].id}`)}>Start game<ArrowRight className="size-4" /></Button>
          </Panel>
        ))}
      </div>

      <div className="mt-12 mb-4 flex flex-wrap items-end justify-between gap-3">
        <h2 className="label-mono text-muted">Glossary</h2>
        <label className="flex h-10 w-full max-w-xs items-center gap-2 rounded-md border border-line-strong bg-ink-900 px-3 focus-within:border-accent">
          <Search className="size-4 text-faint" />
          <span className="sr-only">Search the glossary</span>
          <input id="glossary-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search terms" className="min-w-0 flex-1 bg-transparent text-sm outline-none" />
        </label>
      </div>
      <Panel className="divide-y divide-line">
        {terms.map(([id, g]) => (
          <div key={id} className="grid gap-1 px-5 py-4 sm:grid-cols-[240px_minmax(0,1fr)] sm:gap-6">
            <p className="font-medium">{g.term}</p>
            <p className="text-sm text-muted">{g.short}</p>
          </div>
        ))}
        {terms.length === 0 && <p className="px-5 py-6 text-sm text-muted">No terms match “{q}”.</p>}
      </Panel>
      <LessonDialog lessonId={openLesson} onClose={() => setOpenLesson(null)} onOpenLesson={setOpenLesson} />
    </div>
  );
}
