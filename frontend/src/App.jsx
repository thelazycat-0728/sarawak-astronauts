import { lazy, Suspense } from 'react';
import { BrowserRouter, MemoryRouter, Routes, Route, Navigate } from 'react-router';
import { Header, Footer } from './components/Header';
import { SignInDialog, Assistant } from './components/Overlays';
import { Toaster, TipProvider } from './components/ui';
import Explore from './pages/Explore';

const Learn = lazy(() => import('./pages/Learn'));
const Play = lazy(() => import('./pages/Play'));
const TrainingGame = lazy(() => import('./pages/TrainingGame'));
const Review = lazy(() => import('./pages/Review'));
const Profile = lazy(() => import('./pages/Profile'));

// The single-file preview build (VITE_ROUTER=memory) can't use URL paths, so it routes in memory.
const Router = import.meta.env.VITE_ROUTER === 'memory' ? MemoryRouter : BrowserRouter;

export default function App() {
  return (
    <Router>
      <TipProvider delayDuration={150}>
        <div className="flex min-h-dvh flex-col">
          <Header />
          <main className="flex-1">
            <Suspense fallback={<p className="label-mono p-10 text-muted">Loading…</p>}>
              <Routes>
                <Route path="/" element={<Explore />} />
                <Route path="/learn" element={<Learn />} />
                <Route path="/learn/game" element={<TrainingGame />} />
                <Route path="/play" element={<Play />} />
                {/* The time machine was dropped; old links land on Explore. */}
                <Route path="/time-machine" element={<Navigate to="/" replace />} />
                <Route path="/submit" element={<Navigate to="/play" replace />} />
                <Route path="/review" element={<Review />} />
                <Route path="/profile" element={<Profile />} />
                <Route path="*" element={<Explore />} />
              </Routes>
            </Suspense>
          </main>
          <Footer />
        </div>
        <SignInDialog />
        <Assistant />
        <Toaster />
      </TipProvider>
    </Router>
  );
}
