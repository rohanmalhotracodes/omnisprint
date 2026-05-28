<p align="center">
  <img width="1024" height="1024" alt="omnisprint_social_1024x1024" src="https://github.com/user-attachments/assets/ba2010e4-cad3-4b59-9bd6-b3b51cab847a" />


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
- [How is Coral being used?](#how-is-coral-being-used)
- [Architecture](#architecture)
- [How It Works](#how-it-works)
- [Features](#features)
- [Demo Workspace](#demo-workspace)
- [Tech Stack](#tech-stack)
- [Setup](#setup)
- [Environment Variables](#environment-variables)
- [API Endpoints](#api-endpoints)
- [Custom Coral Source Specs](#custom-coral-source-specs)
- [Coral, Gemini, and Token Efficiency](#coral-gemini-and-token-efficiency)
- [Roadmap](#roadmap)
- [Security](#security)

## Problem

OmniSprint is an AI-powered sprint tracking and engineering intelligence platform designed for fast-moving product and engineering teams.
Most teams already track work across multiple tools: Linear for issues, GitHub for code, Notion for documentation, Google Sheets for planning, and team communication tools for follow-ups. The problem is that sprint health is rarely visible in one place. Project managers, engineering leads, and founders often need to manually check several systems to answer basic questions like:
- Are we on track to complete the sprint?
- Which tasks are blocked?
- Which pull requests are pending review?
- Who needs a follow-up?
- Which issues are at risk of slipping?
- Did a recent commit introduce a regression?
- Which owner should take action next?

## Solution

OmniSprint solves this by connecting project management, documentation, spreadsheets, and engineering activity into one intelligent sprint cockpit.

- Pull planning and GitHub evidence through Coral.
- Group semi-structured planning rows into actual projects.
- Extract and map linked issue/PR references.
- Score project-level delivery risk.
- Identify only projects that need attention.
- Generate targeted Google chat/Slack/Whatsapp/email follow-up drafts only for `HIGH`/`CRITICAL` projects.

## How is Coral being used?

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

- can reduce token load
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

Example focused retrieval query:

```sql
SELECT *
FROM planning.projects
WHERE lower(coalesce(status, '')) != 'completed';
```


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
