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
Supabase (Auth + Postgres)     Python API (FastAPI in Docker)
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
| Python API | **Docker** container on Render, Railway, Fly.io, Google Cloud Run or Hugging Face Spaces, with a persistent disk for the cutout cache |
| Auth and database | **Supabase** (hosted) |
| Source control and CI | **GitHub** + **GitHub Actions** (lint, tests, build) |
| Secrets | Environment variables on Netlify and the API host; never in the frontend bundle |

### Netlify configuration

The frontend is a static Vite build, so Netlify only needs the build settings and a fallback rule for client-side routes. Add `netlify.toml` at the repo root:

```toml
[build]
  base    = "frontend"
  command = "npm run build"
  publish = "dist"          # relative to base

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

**Why the Python API is not on Netlify:** Netlify Functions are short-lived serverless functions with tight execution time limits and no persistent disk between requests. The FITS pipelines need large scientific libraries, slow archive queries and a cutout cache, which fit better in a long-running container.

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
