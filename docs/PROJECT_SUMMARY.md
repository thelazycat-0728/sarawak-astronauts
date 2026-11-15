# Project Summary — SPHEREx Sky Time-Lapse Viewer

**Team:** Sarawak Astronauts · **Event:** Sarawak Space Challenge 2026 (PlanetX & SPHEREx)

## 1. The challenge

Since May 2025, NASA's **SPHEREx** mission has mapped the entire sky every six months in **102 near-infrared bands**, returning images of more than a billion objects. Comets, asteroids, nearby stars, brown dwarfs and even undiscovered planets reveal themselves by **shifting position between images** taken at different times. The data volume is far too large for any one person (or any one piece of code) to sift through.

**Our goal:** build a **public-facing web tool** that lets anyone enter sky coordinates (Right Ascension and Declination) and watch the SPHEREx images of that patch of sky play back **like a short video**, making moving and variable objects easy to spot.

### Why it matters

- Pluto was discovered by comparing two images of the sky taken at different times and noticing one point of light had moved.
- Most known comets and brown dwarfs were found by volunteers doing exactly this kind of comparison (e.g. [Backyard Worlds](https://backyardworlds.org)).
- SPHEREx is uniquely sensitive to objects rich in hydrogen, water, CO₂, CO and other molecules, so its data could yield discoveries other surveys miss — if the public has an easy way to look at it.

## 2. Required test targets

The tool must be verified against these five targets (loaded as one-click presets):

| Target | Type | What to look for |
| --- | --- | --- |
| **Barnard's Star** | Nearby red dwarf, highest known proper motion | Clear positional shift between epochs |
| **Epsilon Indi B** | Pair of brown dwarfs orbiting a star | Co-moving faint companions |
| **WISE 0855** (RA 08h55m10.831s, Dec −7°14′42.53″) | Fast-moving, very cold brown dwarf | Motion across epochs; very red/faint |
| **RW Aurigae** | Variable young star | Brightness changes over time |
| **R Draconis** | Variable (Mira-type) star | Brightness changes over time |

## 3. Feature scope

Features are prioritised in three levels. Level 1 is the core challenge and must be completed first.

### Level 1 — Essential (the actual challenge)

| Feature | What it does |
| --- | --- |
| Coordinate input | Accepts RA and Dec (sexagesimal or decimal degrees) |
| Image retrieval | Retrieves SPHEREx observations covering that position |
| Date selection | Lets users choose which observations to include |
| Time-series viewer | Displays the observations as an ordered sequence |
| Playback controls | Play, pause, next, previous (and speed) |
| Target presets | One-click loading of the five test targets above |

### Level 2 — Useful scientific features

| Feature | What it does |
| --- | --- |
| Blink comparison | Alternates between two chosen observations |
| Image subtraction | Subtracts one epoch from another to highlight differences |
| Brightness inspection | Shows how a source's brightness changes over time |
| Field-of-view control | Zoom in/out; larger FOV tracks faster movers at the cost of data transfer |
| 102-band selection | Inspect individual bands, or co-add bands per detector array for better S/N |
| Metadata display | Observation dates, band/wavelength, quality and contamination flags |

### Level 3 — Advanced (bonus)

| Feature | What it does |
| --- | --- |
| Spectral analysis | Builds a spectrum on the fly from the 102 bands at a selected position |
| Gaia overlay | Overlays Gaia catalogue sources (positions, proper motions) |
| WISE overlay | Overlays WISE/unWISE infrared images or catalogue sources |
| Object tracking | Estimates a source's motion (direction and rate) across epochs |
| Public sharing | Coordinate-based URL (e.g. `?ra=133.795&dec=-7.245&fov=5`) for sharing and embedding |

## 4. Design considerations

- **Easy to update** — new SPHEREx data releases arrive frequently, so data access should go through a query service (e.g. IRSA) rather than a fixed local dataset, with release/version kept in configuration.
- **Making 102 bands usable** — default to a co-added image per detector array (six arrays) for signal-to-noise, with an option to drill down to individual bands.
- **Time-domain tools** — stacking (add) and differencing (subtract) epochs help reveal faint and moving objects.
- **Performance vs. field of view** — let users trade larger FOV against download size; use cutouts rather than full images.
- **Interoperability** — URL query parameters make the tool embeddable in other sites and projects.
- **Reuse existing tools** — e.g. [Astropy](https://www.astropy.org) / astroquery for data handling, and [Aladin Lite](https://aladin.cds.unistra.fr/AladinLite/) (CDS) for sky display and catalogue overlays.

## 5. Public engagement: tutorials, games and plain language

The tool is **public-facing**, and the brief asks us to "help as many people as possible pitch in to make discoveries". Heavy jargon (RA/Dec, epochs, bands, co-adds, contamination flags) will put off non-experts. Making the tool interactive and beginner-friendly supports that goal. It is the same approach used by citizen-science projects such as Backyard Worlds and the Zooniverse, which rely on tutorials and gamified tasks to train volunteers.

### Guiding principles

- **The science comes first.** Tutorials and games run on the same viewer and real SPHEREx data, so they teach people to use the actual tool. They are not a separate mini-app.
- **Layered complexity.** A *Beginner* mode hides advanced controls and uses plain words. An *Explorer/Expert* mode shows the full scientific controls (bands, subtraction, flags, overlays).
- **Plain language with optional depth.** Every technical term has a friendly label plus a "?" tooltip, e.g. "Sky position (RA/Dec)", "Snapshots over time (epochs)", "Infrared colour (band)".
- **Don't block experts.** Tutorials can be skipped, and a coordinate URL loads straight into the viewer.

### Proposed features

| Feature | Level | What it does |
| --- | --- | --- |
| Plain-language labels & glossary | 1 | Friendly names, tooltips and a short glossary for every technical term |
| Guided first-run tour | 2 | Step-by-step overlay: enter a position → press play → spot what moves |
| Beginner / Expert mode toggle | 2 | Hides or shows advanced scientific controls |
| Interactive tutorials on the test targets | 2 | "Watch Barnard's Star move", "See R Draconis brighten and fade": short guided lessons using the real presets |
| "Spot the mover" training game | 3 | A tutorial in game form. Players blink through real cutouts and click the object that moves. Answers are checked against known movers (Barnard's Star, WISE 0855) and the game explains what made it a real mover |
| "Did it change?" training game | 3 | Teaches users to tell real brightness changes from noise and artefacts, using known variables (RW Aur, R Dra) as examples |
| Observation submission | 3 | After training, users submit candidate findings (coordinates, epochs, notes) to scientists for verification |
| AI priority ranking | 3 | A machine-learning model scores each submission so scientists can review the most promising ones first |
| AI explainer assistant | 3 | An LLM-based helper that explains, in plain language, what the user is looking at and what terms mean |
| Accounts, points & ranks | 3 | Account-based progress: every submission earns a few points, verified significant findings earn many more, and points unlock video-game-style ranks |

### From training to discovery: the submission pipeline

The games are **tutorials first**. Each one teaches a specific skill: what a real moving object looks like compared with noise, a cosmic ray or a detector artefact, and what genuine variability looks like. Once trained, users can take part in real science.

1. **Learn:** the user completes the training games on known objects.
2. **Explore:** the user browses new sky fields in the viewer. The AI explainer assistant answers questions such as "what is this streak?" or "what does this flag mean?".
3. **Submit:** the user flags a candidate and submits it with its coordinates, epochs, band and optional notes. The shareable URL format reproduces their exact view.
4. **Prioritise:** the AI ranking model scores the submission, using signals such as motion consistency across epochs, signal-to-noise, contamination flags, matches against known catalogues (Gaia, WISE, known asteroids) and agreement with other users' submissions. This reduces the scientists' workload.
5. **Verify:** scientists review the queue in priority order and mark each submission as confirmed, known object, artefact or rejected.
6. **Reward:** points go to the user's account. A small amount comes from every submission, a large amount from a verified significant finding, and accumulated points unlock ranks.

### Why accounts are required

- Submissions must be attributed to a person, both for credit and so scientists can follow up.
- Points, ranks and tutorial progress must persist across sessions and devices.
- Accounts allow per-user rate limits and reputation, which also feed the AI ranking (e.g. users with a good track record).

### Guest vs. signed-in access

Everything except submitting is **open to everyone without logging in**. An account is needed only to submit observations and earn rewards.

| Capability | Guest (no login) | Signed-in account |
| --- | --- | --- |
| Search coordinates, presets, time-lapse viewer | ✅ | ✅ |
| Image data, all 102 bands, blink, subtraction, spectra | ✅ | ✅ |
| Metadata, Gaia/WISE overlays, shareable URLs | ✅ | ✅ |
| Tutorials, training games, AI explainer assistant | ✅ | ✅ |
| Save tutorial and game progress across devices | ❌ (current browser only) | ✅ |
| **Submit observations** to scientists | ❌ | ✅ |
| Earn points and ranks | ❌ | ✅ |

**Architecture implication:** this adds a backend (user accounts and authentication, a submissions database, a scientist review dashboard, and the two AI services) on top of the otherwise front-end-only viewer.

### Risks to manage

- **Scope creep:** games are a bonus. Build them only after the Level 1 viewer works.
- **Accuracy:** simplified wording must stay scientifically correct. Each glossary entry should be checked against the source definition.
- **False positives:** user submissions are candidates, not confirmed objects. Label them clearly as such until a scientist verifies them.
- **Gaming the points system:** if every submission earns points, people may spam low-quality submissions. Mitigate with very small per-submission points, rate limits and reputation weighting.
- **AI explainer accuracy:** an LLM can state wrong facts confidently. Ground its answers in the tool's glossary and the current observation's metadata, and mark its answers as AI-generated.
- **AI ranking bias:** the ranking model must not bury unusual but genuine finds. Scientists should be able to view the unranked queue too, and the model should be retrained on verified outcomes.
- **Privacy:** accounts hold personal data. Collect the minimum (e.g. username and email) and publish a privacy policy.

## 6. Success criteria

1. A user can type coordinates (or pick a preset) and see an animated sequence of SPHEREx cutouts within seconds.
2. Barnard's Star and WISE 0855 visibly move between epochs.
3. RW Aurigae and R Draconis visibly change brightness.
4. The view can be shared via a URL that reproduces the same target and settings.
5. A first-time visitor with no astronomy background can complete the guided tour and spot a moving object without outside help.

## 7. Current status

| Area | Status |
| --- | --- |
| Repository set-up, CI and deploys | Done: GitHub Actions CI, Netlify (frontend) and Render Blueprint (API) |
| Project summary, tech stack, work plan, API contract | Done (`docs/`) |
| Level 1 features | Viewer, playback, presets, epoch selection and share URLs built. The five presets show **real SPHEREx frames** (shipped in `frontend/public/presets/`) in each target's own detector array; other arrays use the live API or, without one, the simulator. Typed coordinates use the live API |
| Level 2 features | Blink, difference, stack, brightness chart, FOV and array controls built on simulated data |
| Level 3 features | Spectrum view, simulated Gaia overlay and a 25-year time machine page built on simulated data. Object tracking, WISE overlay and real catalogues not started |
| Public engagement (tutorials, games) | Glossary tooltips, Beginner/Expert mode, guided tour, lessons and training game built |
| Accounts & submissions | Hard-coded accounts, no database: demo accounts `volunteer` / `starxplorer` and `scientist` / `starxplorer`, plus accounts created in the browser. Play, Review and Profile work with them |
| AI explainer assistant | ✅ Done (tested locally 2026-11-14): `POST /explain` answers with Google Gemini (free tier, `gemini-3.8-flash` with `gemini-3.5-flash-lite` as fallback), grounded in the glossary and the current view, rate-limited per IP. Live on the deployed site once `GEMINI_API_KEY` is set on Render; until then, and whenever Gemini fails, the assistant gives labelled offline answers |
| Science backend | `/epochs` and `/frame` serve real SPHEREx data with `DATA_MODE=irsa` (synthetic data with `mock`). CORS accepts Netlify deploy previews through `CORS_ORIGIN_REGEX` |
