# Tech Stack — SPHEREx Sky Time-Lapse Viewer

**Team:** Sarawak Astronauts · **Event:** Sarawak Space Challenge 2026 (PlanetX & SPHEREx)

This document lists the technologies used to build the features in [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md): the Level 1–3 viewer features, the public engagement features (tutorials, games, Beginner/Expert mode), accounts and submissions, and the AI services.

## 1. Architecture overview

```
Browser (React + Vite, hosted on Render)
   │                         │
   │ accounts, progress,     │ images, spectra, overlays,
   │ points, submissions     │ AI explainer, AI ranking
   ▼                         ▼
Browser localStorage           Python API (FastAPI on Render)
                                  │
                                  ▼
        IRSA (SPHEREx, 2MASS, WISE) · ESA Gaia · SIMBAD · SkyBoT · Google Gemini API
```

- The **frontend** is a single-page React app on a Render static site. Everything except submitting works without logging in.
- The **science API** does all FITS processing in Python, because the astronomy libraries live there. The browser never calls IRSA directly (avoids CORS issues and heavy FITS handling in JavaScript).
- **Accounts** are hard-coded in the frontend (two fixed demo accounts plus accounts created in the browser); progress, submissions, reviews and points stay in the browser. See section 5.

## 2. Frontend

| Need | Technology | Used for |
| --- | --- | --- |
| Framework and build | **React + Vite** | Whole app; built to static files in `frontend/dist` |
| Screens | **React Router** (`react-router` v7) | `/` (Explore), `/learn` (lessons; training games at `/learn/game`), `/discover` (submit discoveries, formerly Play; `/play` and `/submit` redirect here; only typed or random positions, never the known-object presets: a preset still loaded from Explore leaves Discover empty, and Explore hides **Flag a candidate** on presets; and both Explore and Discover have a **Random sky position** button; no submit button for scientists), `/review` (the scientist's **Submissions** tab, shown in the nav slot the dropped time machine had), `/profile` |
| Styling and UI components | **Tailwind CSS v4** + **Radix UI** primitives, shadcn-style components in `components/ui.jsx` (`clsx`, `tailwind-merge`), **lucide-react** icons | Consistent controls, dialogs and the "?" tooltips for plain-language labels |
| Shared state | **Zustand** | Current target, band, selected epochs, Beginner/Expert mode |
| Shareable views | URL search params | `?target=barnard` or `?ra=…&dec=…`, plus `fov` (3, 6 or 12 arcmin), `array` (1–6) and `mode` (`blink`, `diff`, `stack`), reproduces a view (Level 3 public sharing) |
| Sky map and overlays | **Aladin Lite v3** (CDS), planned | Context map and real Gaia/WISE catalogue overlays. Not added yet: the current Gaia overlay is drawn on the canvas from simulated catalogue stars |
| Time-series viewer, blink, subtraction | **HTML `<canvas>`** components | Level 1 viewer and Level 2 comparison tools (reuses the Spot the Mover canvas code) |
| Charts | **Recharts** | Brightness over time, spectra |
| Guided tour | **driver.js** | First-run tour and target tutorials (`lib/tour.js`) |
| Glossary | `frontend/src/glossary.json` (re-exported as `GLOSSARY` from `lib/data.js`); the backend keeps a copy in `backend/app/ai/glossary.json`, and a test checks the two match | Single source for tooltips, the glossary page and the AI explainer's grounding |
| Guest progress | Browser `localStorage` (Zustand `persist`) | Tutorial and game progress, accounts, submissions, points and the review queue |
| Demo safety | Pre-computed real SPHEREx frames in `public/presets/`; built-in simulator (`lib/sim.js`) | The five presets load real frames even if IRSA or the backend is slow or down. Games and lessons still render simulated fields |

## 3. Science backend (Python)

| Need | Technology | Used for |
| --- | --- | --- |
| Web API | **FastAPI** + Uvicorn | All data endpoints; interactive API docs at `/docs` |
| Input validation | **Pydantic** | Coordinates, field of view, band and epoch parameters |
| Astronomy core | **astropy** | FITS files, WCS (sky position and per-pixel wavelength), coordinates, time |
| Archive queries | **requests** + **pyvo** (VOTable parsing), **astroquery** | SPHEREx exposures via IRSA SIA v2 (`backend/app/irsa/`); Gaia and SIMBAD later |
| Image alignment | **reproject** | Rotating every exposure onto one north-up grid |
| Numerics | **numpy**, **scipy** | Masking, stacking, subtraction, smoothing |
| Photometry and tracking | **photutils** | Brightness inspection (Level 2) and centroids for object tracking (Level 3) |
| Image output | **Pillow** | PNG frames for the viewer |
| Background jobs | FastAPI `BackgroundTasks` (upgrade to **RQ + Redis** later) | Slow jobs such as long multi-epoch builds |
| Rate limiting | In-memory per-IP limiter (`backend/app/ai/ratelimit.py`) | The AI explainer: 6 questions a minute and 60 a day per IP. Submissions are limited per account by the reputation-based wait (`lib/reputation.js`) |
| LLM client | **google-genai** (Google Gemini API, free tier) | The AI explainer (`backend/app/ai/`) |
| Existing pipelines | `spherex_blink.py` | Core processing, to be wrapped as API endpoints. Not in the repo yet (`time_machine.py` is no longer needed: the time machine was dropped) |

### Handling the 102 bands

- **Default view:** one stacked image per detector array (six arrays), for better signal-to-noise.
- **Individual bands:** SPHEREx uses linear variable filters, so wavelength changes across each detector. A "band" is selected by keeping pixels within a narrow wavelength range, using each image's spectral WCS.
- **Context views:** the SPHEREx DR1 All-Sky Cube (102 wavelength layers).

### Data cleaning applied to every image

1. Subtract the zodiacal light model (`ZODI` extension).
2. Mask artifact pixels by flag name (`FLAGS` extension), never the `SOURCE` flag.
3. Reproject onto a common north-up grid.
4. Use one brightness stretch per animation, so changes between frames are real.
5. Remove duplicate exposures served by both DR1 and QR3.

## 4. Data sources

| Source | Access | Used for |
| --- | --- | --- |
| SPHEREx DR1 + QR3 spectral images | IRSA TAP + cutout service | All viewer features. Release names kept in configuration so new releases need no code change |
| SPHEREx DR1 All-Sky Cube | IRSA HiPS | Context map, quick-look spectra |
| Gaia DR3 | ESA Gaia Archive TAP (via astroquery) | Gaia overlay, proper motions, known-star matching |
| CatWISE2020 / AllWISE | IRSA TAP | WISE overlay; brown dwarfs too faint for Gaia |
| SIMBAD | astroquery | Target name lookup, object types |
| SkyBoT (IMCCE) | Cone-search service | Known-asteroid matching for submissions and ranking |

## 5. Accounts, submissions and points

Accounts are **hard-coded in the frontend**: there is no database or auth server. **Supabase was dropped** (it had been the plan for magic-link login and a `submissions` table). The judging panel must be able to sign in as either role with one click, with no sign-up, email or outside service that could fail during judging.

| Need | Technology | Notes |
| --- | --- | --- |
| Demo accounts | Fixed in `frontend/src/lib/accounts.js` | `volunteer` / `starxplorer` (training already done, can submit in Discover) and `scientist` / `starxplorer` (opens the review queue). Shown with one-click buttons in the sign-in window. Public on purpose |
| Other accounts | Created in the sign-in window, stored in the browser (`useAuth` in `src/store.js`) | Username + password; only a salted SHA-256 hash of the password is kept |
| Roles | Fixed per account | `volunteer` submits in Discover. `scientist` doesn't get a submit button; instead it gets a **Submissions** tab (`/review`) in the nav slot the time machine used to have. Only scientists can open `/review` |
| Submissions, points, reputation | Browser `localStorage` (Zustand `persist`, key `starxplorer-submissions`) | Each account keeps its own points; reputation and the wait between submissions come from the account's own submissions (`lib/reputation.js`) |

### Same-device workaround for the scientist dashboard

With no shared database, the volunteer and the scientist hand submissions over through the **browser's storage on one device**:

1. A volunteer submits in Discover. The submission is saved to `localStorage` in that browser (like a cookie, but with room for the epochs, notes and signals).
2. The volunteer signs out and the scientist signs in **on the same device and browser**.
3. The Submissions tab reads every submission from that storage, whichever account made it, and the scientist's verdict is written back to the same place, so the volunteer's points update when they sign in again.

**Limitation:** this only works on one device and browser. Accounts and submissions don't follow anyone to another device, a private window starts empty, and clearing site data resets everything. Moving to a shared database later means replacing `useAuth` and `useSubmissions` in `src/store.js` with API calls; the pages can stay the same.

## 6. AI services

| Service | Approach | Safeguards |
| --- | --- | --- |
| **AI explainer assistant** — ✅ **Done** (tested locally 2026-11-14) | **Google Gemini API, free tier** (default model `gemini-3.8-flash`, set by `GEMINI_MODEL`; when it is overloaded or out of quota, which is common on the free tier, `gemini-3.5-flash-lite` answers instead, set by `GEMINI_FALLBACK_MODEL`), called from the backend (`POST /explain`). One request per question, no tool calling: the system prompt holds the glossary and fixed facts about SPHEREx and the tool, and the user turn holds the current view (target, coordinates, fov, array, mode). Structured JSON output returns the answer and the glossary ids it used | Answers labelled "AI-generated" (offline fallback answers are labelled "Offline answer"); production needs only `GEMINI_API_KEY` set on Render; key kept server-side (`GEMINI_API_KEY` on Render); per-IP rate limit; without a key, or when Gemini fails or its quota runs out, the panel falls back to offline answers. **Free-tier caveat:** Google may use free-tier prompts to improve its products, so the panel tells users not to include personal details. Moving to the paid tier removes this |
| **AI priority ranking** | **scikit-learn gradient boosting** (or LightGBM) on tabular signals: motion consistency, signal-to-noise, contamination flags, catalogue matches, agreement between users, user track record | Scientists can always view the unranked queue; retrained on verified outcomes |
| **Image score** (optional) | Small **PyTorch** CNN classifying epoch stamps as real mover vs. artefact, trained on injected fake movers | Used as one input feature to the ranking model |
| **Note moderation** | LLM or moderation API screening submission notes for spam, abuse and personal data | Flagged notes are hidden; the coordinates still go through vetting |

## 7. Hosting and DevOps

| Piece | Platform |
| --- | --- |
| Frontend | **Render static site** (`sarawak-frontend` in `render.yaml`, live at <https://sarawak-frontend.onrender.com>), deployed from GitHub on every push to `main`. Netlify was the first host; its free credits ran out, so `netlify.toml` stays only as a fallback |
| Python API | **Render** web service on the native Python runtime (no Docker), configured in `render.yaml`. Fallbacks: a local laptop, optionally behind a tunnel (see below) |
| Source control and CI | **GitHub** + **GitHub Actions**: frontend lint and build; backend `ruff` and a syntax check (the pytest suite is not run in CI yet) |
| Secrets | Environment variables on Render (both services); never in the frontend bundle |

### Render static site (frontend)

The frontend is the `sarawak-frontend` service in `render.yaml`: a free static site (no sleeping, unlike the free API service) that runs `npm ci && npm run lint && npm run build` in `frontend/`, publishes `dist/`, and rewrites `/*` to `/index.html` for client-side routes. `buildFilter` skips rebuilds when a commit doesn't touch `frontend/`.

Setup steps:

1. In Render, open the Blueprint and sync it (or **New → Blueprint** with this repo) so it creates `sarawak-frontend`.
2. In `sarawak-frontend` → **Environment**, set `VITE_API_URL` to the API's URL (`https://<api>.onrender.com`, no trailing slash). Vite bakes it in at build time, so redeploy after changing it (**Manual Deploy → Clear build cache & deploy**).
3. Add the site's URL (`https://sarawak-frontend.onrender.com`, or whatever Render assigns) to the API's `CORS_ORIGINS`.

There are no per-PR deploy previews on Render static sites, so `CORS_ORIGIN_REGEX` only matters while the Netlify site is still used.

### Netlify configuration (fallback)

Netlify was the first frontend host. Its free credits ran out, so it's kept only as a fallback. The frontend is a static Vite build, so Netlify only needs the build settings and a fallback rule for client-side routes. Add `netlify.toml` at the repo root:

```toml
[build]
  base    = "frontend"
  command = "npm run lint && npm run build"
  publish = "dist"          # relative to base
  ignore  = "git diff --quiet $CACHED_COMMIT_REF $COMMIT_REF -- . ../netlify.toml"

[build.environment]
  NODE_VERSION = "22"

# React Router: serve index.html for /explore, /learn, etc.
[[redirects]]
  from   = "/*"
  to     = "/index.html"
  status = 200
```

Setup steps:

1. In Netlify, choose **Add new site → Import an existing project**, select the GitHub repo, and keep the settings from `netlify.toml`.
2. Under **Site configuration → Environment variables**, set `VITE_API_URL` to the Python API's public URL. Vite bakes it into the bundle at build time, so trigger a redeploy after changing it.
3. Add the Netlify domain (`https://<site>.netlify.app`) to the API's `CORS_ORIGINS`. Deploy previews are allowed by pattern through `CORS_ORIGIN_REGEX` (set in `render.yaml`).
4. Deploy previews are created automatically for every pull request.

Manual deploys (`netlify deploy --prod --dir=dist` or drag-and-drop) skip `netlify.toml`, so `frontend/public/_redirects` repeats the `/*` → `/index.html` rule.

**Other fallbacks:** Cloudflare Pages (same build settings, with a `/*` → `/index.html` rewrite). As a last resort for a demo, run `npm run build && npx serve -s dist` on a laptop.

**Why the Python API is not on Netlify:** Netlify doesn't host Python servers or containers. Netlify Functions are short-lived JavaScript/TypeScript/Go functions with tight time limits and no persistent disk. The FITS pipelines need large Python libraries, slow archive queries and a cutout cache, so they run on a long-lived Python server.

### Render configuration (Python API)

Render runs the API with its native Python runtime, so no Dockerfile is needed. Add `render.yaml` (a Render *Blueprint*) at the repo root:

```yaml
services:
  - type: web
    name: sarawak-api
    runtime: python
    plan: free
    rootDir: backend
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    healthCheckPath: /health
    autoDeploy: true                 # redeploy on every push to main
    envVars:
      - key: PYTHON_VERSION
        value: 3.12.8
      - key: DATA_MODE
        value: mock
      - key: CORS_ORIGINS
        sync: false                  # set in the dashboard: https://<site>.netlify.app
      - key: CORS_ORIGIN_REGEX       # Netlify deploy previews
        value: ^https://deploy-preview-\d+--aesthetic-parfait-36a9c2\.netlify\.app$
      - key: GEMINI_API_KEY          # AI explainer; secret, set in the dashboard
        sync: false
      - key: GEMINI_MODEL
        value: gemini-3.8-flash
```

Setup steps:

1. In Render, choose **New → Blueprint**, select the GitHub repo, and Render creates the service from `render.yaml`.
2. Fill in `CORS_ORIGINS` and `GEMINI_API_KEY` when prompted, then check `https://<service>.onrender.com/health`. Get the key free from Google AI Studio (<https://aistudio.google.com>, **Get API key**). Leaving it empty keeps the explainer off (`/explain` answers 503 and the panel uses offline answers).
3. Set `VITE_API_URL` on the `sarawak-frontend` static site to the API's URL and redeploy the frontend.
4. After that, every merge to `main` redeploys both the frontend and the API on Render.

**Free-tier limits:** the service sleeps after about 15 minutes without traffic, and the first request after that takes up to a minute while it wakes. The disk is also wiped on every restart or deploy, so the cutout cache is temporary. Before judging, open `/health` to wake it, and rely on the pre-computed preset frames in `frontend/public/` for the demo.

**Render CLI (optional):** the [Render CLI](https://render.com/docs/cli) (`render login`) can trigger deploys, list services and stream logs from the terminal (`render deploys create`, `render logs`). Creating the service itself is done once through the Blueprint above.

### Running the API locally

During development, and as the demo-day backup, the API can run on a laptop:

```
# from backend/
.venv/Scripts/python -m uvicorn app.main:app --port 8000
# from frontend/ (VITE_API_URL=http://localhost:8000)
npm run dev
```

`http://localhost:5173` is already an allowed CORS origin, so no extra setup is needed.

To let the **deployed frontend** call a laptop API, expose it with a tunnel, which provides the HTTPS URL that browsers require:

| Tool | Command | Notes |
| --- | --- | --- |
| Cloudflare Tunnel | `cloudflared tunnel --url http://localhost:8000` | No account needed; random URL each run |
| ngrok | `ngrok http 8000` | Free account includes one fixed domain |

Then start the API with `CORS_ORIGINS=https://sarawak-frontend.onrender.com`, set `VITE_API_URL` on the static site to the tunnel URL, and redeploy. The laptop must stay awake, and a random tunnel URL means a frontend redeploy every time it changes. Close the tunnel when it's not in use.

## 8. Security and privacy

- **Validate every input** with Pydantic. User values are converted to numbers and never pasted into ADQL queries as text.
- **Rate-limit** submissions and the AI explainer, per user and per IP address. The explainer's per-IP limit is in place.
- **Keep the LLM key server-side.** `GEMINI_API_KEY` lives only in Render's environment. The free Gemini tier may use questions to improve Google's products; the assistant panel says so.
- **Lock CORS** to the frontend's domain.
- **Scientist role:** in this hackathon build the role is enforced only in the frontend (hidden UI and a route check), which is acceptable because all data stays on the user's own device. A real deployment must enforce it on the server.
- **Collect minimal personal data** (username and email), publish a privacy policy, and follow Malaysia's Personal Data Protection Act (PDPA).

## 9. Open items for PROJECT_SUMMARY.md

- Add **note moderation** as a third AI service.
- Add the **scientist review dashboard** (the Submissions tab) as a feature row (it is implied by pipeline step 5).
- Add **pre-computed demo data** to the design considerations, so the judging demo never depends on a live query.

## 10. Credits

- **SPHEREx, 2MASS, WISE/NEOWISE:** NASA/IPAC Infrared Science Archive (IRSA), Caltech/JPL.
- **Gaia DR3:** Gaia Collaboration 2022, ESA, https://doi.org/10.5270/esa-qa4lep3 (CC-BY-NC-3.0-IGO: credit required, non-commercial use only).
- **Aladin Lite, SIMBAD, VizieR:** CDS, Strasbourg.
- **SkyBoT:** IMCCE, Paris Observatory.
