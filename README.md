# omnisprint

omnisprint is a Coral-powered delivery-risk platform for software teams.

It pulls planning + execution signals into one project model, scores risk, and generates targeted follow-up drafts only when projects need attention.

For this repo, the demo workspace is Oppia (`oppia/oppia` + public quarterly targets sheet), but the architecture is source-agnostic.

## 1. What the product does

omnisprint supports:

- Project grouping from multi-row planning sheets (one project, many subtasks)
- Issue/PR extraction from sheet rows and subtask notes
- Project-level risk scoring (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`)
- Issue↔PR mapping (same-subtask links + PR text references like `fixes #123`)
- Optional CI/test signal enrichment (when `ci.signals` is connected)
- Owner and contributor risk views
- Targeted reminder generation for high-risk work
- Copy-ready Google Chat text + optional mailto draft links
- Runtime adaptive assistant ("Omni") with Gemini tool-calling + fallback mode

## 2. Feature list by page

- `Overview`: quick KPIs and top action items
- `Latest Activity`: recent PRs/issues/commits and concise PR brief
- `Projects`: searchable risk table + project detail drawer + subtask table
- `Owners`: lead/contributor concentration and follow-up pressure
- `Actions`: high-risk reminder queue
- `Omni`: chat interface backed by `/api/agent/ask`

## 3. Architecture

### High-level flow

1. Coral retrieves planning rows (`oppia_roadmap.projects`).
2. Backend groups rows into real projects (`backend/normalizer.py`).
3. Backend extracts issue/PR links and builds project link index (`project_links`).
4. Coral queries GitHub tables (`github.issues`, `github.pulls`, optional `github.commits`) and optional `ci.signals`.
5. Backend joins evidence, maps issue→PR relationships, scores risk (`backend/risk_engine.py`).
6. Reminder engine generates targeted follow-up text (`backend/reminder_generator.py`).
7. FastAPI serves dashboard/assistant endpoints (`backend/main.py`).
8. Gemini agent dynamically chooses backend tools at runtime (`backend/gemini_agent.py` + `backend/agent_tools.py`).

### Safety boundary

- Gemini does **not** call GitHub/Sheets directly.
- Gemini can only call backend tool functions.
- Tool functions retrieve data through Coral-backed paths.

## 4. Coral integration (exactly what is used)

omnisprint uses these Coral capabilities:

1. Source registration:
   - Built-in GitHub source via `coral source add github`
   - Custom JSONL sources via YAML source specs in `coral/sources/`
2. SQL interface:
   - `coral sql --format json ...` through `backend/coral_client.py`
3. Cross-source joins:
   - `oppia_roadmap.project_links` joined with `github.issues`, `github.pulls`, and optional `ci.signals`
4. Schema introspection:
   - `information_schema.tables` checks to detect available tables (for mode detection and optional features)
5. Query variant fallback:
   - Backend tries multiple SQL shapes for column compatibility across connector versions

Important:

- We do **not** implement a separate explicit Coral cache layer in code.
- Caching in this repo is backend-level (`backend/.cache/project_reports_cache.json` + in-memory TTL).

## 5. Custom source specs (where they are and what they define)

All custom specs are in:

- `coral/sources/oppia_roadmap_sheet.yaml`
- `coral/sources/oppia_team_sheet.yaml`
- `coral/sources/ci_signals.yaml` (optional)

### `oppia_roadmap_sheet.yaml`

Defines schema `oppia_roadmap` with:

- `projects`: planning snapshot rows (project/subtask fields)
- `project_links`: flattened project-to-GitHub link rows for joins

Backed by JSONL files in `coral/data/`:

- `oppia_roadmap_snapshot.jsonl`
- `oppia_roadmap_project_links.jsonl`

### `oppia_team_sheet.yaml`

Defines schema `oppia_team` with:

- `members`: contributor directory (`name`, `email`, `team`, `role`, `github_handle`)

Backed by:

- `coral/data/oppia_team_snapshot.jsonl`

### `ci_signals.yaml` (optional)

Defines schema `ci` with:

- `signals`: CI status by PR (`pr_number`, `ci_status`, `failed_tests`, `flaky_tests`, `last_run`)

Backed by:

- `coral/data/ci_signals.jsonl`

Note:

- Current specs use absolute `file:///Users/...` paths. If running on another machine/path, update `source.location` in these YAML files.

## 6. Data refresh and dedup strategy

### Refresh path

- `POST /api/sync-roadmap` runs `scripts/snapshot_google_sheets.sh`
- Script pulls latest CSV exports and rebuilds JSONL snapshots via:
  - `backend/roadmap_snapshot_builder.py`
  - `backend/team_snapshot_builder.py`

### Dedup controls

- Duplicate roadmap rows are removed in snapshot build and normalization.
- Duplicate subtasks within a project are removed using subtask signatures.
- Duplicate issue/PR evidence is normalized by numeric key.
- Project reports are deduped by `project_id` before API return.

## 7. Risk scoring logic (project level)

Implemented in `backend/risk_engine.py`. Signals include:

- Planned date pressure / overdue projects
- Unfinished subtasks count
- Blocked/stuck/delayed subtasks
- Risky note keywords (dependency, flaky, merge conflict, pending, etc.)
- Missing contributor ownership
- Open and stale linked issues/PRs
- CI/test failures or flaky CI signals on linked PRs (if available)
- Contributor overload (same contributor on multiple high-risk projects)

Output includes:

- `risk_score`, `risk_level`
- `recommended_actions`
- `high_risk_subtasks`
- issue/PR/CI evidence summaries

## 8. Omni assistant (Gemini dynamic tool-calling)

### Runtime behavior

- Endpoint: `POST /api/agent/ask` (`/api/agent-query` kept for compatibility)
- Gemini selects tool calls dynamically from backend tool registry:
  - `get_projects_summary`
  - `get_project_details`
  - `get_owner_risk_summary`
  - `get_recent_pull_requests`
  - `get_recent_issues`
  - `get_latest_commits`
  - `find_possible_regression_sources`
  - `get_reminder_candidates`
  - `generate_project_reminder`
  - `get_latest_activity_summary`
  - `get_technical_evidence`

### Fallback mode

If Gemini key/model fails (missing key, import failure, timeout, 429/rate limit, etc.), omnisprint switches to deterministic fallback routing and still returns useful answers.

## 9. API reference

- `GET /api/health`: product mode + source status
- `GET /api/sync-status`: last snapshot sync status
- `POST /api/sync-roadmap`: refresh planning/team snapshots
- `GET /api/cache-status`: report cache diagnostics
- `GET /api/projects`: normalized project risk reports
- `GET /api/projects/{project_id}`: one project detail
- `GET /api/owners`: lead/contributor rollups
- `GET /api/reminders/high-risk`: reminder candidates
- `POST /api/reminders/generate`: regenerate reminders with filters
- `GET /api/activity/latest`: latest PR/issue/commit activity
- `POST /api/agent/ask`: Omni assistant query
- `POST /api/agent-query`: backward-compatible alias

## 10. Setup (from scratch)

### Prerequisites

- Python 3.10+ (3.11 recommended)
- Node.js 18+
- Coral CLI

Install Coral CLI (macOS):

```bash
brew install withcoral/coral/coral
```

### Install and run

```bash
cd /Users/rohanmalhotra/Desktop/Hack/oppia-roadmap-risk-oracle
cp .env.example .env
make build
make run
```

Or one command:

```bash
make demo
```

Open:

- Frontend: `http://localhost:5173`
- Backend: `http://localhost:8000`
- Health: `http://localhost:8000/api/health`

## 11. Environment variables

Defined in `.env.example`:

- GitHub:
  - `GITHUB_OWNER`
  - `GITHUB_REPO`
  - `GITHUB_TOKEN`
- Planning sheet:
  - `OPPIA_ROADMAP_SHEET_ID`
  - `OPPIA_ROADMAP_GID`
  - `OPPIA_ROADMAP_CSV_URL`
- Team sheet:
  - `OPPIA_TEAM_SHEET_ID`
  - `OPPIA_TEAM_GID`
  - `OPPIA_TEAM_CSV_URL`
- Runtime:
  - `BACKEND_PORT`
  - `FRONTEND_PORT`
- Gemini:
  - `GEMINI_API_KEY`
  - `GEMINI_MODEL` (default in example: `gemini-2.5-flash`)
  - `OMNISPRINT_AGENT_MAX_TOOL_CALLS`
  - `OMNISPRINT_TITLE_GEMINI_ENABLED`
  - `OMNISPRINT_TITLE_GEMINI_MAX_CALLS`

Security:

- Never commit `.env`.
- Never print `GITHUB_TOKEN` or `GEMINI_API_KEY` in logs.

## 12. Coral registration and verification

Register all sources:

```bash
./scripts/register_coral_sources.sh
```

Manual equivalent:

```bash
coral source add github
coral source add --file coral/sources/oppia_roadmap_sheet.yaml
coral source add --file coral/sources/oppia_team_sheet.yaml
coral source add --file coral/sources/ci_signals.yaml
```

Quick SQL checks:

```bash
coral sql "SELECT * FROM oppia_roadmap.projects LIMIT 3;"
coral sql "SELECT * FROM oppia_roadmap.project_links LIMIT 5;"
coral sql "SELECT * FROM github.issues LIMIT 3;"
coral sql "SELECT * FROM github.pulls LIMIT 3;"
```

## 13. Validation commands

```bash
backend/.venv/bin/python -m py_compile backend/*.py || python3 -m py_compile backend/*.py
npm --prefix frontend run build
./scripts/check.sh
```

## 14. Troubleshooting

### `/api/projects` returns 503

- Check Coral availability:
  - `coral --version`
  - `coral source list`
- Check roadmap source table:
  - `coral sql "SELECT * FROM information_schema.tables WHERE table_schema='oppia_roadmap';"`
- Re-run:
  - `./scripts/snapshot_google_sheets.sh`
  - `./scripts/register_coral_sources.sh`

### Assistant falls back repeatedly

Common causes:

- `GEMINI_API_KEY` missing in `.env`
- model mismatch in `GEMINI_MODEL`
- rate limit (HTTP 429 `RESOURCE_EXHAUSTED`)

Check:

- backend started from repo root with `.env` loaded
- key is present in shell-visible `.env`
- reduce request frequency or switch to a model/quota with available capacity

### Latest activity has partial data

- `github.commits` table may be unavailable in your Coral GitHub connector
- endpoint will return commits as empty/unavailable while issues/PRs still load

### CI/test evidence is empty

- `ci.signals` is optional
- if not connected/populated, CI section shows unavailable/unknown signals

## 15. Repo map

- `backend/main.py`: API surface + report assembly + caching
- `backend/normalizer.py`: grouped project parsing and GitHub link extraction
- `backend/risk_engine.py`: project-level risk scoring
- `backend/reminder_generator.py`: targeted reminder generation
- `backend/agent_tools.py`: Coral-backed tool functions for Omni
- `backend/gemini_agent.py`: Gemini function-calling loop + fallback
- `backend/coral_client.py`: Coral CLI SQL wrapper
- `backend/roadmap_snapshot_builder.py`: roadmap CSV -> JSONL + project links
- `backend/team_snapshot_builder.py`: team CSV -> JSONL
- `frontend/src/App.tsx`: main UI and page routing
- `coral/sources/*.yaml`: custom source specs
- `scripts/snapshot_google_sheets.sh`: data sync
- `scripts/register_coral_sources.sh`: source registration
- `scripts/check.sh`: environment and source checks

## 16. License

MIT (see `LICENSE`).
