# Work Plan — SPHEREx Sky Time-Lapse Viewer (2-day hackathon)

**Team:** Sarawak Astronauts · **Event:** Sarawak Space Challenge 2026 (PlanetX & SPHEREx)

This plan fits [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) into a **2-day hackathon** with three people working in parallel. The full feature list in the summary is too large to build in 2 days, so the scope is cut into three tiers:

- **Must:** the core challenge (Level 1) plus the success criteria. This is what the judges test. Finish it by the end of Day 1.
- **Should:** the most visible Level 2 features and the beginner-friendly layer. Build these on Day 2.
- **Stretch:** start only once every Must and Should is merged and working.

People can work in parallel because the interfaces are **agreed in the first hour, and each track then builds against mocks**. Nobody should have to wait for someone else's code to be finished.

| Track | Person   | Focus                                            |
| ----- | -------- | ------------------------------------------------ |
| A     | Daryl    | Frontend and viewer                              |
| B     | Noah     | Science backend and data pipeline                |
| C     | Jonathan | Platform, plain language, AI explainer and pitch |

## 1. Scope for 2 days

| Tier                                        | Features                                                                                                                                                                                                                                             |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Must**                                    | Coordinate input (sexagesimal and decimal), SPHEREx image retrieval, date/epoch selection, time-series viewer, playback controls, five target presets, pre-computed demo frames, shareable URL, deployed site                                        |
| **Should**                                  | Blink comparison, image subtraction, brightness-over-time chart, metadata panel, FOV control, band selection (6 co-added arrays), plain-language labels and glossary tooltips, Beginner/Expert toggle, guided first-run tour, AI explainer assistant |
| **Stretch**                                 | Gaia overlay (Aladin Lite), individual-band selection (102 bands), spectrum at a pixel, object tracking, "Spot the mover" game, accounts and observation submission (Supabase)                                                                       |
| **Cut** (future work, mention in the pitch) | AI priority ranking (no training data yet), scientist review dashboard, points and ranks, note moderation, image-score CNN, WISE overlay, 25-year time machine, "Did it change?" game                                                                |

Hackathon simplifications to the [TECH_STACK.md](TECH_STACK.md):

- No Redis or background job queue. Slow requests fall back to the pre-computed frames.
- No Supabase unless the accounts Stretch item is reached. Guest progress uses `localStorage` only.
- The frontend is hosted on **Netlify** (configured in `netlify.toml`). If Netlify fails, use Cloudflare Pages or a Render static site, or run the built site from a laptop with `npx serve -s dist`.
- The API runs on **Render**'s free tier with the native Python runtime (`render.yaml`, no Docker). If Render fails, run the API on a laptop, behind a tunnel if the Netlify site needs it (see TECH_STACK §7). If all hosting fails, the demo runs on the pre-computed frames alone.

## 2. Hour 0–1: shared contracts (everyone together)

Agree these before splitting up, and commit them to `docs/`:

| Contract                                                                                                                                       | Owner | Consumers |
| ---------------------------------------------------------------------------------------------------------------------------------------------- | ----- | --------- |
| **API spec** (`docs/API.md`): `GET /epochs?ra&dec&fov&band`, `GET /frame/{id}.png`, `GET /diff?a&b`, `GET /lightcurve?ra&dec`, `POST /explain` | B     | A, C      |
| **Mock data**: sample JSON responses plus a few PNG frames in `frontend/public/mock/`                                                          | B     | A         |
| **URL params** (`ra`, `dec`, `fov`, `band`, `epochs`, `mode`) and the Zustand store shape                                                      | A     | B, C      |
| **Repo layout** (`frontend/`, `backend/`) and environment variable names                                                                       | C     | All       |

Also in hour 0–1: whoever has `spherex_blink.py` and `time_machine.py` (listed as existing pipelines in the tech stack) commits them, so B can start from them.

### Working rules

- Each person owns their own folders and doesn't edit anyone else's without asking.
- Short-lived branches with small PRs into `main`. Merge at least every 2–3 hours, and don't hold a big branch overnight.
- A change to a contract is announced in the team chat before it's pushed.
- If a task is blocked for more than 30 minutes, say so in the team chat and switch to the next task.

## 3. Person A: Frontend and viewer

**Owns:** `frontend/` (except `glossary.json` and the explainer panel, owned by C)

