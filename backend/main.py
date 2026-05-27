import os
import re
import json
import hashlib
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .coral_client import CoralClient
from .normalizer import group_roadmap_rows
from .reminder_generator import generate_reminders
from .risk_engine import score_project

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

coral = CoralClient()

ROOT_DIR = Path(__file__).resolve().parents[1]
SNAPSHOT_SCRIPT = ROOT_DIR / "scripts" / "snapshot_google_sheets.sh"

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
    ROOT_DIR / "coral" / "data" / "oppia_roadmap_snapshot.jsonl",
    ROOT_DIR / "coral" / "data" / "oppia_roadmap_project_links.jsonl",
    ROOT_DIR / "coral" / "data" / "oppia_team_snapshot.jsonl",
    ROOT_DIR / "coral" / "data" / "ci_signals.jsonl",
]

_ISSUE_REF_RE = re.compile(r"(?:issues/|#)(\d+)", re.IGNORECASE)


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
        val = val.strip()
        if key:
            out[key] = val
    return out


def _load_env_defaults_from_file() -> None:
    file_env = _load_env_file(ROOT_DIR / ".env")
    for key, val in file_env.items():
        if key and key not in os.environ:
            os.environ[key] = val


_load_env_defaults_from_file()


def _table_exists(schema: str, table: Optional[str] = None) -> bool:
    try:
        if table:
            q = f"SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema = '{schema}' AND table_name = '{table}' LIMIT 1;"
        else:
            q = f"SELECT table_schema FROM information_schema.tables WHERE table_schema = '{schema}' LIMIT 1;"
        rows = coral.run_sql(q)
        if not rows:
            return False
        joined = " ".join(
            [
                " ".join(str(v) for v in r.values()) if isinstance(r, dict) else str(r)
                for r in rows
            ]
        ).lower()
        if schema.lower() not in joined:
            return False
        if table and table.lower() not in joined:
            return False
        return True
    except Exception:
        return False


