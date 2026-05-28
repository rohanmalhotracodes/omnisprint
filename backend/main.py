import os
import re
import json
import hashlib
import subprocess
from time import monotonic
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .coral_client import CoralClient
from .normalizer import group_planning_rows
from .reminder_generator import generate_reminders
from .risk_engine import score_project

ROOT_DIR = Path(__file__).resolve().parents[1]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            os.environ[key] = value
    except Exception:
        # Best-effort .env loading; explicit environment variables still win.
        return


_load_env_file(ROOT_DIR / ".env")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

coral = CoralClient()

SNAPSHOT_SCRIPT = ROOT_DIR / "scripts" / "snapshot_planning_source.sh"

SYNC_STATUS: Dict[str, Any] = {
    "status": "IDLE",
    "last_synced_at": None,
    "message": "Sync has not been triggered in this session.",
    "last_error": None,
}

REPORT_CACHE: Dict[str, Any] = {
    "reports": None,
    "generated_at": None,
    "limit": 0,
    "source_fingerprint": None,
    "source_state": None,
}
REPORT_CACHE_TTL_SECONDS = int(os.getenv("REPORT_CACHE_TTL_SECONDS", "180"))
REPORT_CACHE_SCHEMA_VERSION = 2
REPORT_CACHE_FILE = ROOT_DIR / "backend" / ".cache" / "project_reports_cache.json"
REPORT_CACHE_SNAPSHOT_FILES = [
    ROOT_DIR / "coral" / "data" / "planning_snapshot.jsonl",
    ROOT_DIR / "coral" / "data" / "planning_project_links.jsonl",
    ROOT_DIR / "coral" / "data" / "team_snapshot.jsonl",
    ROOT_DIR / "coral" / "data" / "ci_signals.jsonl",
]
TABLE_EXISTS_CACHE: Dict[str, Tuple[float, bool]] = {}
TABLE_EXISTS_CACHE_TTL_SECONDS = int(os.getenv("TABLE_EXISTS_CACHE_TTL_SECONDS", "20"))

_PR_CLOSING_ISSUE_REF_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*(?:(?P<repo>[a-z0-9_.-]+/[a-z0-9_.-]+))?#(?P<num>\d+)\b",
    re.IGNORECASE,
)
PROJECT_BUILD_TIMEOUT_SECONDS = int(os.getenv("PROJECT_BUILD_TIMEOUT_SECONDS", "30"))


class AgentAskRequest(BaseModel):
    question: str


