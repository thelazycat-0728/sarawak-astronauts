# Tech Stack — SPHEREx Sky Time-Lapse Viewer

**Team:** Sarawak Astronauts · **Event:** Sarawak Space Challenge 2026 (PlanetX & SPHEREx)

This document lists the technologies used to build the features in [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md): the Level 1–3 viewer features, the public engagement features (tutorials, games, Beginner/Expert mode), accounts and submissions, and the AI services.

## 1. Architecture overview

```
Browser (React + Vite, hosted on Netlify)
   │                         │
   │ sign-in, progress,      │ images, spectra, overlays,
   │ points, submissions     │ AI explainer, AI ranking
   ▼                         ▼
Supabase (Auth + Postgres)     Python API (FastAPI on Render)
                                  │
                                  ▼
        IRSA (SPHEREx, 2MASS, WISE) · ESA Gaia · SIMBAD · SkyBoT · LLM API
```

- The **frontend** is a single-page React app on Netlify. Everything except submitting works without logging in.
- The **science API** does all FITS processing in Python, because the astronomy libraries live there. The browser never calls IRSA directly (avoids CORS issues and heavy FITS handling in JavaScript).
- **Supabase** handles accounts and stores progress, submissions, reviews and points.

## 2. Frontend

| Need | Technology | Used for |
| --- | --- | --- |
| Framework and build | **React + Vite** | Whole app; Netlify detects Vite automatically |
| Screens | **React Router** | `/explore`, `/learn`, `/play`, `/time-machine`, `/submit`, `/review`, `/profile` |
| Styling and UI components | **Tailwind CSS** + **shadcn/ui** (Radix UI) | Consistent controls, dialogs and the "?" tooltips for plain-language labels |
| Shared state | **Zustand** | Current target, band, selected epochs, Beginner/Expert mode |
| Shareable views | URL search params | `?ra=133.795&dec=-7.245&fov=5&band=D2` reproduces any view (Level 3 public sharing) |
| Sky map and overlays | **Aladin Lite v3** (CDS) | Context map, Gaia and WISE catalogue overlays |
| Time-series viewer, blink, subtraction | **HTML `<canvas>`** components | Level 1 viewer and Level 2 comparison tools (reuses the Spot the Mover canvas code) |
| Charts | **Recharts** or **Plotly.js** | Brightness over time, spectra |
| Guided tour | **driver.js** or **react-joyride** | First-run tour and target tutorials |
| Glossary | Local JSON file | Single source for tooltips, the glossary page and the AI explainer's grounding |
| Guest progress | Browser `localStorage` | Tutorial and game progress for users without an account |
| Demo safety | Pre-computed frames in `public/` | The five presets load instantly even if IRSA or the backend is slow |

## 3. Science backend (Python)

| Need | Technology | Used for |
| --- | --- | --- |
| Web API | **FastAPI** + Uvicorn | All data endpoints; interactive API docs at `/docs` |
| Input validation | **Pydantic** | Coordinates, field of view, band and epoch parameters |
| Astronomy core | **astropy** | FITS files, WCS (sky position and per-pixel wavelength), coordinates, time |
| Archive queries | **astroquery**, **pyvo** | IRSA TAP/SIA, Gaia, SIMBAD |
| Image alignment | **reproject** | Rotating every exposure onto one north-up grid |
| Numerics | **numpy**, **scipy** | Masking, stacking, subtraction, smoothing |
| Photometry and tracking | **photutils** | Brightness inspection (Level 2) and centroids for object tracking (Level 3) |
| Image output | **Pillow** | PNG frames for the viewer |
| Background jobs | FastAPI `BackgroundTasks` (upgrade to **RQ + Redis** later) | Slow jobs such as the time machine |
| Rate limiting | **slowapi** | Limits on submissions and the AI explainer |
| Existing pipelines | `spherex_blink.py`, `time_machine.py` | Core processing, wrapped as API endpoints |

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
| 2MASS and WISE/NEOWISE images | IRSA SIA + cutouts | 25-year time machine |
| SIMBAD | astroquery | Target name lookup, object types |
| SkyBoT (IMCCE) | Cone-search service | Known-asteroid matching for submissions and ranking |

## 5. Accounts, submissions and points

| Need | Technology | Notes |
| --- | --- | --- |
| Authentication | **Supabase Auth** (email magic link, Google sign-in) | No passwords stored by us |
| Database | **Supabase Postgres** | Users, progress, submissions, reviews, points, ranks |
| Access control | Postgres **row-level security** | Users see only their own private data; only scientist accounts can read the review queue |
| Backend trust | FastAPI verifies the Supabase JWT | The science API knows who is submitting without a second login system |

### Core tables

| Table | Contents |
| --- | --- |
| `profiles` | Username, role (`user` / `scientist`), total points, rank |
| `progress` | Tutorials and training games completed |
| `submissions` | Coordinates, epochs, band, per-epoch track, notes, status, AI score |
| `reviews` | Scientist verdict: confirmed, known object, artefact or rejected |
| `point_events` | Every point award, with its reason (prevents tampering and makes totals auditable) |