def _fetch_roadmap_rows(limit: int = 1000) -> List[Dict[str, Any]]:
    if not coral.available():
        raise RuntimeError("Coral CLI not available")
    q = f"SELECT * FROM oppia_roadmap.projects LIMIT {int(limit)};"
    rows = coral.run_sql(q)
    if not isinstance(rows, list):
        raise RuntimeError("Coral roadmap query did not return row list")
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

    # Preferred path: perform cross-source joins in Coral SQL via project_links.
    used_join_path = False
    if _table_exists("oppia_roadmap", "project_links"):
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
                "FROM oppia_roadmap.project_links pl "
                f"LEFT JOIN github.issues i ON i.number = pl.link_number{owner_repo_issue_join} "
                "WHERE pl.link_type = 'issue' "
                f"{issue_link_filter};",
                "SELECT pl.link_number AS link_number, i.number, i.title, i.state, i.labels, i.updated_at, i.html_url "
                "FROM oppia_roadmap.project_links pl "
                f"LEFT JOIN github.issues i ON i.number = pl.link_number{owner_repo_issue_join} "
                "WHERE pl.link_type = 'issue' "
                f"{issue_link_filter};",
            ]
            try:
                issue_rows, used_q = _run_sql_variants(issue_join_variants, timeout=8)
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
                "FROM oppia_roadmap.project_links pl "
                f"LEFT JOIN github.pulls p ON p.number = pl.link_number{owner_repo_pr_join} "
                "WHERE pl.link_type = 'pr' "
                f"{pr_link_filter};",
                "SELECT pl.link_number AS link_number, p.number, p.title, p.state, p.updated_at, p.draft, p.html_url "
                "FROM oppia_roadmap.project_links pl "
                f"LEFT JOIN github.pulls p ON p.number = pl.link_number{owner_repo_pr_join} "
                "WHERE pl.link_type = 'pr' "
                f"{pr_link_filter};",
            ]
            try:
                pr_rows, used_q = _run_sql_variants(pr_join_variants, timeout=6)
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
                issue_rows, used_q = _run_sql_variants(issue_variants, timeout=8)
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
                pr_rows, used_q = _run_sql_variants(pr_variants, timeout=5)
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

    if _table_exists("oppia_roadmap", "project_links"):
        in_clause = ",".join(str(int(x)) for x in sorted(set(all_pr_nums)))
        join_query = (
            "SELECT DISTINCT pl.link_number AS pr_number, c.ci_status, c.failed_tests, c.flaky_tests, c.last_run "
            "FROM oppia_roadmap.project_links pl "
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


def _is_valid_email(val: str) -> bool:
    text = _clean(val)
    return "@" in text and "." in text and " " not in text


def _fetch_contributor_email_map() -> Tuple[Dict[str, str], List[str]]:
    contributor_email_map: Dict[str, str] = {}
    queries: List[str] = []

    if not coral.available():
        return contributor_email_map, queries

    candidate_tables: List[str] = []
    if _table_exists("oppia_team", "members"):
        candidate_tables.append("oppia_team.members")
    if _table_exists("team_context", "members"):
        candidate_tables.append("team_context.members")

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
                local = email.split("@")[0]
                fallback_name = local.replace(".", " ").replace("_", " ")
                names = [fallback_name]

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
    owner = (os.getenv("GITHUB_OWNER") or os.getenv("GITHUB_REPO_OWNER") or "oppia").strip()
    repo = (os.getenv("GITHUB_REPO") or "oppia").strip()
    return f"{owner}/{repo}"


def _fallback_issue_row(number: int) -> Dict[str, Any]:
    slug = _repo_slug()
    return {
        "number": int(number),
        "state": "unknown",
        "title": f"Issue #{int(number)}",
        "html_url": f"https://github.com/{slug}/issues/{int(number)}",
        "source": "link_only",
    }


def _fallback_pr_row(number: int) -> Dict[str, Any]:
    slug = _repo_slug()
    return {
        "number": int(number),
        "state": "unknown",
        "title": f"PR #{int(number)}",
        "html_url": f"https://github.com/{slug}/pull/{int(number)}",
        "source": "link_only",
    }


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


def _extract_issue_refs(*texts: Any) -> List[int]:
    nums: List[int] = []
    for text in texts:
        if text is None:
            continue
        nums.extend([_to_int(x) for x in _ISSUE_REF_RE.findall(str(text))])
    return sorted({n for n in nums if n > 0})


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

    # Parse PR text references to issue numbers and map them.
    for pr_num, pr in pr_map.items():
        refs = _extract_issue_refs(pr.get("title"), pr.get("body"), pr.get("html_url"))
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
        issue_pr_links.append(
            {
                "issue_number": issue_num,
                "related_pr_numbers": related_prs,
                "link_sources": link_sources,
                "issue_evidence": issue_map.get(issue_num),
                "pr_evidence": [pr_map[p] for p in related_prs if p in pr_map],
            }
        )

    return issue_pr_links, enriched_subtasks


def _build_project_reports(limit: int = 1000, force_refresh: bool = False) -> List[Dict[str, Any]]:
    source_state = _build_source_state()
    source_fingerprint = _source_fingerprint(source_state)

    if not force_refresh and _memory_cache_is_valid(limit, source_fingerprint):
        return list(REPORT_CACHE.get("reports") or [])

    if not force_refresh:
        payload = _disk_cache_payload()
        if payload and _disk_cache_is_valid(payload, limit, source_fingerprint):
            return _hydrate_memory_cache_from_disk(payload)

    rows = _fetch_roadmap_rows(limit)
    projects = group_roadmap_rows(rows)

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

    try:
        ci_signals_map, ci_queries = _fetch_ci_signals_map(all_pr_nums)
        coral_queries.extend(ci_queries)
    except Exception:
        ci_signals_map = {}

    try:
        contributor_email_map, contributor_queries = _fetch_contributor_email_map()
        coral_queries.extend(contributor_queries)
    except Exception:
        contributor_email_map = {}

    pre_reports: List[Tuple[Any, Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]] = []
    for p in projects:
        subtasks = [st.dict() for st in p.subtasks]
        resolved_contributor, owner_resolution = _resolve_project_contributor(p.project_owner_contributor, subtasks)

        project_issue_map: Dict[int, Dict[str, Any]] = {}
        for n in (p.all_github_issue_numbers or []):
            num = _to_int(n)
            if num <= 0:
                continue
            project_issue_map[num] = issues_map.get(num) or _fallback_issue_row(num)

        project_pr_map: Dict[int, Dict[str, Any]] = {}
        for n in (p.all_github_pr_numbers or []):
            num = _to_int(n)
            if num <= 0:
                continue
            project_pr_map[num] = prs_map.get(num) or _fallback_pr_row(num)
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

        coral_query_flow_used = {
            "steps": [
                "Coral retrieves planning sheet rows.",
                "Backend groups rows into projects.",
                "Backend extracts GitHub issue/PR numbers.",
                "Coral joins oppia_roadmap.project_links with github.issues/github.pulls when available.",
                "Backend links issue/PR evidence to subtasks.",
                "Backend applies CI/test-failure signals and scores project risk.",
                "Reminder generator creates Google Chat text only for high-risk projects.",
            ],
            "queries": ["SELECT * FROM oppia_roadmap.projects;"] + coral_queries,
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
            "risk_drivers": rpt.risk_drivers,
            "recommendations": rpt.recommendations,
            "subtasks": subtasks,
            "issue_pr_links": meta.get("issue_pr_links") or [],
            "github_issue_evidence": rpt.github_issue_evidence,
            "github_pr_evidence": rpt.github_pr_evidence,
            "ci_evidence": rpt.ci_evidence,
            "evidence_by_source": rpt.evidence_by_source,
            "coral_query_flow_used": coral_query_flow_used,
        }
        reports.append(report_dict)

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

    if "OPPIA_ROADMAP_CSV_URL not set" in combined:
        return "NOT_CONFIGURED", combined

    return "SYNCED", combined or "Snapshot sync completed"


@app.get("/api/health")
def health():
    if not coral.available():
        return {
            "product": "Sprint Tracker",
            "mode": "NOT_READY",
            "backend": "ok",
            "coral": "missing",
            "demo_workspace": "Oppia",
            "target_repo": "oppia/oppia",
            "planning_source": "Public quarterly targets sheet",
            "engineering_source": "oppia/oppia GitHub",
            "demo_planning_source": "Oppia Quarterly Targets Sheet",
            "demo_engineering_source": "oppia/oppia GitHub",
            "connected_sources_count": 0,
            "sources": [],
        }

    roadmap_present = _table_exists("oppia_roadmap", "projects")
    issues_present = _table_exists("github", "issues")
    pulls_present = _table_exists("github", "pulls")
    actions_present = _table_exists("ci", "signals")
    team_present = _table_exists("oppia_team", "members") or _table_exists("team_context", "members")

    if roadmap_present and issues_present and pulls_present:
        mode = "LIVE"
    elif roadmap_present and (issues_present or pulls_present):
        mode = "HYBRID"
    elif roadmap_present:
        mode = "HYBRID"
    else:
        mode = "NOT_READY"

    sources = [
        {"name": "Oppia Quarterly Targets Sheet", "table": "oppia_roadmap.projects", "status": "connected" if roadmap_present else "missing"},
        {"name": "Live GitHub Issues", "table": "github.issues", "status": "connected" if issues_present else "missing"},
        {"name": "Live GitHub PRs", "table": "github.pulls", "status": "connected" if pulls_present else "missing"},
        {"name": "Contributor Directory", "table": "oppia_team.members", "status": "connected" if team_present else "missing"},
    ]
    if actions_present:
        sources.append({"name": "GitHub Actions", "table": "ci.signals", "status": "connected"})

    connected_sources_count = len([s for s in sources if s["status"] == "connected"])
    return {
        "product": "Sprint Tracker",
        "mode": mode,
        "demo_workspace": "Oppia",
        "planning_source": "Public quarterly targets sheet",
        "engineering_source": "oppia/oppia GitHub",
        "demo_planning_source": "Oppia Quarterly Targets Sheet",
        "demo_engineering_source": "oppia/oppia GitHub",
        "backend": "ok",
        "coral": "ok",
        "target_repo": "oppia/oppia",
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


@app.post("/api/sync-roadmap")
def post_sync_roadmap():
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


@app.post("/api/agent-query")
def agent_query(payload: dict):
    q = (payload or {}).get("question")
    if not q:
        raise HTTPException(status_code=400, detail="question required")
    from .agent import handle_agent_query

    return handle_agent_query(q, coral)
