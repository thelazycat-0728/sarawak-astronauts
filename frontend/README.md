# StarXplorer frontend (React + Vite)

The public web app: viewer, lessons, training game, submissions and the scientists' Submissions tab.
See [docs/TECH_STACK.md](../docs/TECH_STACK.md) for the libraries and [docs/API.md](../docs/API.md) for the backend contract.

## Run (from `frontend/`)

```
npm install
npm run dev      # http://localhost:5173
npm run lint
npm run build    # output in dist/, deployed as a Render static site (see ../render.yaml)
```

## Environment variables

All are optional. Put them in `frontend/.env.local` (ignored by git) or in the `sarawak-frontend` service's environment settings on Render. They end up in the public bundle, so never put secrets here.

| Variable | Effect when set | Without it |
| --- | --- | --- |
| `VITE_API_URL` | Typed-in coordinates load frames from the Python API, and the assistant calls `POST /explain` (Google Gemini, when the API has a `GEMINI_API_KEY`) | Typed-in coordinates run on simulated fields and the assistant gives offline answers |

The five presets load real SPHEREx frames from `public/presets/` (static files, so they work without the API) when the view is in the target's own detector array: array 2, or array 6 for WISE 0855. In other arrays a preset behaves like typed coordinates. Lessons, games and the review queue use the built-in simulator (`src/lib/sim.js`).

## Accounts

There is no database: accounts are hard-coded in `src/lib/accounts.js` and kept in the browser. The sign-in window offers two demo accounts with one click:

| Account | Username | Password | Use it for |
| --- | --- | --- | --- |
| Volunteer | `volunteer` | `starxplorer` | Submitting discoveries in Discover (training already done) |
| Scientist | `scientist` | `starxplorer` | The **Submissions** tab (`/review`), which takes the nav slot of the dropped time machine. No submit button |

Anyone can also create an account in the sign-in window; it lives in that browser only. There is no Supabase or other database: submissions are saved in `localStorage` (`starxplorer-submissions`), so the scientist sees the ones made **on the same device and browser**. To demo it, sign in as `volunteer`, pick a field in **Discover** (typed or **Random sky position**; the presets aren't used there), flag and submit, then sign out and sign in as `scientist`.

## Layout

| Path | Contents |
| --- | --- |
| `src/pages/` | One file per route: Explore (`/`), Learn, TrainingGame (`/learn/game`), Discover (submissions; `/play` redirects here), Review (the scientist's Submissions tab), Profile |
| `src/components/` | Header, overlays (sign-in, assistant) and shared UI primitives (`ui.jsx`) |
| `src/lib/api.js` | Backend access with simulator fallback, and the AI explainer client |
| `src/lib/data.js` | Detector arrays, presets, glossary, lessons, game levels |
| `src/lib/sim.js` | Simulated SPHEREx fields |
| `src/store.js` | Zustand stores (viewer, UI, auth, progress, submissions), share URLs |
| `public/mock/` | Synthetic API responses matching `docs/API.md` |