| When                                | Tasks                                                                                                                                                                   |
| ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Day 1 AM** (Must)                 | Vite + React + Tailwind + shadcn scaffold; RA/Dec input with sexagesimal and decimal parsing; five preset buttons; `<canvas>` time-series viewer running on mock frames |
| **Day 1 PM** (Must)                 | Play/pause/next/prev/speed; epoch picker; URL sync for shareable views; switch from mocks to B's real API                                                               |
| **Day 2 AM** (Should)               | Blink comparison; subtraction view; brightness chart (Recharts); metadata panel; FOV and band (6 arrays) controls                                                       |
| **Day 2 PM** (Should, then Stretch) | Beginner/Expert toggle; guided tour (driver.js) using the presets; then Aladin Lite Gaia overlay or "Spot the mover" game                                               |

## 4. Person B: Science backend and data pipeline

**Owns:** `backend/`, except `/explain` (owned by C)

| When                   | Tasks                                                                                                                                                                                                                                                                                                         |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Day 1 AM** (Must)    | Mock data for A (first hour); FastAPI scaffold; IRSA TAP query for SPHEREx DR1/QR3 epochs at a position; cutout retrieval and cache                                                                                                                                                                           |
| **Day 1 PM** (Must)    | Cleaning pipeline (ZODI subtraction, FLAGS mask, reproject north-up, one shared stretch, de-duplication); PNG frames; **pre-computed frames for all five presets**, committed to `frontend/public/presets/`; confirm Barnard's Star and WISE 0855 visibly move and RW Aur and R Dra visibly change brightness |
| **Day 2 AM** (Should)  | Difference images; co-added images per detector array; photometry endpoint (photutils) for the light curve; quality and contamination metadata                                                                                                                                                                |
| **Day 2 PM** (Stretch) | Gaia DR3 cone search for the overlay; spectrum at a pixel; centroid-based motion tracking                                                                                                                                                                                                                     |

**Note:** the pre-computed preset frames are the most important deliverable in the whole plan. The demo must work even if IRSA or the hosted API is slow during judging.

## 5. Person C: Platform, plain language, AI explainer and pitch

**Owns:** repo setup, CI/deploy, `frontend/src/glossary.json`, the explainer panel, `backend/app/ai/`, the pitch

| When                               | Tasks                                                                                                                                                                           |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Day 1 AM** (Must)                | Repo scaffold and layout; GitHub Actions (lint and build); Netlify deploy for the frontend (`netlify.toml`, see TECH_STACK §7); API deploy on Render (`render.yaml`, see TECH_STACK §7); CORS and secrets                        |
| **Day 1 PM** (Should)              | `glossary.json` with plain-language labels and "?" tooltip text, each checked against its source definition; tooltip component for A to drop in; draft the pitch outline        |
| **Day 2 AM** (Should)              | AI explainer: `POST /explain` calling an LLM with the glossary and current frame metadata as context, rate-limited, answers labelled "AI-generated"; chat panel in the frontend |
| **Day 2 PM** (Stretch, then pitch) | Supabase magic-link login and a simple `submissions` table with a submit form, if time allows; **from 14:00, slides, demo script and backup screen recording**                  |

## 6. Timeline

```
            Day 1                                   Day 2
Hour 0–1    Contracts + mocks (all)
AM          A: scaffold + viewer on mocks           A: blink, subtraction, charts, controls
            B: IRSA query + cutouts                 B: diff, co-adds, light curve
            C: repo, CI, deploys                    C: AI explainer
PM          A: playback, URL, real API              A: Beginner mode, tour → stretch
            B: cleaning, PNGs, preset frames        B: stretch (Gaia, spectrum, tracking)
            C: glossary + tooltips, pitch outline   C: stretch (submissions) → pitch
Evening     ★ Checkpoint 1: MVP live                ★ 15:00 feature freeze → demo prep
```

### Checkpoints

1. **End of Day 1 — MVP live:** the deployed site loads all five presets, plays them as a time-lapse, and the share URL works. If this isn't done, Day 2 morning goes to finishing it before any Should work.
2. **Day 2 midday — Should features merged:** blink, subtraction, light curve, plain-language labels and the explainer all work on the deployed site.
3. **Day 2, 15:00 — feature freeze:** no new features after this. Bug fixes only, then a full demo run-through on the deployed site, using the success criteria in PROJECT_SUMMARY.md §6 as the checklist.

### Demo-day fallbacks

- The presets load from pre-computed frames, so they don't depend on IRSA or the API.
- C records a backup video of the full demo before the freeze.
- The final working commit is tagged `demo` so it can be redeployed quickly.
