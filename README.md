# Sprint Tracker

**Track:** Track 1 — Build an Enterprise Agent

**One-line pitch:** Sprint Tracker is a Coral-powered agent that joins planning sheets, GitHub issues, and GitHub pull requests to identify high-risk projects and generate targeted Google Chat follow-up messages.

## Product
Sprint Tracker is a general software delivery-risk tracker for engineering teams.

## Demo
For this hackathon, Sprint Tracker is connected to:
- Oppia public quarterly targets sheet
- oppia/oppia GitHub issues
- oppia/oppia GitHub pull requests
- Oppia contributor directory sheet (for reminder email drafts)

## Problem
Engineering leads struggle to see which projects are at risk because planning data, GitHub issues/PRs, ownership information, and CI signals live in separate systems.

## Solution
Sprint Tracker uses Coral to expose each data source as SQL, then the backend groups planning-sheet rows into real projects (with multiple subtasks), computes a project-level delivery risk score, and surfaces evidence and recommended actions via a web UI and an agent interface.

## Why this matters
Engineering leads often manually inspect planning sheets and message every contributor for updates. Sprint Tracker only flags high-risk projects and generates targeted Google Chat reminder text for the relevant contributor, avoiding unnecessary reminders to people whose work is on track.

## How Coral Powers This
- Coral exposes Google Sheets, GitHub, and local CSVs as SQL tables.
- The backend issues Coral SQL queries and receives normalized tabular results.
- All data retrieval goes through Coral (no direct GitHub or Google API calls from the backend).
- Query flow:
  1. Coral retrieves planning sheet rows.
  2. Backend groups rows into projects.
  3. Backend builds/uses `oppia_roadmap.project_links` (normalized project-to-link table).
  4. Coral performs cross-source SQL joins from `project_links` to `github.issues`, `github.pulls`, and CI signals.
  5. Backend joins evidence and scores project risk.
  6. Reminder generator creates Google Chat text only for high-risk projects.

## Cross-source join
Coral brings together the planning sheet (demo: Oppia quarterly targets), contributor directory, GitHub issues/PRs (engineering execution), and optionally CI signals (GitHub Actions) when available. The app now prefers SQL-side joins using `oppia_roadmap.project_links` before falling back to direct `IN (...)` lookups.

## Caching
- In-memory report cache with TTL (configurable via `REPORT_CACHE_TTL_SECONDS`).
- Persistent disk cache at `backend/.cache/project_reports_cache.json`.
- Automatic cache invalidation using a fingerprint of snapshot files (`roadmap`, `project_links`, `team`, `ci`).
- `GET /api/cache-status` shows cache validity, fingerprint, and source freshness.

## Architecture
- Frontend: React + Vite + TypeScript + custom CSS (dashboard + agent chat + reminders)
- Backend: FastAPI + Coral CLI wrapper + risk engine
- Coral: source specs live in `coral/sources/`, and snapshot data in `coral/data/`.

## Quickstart
1. Copy environment template:

   cp .env.example .env

2. (Optional) Add `GITHUB_TOKEN` to `.env` for higher GitHub rate limits.

3. Build and run locally:

   make build
   make run

Or using scripts:

   ./build.sh
   ./run.sh

`make demo` runs `./dev.sh` which will build if needed then run the app.

Open: http://localhost:5173

## Modes
- LIVE: planning sheet + GitHub issues + GitHub PRs are connected through Coral.
- HYBRID: planning sheet is connected, but one or more GitHub sources are unavailable.
- NOT_READY: Required Coral sources are not connected.

There is no demo fallback dataset in the UI. The dashboard shows real data or an error state.

## Demo script
1. Run `make demo`.
2. Open the frontend and confirm the mode badge (LIVE/HYBRID/NOT_READY).
3. Inspect the top-risk project list and owner follow-up sections.
4. Click the highest-risk project and expand "Coral Query Flow Used".
5. Open "Smart Google Chat Reminders" and generate high-risk reminder messages.
6. Ask the agent: "Which projects need reminders?"

## Coral commands used in setup
- `coral source add github`
- `coral source add --file coral/sources/oppia_roadmap_sheet.yaml`
- `coral source add --file coral/sources/oppia_team_sheet.yaml`
- `coral sql "SELECT * FROM oppia_roadmap.projects LIMIT 3;"`
- `coral sql "SELECT * FROM oppia_roadmap.project_links LIMIT 5;"`
- `./scripts/snapshot_google_sheets.sh` (downloads latest sheet CSV and rebuilds `coral/data/oppia_roadmap_snapshot.jsonl` and `coral/data/oppia_roadmap_project_links.jsonl`)

## Judging criteria mapping
- Potential Impact: Predicts roadmap slippage and prioritizes reviewer/blocker work.
- Creativity: Joins public roadmap sheets with live GitHub data using Coral.
- Learning: Demonstrates writing custom Coral source specs and cross-source SQL.
- Technical Implementation: FastAPI backend, Coral SQL, deterministic agent, risk scoring.
- Aesthetics & UX: Dashboard, owner views, reminder workflow, and assistant panel.
- Best Use of Coral: Multiple heterogeneous sources exposed as SQL and joined.

## Future work
- Add temporal risk modeling and ML-based slip prediction.
- Add richer CI signals and GitHub checks integration via Coral.
- Improve agent natural-language understanding and context windows.

## Files created
See the project tree under `oppia-roadmap-risk-oracle/` for implementation files, scripts, Coral source specs, and sample data.

---
**Note:** This repository uses Coral as the single retrieval layer. The backend never calls GitHub or Google Sheets directly; all data access is intended to go through Coral sources or snapshots exposed through Coral.
