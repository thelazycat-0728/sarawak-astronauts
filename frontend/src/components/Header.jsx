import { NavLink, useNavigate, useLocation } from 'react-router';
import { Bookmark, Share2, Sparkles, LogIn } from 'lucide-react';
import { Button, Segmented, Tip, Logo } from './ui';
import { useUi, useAuth, useViewer, useProgress, shareUrl } from '../store';
import { rankFor, PRESET_BY_ID } from '../lib/data';
import { IS_LIVE } from '../lib/api';
import { cn } from '../lib/util';

const NAV = [
  { to: '/', label: 'Explore', end: true },
  { to: '/learn', label: 'Learn' },
  { to: '/discover', label: 'Discover' },
];

export function Header() {
  const expert = useUi((s) => s.expert);
  const setExpert = useUi((s) => s.setExpert);
  const setAssistant = useUi((s) => s.setAssistant);
  const setSignIn = useUi((s) => s.setSignIn);
  const toast = useUi((s) => s.toast);
  const user = useAuth((s) => s.user);
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const onExplore = pathname === '/';

  const share = async () => {
    const url = shareUrl(useViewer.getState());
    try {
      await navigator.clipboard.writeText(url);
      toast('Link copied. Anyone who opens it sees this exact view.', 'good');
    } catch {
      toast(`Copy this link: ${url}`);
    }
  };
  const bookmark = () => {
    const v = useViewer.getState();
    useProgress.getState().saveView({
      name: v.targetId ? PRESET_BY_ID[v.targetId].name : `${v.ra} ${v.dec}`,
      targetId: v.targetId, ra: v.ra, dec: v.dec, fov: v.fov, arrayIdx: v.arrayIdx, mode: v.mode,
    });
    toast('View saved. Find it on your profile.', 'good');
  };

  // Scientists get the Submissions tab (the review queue) in the slot the time machine had.
  const nav = [...NAV, ...(user?.role === 'scientist' ? [{ to: '/review', label: 'Submissions' }] : [])];

  return (
    <header className="sticky z-40 border-b border-line bg-ink-950/90 backdrop-blur" style={{ top: 'env(safe-area-inset-top, 0px)' }}>
      <div className="mx-auto flex max-w-[1680px] flex-wrap items-center gap-x-6 gap-y-2 px-[max(16px,env(safe-area-inset-left,0px))] py-3 lg:px-10">
        <button type="button" onClick={() => navigate('/')} className="flex items-center gap-3" aria-label="StarXplorer home">
          <Logo className="size-7 text-accent-fg" />
          <span className="text-[17px] font-semibold tracking-[0.18em]">STARXPLORER</span>
          <span className="hidden h-5 w-px bg-line-strong sm:block" />
          <span className="label-mono hidden text-muted sm:block">SPHEREx explorer</span>
        </button>

        <nav aria-label="Main" className="order-3 -mx-1 flex w-full gap-1 overflow-x-auto lg:order-none lg:w-auto">
          {nav.map((n) => (
            <NavLink
              key={n.to} to={n.to} end={n.end}
              className={({ isActive }) => cn(
                'rounded-md px-3 py-1.5 text-sm whitespace-nowrap transition-colors',
                isActive ? 'bg-ink-800 text-fg shadow-[inset_0_-2px_0_var(--color-accent)]' : 'text-muted hover:text-fg',
              )}
            >
              {n.label}
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          <div id="tour-expert">
            <Segmented
              size="sm" label="Interface mode" value={expert ? 'expert' : 'beginner'}
              onChange={(v) => setExpert(v === 'expert')}
              options={[{ value: 'beginner', label: 'Beginner', title: 'Plain language, essential controls' }, { value: 'expert', label: 'Expert', title: 'Full scientific controls' }]}
            />
          </div>
          <Tip content={IS_LIVE ? 'Live data from NASA/IPAC IRSA: SPHEREx DR1 + weekly QR3.' : 'Demo build: simulated frames shaped like SPHEREx DR1 + QR3.'}>
            <span className="label-mono hidden items-center gap-2 px-2 text-muted xl:flex">
              <span className={cn('size-1.5 rounded-full', IS_LIVE ? 'bg-good' : 'bg-amber')} />
              Data release DR1 + QR3
            </span>
          </Tip>
          <Button id="tour-ask" variant="outline" size="sm" onClick={() => setAssistant(true)}>
            <Sparkles className="size-4 text-accent-fg" /><span className="hidden sm:inline">Ask AI</span>
          </Button>
          {onExplore && (
            <>
              <Tip content="Save this view"><Button variant="outline" size="iconSm" aria-label="Save this view" onClick={bookmark}><Bookmark className="size-4" /></Button></Tip>
              <Button variant="outline" size="sm" onClick={share}><Share2 className="size-4" /><span className="hidden md:inline">Share view</span></Button>
            </>
          )}
          {user ? (
            <button type="button" onClick={() => navigate('/profile')} className="flex items-center gap-2 rounded-md border border-line-strong py-1 pr-3 pl-1 hover:border-faint">
              <span className="grid size-6 place-items-center rounded bg-accent-soft text-xs font-semibold text-accent-fg">{user.name[0]?.toUpperCase()}</span>
              <span className="tabular hidden text-[13px] sm:inline">{user.points} pts · {rankFor(user.points).name}</span>
            </button>
          ) : (
            <Button variant="subtle" size="sm" onClick={() => setSignIn(true)}><LogIn className="size-4" />Sign in</Button>
          )}
        </div>
      </div>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="mx-auto flex max-w-[1680px] flex-wrap justify-between gap-3 px-[max(16px,env(safe-area-inset-left,0px))] py-8 lg:px-10">
      <p className="label-mono text-faint">StarXplorer · Sarawak Astronauts · Sarawak Space Challenge 2026</p>
      <p className="label-mono text-faint">
        {IS_LIVE ? 'SPHEREx data: NASA/IPAC Infrared Science Archive' : 'Imagery shown is simulated for this draft'}
      </p>
    </footer>
  );
}
