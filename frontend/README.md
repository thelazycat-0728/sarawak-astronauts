# StarXplorer frontend (React + Vite)

The public web app: viewer, lessons, training game, time machine, submissions and the review queue.
See [docs/TECH_STACK.md](../docs/TECH_STACK.md) for the libraries and [docs/API.md](../docs/API.md) for the backend contract.

## Run (from `frontend/`)

```
npm install
npm run dev      # http://localhost:5173
npm run lint
npm run build    # output in dist/, deployed by Netlify (see ../netlify.toml)
```

## Environment variables

All are optional. Put them in `frontend/.env.local` (ignored by git) or in Netlify's environment settings. They end up in the public bundle, so never put secrets here.

| Variable | Effect when set | Without it |
| --- | --- | --- |
| `VITE_API_URL` | Typed-in coordinates load frames from the Python API, and the assistant calls `POST /explain` | Everything runs on simulated fields and offline answers |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | Real sign-in (email magic link, Google) through Supabase | Browser-only demo accounts |

The five presets, lessons, games and the time machine always use the built-in simulator (`src/lib/sim.js`) for now, so the demo works offline.

## Layout

| Path | Contents |
| --- | --- |
| `src/pages/` | One file per route: Explore (`/`), Learn, Play, TimeMachine, Submit, Review, Profile |
| `src/components/` | Header, overlays (sign-in, assistant) and shared UI primitives (`ui.jsx`) |
| `src/lib/api.js` | Backend access with simulator fallback, and the AI explainer client |
| `src/lib/data.js` | Detector arrays, presets, glossary, lessons, game levels |
| `src/lib/sim.js` | Simulated SPHEREx fields |
| `src/store.js` | Zustand stores (viewer, UI, auth, progress, submissions), share URLs |
| `public/mock/` | Synthetic API responses matching `docs/API.md` |
