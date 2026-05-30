---
title: OmniSprint
emoji: 🚀
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Coral-powered sprint intelligence for software teams
---

<p align="center">
  <img src="assets/omnisprint-logo.png" alt="OmniSprint Logo" width="160" />
</p>

# OmniSprint

<p align="center"><strong>Coral-powered sprint intelligence for software teams.</strong></p>

<p align="center">
  <img src="https://img.shields.io/badge/Built%20with-Coral-0ea5e9" alt="Built with Coral" />
  <img src="https://img.shields.io/badge/Backend-FastAPI-059669" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Frontend-React%20%2B%20TypeScript-2563eb" alt="React TypeScript" />
  <img src="https://img.shields.io/badge/Source-GitHub-111827" alt="GitHub" />
  <img src="https://img.shields.io/badge/Use%20Case-Delivery%20Risk%20Intelligence-f97316" alt="Delivery Risk Intelligence" />
</p>

OmniSprint connects planning sheets with GitHub issues, pull requests, and CI signals to detect delivery risk and generate targeted follow-up drafts for project leads.

## Table of Contents

- [Problem](#problem)
- [Solution](#solution)
- [Why Coral](#why-coral)
- [Architecture](#architecture)
- [How It Works](#how-it-works)
- [Features](#features)
- [Demo Workspace](#demo-workspace)
- [Screenshots](#screenshots)
- [Tech Stack](#tech-stack)
- [Setup](#setup)
- [Deploying to Hugging Face Spaces](#deploying-to-hugging-face-spaces)
- [Environment Variables](#environment-variables)
- [API Endpoints](#api-endpoints)
- [Custom Coral Source Specs](#custom-coral-source-specs)
- [Coral, Gemini, and Token Efficiency](#coral-gemini-and-token-efficiency)
- [Hackathon Judging Criteria Alignment](#hackathon-judging-criteria-alignment)
- [Demo Script](#demo-script)
- [Roadmap](#roadmap)
- [Security](#security)

## Problem

Engineering leads often need to manually cross-reference:

- planning sheets
- GitHub issues
- GitHub pull requests
- CI/check status
- contributor updates

This workflow is slow and error-prone.

Common failure modes:

- Broad reminders are sent to everyone, including contributors who are already on track.
- Real blockers are missed because evidence is spread across multiple tools.
- Risk visibility degrades in volunteer/contributor-driven teams where activity and availability vary.

## Solution

OmniSprint turns that fragmented process into a structured risk workflow:

- Pull planning and GitHub evidence through Coral.
- Group semi-structured planning rows into actual projects.
- Extract and map linked issue/PR references.
- Score project-level delivery risk.
- Identify only projects that need attention.
- Generate targeted Google Chat/email follow-up drafts only for `HIGH`/`CRITICAL` projects.

## Why Coral

Coral is the retrieval layer in OmniSprint, not a branding add-on.

In this repository:

- Planning rows are queried from `planning.projects` (custom source spec).
- In legacy demo naming, the same planning table can appear as `oppia_roadmap.projects`.
- Link-index rows are queried from `planning.project_links`.
- GitHub evidence is queried from `github.issues` and `github.pulls`.
- Optional CI evidence is queried from `ci.signals`.
- Backend executes `coral sql ...` via [`backend/coral_client.py`](backend/coral_client.py).
- Source specs are versioned in [`coral/sources/`](coral/sources).
- Health checks and source availability use `information_schema` checks.

After Coral returns rows, OmniSprint performs:

- project grouping
- risk scoring
- owner-level aggregation
- targeted reminder generation

### Why this helps the agent layer

Without Coral, the agent would need large raw planning-sheet exports and large GitHub dumps in prompt context.

With Coral, OmniSprint retrieves focused rows and compact evidence first, then passes only relevant context into the Gemini/agent layer.

This approach:

- helps reduce unnecessary prompt context
- can reduce token load
- keeps responses more retrieval-grounded
- avoids dumping entire sheets/repos into Gemini

## Architecture

```text
Planning Sheet
   ↓
Coral source: planning.projects (legacy demo alias: oppia_roadmap.projects)
   ↓
Backend normalizer groups rows into projects

GitHub Issues / PRs
   ↓
Coral source: github.issues / github.pulls
   ↓
GitHub evidence lookup

Project Evidence
   ↓
Risk Engine
   ↓
Owner Dashboard + Targeted Reminder Generator
```

### Component Breakdown

- Frontend: React dashboard
- Backend: FastAPI service
- Coral Client: wrapper around `coral sql`
- Normalizer: groups sheet rows into projects
- Risk Engine: calculates project risk
- Reminder Generator: creates Google Chat/email drafts
- Coral Sources: planning/GitHub/CI/team retrieval specs

## How It Works

1. Sync/query planning-sheet data through Coral.
2. Normalize sheet rows into project entities.
3. Extract linked GitHub issue/PR numbers from subtasks and notes.
4. Query GitHub issues and PRs through Coral.
5. Join planning and GitHub evidence in backend.
6. Score delivery risk at project level.
7. Generate targeted follow-up drafts for high-risk projects.
8. Surface projects/owners/reminders in the frontend dashboard.

## Features

- Project risk dashboard
- Owner-level risk summaries
- High-risk project detection
- GitHub issue/PR evidence mapping
- Stale PR/issue signal handling
- Blocked/overdue project detection
- Targeted reminder generation
- Copy-ready Google Chat messages
- Email draft links
- Coral-powered source health checks

## Demo Workspace

For the demo, OmniSprint is connected to:

- Oppia Foundation public quarterly planning sheet
- Oppia GitHub repository
- GitHub issues and pull requests
- CI/check signals where available

OmniSprint is not Oppia-specific. Oppia is the demo workspace; the architecture is reusable for other software teams using planning sheets plus GitHub.

## Screenshots

![Dashboard](assets/screenshots/dashboard.png)
![Project Detail](assets/screenshots/project-detail.png)
![Reminders](assets/screenshots/reminders.png)

If these files are not present yet, add screenshots under `assets/screenshots/` before publishing.

If the logo is not present yet, place it at `assets/omnisprint-logo.png`.

## Tech Stack

- Coral (retrieval/query layer)
- FastAPI (backend APIs)
- React + TypeScript (frontend)
- GitHub Coral source (`github`)
- Python risk engine and reminder generator
- Planning-sheet source via Coral custom spec
- Gemini-powered agent layer with deterministic fallback

## Setup

From project root:

```bash
cp .env.example .env
make build
make run
```

Alternative:

```bash
./build.sh
./run.sh
```

One-command dev flow:

```bash
make demo
```

### Notes

- Add `GITHUB_TOKEN` in `.env` for better GitHub source reliability/rate limits.
- Never commit `.env`.
- Keep `.env.example` secret-free.

## Deploying to Hugging Face Spaces

OmniSprint should be deployed as a **Docker Space** because it requires FastAPI, React, Coral CLI, and source-registration scripts.

1. Go to `https://huggingface.co/spaces`.
2. Create a new Space.
3. Name: `omnisprint`.
4. SDK: `Docker`.
5. Visibility: Public or Private.
6. Push this repository to the Space remote.
7. Add Hugging Face Space Secrets:
`GITHUB_TOKEN`
`GEMINI_API_KEY` (optional)
8. Add Hugging Face Space Variables:
`GITHUB_OWNER=oppia`
`GITHUB_REPO=oppia`
`GEMINI_MODEL=gemini-2.5-flash`
`WORKSPACE_ORG_NAME=Oppia Demo`
`PLANNING_CSV_URL=<public planning csv>`
9. Wait for Docker build + startup.
10. Open the Space URL.

Local Docker test:

```bash
docker build -t omnisprint .
docker run --rm -p 7860:7860 \
  -e GITHUB_TOKEN=... \
  -e GITHUB_OWNER=oppia \
  -e GITHUB_REPO=oppia \
  -e GEMINI_API_KEY=... \
  -e PORT=7860 \
  omnisprint
```

Open:

```text
http://localhost:7860
```

## Environment Variables

Current primary variables used by OmniSprint:

```env
WORKSPACE_ORG_NAME=Your Org
GITHUB_OWNER=oppia
GITHUB_REPO=oppia
GITHUB_TOKEN=
PLANNING_CSV_URL=
PLANNING_SHEET_ID=
PLANNING_GID=
TEAM_CSV_URL=
TEAM_SHEET_ID=
TEAM_GID=
PLANNING_SCHEMA=planning
PLANNING_PROJECTS_TABLE=projects
PLANNING_PROJECT_LINKS_TABLE=project_links
TEAM_SCHEMA=team_context
TEAM_MEMBERS_TABLE=members
PORT=7860
BACKEND_PORT=8000
FRONTEND_PORT=5173
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
OMNISPRINT_AGENT_MAX_TOOL_CALLS=5
GITHUB_MATCH_TARGET_REPO_ONLY=0
```

## API Endpoints

- `GET /api/health`
- `GET /api/projects`
- `GET /api/owners`
- `GET /api/reminders/high-risk`
- `POST /api/reminders/generate`
- `POST /api/agent-query`

Additional useful endpoints:

- `POST /api/agent/ask`
- `GET /api/activity/latest`
- `POST /api/sync-planning`
- `GET /api/sync-status`

## Custom Coral Source Specs

OmniSprint uses Coral source specs to expose project-management data as SQL-queryable tables.

### Source specs currently present in this repo

- [`coral/sources/planning_sheet.yaml`](coral/sources/planning_sheet.yaml)
- [`coral/sources/ci_signals.yaml`](coral/sources/ci_signals.yaml)
- [`coral/sources/team_directory.yaml`](coral/sources/team_directory.yaml)

### Source specs referenced in earlier demo iterations

The following names may appear in older docs/branches but are not the current default in this branch:

- `coral/sources/oppia_roadmap_sheet.yaml` (replaced by `planning_sheet.yaml`)
- `coral/sources/oppia_team_sheet.yaml` (replaced by `team_directory.yaml`)
- `coral/sources/blocker_notes.yaml` (optional, not present by default)

### What each source does

1. `planning_sheet.yaml`
- Exposes planning data as `planning.projects` and `planning.project_links`.
- Purpose: project names, leads, contributors, status, subtasks, planned dates, notes, and GitHub links.

2. `ci_signals.yaml`
- Exposes CI/check signals as `ci.signals`.
- Purpose: failing/flaky signal enrichment for linked PRs.

3. `team_directory.yaml`
- Exposes team context as `team_context.members`.
- Purpose: contributor-owner/email mapping for targeted reminders.

4. Optional `blocker_notes.yaml` (if added)
- Suggested table: `team_context.blocker_notes`.
- Purpose: human context such as dependency blockers, review bottlenecks, delayed ownership.

### Bundled vs custom Coral sources

- Bundled Coral source: `github`
- Custom/local source specs: planning sheet, CI signals, team directory (and optional blocker notes)

Register bundled GitHub source:

```bash
coral source add github
```

Register local specs:

```bash
./scripts/register_coral_sources.sh
```

### Source map

| Source | Coral table | Type | Purpose |
|---|---|---|---|
| Planning sheet | `planning.projects` | Custom source spec | Project ownership, status, deadlines |
| Planning link index | `planning.project_links` | Custom source spec | Project ↔ issue/PR join index |
| GitHub issues | `github.issues` | Bundled Coral source | Linked issue state and labels |
| GitHub PRs | `github.pulls` | Bundled Coral source | PR state, staleness, review status |
| CI signals | `ci.signals` | Custom source spec | Failing/flaky checks |
| Team members | `team_context.members` | Custom source spec | Contributor/email mapping |
| Blocker notes (optional) | `team_context.blocker_notes` | Custom source spec | Human project context |

> Legacy demo table names such as `oppia_roadmap.projects` and `oppia_team.members` can be supported via schema aliases, but current default schemas are `planning` and `team_context`.

## Coral, Gemini, and Token Efficiency

OmniSprint can use a Gemini-powered agent layer for natural-language questions and response generation, but Coral keeps the agent grounded and compact.

Without Coral:

- The agent would need large raw planning-sheet payloads.
- It might also need large GitHub issue/PR dumps.
- Context size and token usage would increase.
- The model would have to search through unstructured data itself.

With Coral:

- Backend queries Coral for specific rows/evidence via SQL.
- Coral returns compact, structured results.
- Gemini receives only relevant project evidence.
- This helps reduce unnecessary context sent to the model.
- It can lower token load and improve response grounding.
- The agent focuses on reasoning/writing while Coral handles retrieval.

Careful claim:

- OmniSprint helps reduce unnecessary prompt context and can reduce token load.
- It does not claim fixed percentage savings or guaranteed hallucination elimination.

Example focused retrieval query:

```sql
SELECT *
FROM planning.projects
WHERE lower(coalesce(status, '')) != 'completed';
```

## Hackathon Judging Criteria Alignment

### 🏴‍☠️ Potential Impact

OmniSprint reduces manual follow-up overhead and helps leads focus on risky projects instead of broadcasting reminders to everyone.

### ⚓ Creativity & Originality

OmniSprint does more than dashboarding. It converts cross-source risk evidence into targeted follow-up drafts for the right contributors.

### 🗺️ Learning & Growth

The project required hands-on learning across:

- Coral source setup
- SQL retrieval patterns
- GitHub source integration
- semi-structured planning-sheet normalization
- project-level risk modeling

### ⚔️ Technical Implementation

Implementation combines:

- FastAPI backend
- React frontend
- Coral CLI + SQL retrieval
- project normalizer
- risk engine
- reminder generator

### 🎨 Aesthetics & UX

The product provides a professional dashboard with project risk views, owner summaries, detail drill-downs, and reminder actions.

### 🪸 Best Use of Coral

Coral is the operational retrieval layer for planning and GitHub evidence. SQL-based multi-source access keeps the agent layer grounded with compact evidence instead of large prompt stuffing.

## Demo Script

1. Open the dashboard.
2. Show at-risk project count.
3. Open a high-risk project detail.
4. Show planning + linked issue/PR evidence.
5. Generate Google Chat reminder draft.
6. Open email draft link.
7. Show source health in `/api/health`.
8. Explain Coral retrieval flow and how risk is computed.

## Roadmap

- Real-time planning-source connector improvements
- Native Google Chat integration (send workflow)
- Deeper GitHub Actions/CI diagnostics
- Slack/Linear/Jira adapters
- Historical risk trends and reporting
- Multi-workspace support
- Admin configuration UI

## Security

- Use a read-only GitHub token where possible.
- Never commit `.env`.
- No automatic reminders are sent by default.
- Leads review and send generated drafts manually.

---

For contribution and implementation details, start with:

- [`backend/main.py`](backend/main.py)
- [`backend/normalizer.py`](backend/normalizer.py)
- [`backend/risk_engine.py`](backend/risk_engine.py)
- [`backend/reminder_generator.py`](backend/reminder_generator.py)
- [`backend/agent_tools.py`](backend/agent_tools.py)
- [`backend/gemini_agent.py`](backend/gemini_agent.py)
- [`coral/sources/`](coral/sources)
