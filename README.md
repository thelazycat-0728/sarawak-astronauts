# sarawak-astronauts
Sarawak Space Challenge 2026 - PlanetX and SphereX

A public web tool for viewing SPHEREx sky images as a time-lapse, so anyone can spot moving and variable objects.

**Live site:** <https://sarawak-frontend.onrender.com> (API: <https://sarawak-api.onrender.com/health>). Both are on Render's free tier; the API sleeps when idle, so open its `/health` link a minute before a demo to wake it. The five presets work even while it's asleep.

See the [Project Summary](docs/PROJECT_SUMMARY.md) for the challenge, test targets and feature roadmap, the [Tech Stack](docs/TECH_STACK.md) for the technologies, and the [Work Plan](docs/WORK_PLAN.md) for how the work is split across the team.

## Demo accounts

No sign-up needed: the sign-in window has one-click buttons for both.

| Account | Username | Password | Use it for |
| --- | --- | --- | --- |
| Volunteer | `volunteer` | `starxplorer` | Submitting discoveries in Play (training already done) |
| Scientist | `scientist` | `starxplorer` | The **Submissions** tab: reviewing what volunteers submitted (scientists can't submit) |

Accounts are hard-coded in the frontend (`frontend/src/lib/accounts.js`); there is no database and no Supabase. This is deliberate: the judges can sign in with one click, with no sign-up, email or external service. Submissions are saved in the browser, so the scientist sees them only on the **same device and browser** the volunteer used. To demo the flow, submit as `volunteer`, sign out, then sign in as `scientist` in the same browser.

## Current status

- **🧊 Frozen for the video recording (from 2026-11-15).** The live site is the version being recorded. Don't merge to `main` unless it fixes a bug found while recording: every merge that touches `frontend/` or `backend/` redeploys the live site.
- **We're keeping pull requests to a minimum.** Bundle related changes into as few PRs as possible. Don't open a PR just for a small docs or status tweak; fold it into the next feature PR. For the rest of the hackathon, this replaces the "small PRs, merge every 2–3 hours" rule in the [Work Plan](docs/WORK_PLAN.md). AI coding assistants working in this repo should follow it too when they propose branches and PRs.
- Feature-by-feature status: see [Project Summary §7](docs/PROJECT_SUMMARY.md#7-current-status).
