# OmniSprint

OmniSprint is a Coral-powered delivery-risk dashboard for software teams.

It connects planning sources and engineering execution sources, normalizes project data into a single model, highlights high-risk work, and generates targeted follow-up drafts for the right contributors.

## What OmniSprint Does
- Groups planning rows into real projects with multiple subtasks
- Tracks owner lead and contributor risk concentration
- Joins linked GitHub issues/PRs and CI signals into project evidence
- Scores project delivery risk (LOW/MEDIUM/HIGH/CRITICAL)
- Surfaces top risk drivers and recommended actions
- Generates targeted Google Chat follow-up drafts only for projects that need attention
- Supports contributor email draft links when email data is available

## Dashboard Analytics
OmniSprint provides an executive view with:
- KPI cards (total projects, at-risk, critical, owners needing follow-up, reminders ready, open linked PRs/issues)
- Risk distribution by level
- Owner risk concentration chart
- Top risky projects panel
- Action queue for follow-ups and reminders

## Why This Matters
Engineering leads often spend time manually stitching together planning sheets, issue trackers, PRs, and owner updates. OmniSprint reduces that manual work by:
- highlighting only what needs attention
- avoiding unnecessary follow-ups to on-track contributors
- producing copy-ready, evidence-backed follow-up drafts

## Generic Architecture
OmniSprint uses a normalized project model and Coral as the retrieval layer.

That means teams can map different source systems into the same model (for example Jira, Linear, Notion, Airtable, GitLab, Bitbucket, Slack/Chat, or Google Sheets), while keeping dashboard logic and risk scoring consistent.

In this repo, the demo mappings are Google Sheets + GitHub + optional CI signals.

## How Coral Is Used
- Coral exposes sources as SQL tables
- Backend queries data through Coral SQL
- Cross-source join workflow:
  1. Coral retrieves planning rows
  2. Backend groups rows into projects
  3. Backend builds/uses `oppia_roadmap.project_links`
  4. Coral joins `project_links` to `github.issues`, `github.pulls`, and `ci.signals`
  5. Backend computes risk and recommendations
  6. Reminder generator creates targeted follow-up drafts

## Dynamic Gemini Tool Calling
OmniSprint now supports runtime-adaptive agent behavior through Gemini function/tool calling.

Instead of fixed intent routing, Gemini decides which backend tool to call at runtime based on the user question. Each backend tool is Coral-backed, so data retrieval remains centralized, consistent, and auditable.

### Agent Architecture
User question  
→ Gemini selects tool  
→ Backend executes tool safely  
→ Tool queries Coral sources  
→ Gemini reasons over evidence  
→ OmniSprint returns answer + confidence + actions + tool trace

### Example Flow
Question: `Which commit may have caused regression?`

1. Gemini calls `get_projects_summary`.
2. Gemini calls `find_possible_regression_sources`.
3. Backend queries Coral-backed issues/PRs/commits tools.
4. Gemini returns likely suspects (not certainty) with confidence.
5. OmniSprint can include targeted follow-up draft actions.

### Fallback Mode
If `GEMINI_API_KEY` is missing, the SDK is unavailable, or a Gemini call fails, OmniSprint automatically uses deterministic fallback routing. This still returns useful answers from Coral-backed tools and includes tool call traces.

## Data Freshness and Caching
- Planning sheet is refreshed on demand (`POST /api/sync-roadmap`) via snapshot rebuild
- GitHub/CI signals are retrieved through Coral during report generation
- Backend report caching:
  - in-memory TTL cache (`REPORT_CACHE_TTL_SECONDS`)
  - persistent disk cache (`backend/.cache/project_reports_cache.json`)
  - source-fingerprint invalidation
- Cache observability endpoint: `GET /api/cache-status`

## Project Structure
- `frontend/`: React + TypeScript + Vite UI
- `backend/`: FastAPI APIs, normalizer, risk engine, reminder generation
- `coral/sources/`: Coral source specs
- `coral/data/`: snapshot data files
- `scripts/`: setup, source registration, checks, snapshot sync

## Quickstart
```bash
cd oppia-roadmap-risk-oracle
cp .env.example .env
make build
make run
```

Or run demo mode:
```bash
make demo
```

Open:
- Frontend: `http://localhost:5173`
- Backend health: `http://localhost:8000/api/health`

## Configuration
Set environment values in `.env`.

Common values:
- `GITHUB_TOKEN` (recommended for stronger GitHub rate limits)
- `GITHUB_OWNER`, `GITHUB_REPO` (demo defaults to `oppia/oppia`)
- `OPPIA_ROADMAP_CSV_URL` or `OPPIA_ROADMAP_SHEET_ID` + `OPPIA_ROADMAP_GID`
- `OPPIA_TEAM_CSV_URL` or `OPPIA_TEAM_SHEET_ID` + `OPPIA_TEAM_GID`
- `GEMINI_API_KEY` (optional; enables Gemini runtime tool-calling)
- `GEMINI_MODEL` (default `gemini-2.5-flash`)
- `OMNISPRINT_AGENT_MAX_TOOL_CALLS` (default `5`)

## Core APIs
- `GET /api/health`
- `GET /api/projects`
- `GET /api/projects/{project_id}`
- `GET /api/owners`
- `GET /api/reminders/high-risk`
- `POST /api/reminders/generate`
- `GET /api/activity/latest`
- `POST /api/agent/ask`
- `POST /api/agent-query`
- `GET /api/sync-status`
- `POST /api/sync-roadmap`
- `GET /api/cache-status`

## Coral Source Setup
```bash
coral source add github
coral source add --file coral/sources/oppia_roadmap_sheet.yaml
coral source add --file coral/sources/oppia_team_sheet.yaml
coral source add --file coral/sources/ci_signals.yaml
```

Useful checks:
```bash
coral sql "SELECT * FROM oppia_roadmap.projects LIMIT 3;"
coral sql "SELECT * FROM oppia_roadmap.project_links LIMIT 5;"
```

## Verification
```bash
npm --prefix frontend run build
backend/.venv/bin/python -m py_compile backend/*.py
./scripts/check.sh
```

## Current Limitations
- Planning data is refresh-based, not continuous streaming
- Historical trend modeling is limited (primarily current-state risk)
- Source adapters for Jira/Linear/etc. are not preconfigured in this repo (but model supports them)

## License / Usage Notes
This repository is built as a hackathon project demonstrating Coral-powered cross-source delivery-risk intelligence.