def _clean(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", "null"):
        return ""
    return s


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except Exception:
        return 0


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    text = _clean(v).lower()
    return text in ("1", "true", "yes", "on")


def _env_str(key: str, default: str = "") -> str:
    return str(os.getenv(key) or default).strip()


def _dedupe_ordered(values: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = _clean(value)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _parse_aliases(raw: str) -> List[str]:
    text = _clean(raw)
    if not text:
        return []
    parts = [p.strip() for p in text.split(",")]
    return [p for p in parts if p]


def _planning_schema_name() -> str:
    return _env_str("PLANNING_SCHEMA", "planning")


def _planning_projects_table_name() -> str:
    return _env_str("PLANNING_PROJECTS_TABLE", "projects")


def _planning_project_links_table_name() -> str:
    return _env_str("PLANNING_PROJECT_LINKS_TABLE", "project_links")


def _team_schema_name() -> str:
    return _env_str("TEAM_SCHEMA", "team_context")


def _team_members_table_name() -> str:
    return _env_str("TEAM_MEMBERS_TABLE", "members")


def _planning_schema_candidates() -> List[str]:
    aliases = _parse_aliases(_env_str("PLANNING_SCHEMA_ALIASES"))
    return _dedupe_ordered([_planning_schema_name()] + aliases + ["planning"])


def _team_schema_candidates() -> List[str]:
    aliases = _parse_aliases(_env_str("TEAM_SCHEMA_ALIASES"))
    return _dedupe_ordered([_team_schema_name()] + aliases + ["team_context"])


def _resolve_table_ref(schema_candidates: List[str], table_name: str) -> Tuple[str, str]:
    for schema in schema_candidates:
        if _table_exists(schema, table_name):
            return schema, table_name
    return schema_candidates[0], table_name


def _planning_projects_ref() -> Tuple[str, str]:
    return _resolve_table_ref(_planning_schema_candidates(), _planning_projects_table_name())


def _planning_links_ref() -> Tuple[str, str]:
    return _resolve_table_ref(_planning_schema_candidates(), _planning_project_links_table_name())


def _team_members_ref() -> Tuple[str, str]:
    return _resolve_table_ref(_team_schema_candidates(), _team_members_table_name())


def _qualified_table(schema: str, table: str) -> str:
    return f"{schema}.{table}"


def _parse_dt(s: Any):
    if s is None:
        return None
    raw = str(s).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", ""))
    except Exception:
        return None


def _utc_now() -> datetime:
    return datetime.utcnow()


def _enforce_build_deadline(start_ts: float, stage: str) -> None:
    elapsed = monotonic() - start_ts
    if elapsed > PROJECT_BUILD_TIMEOUT_SECONDS:
        raise RuntimeError(
            f"Project report generation timed out after {elapsed:.1f}s during {stage}. "
            "Coral/source retrieval is too slow right now."
        )


def _dt_to_cache_str(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat() + "Z"


def _cache_str_to_dt(raw: Any):
    text = _clean(raw)
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1]
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except Exception:
        return None


def _build_source_state() -> Dict[str, Any]:
    files_state: List[Dict[str, Any]] = []
    for fp in REPORT_CACHE_SNAPSHOT_FILES:
        rel = str(fp.relative_to(ROOT_DIR))
        if not fp.exists():
            files_state.append({"path": rel, "exists": False})
            continue
        st = fp.stat()
        files_state.append(
            {
                "path": rel,
                "exists": True,
                "size": int(st.st_size),
                "mtime_ns": int(st.st_mtime_ns),
            }
        )

    return {
        "repo_slug": _repo_slug(),
        "files": files_state,
    }


def _source_fingerprint(source_state: Dict[str, Any]) -> str:
    encoded = json.dumps(source_state, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _memory_cache_is_valid(limit: int, source_fingerprint: str) -> bool:
    cache_reports = REPORT_CACHE.get("reports")
    cache_generated_at = REPORT_CACHE.get("generated_at")
    cache_limit = int(REPORT_CACHE.get("limit") or 0)
    cache_source = _clean(REPORT_CACHE.get("source_fingerprint"))
    return bool(
        isinstance(cache_reports, list)
        and cache_generated_at
        and cache_limit >= int(limit)
        and cache_source
        and cache_source == source_fingerprint
        and _utc_now() - cache_generated_at < timedelta(seconds=REPORT_CACHE_TTL_SECONDS)
    )


def _disk_cache_payload() -> Optional[Dict[str, Any]]:
    if not REPORT_CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(REPORT_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if int(payload.get("schema_version", 0)) != REPORT_CACHE_SCHEMA_VERSION:
        return None
    return payload


def _disk_cache_is_valid(payload: Dict[str, Any], limit: int, source_fingerprint: str) -> bool:
    reports = payload.get("reports")
    generated_at = _cache_str_to_dt(payload.get("generated_at"))
    cache_limit = int(payload.get("limit") or 0)
    cache_source = _clean(payload.get("source_fingerprint"))
    return bool(
        isinstance(reports, list)
        and generated_at
        and cache_limit >= int(limit)
        and cache_source
        and cache_source == source_fingerprint
        and _utc_now() - generated_at < timedelta(seconds=REPORT_CACHE_TTL_SECONDS)
    )


def _hydrate_memory_cache_from_disk(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    reports = list(payload.get("reports") or [])
    REPORT_CACHE["reports"] = reports
    REPORT_CACHE["generated_at"] = _cache_str_to_dt(payload.get("generated_at"))
    REPORT_CACHE["limit"] = int(payload.get("limit") or 0)
    REPORT_CACHE["source_fingerprint"] = payload.get("source_fingerprint")
    REPORT_CACHE["source_state"] = payload.get("source_state")
    return reports


def _persist_disk_cache(
    reports: List[Dict[str, Any]],
    generated_at: datetime,
    limit: int,
    source_fingerprint: str,
    source_state: Dict[str, Any],
) -> None:
    payload = {
        "schema_version": REPORT_CACHE_SCHEMA_VERSION,
        "generated_at": _dt_to_cache_str(generated_at),
        "limit": int(limit),
        "source_fingerprint": source_fingerprint,
        "source_state": source_state,
        "reports": reports,
    }
    try:
        REPORT_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        REPORT_CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    except Exception:
        # Cache persistence is best-effort; report generation should still succeed.
        pass


def _clear_report_cache() -> None:
    REPORT_CACHE["reports"] = None
    REPORT_CACHE["generated_at"] = None
    REPORT_CACHE["limit"] = 0
    REPORT_CACHE["source_fingerprint"] = None
    REPORT_CACHE["source_state"] = None
    try:
        REPORT_CACHE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _load_env_file(env_file: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not env_file.exists():
        return out
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        val = val.strip()
        # Strip wrapping quotes for simple KEY="value" or KEY='value' entries.
        if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
            val = val[1:-1]
        # Strip inline comments for unquoted values: KEY=value # comment
        if " #" in val:
            val = val.split(" #", 1)[0].rstrip()
        if key:
            out[key] = val
    return out


def _load_env_defaults_from_file() -> None:
    file_env = _load_env_file(ROOT_DIR / ".env")
    for key, val in file_env.items():
        existing = str(os.environ.get(key, "")).strip() if key else ""
        # Prefer process env when explicitly set, but allow .env to override
        # unset/blank values.
        if key and (key not in os.environ or not existing):
            os.environ[key] = val


_load_env_defaults_from_file()


def _table_exists(schema: str, table: Optional[str] = None) -> bool:
    cache_key = f"{schema}.{table or '*'}"
    now = monotonic()
    cached = TABLE_EXISTS_CACHE.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    try:
        if table:
            q = f"SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema = '{schema}' AND table_name = '{table}' LIMIT 1;"
        else:
            q = f"SELECT table_schema FROM information_schema.tables WHERE table_schema = '{schema}' LIMIT 1;"
        rows = coral.run_sql(q, timeout=8)
        if not rows:
            TABLE_EXISTS_CACHE[cache_key] = (now + TABLE_EXISTS_CACHE_TTL_SECONDS, False)
            return False
        joined = " ".join(
            [
                " ".join(str(v) for v in r.values()) if isinstance(r, dict) else str(r)
                for r in rows
            ]
        ).lower()
        if schema.lower() not in joined:
            TABLE_EXISTS_CACHE[cache_key] = (now + TABLE_EXISTS_CACHE_TTL_SECONDS, False)
            return False
        if table and table.lower() not in joined:
            TABLE_EXISTS_CACHE[cache_key] = (now + TABLE_EXISTS_CACHE_TTL_SECONDS, False)
            return False
        TABLE_EXISTS_CACHE[cache_key] = (now + TABLE_EXISTS_CACHE_TTL_SECONDS, True)
        return True
    except Exception:
        TABLE_EXISTS_CACHE[cache_key] = (now + TABLE_EXISTS_CACHE_TTL_SECONDS, False)
        return False


def _fetch_planning_rows(limit: int = 1000) -> List[Dict[str, Any]]:
    if not coral.available():
        raise RuntimeError("Coral CLI not available")
    planning_schema, planning_table = _planning_projects_ref()
    q = f"SELECT * FROM {_qualified_table(planning_schema, planning_table)} LIMIT {int(limit)};"
    rows = coral.run_sql(q, timeout=12)
    if not isinstance(rows, list):
        raise RuntimeError("Coral planning query did not return row list")
    return rows


def _run_sql_variants(variants: List[str], timeout: int = 8) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    last_error: Optional[Exception] = None
    for q in variants:
        try:
            rows = coral.run_sql(q, timeout=timeout)
            if isinstance(rows, list):
                return rows, q
            return [], q
        except Exception as e:
            last_error = e
            # Fail fast for transport/runtime failures; retrying alternate SQL shapes
            # does not help and can stall API responses.
            msg = str(e).lower()
            if (
                "timed out" in msg
                or "operation not permitted" in msg
                or "permission denied" in msg
                or "connection" in msg
                or "network" in msg
                or "unauthorized" in msg
                or "forbidden" in msg
                or "token" in msg
                or "rate limit" in msg
            ):
                raise
            continue
    if last_error is not None:
        raise last_error
    return [], None


def _sql_literal(value: str) -> str:
    return str(value).replace("'", "''")


def _fetch_live_github_maps(
    all_issue_nums: List[int], all_pr_nums: List[int]
) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]], List[str]]:
    issues_map: Dict[int, Dict[str, Any]] = {}
    prs_map: Dict[int, Dict[str, Any]] = {}
    queries: List[str] = []

    if not coral.available():
        return issues_map, prs_map, queries

    default_owner, default_repo = _repo_slug().split("/", 1)
    gh_owner = (os.getenv("GITHUB_OWNER") or os.getenv("GITHUB_REPO_OWNER") or default_owner).strip()
    gh_repo = (os.getenv("GITHUB_REPO") or default_repo).strip()
    owner_clause = f"owner = '{_sql_literal(gh_owner)}'" if gh_owner else None
    repo_clause = f"repo = '{_sql_literal(gh_repo)}'" if gh_repo else None

    prefix_clause = ""
    if owner_clause and repo_clause:
        prefix_clause = f"{owner_clause} AND {repo_clause} AND "
    elif owner_clause:
        prefix_clause = f"{owner_clause} AND "

    planning_links_schema, planning_links_table = _planning_links_ref()
    planning_links_q = _qualified_table(planning_links_schema, planning_links_table)

    # Preferred path: perform cross-source joins in Coral SQL via project_links.
    used_join_path = False
    if _table_exists(planning_links_schema, planning_links_table):
        owner_repo_issue_join = ""
        owner_repo_pr_join = ""
        if gh_owner:
            owner_repo_issue_join += f" AND i.owner = '{_sql_literal(gh_owner)}'"
            owner_repo_pr_join += f" AND p.owner = '{_sql_literal(gh_owner)}'"
        if gh_repo:
            owner_repo_issue_join += f" AND i.repo = '{_sql_literal(gh_repo)}'"
            owner_repo_pr_join += f" AND p.repo = '{_sql_literal(gh_repo)}'"

        if all_issue_nums:
            issue_in_clause = ",".join(str(int(x)) for x in sorted(set(all_issue_nums)))
            issue_link_filter = f" AND pl.link_number IN ({issue_in_clause})"
            issue_join_variants = [
                "SELECT pl.link_number AS link_number, i.number, i.title, i.state, i.labels, i.updated_at, i.html_url, i.body "
                f"FROM {planning_links_q} pl "
                f"LEFT JOIN github.issues i ON i.number = pl.link_number{owner_repo_issue_join} "
                "WHERE pl.link_type = 'issue' "
                f"{issue_link_filter};",
                "SELECT pl.link_number AS link_number, i.number, i.title, i.state, i.labels, i.updated_at, i.html_url "
                f"FROM {planning_links_q} pl "
                f"LEFT JOIN github.issues i ON i.number = pl.link_number{owner_repo_issue_join} "
                "WHERE pl.link_type = 'issue' "
                f"{issue_link_filter};",
            ]
            try:
                issue_rows, used_q = _run_sql_variants(issue_join_variants, timeout=4)
                if used_q:
                    queries.append(used_q)
                if issue_rows:
                    used_join_path = True
                for r in issue_rows or []:
                    num = _to_int(r.get("number") or r.get("link_number") or r.get("issue_number") or r.get("id"))
                    if num > 0 and num not in issues_map:
                        issues_map[num] = r
            except Exception:
                pass

        if all_pr_nums:
            pr_in_clause = ",".join(str(int(x)) for x in sorted(set(all_pr_nums)))
            pr_link_filter = f" AND pl.link_number IN ({pr_in_clause})"
            pr_join_variants = [
                "SELECT pl.link_number AS link_number, p.number, p.title, p.state, p.updated_at, p.draft, p.html_url, p.merged_at "
                f"FROM {planning_links_q} pl "
                f"LEFT JOIN github.pulls p ON p.number = pl.link_number{owner_repo_pr_join} "
                "WHERE pl.link_type = 'pr' "
                f"{pr_link_filter};",
                "SELECT pl.link_number AS link_number, p.number, p.title, p.state, p.updated_at, p.draft, p.html_url "
                f"FROM {planning_links_q} pl "
                f"LEFT JOIN github.pulls p ON p.number = pl.link_number{owner_repo_pr_join} "
                "WHERE pl.link_type = 'pr' "
                f"{pr_link_filter};",
            ]
            try:
                pr_rows, used_q = _run_sql_variants(pr_join_variants, timeout=4)
                if used_q:
                    queries.append(used_q)
                if pr_rows:
                    used_join_path = True
                for r in pr_rows or []:
                    num = _to_int(r.get("number") or r.get("link_number") or r.get("id"))
                    if num > 0 and num not in prs_map:
                        prs_map[num] = r
            except Exception:
                pass

    # Fallback path: fetch by IN lists if joins are unavailable or empty.
    if not used_join_path:
        if all_issue_nums:
            in_clause = ",".join(str(int(x)) for x in sorted(set(all_issue_nums)))
            issue_variants = [
                "SELECT number, title, state, labels, updated_at, html_url, body "
                f"FROM github.issues WHERE {prefix_clause} number IN ({in_clause});",
                "SELECT number, title, state, labels, updated_at, html_url "
                f"FROM github.issues WHERE {prefix_clause} number IN ({in_clause});",
            ]
            try:
                issue_rows, used_q = _run_sql_variants(issue_variants, timeout=4)
                if used_q:
                    queries.append(used_q)
                for r in issue_rows or []:
                    num = _to_int(r.get("number") or r.get("issue_number") or r.get("id"))
                    if num > 0:
                        issues_map[num] = r
            except Exception:
                pass

        if all_pr_nums:
            in_clause = ",".join(str(int(x)) for x in sorted(set(all_pr_nums)))
            pr_variants = [
                "SELECT number, title, state, updated_at, draft, html_url, merged_at "
                f"FROM github.pulls WHERE {prefix_clause} number IN ({in_clause});",
                "SELECT number, title, state, updated_at, draft, html_url "
                f"FROM github.pulls WHERE {prefix_clause} number IN ({in_clause});",
            ]
            try:
                pr_rows, used_q = _run_sql_variants(pr_variants, timeout=4)
                if used_q:
                    queries.append(used_q)
                for r in pr_rows or []:
                    num = _to_int(r.get("number") or r.get("id"))
                    if num > 0:
                        prs_map[num] = r
            except Exception:
                pass

    return issues_map, prs_map, queries


def _fetch_ci_signals_map(all_pr_nums: List[int]) -> Tuple[Dict[int, Dict[str, Any]], List[str]]:
    ci_map: Dict[int, Dict[str, Any]] = {}
    queries: List[str] = []

    if not all_pr_nums or not coral.available() or not _table_exists("ci", "signals"):
        return ci_map, queries

    planning_links_schema, planning_links_table = _planning_links_ref()
    planning_links_q = _qualified_table(planning_links_schema, planning_links_table)

    if _table_exists(planning_links_schema, planning_links_table):
        in_clause = ",".join(str(int(x)) for x in sorted(set(all_pr_nums)))
        join_query = (
            "SELECT DISTINCT pl.link_number AS pr_number, c.ci_status, c.failed_tests, c.flaky_tests, c.last_run "
            f"FROM {planning_links_q} pl "
            "LEFT JOIN ci.signals c ON c.pr_number = pl.link_number "
            "WHERE pl.link_type = 'pr' "
            f"AND pl.link_number IN ({in_clause});"
        )
        try:
            rows = coral.run_sql(join_query) or []
            queries.append(join_query)
            for r in rows:
                pr_num = _to_int(r.get("pr_number") or r.get("number"))
                if pr_num > 0 and any(_clean(r.get(k)) for k in ("ci_status", "failed_tests", "flaky_tests", "last_run")):
                    ci_map[pr_num] = r
            if ci_map:
                return ci_map, queries
        except Exception:
            pass

    in_clause = ",".join(str(int(x)) for x in sorted(set(all_pr_nums)))
    q = (
        "SELECT pr_number, ci_status, failed_tests, flaky_tests, last_run "
        f"FROM ci.signals WHERE pr_number IN ({in_clause});"
    )
    try:
        rows = coral.run_sql(q) or []
        queries.append(q)
        for r in rows:
            pr_num = _to_int(r.get("pr_number") or r.get("number"))
            if pr_num > 0:
                ci_map[pr_num] = r
    except Exception:
        pass

    return ci_map, queries


def _augment_projects_with_planning_links(projects: List[Any]) -> None:
    if not projects or not coral.available():
        return

    planning_links_schema, planning_links_table = _planning_links_ref()
    if not _table_exists(planning_links_schema, planning_links_table):
        return

    planning_links_q = _qualified_table(planning_links_schema, planning_links_table)
    variants = [
        f"SELECT project_id, link_type, link_number FROM {planning_links_q};",
        f"SELECT project_id, type AS link_type, number AS link_number FROM {planning_links_q};",
    ]
    try:
        rows, _ = _run_sql_variants(variants, timeout=6)
    except Exception:
        return
    if not rows:
        return

    issue_by_project: Dict[str, set[int]] = defaultdict(set)
    pr_by_project: Dict[str, set[int]] = defaultdict(set)
    for row in rows:
        project_id = _clean(row.get("project_id"))
        link_type = _clean(row.get("link_type")).lower()
        link_number = _to_int(row.get("link_number") or row.get("number"))
        if not project_id or link_number <= 0:
            continue
        if link_type == "issue":
            issue_by_project[project_id].add(link_number)
        elif link_type == "pr":
            pr_by_project[project_id].add(link_number)

    for project in projects:
        pid = _clean(getattr(project, "project_id", ""))
        if not pid:
            continue
        current_issue_nums = {_to_int(n) for n in (getattr(project, "all_github_issue_numbers", None) or []) if _to_int(n) > 0}
        current_pr_nums = {_to_int(n) for n in (getattr(project, "all_github_pr_numbers", None) or []) if _to_int(n) > 0}
        current_issue_nums.update(issue_by_project.get(pid, set()))
        current_pr_nums.update(pr_by_project.get(pid, set()))
        project.all_github_issue_numbers = sorted(current_issue_nums)
        project.all_github_pr_numbers = sorted(current_pr_nums)


def _is_valid_email(val: str) -> bool:
    text = _clean(val)
    return "@" in text and "." in text and " " not in text


def _fetch_contributor_email_map() -> Tuple[Dict[str, str], List[str]]:
    contributor_email_map: Dict[str, str] = {}
    queries: List[str] = []

    if not coral.available():
        return contributor_email_map, queries

    member_table = _team_members_table_name()
    candidate_tables: List[str] = []
    for schema in _team_schema_candidates():
        if _table_exists(schema, member_table):
            candidate_tables.append(_qualified_table(schema, member_table))

    for table_name in candidate_tables:
        variants = [
            f"SELECT name, email, role, team, github_handle FROM {table_name};",
            f"SELECT contributor, contributor_email FROM {table_name};",
            f"SELECT * FROM {table_name};",
        ]
        try:
            rows, used_q = _run_sql_variants(variants, timeout=6)
        except Exception:
            continue

        if used_q:
            queries.append(used_q)

        for row in rows or []:
            email = _clean(
                row.get("email")
                or row.get("contributor_email")
                or row.get("owner_email")
                or row.get("mail")
            )
            if not _is_valid_email(email):
                continue

            raw_name = _clean(
                row.get("name")
                or row.get("contributor")
                or row.get("owner_contributor")
                or row.get("member_name")
                or row.get("project_owner_contributor")
            )
            names = _split_people(raw_name)
            if not names:
                continue

            for name in names:
                norm = _normalize_person(name)
                if not norm:
                    continue
                if norm not in contributor_email_map:
                    contributor_email_map[norm] = email
                first_token = _clean(name).split(" ")[0] if _clean(name) else ""
                first_norm = _normalize_person(first_token)
                if first_norm and first_norm not in contributor_email_map:
                    contributor_email_map[first_norm] = email

    return contributor_email_map, queries


def _repo_slug() -> str:
    owner = (os.getenv("GITHUB_OWNER") or os.getenv("GITHUB_REPO_OWNER") or "your-org").strip()
    repo = (os.getenv("GITHUB_REPO") or "your-repo").strip()
    return f"{owner}/{repo}"


def _workspace_org_name() -> str:
    explicit = _clean(os.getenv("WORKSPACE_ORG_NAME") or os.getenv("WORKSPACE_NAME"))
    if explicit:
        return explicit
    owner = _clean(os.getenv("GITHUB_OWNER") or os.getenv("GITHUB_REPO_OWNER"))
    if owner:
        normalized = owner.replace("-", " ").replace("_", " ").strip()
        return normalized.title() if normalized else owner
    return "Your Org"


def _planning_source_label() -> str:
    return _clean(os.getenv("PLANNING_SOURCE_LABEL")) or "Planning sheet"


def _planning_source_display_name() -> str:
    return _clean(os.getenv("PLANNING_SOURCE_DISPLAY_NAME")) or _planning_source_label()


def _engineering_source_label() -> str:
    configured = _clean(os.getenv("ENGINEERING_SOURCE_LABEL"))
    if configured:
        return configured
    return f"{_repo_slug()} GitHub"


def _team_source_label() -> str:
    return _clean(os.getenv("TEAM_SOURCE_LABEL")) or "Contributor directory"


def _issue_url(number: int) -> str:
    return f"https://github.com/{_repo_slug()}/issues/{int(number)}"


def _pr_url(number: int) -> str:
    return f"https://github.com/{_repo_slug()}/pull/{int(number)}"


def _normalize_issue_evidence_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    num = _to_int(row.get("number") or row.get("link_number") or row.get("issue_number") or row.get("id"))
    if num <= 0:
        return None
    return {
        "number": num,
        "title": _clean(row.get("title")),
        "state": (_clean(row.get("state")) or "unknown").lower(),
        "labels": row.get("labels"),
        "updated_at": _clean(row.get("updated_at") or row.get("updated")),
        "html_url": _clean(row.get("html_url")) or _issue_url(num),
    }


def _normalize_pr_evidence_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    num = _to_int(row.get("number") or row.get("link_number") or row.get("id"))
    if num <= 0:
        return None
    return {
        "number": num,
        "title": _clean(row.get("title")),
        "state": (_clean(row.get("state")) or "unknown").lower(),
        "draft": bool(row.get("draft")) if row.get("draft") is not None else None,
        "updated_at": _clean(row.get("updated_at") or row.get("updated")),
        "html_url": _clean(row.get("html_url")) or _pr_url(num),
    }


def _build_risk_summary(
    risk_level: str,
    risk_drivers: List[str],
    blocked_subtasks: int,
    open_issues: int,
    open_prs: int,
    stale_prs: int,
    failing_ci_prs: int,
) -> str:
    level = (risk_level or "LOW").upper()
    reasons: List[str] = []
    if blocked_subtasks > 0:
        reasons.append(f"{blocked_subtasks} subtasks are blocked")
    if open_issues > 0:
        reasons.append(f"{open_issues} linked issues are open")
    if open_prs > 0:
        reasons.append(f"{open_prs} linked pull requests are open")
    if stale_prs > 0:
        reasons.append(f"{stale_prs} linked pull requests are stale")
    if failing_ci_prs > 0:
        reasons.append(f"{failing_ci_prs} linked pull requests have failing CI checks")

    if not reasons:
        for driver in risk_drivers[:3]:
            cleaned = _clean(driver)
            if cleaned:
                reasons.append(cleaned)

    if not reasons:
        return "Project appears on track based on current planning and engineering evidence."

    intro = "needs attention" if level in ("HIGH", "CRITICAL") else "is being monitored"
    return f"This project {intro} because " + ", ".join(reasons[:3]) + "."


def _normalize_ci_evidence_rows(
    ci_rows: List[Dict[str, Any]],
    pr_url_by_number: Dict[int, str],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in ci_rows or []:
        pr_number = _to_int(row.get("pr_number") or row.get("number"))
        status_raw = _clean(row.get("ci_status") or row.get("status")).lower()
        failed_tests = _to_int(row.get("failed_tests"))
        flaky_tests = _to_int(row.get("flaky_tests"))

        if status_raw in ("failed", "failure", "error", "timed_out", "cancelled") or failed_tests > 0:
            status = "failed"
        elif status_raw in ("success", "passed", "pass"):
            status = "passed"
        elif status_raw in ("in_progress", "pending", "queued"):
            status = "pending"
        elif status_raw:
            status = status_raw
        else:
            status = "unknown"

        summary_bits: List[str] = []
        if failed_tests > 0:
            summary_bits.append(f"{failed_tests} failing tests")
        if flaky_tests > 0:
            summary_bits.append(f"{flaky_tests} flaky tests")
        if status not in ("passed", "unknown", "pending"):
            summary_bits.append(f"status: {status}")

        run_id = _to_int(row.get("run_id") or row.get("workflow_run_id"))
        html_url = _clean(row.get("html_url") or row.get("log_url") or row.get("run_url"))
        if not html_url and run_id > 0:
            html_url = f"https://github.com/{_repo_slug()}/actions/runs/{run_id}"
        if not html_url and pr_number > 0:
            html_url = pr_url_by_number.get(pr_number, "")

        out.append(
            {
                "source": "github_actions",
                "status": status,
                "name": _clean(
                    row.get("name")
                    or row.get("workflow_name")
                    or row.get("workflow")
                    or row.get("job_name")
                )
                or f"PR #{pr_number} CI signal",
                "summary": ", ".join(summary_bits) if summary_bits else "No failure details provided by source.",
                "html_url": html_url or None,
                "updated_at": _clean(row.get("updated_at") or row.get("last_run") or row.get("updated")),
                "pr_number": pr_number if pr_number > 0 else None,
            }
        )
    return out

def _derive_project_status(subtasks: List[Dict[str, Any]]) -> str:
    if not subtasks:
        return "Not Started"
    statuses = [str(st.get("status") or "").lower() for st in subtasks]
    if any("block" in s or "delay" in s or "stuck" in s for s in statuses):
        return "Blocked"
    if statuses and all(any(x in s for x in ("done", "complete", "completed", "closed")) for s in statuses if s):
        return "Completed"
    if any("in progress" in s or "in-progress" in s for s in statuses):
        return "In Progress"
    if any(s for s in statuses):
        return "Active"
    return "Not Started"


def _normalize_person(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _split_people(raw: str) -> List[str]:
    text = _clean(raw)
    if not text:
        return []
    normalized = re.sub(r"\s+(and|&)\s+", "+", text, flags=re.IGNORECASE)
    normalized = normalized.replace("/", "+").replace("|", "+")
    parts = re.split(r"\+|,", normalized)
    cleaned = [p.strip() for p in parts if p.strip()]
    # Preserve order while de-duplicating by normalized form.
    out: List[str] = []
    seen = set()
    for p in cleaned:
        key = _normalize_person(p)
        if key and key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _resolve_project_contributor(project_contributor: Optional[str], subtasks: List[Dict[str, Any]]) -> Tuple[Optional[str], Dict[str, Any]]:
    raw = _clean(project_contributor)
    candidates = _split_people(raw)

    assignee_counts: Counter[str] = Counter()
    assignee_display: Dict[str, str] = {}
    for st in subtasks:
        assignee_raw = _clean(st.get("assignee"))
        for person in _split_people(assignee_raw):
            key = _normalize_person(person)
            if not key:
                continue
            assignee_counts[key] += 1
            assignee_display.setdefault(key, person)

    resolution = {
        "source_value": raw or None,
        "resolved_value": raw or None,
        "reason": "sheet_value" if raw else "missing",
    }

    if not raw and assignee_counts:
        best_key, _ = assignee_counts.most_common(1)[0]
        resolved = assignee_display.get(best_key)
        resolution["resolved_value"] = resolved
        resolution["reason"] = "derived_from_assignees"
        return resolved, resolution

    if len(candidates) <= 1:
        return raw or None, resolution

    # Multi-owner in sheet. Use the first listed contributor as the routing owner,
    # and keep the original raw value for transparency.
    resolved = candidates[0]
    resolution["resolved_value"] = resolved
    resolution["reason"] = "multi_owner_default_first_candidate"
    return resolved, resolution


def _extract_issue_refs_from_pr_closing_text(*texts: Any) -> List[int]:
    nums: set[int] = set()
    repo = _repo_slug().lower()
    for text in texts:
        if text is None:
            continue
        for m in _PR_CLOSING_ISSUE_REF_RE.finditer(str(text)):
            ref_repo = _clean(m.group("repo")).lower()
            if ref_repo and ref_repo != repo:
                continue
            num = _to_int(m.group("num"))
            if num > 0:
                nums.add(num)
    return sorted(nums)


def _build_issue_pr_subtask_links(
    project_issue_numbers: List[int],
    project_pr_numbers: List[int],
    subtasks: List[Dict[str, Any]],
    issue_map: Dict[int, Dict[str, Any]],
    pr_map: Dict[int, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    issue_set = set(project_issue_numbers or [])
    pr_set = set(project_pr_numbers or [])

    issue_to_pr: Dict[int, set[int]] = defaultdict(set)
    source_map: Dict[int, Dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))

    for st in subtasks:
        issue_nums = sorted({_to_int(x) for x in (st.get("github_issue_numbers") or []) if _to_int(x) > 0})
        pr_nums = sorted({_to_int(x) for x in (st.get("github_pr_numbers") or []) if _to_int(x) > 0})

        for issue_num in issue_nums:
            for pr_num in pr_nums:
                issue_to_pr[issue_num].add(pr_num)
                source_map[issue_num][pr_num].add("same_subtask")

    # Parse PR closing-keyword references (for example, "Fixes #123") and map them.
    for pr_num, pr in pr_map.items():
        refs = _extract_issue_refs_from_pr_closing_text(pr.get("title"), pr.get("body"))
        for issue_num in refs:
            if issue_num in issue_set:
                issue_to_pr[issue_num].add(pr_num)
                source_map[issue_num][pr_num].add("pr_text_reference")

    enriched_subtasks: List[Dict[str, Any]] = []
    for st in subtasks:
        issue_nums = sorted({_to_int(x) for x in (st.get("github_issue_numbers") or []) if _to_int(x) > 0})
        pr_nums = sorted({_to_int(x) for x in (st.get("github_pr_numbers") or []) if _to_int(x) > 0})

        derived_prs = set(pr_nums)
        for issue_num in issue_nums:
            derived_prs.update(issue_to_pr.get(issue_num, set()))

        item = dict(st)
        item["github_issue_numbers"] = issue_nums
        item["github_pr_numbers"] = pr_nums
        item["issue_evidence"] = [issue_map[n] for n in issue_nums if n in issue_map]
        item["pr_evidence"] = [pr_map[n] for n in pr_nums if n in pr_map]
        item["derived_related_pr_numbers"] = sorted([n for n in derived_prs if n in pr_set])
        enriched_subtasks.append(item)

    issue_pr_links: List[Dict[str, Any]] = []
    for issue_num in sorted(issue_to_pr.keys()):
        related_prs = sorted(issue_to_pr[issue_num])
        if not related_prs:
            continue
        link_sources = {}
        for pr_num in related_prs:
            link_sources[pr_num] = sorted(source_map[issue_num].get(pr_num, set()))
        primary_pr_num = None
        for pr_num in related_prs:
            if "same_subtask" in source_map[issue_num].get(pr_num, set()):
                primary_pr_num = pr_num
                break
        if primary_pr_num is None:
            primary_pr_num = related_prs[0]
        primary_link_basis = "same_subtask" if "same_subtask" in source_map[issue_num].get(primary_pr_num, set()) else "pr_text_reference"
        issue_pr_links.append(
            {
                "issue_number": issue_num,
                "related_pr_numbers": related_prs,
                "link_sources": link_sources,
                "primary_pr_number": primary_pr_num,
                "primary_link_basis": primary_link_basis,
                "issue_evidence": issue_map.get(issue_num),
                "pr_evidence": [pr_map[p] for p in related_prs if p in pr_map],
                "primary_pr_evidence": pr_map.get(primary_pr_num),
            }
        )

    return issue_pr_links, enriched_subtasks


def _build_project_reports(limit: int = 1000, force_refresh: bool = False) -> List[Dict[str, Any]]:
    build_started = monotonic()
    source_state = _build_source_state()
    source_fingerprint = _source_fingerprint(source_state)

    if not force_refresh and _memory_cache_is_valid(limit, source_fingerprint):
        return list(REPORT_CACHE.get("reports") or [])

    if not force_refresh:
        payload = _disk_cache_payload()
        if payload and _disk_cache_is_valid(payload, limit, source_fingerprint):
            return _hydrate_memory_cache_from_disk(payload)

    rows = _fetch_planning_rows(limit)
    _enforce_build_deadline(build_started, "planning query")
    projects = group_planning_rows(rows)
    _augment_projects_with_planning_links(projects)

    if not projects:
        generated_at = _utc_now()
        REPORT_CACHE["reports"] = []
        REPORT_CACHE["generated_at"] = generated_at
        REPORT_CACHE["limit"] = int(limit)
        REPORT_CACHE["source_fingerprint"] = source_fingerprint
        REPORT_CACHE["source_state"] = source_state
        _persist_disk_cache(
            reports=[],
            generated_at=generated_at,
            limit=int(limit),
            source_fingerprint=source_fingerprint,
            source_state=source_state,
        )
        return []

    all_issue_nums: List[int] = []
    all_pr_nums: List[int] = []
    for p in projects:
        all_issue_nums.extend(p.all_github_issue_numbers or [])
        all_pr_nums.extend(p.all_github_pr_numbers or [])

    issues_map: Dict[int, Dict[str, Any]] = {}
    prs_map: Dict[int, Dict[str, Any]] = {}
    ci_signals_map: Dict[int, Dict[str, Any]] = {}
    contributor_email_map: Dict[str, str] = {}
    coral_queries: List[str] = []

    try:
        issues_map, prs_map, github_queries = _fetch_live_github_maps(all_issue_nums, all_pr_nums)
        coral_queries.extend(github_queries)
    except Exception:
        issues_map, prs_map = {}, {}
    _enforce_build_deadline(build_started, "github evidence queries")

    try:
        ci_signals_map, ci_queries = _fetch_ci_signals_map(all_pr_nums)
        coral_queries.extend(ci_queries)
    except Exception:
        ci_signals_map = {}
    _enforce_build_deadline(build_started, "ci signal queries")

    try:
        contributor_email_map, contributor_queries = _fetch_contributor_email_map()
        coral_queries.extend(contributor_queries)
    except Exception:
        contributor_email_map = {}
    _enforce_build_deadline(build_started, "contributor directory queries")

    pre_reports: List[Tuple[Any, Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]] = []
    for p in projects:
        subtasks = [st.dict() for st in p.subtasks]
        resolved_contributor, owner_resolution = _resolve_project_contributor(p.project_owner_contributor, subtasks)

        project_issue_map: Dict[int, Dict[str, Any]] = {}
        for n in (p.all_github_issue_numbers or []):
            num = _to_int(n)
            if num <= 0:
                continue
            issue_row = issues_map.get(num)
            if issue_row:
                project_issue_map[num] = issue_row
        for n in (p.all_github_issue_numbers or []):
            num = _to_int(n)
            if num <= 0 or num in project_issue_map:
                continue
            project_issue_map[num] = {
                "number": num,
                "title": f"Issue #{num}",
                "state": "unknown",
                "html_url": _issue_url(num),
            }

        project_pr_map: Dict[int, Dict[str, Any]] = {}
        for n in (p.all_github_pr_numbers or []):
            num = _to_int(n)
            if num <= 0:
                continue
            pr_row = prs_map.get(num)
            if pr_row:
                project_pr_map[num] = pr_row
        for n in (p.all_github_pr_numbers or []):
            num = _to_int(n)
            if num <= 0 or num in project_pr_map:
                continue
            project_pr_map[num] = {
                "number": num,
                "title": f"PR #{num}",
                "state": "unknown",
                "html_url": _pr_url(num),
            }
        issue_pr_links, enriched_subtasks = _build_issue_pr_subtask_links(
            p.all_github_issue_numbers or [],
            p.all_github_pr_numbers or [],
            subtasks,
            project_issue_map,
            project_pr_map,
        )

        pre_reports.append(
            (
                p,
                {
                    "resolved_contributor": resolved_contributor,
                    "owner_resolution": owner_resolution,
                    "subtasks": enriched_subtasks,
                    "issue_pr_links": issue_pr_links,
                },
                [project_issue_map[n] for n in sorted(project_issue_map.keys())],
                [project_pr_map[n] for n in sorted(project_pr_map.keys())],
            )
        )
    _enforce_build_deadline(build_started, "subtask/evidence enrichment")

    contributor_high_risk_counts: Dict[str, int] = {}
    for p, meta, live_issues, live_prs in pre_reports:
        ci_context = {
            _to_int(pr.get("number") or pr.get("id")): ci_signals_map.get(_to_int(pr.get("number") or pr.get("id")))
            for pr in live_prs
            if _to_int(pr.get("number") or pr.get("id")) > 0
        }
        ci_context = {k: v for k, v in ci_context.items() if v}

        tmp_project = p.copy(deep=True)
        tmp_project.project_owner_contributor = meta.get("resolved_contributor")
        rpt = score_project(
            tmp_project,
            {
                "issues": live_issues,
                "prs": live_prs,
                "ci_signals_by_pr": ci_context,
                "issue_pr_links": meta.get("issue_pr_links") or [],
            },
        )
        contributor = (tmp_project.project_owner_contributor or "").strip()
        if contributor and rpt.risk_level in ("HIGH", "CRITICAL"):
            contributor_high_risk_counts[contributor] = contributor_high_risk_counts.get(contributor, 0) + 1
    _enforce_build_deadline(build_started, "risk pre-pass")

    reports: List[Dict[str, Any]] = []
    for p, meta, live_issues, live_prs in pre_reports:
        project = p.copy(deep=True)
        project.project_owner_contributor = meta.get("resolved_contributor")
        contributor_email = contributor_email_map.get(_normalize_person(project.project_owner_contributor or ""))

        ci_context = {
            _to_int(pr.get("number") or pr.get("id")): ci_signals_map.get(_to_int(pr.get("number") or pr.get("id")))
            for pr in live_prs
            if _to_int(pr.get("number") or pr.get("id")) > 0
        }
        ci_context = {k: v for k, v in ci_context.items() if v}

        contributor = (project.project_owner_contributor or "").strip()
        rpt = score_project(
            project,
            {
                "issues": live_issues,
                "prs": live_prs,
                "ci_signals_by_pr": ci_context,
                "issue_pr_links": meta.get("issue_pr_links") or [],
                "contributor_high_risk_projects": contributor_high_risk_counts.get(contributor, 0),
            },
        )

        normalized_issue_map: Dict[int, Dict[str, Any]] = {}
        for issue_row in rpt.github_issue_evidence or []:
            normalized = _normalize_issue_evidence_row(issue_row)
            if not normalized:
                continue
            normalized_issue_map[int(normalized["number"])] = normalized
        for num in project.all_github_issue_numbers or []:
            issue_num = _to_int(num)
            if issue_num <= 0 or issue_num in normalized_issue_map:
                continue
            normalized_issue_map[issue_num] = {
                "number": issue_num,
                "title": "",
                "state": "unknown",
                "labels": None,
                "updated_at": "",
                "html_url": _issue_url(issue_num),
            }
        normalized_issue_evidence = [
            normalized_issue_map[k] for k in sorted(normalized_issue_map.keys())
        ]

        normalized_pr_map: Dict[int, Dict[str, Any]] = {}
        for pr_row in rpt.github_pr_evidence or []:
            normalized = _normalize_pr_evidence_row(pr_row)
            if not normalized:
                continue
            normalized_pr_map[int(normalized["number"])] = normalized
        for num in project.all_github_pr_numbers or []:
            pr_num = _to_int(num)
            if pr_num <= 0 or pr_num in normalized_pr_map:
                continue
            normalized_pr_map[pr_num] = {
                "number": pr_num,
                "title": "",
                "state": "unknown",
                "draft": None,
                "updated_at": "",
                "html_url": _pr_url(pr_num),
            }
        normalized_pr_evidence = [normalized_pr_map[k] for k in sorted(normalized_pr_map.keys())]

        pr_url_by_number = {
            int(item.get("number")): item.get("html_url")
            for item in normalized_pr_evidence
            if _to_int(item.get("number")) > 0 and _clean(item.get("html_url"))
        }
        normalized_ci_evidence = _normalize_ci_evidence_rows(rpt.ci_evidence or [], pr_url_by_number)
        ci_pr_numbers_present = {
            _to_int(row.get("pr_number"))
            for row in normalized_ci_evidence
            if _to_int(row.get("pr_number")) > 0
        }
        for pr_item in normalized_pr_evidence:
            pr_num = _to_int(pr_item.get("number"))
            if pr_num <= 0 or pr_num in ci_pr_numbers_present:
                continue
            if _clean(pr_item.get("state")).lower() != "open":
                continue
            normalized_ci_evidence.append(
                {
                    "source": "github_actions",
                    "status": "unknown",
                    "name": f"PR #{pr_num} CI signal",
                    "summary": "CI/test status unavailable from connected Coral sources for this PR.",
                    "html_url": _clean(pr_item.get("html_url")) or _pr_url(pr_num),
                    "updated_at": _clean(pr_item.get("updated_at")),
                    "pr_number": pr_num,
                }
            )
            ci_pr_numbers_present.add(pr_num)

        normalized_ci_evidence = sorted(
            normalized_ci_evidence,
            key=lambda row: (_to_int(row.get("pr_number")), _clean(row.get("name"))),
        )
        failing_ci_evidence_count = len(
            [row for row in normalized_ci_evidence if _clean(row.get("status")).lower() == "failed"]
        )

        subtasks = list(meta.get("subtasks") or [])
        total_subtasks = len(subtasks)
        completed_subtasks = len(
            [
                st
                for st in subtasks
                if any(x in str(st.get("status") or "").lower() for x in ("done", "complete", "completed", "closed"))
            ]
        )
        in_progress_subtasks = len(
            [st for st in subtasks if "in progress" in str(st.get("status") or "").lower() or "in-progress" in str(st.get("status") or "").lower()]
        )
        blocked_subtasks = len(
            [st for st in subtasks if any(k in f"{st.get('status') or ''} {st.get('notes') or ''}".lower() for k in ("blocked", "stuck", "waiting", "delayed"))]
        )

        project_status = _derive_project_status(subtasks)
        linked_issue_count = len(project.all_github_issue_numbers or [])
        linked_pr_count = len(project.all_github_pr_numbers or [])
        risk_summary = _build_risk_summary(
            rpt.risk_level,
            rpt.risk_drivers or [],
            blocked_subtasks,
            rpt.open_linked_issue_count,
            rpt.open_linked_pr_count,
            rpt.stale_open_pr_count,
            failing_ci_evidence_count,
        )

        coral_query_flow_used = {
            "steps": [
                "Coral retrieves planning sheet rows.",
                "Backend groups rows into projects.",
                "Backend extracts GitHub issue/PR numbers.",
                "Coral joins planning.project_links with github.issues/github.pulls when available.",
                "Backend links issue/PR evidence to subtasks.",
                "Backend applies CI/test-failure signals and scores project risk.",
                "Reminder generator creates Google Chat text only for high-risk projects.",
            ],
            "queries": ["SELECT * FROM planning.projects;"] + coral_queries,
        }

        report_dict: Dict[str, Any] = {
            "project_id": project.project_id,
            "project_name": project.project_name,
            "project_description": project.project_description,
            "project_owner_lead": project.project_owner_lead,
            "project_owner_contributor": project.project_owner_contributor,
            "contributor_email": contributor_email,
            "can_email": bool(contributor_email),
            "project_owner_contributor_raw": p.project_owner_contributor,
            "owner_resolution": meta.get("owner_resolution") or {},
            "planned_completion_date": project.planned_completion_date,
            "project_status": project_status,
            "total_subtasks": total_subtasks,
            "completed_subtasks": completed_subtasks,
            "in_progress_subtasks": in_progress_subtasks,
            "blocked_subtasks": blocked_subtasks,
            "high_risk_subtasks": rpt.high_risk_subtasks,
            "linked_issue_count": linked_issue_count,
            "open_linked_issue_count": rpt.open_linked_issue_count,
            "linked_pr_count": linked_pr_count,
            "open_linked_pr_count": rpt.open_linked_pr_count,
            "stale_open_issue_count": rpt.stale_open_issue_count,
            "stale_open_pr_count": rpt.stale_open_pr_count,
            "failing_ci_pr_count": rpt.failing_ci_pr_count,
            "flaky_ci_pr_count": rpt.flaky_ci_pr_count,
            "failed_tests_total": rpt.failed_tests_total,
            "flaky_tests_total": rpt.flaky_tests_total,
            "stale_ci_signal_count": rpt.stale_ci_signal_count,
            "all_github_issue_numbers": project.all_github_issue_numbers,
            "all_github_pr_numbers": project.all_github_pr_numbers,
            "risk_score": rpt.risk_score,
            "risk_level": rpt.risk_level,
            "risk_summary": risk_summary,
            "risk_drivers": rpt.risk_drivers,
            "recommendations": rpt.recommendations,
            "subtasks": subtasks,
            "issue_pr_links": meta.get("issue_pr_links") or [],
            "github_issue_evidence": normalized_issue_evidence,
            "github_pr_evidence": normalized_pr_evidence,
            "ci_evidence": normalized_ci_evidence,
            "evidence_by_source": rpt.evidence_by_source,
            "coral_query_flow_used": coral_query_flow_used,
        }
        reports.append(report_dict)
    _enforce_build_deadline(build_started, "final report assembly")

    deduped_reports: List[Dict[str, Any]] = []
    deduped_index: Dict[str, int] = {}
    for report in reports:
        key = _clean(report.get("project_id")) or _clean(report.get("project_name"))
        if not key:
            deduped_reports.append(report)
            continue
        if key not in deduped_index:
            deduped_index[key] = len(deduped_reports)
            deduped_reports.append(report)
            continue

        idx = deduped_index[key]
        existing = deduped_reports[idx]
        existing_subtasks = len(existing.get("subtasks") or [])
        current_subtasks = len(report.get("subtasks") or [])
        existing_score = _to_int(existing.get("risk_score"))
        current_score = _to_int(report.get("risk_score"))

        if current_subtasks > existing_subtasks or (
            current_subtasks == existing_subtasks and current_score > existing_score
        ):
            deduped_reports[idx] = report

    reports = deduped_reports
    reports = sorted(reports, key=lambda r: r.get("risk_score", 0), reverse=True)
    generated_at = _utc_now()
    REPORT_CACHE["reports"] = reports
    REPORT_CACHE["generated_at"] = generated_at
    REPORT_CACHE["limit"] = int(limit)
    REPORT_CACHE["source_fingerprint"] = source_fingerprint
    REPORT_CACHE["source_state"] = source_state
    _persist_disk_cache(
        reports=reports,
        generated_at=generated_at,
        limit=int(limit),
        source_fingerprint=source_fingerprint,
        source_state=source_state,
    )
    return reports


def _run_snapshot_sync() -> Tuple[str, str]:
    if not SNAPSHOT_SCRIPT.exists():
        raise RuntimeError(f"Snapshot script not found: {SNAPSHOT_SCRIPT}")

    env = os.environ.copy()
    env.update(_load_env_file(ROOT_DIR / ".env"))

    proc = subprocess.run(
        [str(SNAPSHOT_SCRIPT)],
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    combined = "\n".join([x for x in [stdout, stderr] if x]).strip()

    if proc.returncode != 0:
        raise RuntimeError(combined or f"Snapshot sync failed with exit code {proc.returncode}")

    if "PLANNING_CSV_URL not set" in combined:
        return "NOT_CONFIGURED", combined

    return "SYNCED", combined or "Snapshot sync completed"


@app.get("/api/health")
def health():
    workspace_name = _workspace_org_name()
    target_repo = _repo_slug()
    planning_source = _planning_source_label()
    planning_display = _planning_source_display_name()
    engineering_source = _engineering_source_label()
    team_source = _team_source_label()
    planning_schema, planning_table = _planning_projects_ref()
    team_schema, team_table = _team_members_ref()
    planning_table_ref = _qualified_table(planning_schema, planning_table)
    team_table_ref = _qualified_table(team_schema, team_table)

    if not coral.available():
        return {
            "product": "OmniSprint",
            "mode": "NOT_READY",
            "backend": "ok",
            "coral": "missing",
            "workspace": workspace_name,
            "target_repo": target_repo,
            "planning_source": planning_source,
            "engineering_source": engineering_source,
            "connected_sources_count": 0,
            "sources": [],
        }

    planning_present = _table_exists(planning_schema, planning_table)
    issues_present = _table_exists("github", "issues")
    pulls_present = _table_exists("github", "pulls")
    actions_present = _table_exists("ci", "signals")
    team_present = _table_exists(team_schema, team_table)

    if planning_present and issues_present and pulls_present:
        mode = "LIVE"
    elif planning_present and (issues_present or pulls_present):
        mode = "HYBRID"
    elif planning_present:
        mode = "HYBRID"
    else:
        mode = "NOT_READY"

    sources = [
        {"name": planning_display, "table": planning_table_ref, "status": "connected" if planning_present else "missing"},
        {"name": "GitHub Issues", "table": "github.issues", "status": "connected" if issues_present else "missing"},
        {"name": "GitHub PRs", "table": "github.pulls", "status": "connected" if pulls_present else "missing"},
        {"name": team_source, "table": team_table_ref, "status": "connected" if team_present else "missing"},
    ]
    if actions_present:
        sources.append({"name": "GitHub Actions", "table": "ci.signals", "status": "connected"})

    connected_sources_count = len([s for s in sources if s["status"] == "connected"])
    return {
        "product": "OmniSprint",
        "mode": mode,
        "workspace": workspace_name,
        "planning_source": planning_source,
        "engineering_source": engineering_source,
        "backend": "ok",
        "coral": "ok",
        "target_repo": target_repo,
        "connected_sources_count": connected_sources_count,
        "sources": sources,
    }


@app.get("/api/sync-status")
def get_sync_status():
    return SYNC_STATUS


@app.get("/api/cache-status")
def get_cache_status(limit: int = 1200):
    source_state = _build_source_state()
    source_fingerprint = _source_fingerprint(source_state)
    memory_generated_at = REPORT_CACHE.get("generated_at")
    memory_limit = int(REPORT_CACHE.get("limit") or 0)
    memory_source = _clean(REPORT_CACHE.get("source_fingerprint"))
    memory_valid = _memory_cache_is_valid(limit, source_fingerprint)

    payload = _disk_cache_payload()
    disk_generated_at = payload.get("generated_at") if payload else None
    disk_limit = int(payload.get("limit") or 0) if payload else 0
    disk_source = _clean(payload.get("source_fingerprint")) if payload else ""
    disk_valid = bool(payload and _disk_cache_is_valid(payload, limit, source_fingerprint))

    return {
        "ttl_seconds": REPORT_CACHE_TTL_SECONDS,
        "requested_limit": int(limit),
        "source_fingerprint": source_fingerprint,
        "source_state": source_state,
        "memory_cache": {
            "present": isinstance(REPORT_CACHE.get("reports"), list),
            "valid": memory_valid,
            "generated_at": _dt_to_cache_str(memory_generated_at) if memory_generated_at else None,
            "limit": memory_limit,
            "source_fingerprint": memory_source or None,
            "source_matches": bool(memory_source and memory_source == source_fingerprint),
        },
        "disk_cache": {
            "path": str(REPORT_CACHE_FILE),
            "present": REPORT_CACHE_FILE.exists(),
            "valid": disk_valid,
            "generated_at": disk_generated_at,
            "limit": disk_limit,
            "source_fingerprint": disk_source or None,
            "source_matches": bool(disk_source and disk_source == source_fingerprint),
        },
    }


def _sync_planning_source():
    try:
        status, message = _run_snapshot_sync()
        SYNC_STATUS["status"] = status
        SYNC_STATUS["message"] = message
        SYNC_STATUS["last_error"] = None
        SYNC_STATUS["last_synced_at"] = datetime.utcnow().isoformat() + "Z"
        _clear_report_cache()
        return SYNC_STATUS
    except Exception as e:
        SYNC_STATUS["status"] = "FAILED"
        SYNC_STATUS["message"] = "Snapshot sync failed"
        SYNC_STATUS["last_error"] = str(e)
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/sync-planning")
def post_sync_planning():
    return _sync_planning_source()


@app.post("/api/sync-roadmap")
def post_sync_roadmap_alias():
    # Backward-compatible alias.
    return _sync_planning_source()


@app.get("/api/projects")
def get_projects(force_refresh: bool = False):
    try:
        return _build_project_reports(1200, force_refresh=force_refresh)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/projects/{project_id}")
def get_project(project_id: str, force_refresh: bool = False):
    try:
        reports = _build_project_reports(1200, force_refresh=force_refresh)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

    match = next(
        (
            r
            for r in reports
            if r.get("project_id") == project_id or r.get("project_name") == project_id
        ),
        None,
    )
    if not match:
        raise HTTPException(status_code=404, detail="Project not found")
    return match


@app.get("/api/owners")
def get_owners(force_refresh: bool = False):
    try:
        reports = _build_project_reports(1200, force_refresh=force_refresh)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

    lead_map: Dict[str, Dict[str, Any]] = {}
    contributor_map: Dict[str, Dict[str, Any]] = {}

    reminders = generate_reminders(reports, risk_threshold="HIGH")
    reminder_by_project = {r["project_id"]: r for r in reminders}

    for r in reports:
        lead = (r.get("project_owner_lead") or "").strip() or "(unassigned lead)"
        contributor = (r.get("project_owner_contributor") or "").strip() or "(unassigned contributor)"
        high_risk = r.get("risk_level") in ("HIGH", "CRITICAL")

        if lead not in lead_map:
            lead_map[lead] = {
                "owner_lead": lead,
                "total_projects_owned": 0,
                "high_risk_projects": 0,
                "contributors_needing_follow_up": set(),
                "highest_risk_project": None,
                "_max_risk": -1,
                "generated_reminder_count": 0,
            }
        lm = lead_map[lead]
        lm["total_projects_owned"] += 1
        if high_risk:
            lm["high_risk_projects"] += 1
        if r.get("project_id") in reminder_by_project:
            lm["generated_reminder_count"] += 1
            if contributor:
                lm["contributors_needing_follow_up"].add(contributor)
        if r.get("risk_score", 0) > lm["_max_risk"]:
            lm["_max_risk"] = r.get("risk_score", 0)
            lm["highest_risk_project"] = r.get("project_name")

        if contributor not in contributor_map:
            contributor_map[contributor] = {
                "owner_contributor": contributor,
                "contributor_email": r.get("contributor_email"),
                "total_assigned_projects": 0,
                "high_risk_assigned_projects": 0,
                "blocked_subtasks": 0,
                "open_linked_prs": 0,
                "open_linked_issues": 0,
                "failing_ci_prs": 0,
                "failed_tests_total": 0,
            }
        cm = contributor_map[contributor]
        if not cm.get("contributor_email") and r.get("contributor_email"):
            cm["contributor_email"] = r.get("contributor_email")
        cm["total_assigned_projects"] += 1
        if high_risk:
            cm["high_risk_assigned_projects"] += 1
        cm["blocked_subtasks"] += int(r.get("blocked_subtasks", 0))
        cm["open_linked_prs"] += int(r.get("open_linked_pr_count", 0))
        cm["open_linked_issues"] += int(r.get("open_linked_issue_count", 0))
        cm["failing_ci_prs"] += int(r.get("failing_ci_pr_count", 0))
        cm["failed_tests_total"] += int(r.get("failed_tests_total", 0))

    leads = []
    for _, lm in lead_map.items():
        leads.append(
            {
                "owner_lead": lm["owner_lead"],
                "total_projects_owned": lm["total_projects_owned"],
                "high_risk_projects": lm["high_risk_projects"],
                "contributors_needing_follow_up": sorted(list(lm["contributors_needing_follow_up"])),
                "highest_risk_project": lm["highest_risk_project"],
                "generated_reminder_count": lm["generated_reminder_count"],
            }
        )

    contributors = sorted(
        list(contributor_map.values()),
        key=lambda x: x.get("high_risk_assigned_projects", 0),
        reverse=True,
    )
    leads = sorted(leads, key=lambda x: x.get("high_risk_projects", 0), reverse=True)

    return {"leads": leads, "contributors": contributors}


@app.get("/api/reminders/high-risk")
def get_high_risk_reminders(force_refresh: bool = False):
    try:
        reports = _build_project_reports(1200, force_refresh=force_refresh)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

    reminders = generate_reminders(reports, risk_threshold="HIGH")
    return {"count": len(reminders), "reminders": reminders}


@app.post("/api/reminders/generate")
def post_generate_reminders(payload: dict):
    risk_threshold = (payload or {}).get("risk_threshold", "HIGH")
    project_id = (payload or {}).get("project_id")
    owner_lead = (payload or {}).get("owner_lead")

    try:
        reports = _build_project_reports(1200, force_refresh=_to_bool((payload or {}).get("force_refresh")))
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

    reminders = generate_reminders(
        reports,
        risk_threshold=risk_threshold,
        project_id=project_id,
        owner_lead=owner_lead,
    )
    return {"count": len(reminders), "reminders": reminders}


@app.get("/api/activity/latest")
def get_latest_activity(limit: int = 10):
    from .agent_tools import get_latest_activity_summary

    return get_latest_activity_summary(limit=limit)


@app.post("/api/agent/ask")
def agent_ask(payload: AgentAskRequest):
    q = _clean(payload.question)
    if not q:
        raise HTTPException(status_code=400, detail="question required")
    from .gemini_agent import ask_agent

    return ask_agent(q)


@app.post("/api/agent-query")
def agent_query(payload: dict):
    q = _clean((payload or {}).get("question"))
    if not q:
        raise HTTPException(status_code=400, detail="question required")
    from .gemini_agent import ask_agent

    return ask_agent(q)