## 6. AI services

| Service | Approach | Safeguards |
| --- | --- | --- |
| **AI explainer assistant** | LLM API (Claude, Gemini or OpenAI) called from the backend with **tool calling** to our own endpoints, grounded in the glossary JSON and the current frame's metadata | Answers labelled "AI-generated"; API key kept server-side; rate-limited |
| **AI priority ranking** | **scikit-learn gradient boosting** (or LightGBM) on tabular signals: motion consistency, signal-to-noise, contamination flags, catalogue matches, agreement between users, user track record | Scientists can always view the unranked queue; retrained on verified outcomes |
| **Image score** (optional) | Small **PyTorch** CNN classifying epoch stamps as real mover vs. artefact, trained on injected fake movers | Used as one input feature to the ranking model |
| **Note moderation** | LLM or moderation API screening submission notes for spam, abuse and personal data | Flagged notes are hidden; the coordinates still go through vetting |

## 7. Hosting and DevOps

| Piece | Platform |
| --- | --- |
| Frontend | **Netlify**, deployed from GitHub with a deploy preview for every pull request |
| Python API | **Render** web service on the native Python runtime (no Docker), configured in `render.yaml`. Fallbacks: a local laptop, optionally behind a tunnel (see below) |
| Auth and database | **Supabase** (hosted) |
| Source control and CI | **GitHub** + **GitHub Actions** (lint, tests, build) |
| Secrets | Environment variables on Netlify and the API host; never in the frontend bundle |

### Netlify configuration

The frontend is a static Vite build, so Netlify only needs the build settings and a fallback rule for client-side routes. Add `netlify.toml` at the repo root:

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
3. Add the Netlify domain (`https://<site>.netlify.app`, plus the deploy-preview pattern if previews must call the API) to the FastAPI **CORS allow-list**.
4. Deploy previews are created automatically for every pull request.

**Fallbacks if Netlify is unavailable:** Cloudflare Pages or a Render static site (same build settings, with a `/*` → `/index.html` rewrite). As a last resort for a demo, run `npm run build && npx serve -s dist` on a laptop.

**Why the Python API is not on Netlify or Supabase:** neither hosts Python servers or containers. Netlify Functions are short-lived JavaScript/TypeScript/Go functions with tight time limits and no persistent disk, and Supabase Edge Functions run TypeScript on Deno. The FITS pipelines need large Python libraries, slow archive queries and a cutout cache, so they run on a long-lived Python server.

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
```

Setup steps:

1. In Render, choose **New → Blueprint**, select the GitHub repo, and Render creates the service from `render.yaml`.
2. Fill in `CORS_ORIGINS` when prompted, then check `https://<service>.onrender.com/health`.
3. Set Netlify's `VITE_API_URL` to the Render URL and redeploy the frontend.
4. After that, every merge to `main` redeploys both the frontend (Netlify) and the API (Render).

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

To let the **Netlify site** call a laptop API, expose it with a tunnel, which provides the HTTPS URL that browsers require:

| Tool | Command | Notes |
| --- | --- | --- |
| Cloudflare Tunnel | `cloudflared tunnel --url http://localhost:8000` | No account needed; random URL each run |
| ngrok | `ngrok http 8000` | Free account includes one fixed domain |

Then start the API with `CORS_ORIGINS=https://<site>.netlify.app`, set `VITE_API_URL` on Netlify to the tunnel URL, and redeploy. The laptop must stay awake, and a random tunnel URL means a Netlify redeploy every time it changes. Close the tunnel when it's not in use.

## 8. Security and privacy

- **Validate every input** with Pydantic. User values are converted to numbers and never pasted into ADQL queries as text.
- **Rate-limit** submissions and the AI explainer, per user and per IP address.
- **Lock CORS** to the Netlify domain.
- **Enforce the scientist role in the database** (row-level security), not just by hiding UI.
- **Collect minimal personal data** (username and email), publish a privacy policy, and follow Malaysia's Personal Data Protection Act (PDPA).

## 9. Open items for PROJECT_SUMMARY.md

- Add the **25-year time machine** (2MASS → WISE/NEOWISE → SPHEREx) to Level 3.
- Add **note moderation** as a third AI service.
- Add the **scientist review dashboard** as a feature row (it is implied by pipeline step 5).
- Add **pre-computed demo data** to the design considerations, so the judging demo never depends on a live query.

## 10. Credits

- **SPHEREx, 2MASS, WISE/NEOWISE:** NASA/IPAC Infrared Science Archive (IRSA), Caltech/JPL.
- **Gaia DR3:** Gaia Collaboration 2022, ESA, https://doi.org/10.5270/esa-qa4lep3 (CC-BY-NC-3.0-IGO: credit required, non-commercial use only).
- **Aladin Lite, SIMBAD, VizieR:** CDS, Strasbourg.
- **SkyBoT:** IMCCE, Paris Observatory.
