import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from .reminder_generator import generate_reminders


PROJECT_CORAL_SOURCES = ["oppia_roadmap.projects", "github.issues", "github.pulls"]


def _get_main():
    from . import main as main_mod

    return main_mod


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("none", "null", "nan"):
        return ""
    return text


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _parse_dt(value: Any) -> Optional[datetime]:
    raw = _clean(value)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", ""))
    except Exception:
        return None


def _repo_slug() -> str:
    owner = _clean(os.getenv("GITHUB_OWNER")) or "oppia"
    repo = _clean(os.getenv("GITHUB_REPO")) or "oppia"
    return f"{owner}/{repo}"


def _status_payload(
    status: str,
    summary: str,
    coral_sources_used: List[str],
    data: Any,
    **extra: Any,
) -> Dict[str, Any]:
    out = {
        "status": status,
        "summary": summary,
        "coral_sources_used": _unique_nonempty(coral_sources_used),
        "data": data,
    }
    out.update(extra)
    return out


def _unique_nonempty(values: List[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values or []:
        text = _clean(value)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _load_reports(force_refresh: bool = False) -> List[Dict[str, Any]]:
    main_mod = _get_main()
    return main_mod._build_project_reports(1200, force_refresh=bool(force_refresh))


def _risk_matches(level: str, risk_filter: str) -> bool:
    normalized_level = (_clean(level) or "LOW").upper()
    normalized_filter = (_clean(risk_filter) or "ALL").upper()
    if normalized_filter == "ALL":
        return True
    if normalized_filter == "HIGH":
        return normalized_level == "HIGH"
    if normalized_filter == "CRITICAL":
        return normalized_level == "CRITICAL"
    if normalized_filter == "HIGH_OR_CRITICAL":
        return normalized_level in ("HIGH", "CRITICAL")
    if normalized_filter == "LOW_OR_MEDIUM":
        return normalized_level in ("LOW", "MEDIUM")
    return True


def _project_summary_row(report: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "project_id": report.get("project_id"),
        "project_name": report.get("project_name"),
        "owner_lead": report.get("project_owner_lead"),
        "owner_contributor": report.get("project_owner_contributor"),
        "status": report.get("project_status"),
        "planned_completion_date": report.get("planned_completion_date"),
        "risk_score": _to_int(report.get("risk_score")),
        "risk_level": report.get("risk_level"),
        "risk_summary": report.get("risk_summary"),
        "top_risk_drivers": list(report.get("risk_drivers") or [])[:3],
        "linked_issue_numbers": list(report.get("all_github_issue_numbers") or []),
        "linked_pr_numbers": list(report.get("all_github_pr_numbers") or []),
    }


def _find_project_matches(
    reports: List[Dict[str, Any]],
    project_ref: str,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    ref = _clean(project_ref)
    if not ref:
        return None, []

    ref_lower = ref.lower()
    exact_id = [r for r in reports if _clean(r.get("project_id")) == ref]
    if exact_id:
        return exact_id[0], exact_id[:5]

    exact_name = [r for r in reports if _clean(r.get("project_name")).lower() == ref_lower]
    if exact_name:
        return exact_name[0], exact_name[:5]

    contains = [r for r in reports if ref_lower in _clean(r.get("project_name")).lower()]
    if not contains:
        return None, []

    contains_sorted = sorted(
        contains,
        key=lambda r: (
            -_to_int(r.get("risk_score")),
            len(_clean(r.get("project_name"))),
        ),
    )
    return contains_sorted[0], contains_sorted[:5]


def _extract_coral_sources_from_reports(reports: List[Dict[str, Any]]) -> List[str]:
    sources = list(PROJECT_CORAL_SOURCES)
    for report in reports or []:
        ci_rows = report.get("ci_evidence") or []
        if ci_rows:
            sources.append("ci.signals")
            break
    return _unique_nonempty(sources)


def _safe_limit(limit: Any, default: int = 10, minimum: int = 1, maximum: int = 100) -> int:
    value = _to_int(limit, default)
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def _email_draft_from_text(project_name: str, body: str, to_email: str = "") -> Dict[str, Any]:
    subject = f"Quick check-in on {project_name or 'project'}"
    mailto_url = None
    if _clean(to_email):
        mailto_url = f"mailto:{to_email}?subject={quote(subject)}&body={quote(body or '')}"
    return {
        "to": to_email or None,
        "subject": subject,
        "body": body,
        "mailto_url": mailto_url,
    }


def _run_sql_variants(variants: List[str], timeout: int = 8) -> Tuple[List[Dict[str, Any]], Optional[str], Optional[str]]:
    main_mod = _get_main()
    try:
        rows, used = main_mod._run_sql_variants(variants, timeout=timeout)
        return list(rows or []), used, None
    except Exception as e:
        return [], None, str(e)


def _normalize_pr_row(row: Dict[str, Any]) -> Dict[str, Any]:
    num = _to_int(row.get("number") or row.get("id") or row.get("pr_number"))
    return {
        "number": num if num > 0 else None,
        "title": _clean(row.get("title")),
        "state": (_clean(row.get("state")) or "unknown").lower(),
        "updated_at": _clean(row.get("updated_at") or row.get("updated")),
        "draft": row.get("draft") if row.get("draft") is not None else None,
        "html_url": _clean(row.get("html_url")),
    }


def _normalize_issue_row(row: Dict[str, Any]) -> Dict[str, Any]:
    num = _to_int(row.get("number") or row.get("id") or row.get("issue_number"))
    return {
        "number": num if num > 0 else None,
        "title": _clean(row.get("title")),
        "state": (_clean(row.get("state")) or "unknown").lower(),
        "labels": row.get("labels"),
        "updated_at": _clean(row.get("updated_at") or row.get("updated")),
        "html_url": _clean(row.get("html_url")),
    }


def _normalize_commit_row(row: Dict[str, Any]) -> Dict[str, Any]:
    sha = _clean(row.get("sha") or row.get("commit_sha") or row.get("id"))
    committed_at = _clean(
        row.get("committed_at")
        or row.get("commit__committer__date")
        or row.get("commit__author__date")
        or row.get("authored_at")
        or row.get("date")
        or row.get("committer_date")
    )
    return {
        "sha": sha,
        "message": _clean(row.get("message") or row.get("commit__message") or row.get("title")),
        "author": _clean(
            row.get("author")
            or row.get("commit__author__name")
            or row.get("author__login")
            or row.get("author_name")
            or row.get("committer")
        ),
        "committed_at": committed_at,
        "html_url": _clean(row.get("html_url") or row.get("url")),
    }


def _issue_url(issue_number: int) -> str:
    return f"https://github.com/{_repo_slug()}/issues/{issue_number}"


def _pr_url(pr_number: int) -> str:
    return f"https://github.com/{_repo_slug()}/pull/{pr_number}"


def _better_activity_row(candidate: Dict[str, Any], current: Dict[str, Any]) -> bool:
    candidate_dt = _parse_dt(candidate.get("updated_at"))
    current_dt = _parse_dt(current.get("updated_at"))
    if candidate_dt and current_dt:
        return candidate_dt > current_dt
    if candidate_dt and not current_dt:
        return True
    if not candidate_dt and current_dt:
        return False

    candidate_state = _clean(candidate.get("state")).lower()
    current_state = _clean(current.get("state")).lower()
    candidate_known = candidate_state in ("open", "closed", "merged")
    current_known = current_state in ("open", "closed", "merged")
    if candidate_known != current_known:
        return candidate_known
    return False


def _activity_fallback_from_project_reports(
    kind: str,
    limit: int,
    state: str = "all",
) -> Dict[str, Any]:
    cap = _safe_limit(limit, default=10, minimum=1, maximum=100)
    state_norm = (_clean(state) or "all").lower()
    if state_norm not in ("all", "open", "closed"):
        state_norm = "all"

    try:
        reports = _load_reports(force_refresh=False)
    except Exception as e:
        return {
            "status": "unavailable",
            "summary": f"Fallback evidence unavailable: {e}",
            "coral_sources_used": PROJECT_CORAL_SOURCES,
            "data": [],
        }

    if kind == "pulls":
        source_key = "github_pr_evidence"
        number_key = "number"
        normalizer = _normalize_pr_row
        fallback_url = _pr_url
    else:
        source_key = "github_issue_evidence"
        number_key = "number"
        normalizer = _normalize_issue_row
        fallback_url = _issue_url

    by_number: Dict[int, Dict[str, Any]] = {}
    for report in reports:
        project_name = _clean(report.get("project_name"))
        for raw in report.get(source_key) or []:
            if not isinstance(raw, dict):
                continue
            normalized = normalizer(raw)
            number = _to_int(normalized.get(number_key))
            if number <= 0:
                continue

            state_value = _clean(normalized.get("state")).lower()
            if state_norm in ("open", "closed") and state_value not in (state_norm,):
                continue

            normalized[number_key] = number
            if not _clean(normalized.get("html_url")):
                normalized["html_url"] = fallback_url(number)
            if project_name:
                normalized["linked_project_name"] = project_name
            current = by_number.get(number)
            if current is None or _better_activity_row(normalized, current):
                by_number[number] = normalized

    rows = list(by_number.values())
    has_ts = any(_parse_dt(row.get("updated_at")) for row in rows)
    if has_ts:
        rows = sorted(rows, key=lambda row: _parse_dt(row.get("updated_at")) or datetime.min, reverse=True)
    else:
        rows = sorted(rows, key=lambda row: _to_int(row.get(number_key)), reverse=True)
    rows = rows[:cap]

    source_name = "github.pulls" if kind == "pulls" else "github.issues"
    return {
        "status": "success",
        "summary": f"Using project-linked {source_name} evidence fallback ({len(rows)} rows).",
        "coral_sources_used": _extract_coral_sources_from_reports(reports),
        "data": rows,
        "fallback_used": True,
        "data_origin": "project_linked_evidence",
    }


def _extract_gemini_text(resp: Any) -> str:
    text = _clean(getattr(resp, "text", ""))
    if text:
        return text
    candidates = getattr(resp, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", []) or []:
            part_text = _clean(getattr(part, "text", ""))
            if part_text:
                return part_text
    return ""


def _gemini_brief_latest_pr(pr_row: Dict[str, Any]) -> str:
    api_key = _clean(os.getenv("GEMINI_API_KEY"))
    if not api_key or not isinstance(pr_row, dict):
        return ""

    number = _to_int(pr_row.get("number"))
    title = _clean(pr_row.get("title"))
    state = _clean(pr_row.get("state")) or "unknown"
    updated_at = _clean(pr_row.get("updated_at"))
    if not title:
        return ""

    try:
        from google import genai
        from google.genai import types
    except Exception:
        return ""

    model = _clean(os.getenv("GEMINI_MODEL")) or "gemini-2.5-flash"
    prompt = (
        "Summarize this pull request in one concise operational sentence for an engineering lead. "
        "Avoid markdown. Avoid hype. Keep under 28 words.\n"
        f"PR #{number if number > 0 else '?'}\n"
        f"Title: {title}\n"
        f"State: {state}\n"
        f"Updated: {updated_at or 'unknown'}"
    )

    try:
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=6000),
        )
    except Exception:
        try:
            client = genai.Client(api_key=api_key)
        except Exception:
            return ""

    try:
        try:
            cfg = types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_budget=0),
                max_output_tokens=80,
            )
        except Exception:
            cfg = types.GenerateContentConfig(max_output_tokens=80)
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=cfg,
        )
        out = _extract_gemini_text(resp)
        return _clean(out).splitlines()[0][:280] if _clean(out) else ""
    except Exception:
        return ""


def _fallback_latest_pr_brief(pr_row: Dict[str, Any]) -> str:
    if not isinstance(pr_row, dict):
        return ""
    number = _to_int(pr_row.get("number"))
    title = _clean(pr_row.get("title")) or "Untitled pull request"
    state = (_clean(pr_row.get("state")) or "unknown").lower()
    return f"PR #{number if number > 0 else '?'} is {state}: {title}"


def get_projects_summary(risk_filter: str = "ALL", limit: int = 20) -> Dict[str, Any]:
    try:
        reports = _load_reports(force_refresh=False)
    except Exception as e:
        return _status_payload(
            "error",
            f"Unable to load projects: {e}",
            PROJECT_CORAL_SOURCES,
            [],
        )

    filtered = [r for r in reports if _risk_matches(r.get("risk_level"), risk_filter)]
    filtered = sorted(filtered, key=lambda r: _to_int(r.get("risk_score")), reverse=True)
    cap = _safe_limit(limit, default=20, minimum=1, maximum=200)
    rows = [_project_summary_row(r) for r in filtered[:cap]]
    return _status_payload(
        "success",
        f"Found {len(rows)} projects",
        _extract_coral_sources_from_reports(reports),
        rows,
    )


def get_project_details(project_ref: str) -> Dict[str, Any]:
    try:
        reports = _load_reports(force_refresh=False)
    except Exception as e:
        return _status_payload(
            "error",
            f"Unable to load projects: {e}",
            PROJECT_CORAL_SOURCES,
            {"project": None},
        )

    best, matches = _find_project_matches(reports, project_ref)
    if not best:
        suggestions = sorted(
            [r.get("project_name") for r in reports if _clean(r.get("project_name"))]
        )[:10]
        return _status_payload(
            "not_found",
            f"No project found for reference '{project_ref}'",
            _extract_coral_sources_from_reports(reports),
            {"project": None, "alternatives": suggestions},
        )

    alternatives = [
        {"project_id": r.get("project_id"), "project_name": r.get("project_name")}
        for r in matches[1:]
    ]
    return _status_payload(
        "success",
        f"Resolved project '{best.get('project_name')}'",
        _extract_coral_sources_from_reports(reports),
        {
            "project": best,
            "subtasks": list(best.get("subtasks") or []),
            "github_issue_evidence": list(best.get("github_issue_evidence") or []),
            "github_pr_evidence": list(best.get("github_pr_evidence") or []),
            "risk_drivers": list(best.get("risk_drivers") or []),
            "recommended_actions": list(best.get("recommendations") or []),
            "alternatives": alternatives,
        },
    )


def get_owner_risk_summary(owner_name: Optional[str] = None) -> Dict[str, Any]:
    try:
        reports = _load_reports(force_refresh=False)
    except Exception as e:
        return _status_payload(
            "error",
            f"Unable to load owner summary: {e}",
            PROJECT_CORAL_SOURCES,
            [],
        )

    owner_map: Dict[str, Dict[str, Any]] = {}
    for report in reports:
        lead = _clean(report.get("project_owner_lead")) or "(unassigned lead)"
        item = owner_map.setdefault(
            lead,
            {
                "owner_lead": lead,
                "total_projects": 0,
                "high_risk_projects": 0,
                "contributors_needing_followup": set(),
                "highest_risk_project": None,
                "average_risk_score": 0,
                "_score_sum": 0,
                "_max_score": -1,
            },
        )
        item["total_projects"] += 1
        score = _to_int(report.get("risk_score"))
        item["_score_sum"] += score
        if score > item["_max_score"]:
            item["_max_score"] = score
            item["highest_risk_project"] = report.get("project_name")
        if (_clean(report.get("risk_level"))).upper() in ("HIGH", "CRITICAL"):
            item["high_risk_projects"] += 1
            contributor = _clean(report.get("project_owner_contributor"))
            if contributor:
                item["contributors_needing_followup"].add(contributor)

    data: List[Dict[str, Any]] = []
    owner_filter = _clean(owner_name).lower()
    for _, item in owner_map.items():
        if owner_filter and owner_filter not in item["owner_lead"].lower():
            continue
        total = max(1, _to_int(item["total_projects"], 1))
        data.append(
            {
                "owner_lead": item["owner_lead"],
                "total_projects": item["total_projects"],
                "high_risk_projects": item["high_risk_projects"],
                "contributors_needing_followup": sorted(list(item["contributors_needing_followup"])),
                "highest_risk_project": item["highest_risk_project"],
                "average_risk_score": int(item["_score_sum"] / total),
            }
        )

    data = sorted(data, key=lambda x: (x["high_risk_projects"], x["average_risk_score"]), reverse=True)
    return _status_payload(
        "success",
        f"Found {len(data)} owner lead profiles",
        _extract_coral_sources_from_reports(reports),
        data,
    )


def get_recent_pull_requests(limit: int = 10, state: str = "all") -> Dict[str, Any]:
    main_mod = _get_main()
    if not main_mod.coral.available():
        return _status_payload(
            "unavailable",
            "Coral CLI is not available.",
            ["github.pulls"],
            [],
        )

    cap = _safe_limit(limit, default=10, minimum=1, maximum=100)
    state_norm = (_clean(state) or "all").lower()
    if state_norm not in ("all", "open", "closed"):
        state_norm = "all"

    owner, repo = _repo_slug().split("/", 1)
    state_clause = "" if state_norm == "all" else f" AND state = '{state_norm}'"
    query_limit = max(cap, min(300, cap * 4))
    variants = [
        "SELECT number, title, state, updated_at, draft, html_url "
        f"FROM github.pulls WHERE owner = '{owner}' AND repo = '{repo}'{state_clause} "
        f"LIMIT {query_limit};",
        "SELECT number, title, state, updated_at, html_url "
        f"FROM github.pulls WHERE owner = '{owner}' AND repo = '{repo}'{state_clause} "
        f"LIMIT {query_limit};",
        "SELECT number, title, state, updated_at, draft, html_url "
        f"FROM github.pulls WHERE owner = '{owner}' AND repo = '{repo}'{state_clause} "
        f"AND html_url LIKE '%/{owner}/{repo}/pull/%' LIMIT {query_limit};",
    ]

    rows, used_query, err = _run_sql_variants(variants, timeout=6)
    if err:
        fallback = _activity_fallback_from_project_reports("pulls", cap, state_norm)
        if fallback.get("data"):
            return _status_payload(
                "partial",
                f"Using project-linked pull request evidence because github.pulls query failed: {err}",
                list(fallback.get("coral_sources_used") or ["github.pulls"]),
                fallback.get("data") or [],
                used_query=used_query,
                query_error=err,
                fallback_used=True,
                data_origin=fallback.get("data_origin") or "project_linked_evidence",
            )
        return _status_payload(
            "error",
            f"Unable to query github.pulls: {err}",
            ["github.pulls"],
            [],
            used_query=used_query,
        )

    data = [_normalize_pr_row(r) for r in rows if isinstance(r, dict)]
    data = [r for r in data if r.get("number")]
    data = sorted(data, key=lambda x: _parse_dt(x.get("updated_at")) or datetime.min, reverse=True)[:cap]
    if not data:
        fallback = _activity_fallback_from_project_reports("pulls", cap, state_norm)
        if fallback.get("data"):
            return _status_payload(
                "partial",
                "No direct pull request activity rows returned; showing project-linked fallback evidence.",
                list(fallback.get("coral_sources_used") or ["github.pulls"]),
                fallback.get("data") or [],
                used_query=used_query,
                fallback_used=True,
                data_origin=fallback.get("data_origin") or "project_linked_evidence",
            )
    return _status_payload(
        "success",
        f"Found {len(data)} latest pull requests",
        ["github.pulls"],
        data,
        used_query=used_query,
    )


def get_recent_issues(limit: int = 10, state: str = "all") -> Dict[str, Any]:
    main_mod = _get_main()
    if not main_mod.coral.available():
        return _status_payload(
            "unavailable",
            "Coral CLI is not available.",
            ["github.issues"],
            [],
        )

    cap = _safe_limit(limit, default=10, minimum=1, maximum=100)
    state_norm = (_clean(state) or "all").lower()
    if state_norm not in ("all", "open", "closed"):
        state_norm = "all"

    owner, repo = _repo_slug().split("/", 1)
    state_clause = "" if state_norm == "all" else f" AND state = '{state_norm}'"
    query_limit = max(cap, min(300, cap * 8))
    variants = [
        "SELECT number, title, state, labels, updated_at, html_url "
        f"FROM github.issues WHERE owner = '{owner}' AND repo = '{repo}'{state_clause} "
        f"LIMIT {query_limit};",
        "SELECT number, title, state, updated_at, html_url "
        f"FROM github.issues WHERE owner = '{owner}' AND repo = '{repo}'{state_clause} "
        f"LIMIT {query_limit};",
        "SELECT number, title, state, updated_at, html_url "
        f"FROM github.issues WHERE owner = '{owner}' AND repo = '{repo}'{state_clause} "
        f"AND html_url LIKE '%/{owner}/{repo}/issues/%' LIMIT {query_limit};",
    ]

    rows, used_query, err = _run_sql_variants(variants, timeout=6)
    if err:
        fallback = _activity_fallback_from_project_reports("issues", cap, state_norm)
        if fallback.get("data"):
            return _status_payload(
                "partial",
                f"Using project-linked issue evidence because github.issues query failed: {err}",
                list(fallback.get("coral_sources_used") or ["github.issues"]),
                fallback.get("data") or [],
                used_query=used_query,
                query_error=err,
                fallback_used=True,
                data_origin=fallback.get("data_origin") or "project_linked_evidence",
            )
        return _status_payload(
            "error",
            f"Unable to query github.issues: {err}",
            ["github.issues"],
            [],
            used_query=used_query,
        )

    data = [_normalize_issue_row(r) for r in rows if isinstance(r, dict)]
    data = [r for r in data if r.get("number")]
    issue_only = [
        r
        for r in data
        if "/issues/" in _clean(r.get("html_url")).lower() or "/pull/" not in _clean(r.get("html_url")).lower()
    ]
    data = issue_only or data
    data = sorted(data, key=lambda x: _parse_dt(x.get("updated_at")) or datetime.min, reverse=True)[:cap]
    if not data:
        fallback = _activity_fallback_from_project_reports("issues", cap, state_norm)
        if fallback.get("data"):
            return _status_payload(
                "partial",
                "No direct issue activity rows returned; showing project-linked fallback evidence.",
                list(fallback.get("coral_sources_used") or ["github.issues"]),
                fallback.get("data") or [],
                used_query=used_query,
                fallback_used=True,
                data_origin=fallback.get("data_origin") or "project_linked_evidence",
            )
    return _status_payload(
        "success",
        f"Found {len(data)} latest issues",
        ["github.issues"],
        data,
        used_query=used_query,
    )


def get_latest_commits(limit: int = 10) -> Dict[str, Any]:
    main_mod = _get_main()
    cap = _safe_limit(limit, default=10, minimum=1, maximum=100)
    if not main_mod.coral.available():
        return _status_payload(
            "unavailable",
            "Coral CLI is not available.",
            ["information_schema.tables"],
            [],
        )

    if not main_mod._table_exists("github", "commits"):
        return _status_payload(
            "unavailable",
            "github.commits is not available in this Coral GitHub source.",
            ["information_schema.tables"],
            [],
        )

    owner, repo = _repo_slug().split("/", 1)
    query_limit = max(cap, min(300, cap * 4))
    variants = [
        "SELECT sha, commit__message AS message, commit__author__name AS author, "
        "commit__committer__date AS committed_at, html_url "
        f"FROM github.commits WHERE owner = '{owner}' AND repo = '{repo}' LIMIT {query_limit};",
        "SELECT sha, commit__message, commit__author__name, commit__author__date, "
        "commit__committer__date, html_url "
        f"FROM github.commits WHERE owner = '{owner}' AND repo = '{repo}' LIMIT {query_limit};",
        "SELECT sha, author__login AS author, commit__message AS message, "
        "commit__author__date AS committed_at, html_url "
        f"FROM github.commits WHERE owner = '{owner}' AND repo = '{repo}' LIMIT {query_limit};",
    ]
    rows, used_query, err = _run_sql_variants(variants, timeout=8)
    if err:
        return _status_payload(
            "error",
            f"Unable to query github.commits: {err}",
            ["github.commits"],
            [],
            used_query=used_query,
        )

    data = [_normalize_commit_row(r) for r in rows if isinstance(r, dict)]
    data = [r for r in data if _clean(r.get("sha"))]
    data = sorted(data, key=lambda x: _parse_dt(x.get("committed_at")) or datetime.min, reverse=True)[:cap]
    return _status_payload(
        "success",
        f"Found {len(data)} latest commits",
        ["github.commits"],
        data,
        used_query=used_query,
    )


def find_possible_regression_sources(
    project_ref: Optional[str] = None,
    lookback_days: int = 14,
) -> Dict[str, Any]:
    lookback = _safe_limit(lookback_days, default=14, minimum=1, maximum=90)
    now = datetime.utcnow()
    cutoff = now - timedelta(days=lookback)

    sources_used: List[str] = []
    suspects: List[Dict[str, Any]] = []
    seen = set()

    details = None
    if _clean(project_ref):
        details = get_project_details(_clean(project_ref))
        sources_used.extend(details.get("coral_sources_used") or [])

    target_projects: List[Dict[str, Any]] = []
    if details and details.get("status") == "success":
        target_projects = [details.get("data", {}).get("project") or {}]
    else:
        high_risk = get_projects_summary(risk_filter="HIGH_OR_CRITICAL", limit=5)
        sources_used.extend(high_risk.get("coral_sources_used") or [])
        target_projects = list(high_risk.get("data") or [])

    def _add_suspect(item: Dict[str, Any]) -> None:
        key = f"{item.get('type')}:{item.get('number_or_sha')}"
        if key in seen:
            return
        seen.add(key)
        suspects.append(item)

    for project in target_projects:
        if not isinstance(project, dict):
            continue
        project_name = _clean(project.get("project_name")) or "project"
        pr_rows = list(project.get("github_pr_evidence") or [])
        ci_rows = list(project.get("ci_evidence") or [])
        ci_by_pr = {}
        for row in ci_rows:
            pr_num = _to_int(row.get("pr_number"))
            if pr_num > 0:
                ci_by_pr[pr_num] = row

        for pr in pr_rows:
            pr_num = _to_int(pr.get("number"))
            if pr_num <= 0:
                continue
            state = _clean(pr.get("state")).lower()
            updated_at = _parse_dt(pr.get("updated_at"))
            stale = bool(updated_at and updated_at < cutoff and state == "open")
            ci = ci_by_pr.get(pr_num) or {}
            ci_status = _clean(ci.get("status")).lower()
            failed_ci = ci_status == "failed"
            if not (state == "open" or stale or failed_ci):
                continue

            confidence = "MEDIUM"
            reasons: List[str] = []
            if failed_ci:
                confidence = "HIGH"
                reasons.append("linked CI/test evidence is failing for this PR")
            if stale:
                reasons.append(f"open PR has not been updated in >{lookback} days")
            if state == "open":
                reasons.append("PR is still open on a risky project")
            if not reasons:
                reasons.append("PR is linked to a high-risk project")
            _add_suspect(
                {
                    "type": "pull_request",
                    "number_or_sha": pr_num,
                    "title_or_message": _clean(pr.get("title")) or f"PR #{pr_num}",
                    "url": _clean(pr.get("html_url")) or f"https://github.com/{_repo_slug()}/pull/{pr_num}",
                    "reason": f"{project_name}: " + "; ".join(reasons),
                    "confidence": confidence,
                }
            )

    commits = get_latest_commits(limit=max(10, lookback))
    sources_used.extend(commits.get("coral_sources_used") or [])
    if commits.get("status") == "success":
        for commit in commits.get("data") or []:
            message = _clean(commit.get("message")).lower()
            if not any(k in message for k in ("regression", "flaky", "ci", "test", "bug", "fix")):
                continue
            _add_suspect(
                {
                    "type": "commit",
                    "number_or_sha": _clean(commit.get("sha"))[:12],
                    "title_or_message": _clean(commit.get("message")),
                    "url": _clean(commit.get("html_url")),
                    "reason": "recent commit message suggests test/CI/regression-related changes",
                    "confidence": "LOW",
                }
            )

    if not suspects:
        recent_prs = get_recent_pull_requests(limit=5, state="open")
        sources_used.extend(recent_prs.get("coral_sources_used") or [])
        for pr in recent_prs.get("data") or []:
            pr_num = _to_int(pr.get("number"))
            if pr_num <= 0:
                continue
            _add_suspect(
                {
                    "type": "pull_request",
                    "number_or_sha": pr_num,
                    "title_or_message": _clean(pr.get("title")) or f"PR #{pr_num}",
                    "url": _clean(pr.get("html_url")) or f"https://github.com/{_repo_slug()}/pull/{pr_num}",
                    "reason": "recent open PR near active roadmap work (correlation, not proven causality)",
                    "confidence": "LOW",
                }
            )

    suspects = suspects[:12]
    return _status_payload(
        "success",
        "OmniSprint cannot prove causality without full CI logs, but found likely suspects.",
        _unique_nonempty(sources_used or PROJECT_CORAL_SOURCES),
        {"suspects": suspects},
    )


def get_reminder_candidates(owner_name: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
    try:
        reports = _load_reports(force_refresh=False)
    except Exception as e:
        return _status_payload(
            "error",
            f"Unable to load reminder candidates: {e}",
            PROJECT_CORAL_SOURCES,
            [],
        )

    reminders = generate_reminders(reports, risk_threshold="HIGH")
    owner_filter = _clean(owner_name).lower()
    if owner_filter:
        reminders = [
            r
            for r in reminders
            if owner_filter in _clean(r.get("project_owner_lead")).lower()
        ]
    cap = _safe_limit(limit, default=10, minimum=1, maximum=200)
    reminders = reminders[:cap]

    data = []
    for reminder in reminders:
        chat_text = _clean(reminder.get("google_chat_text"))
        email = _email_draft_from_text(
            _clean(reminder.get("project_name")),
            chat_text,
            _clean(reminder.get("contributor_email")),
        )
        if reminder.get("mailto_url"):
            email["mailto_url"] = reminder.get("mailto_url")
        data.append(
            {
                "project_id": reminder.get("project_id"),
                "project_name": reminder.get("project_name"),
                "lead": reminder.get("project_owner_lead"),
                "contributor": reminder.get("project_owner_contributor"),
                "risk_level": reminder.get("risk_level"),
                "risk_score": reminder.get("risk_score"),
                "reason": reminder.get("reason"),
                "google_chat_text": chat_text,
                "email_subject": email.get("subject"),
                "email_body": email.get("body"),
                "mailto_url": email.get("mailto_url"),
                "contributor_email": reminder.get("contributor_email"),
            }
        )

    return _status_payload(
        "success",
        f"Found {len(data)} reminder candidates",
        _extract_coral_sources_from_reports(reports),
        data,
    )


def generate_project_reminder(project_ref: str) -> Dict[str, Any]:
    details = get_project_details(project_ref)
    sources_used = list(details.get("coral_sources_used") or PROJECT_CORAL_SOURCES)
    if details.get("status") != "success":
        return _status_payload(
            details.get("status", "not_found"),
            details.get("summary") or "Project not found.",
            sources_used,
            {"project": None, "reminder": None},
        )

    project = details.get("data", {}).get("project") or {}
    risk_level = (_clean(project.get("risk_level")) or "LOW").upper()
    if risk_level not in ("HIGH", "CRITICAL"):
        return _status_payload(
            "success",
            "No reminder needed — project appears on track.",
            sources_used,
            {"project": project, "reminder": None},
        )

    try:
        reports = _load_reports(force_refresh=False)
    except Exception as e:
        return _status_payload(
            "error",
            f"Unable to generate reminder: {e}",
            sources_used,
            {"project": project, "reminder": None},
        )

    reminders = generate_reminders(
        reports,
        risk_threshold="HIGH",
        project_id=project.get("project_id"),
    )
    if not reminders:
        return _status_payload(
            "success",
            "No reminder needed — project appears on track.",
            _extract_coral_sources_from_reports(reports),
            {"project": project, "reminder": None},
        )

    reminder = reminders[0]
    email = _email_draft_from_text(
        _clean(reminder.get("project_name")),
        _clean(reminder.get("google_chat_text")),
        _clean(reminder.get("contributor_email")),
    )
    if reminder.get("mailto_url"):
        email["mailto_url"] = reminder.get("mailto_url")

    return _status_payload(
        "success",
        f"Generated reminder for {reminder.get('project_name')}",
        _extract_coral_sources_from_reports(reports),
        {
            "project": project,
            "reminder": reminder,
            "email_subject": email.get("subject"),
            "email_body": email.get("body"),
            "mailto_url": email.get("mailto_url"),
            "contributor_email": reminder.get("contributor_email"),
        },
    )


def get_latest_activity_summary(limit: int = 10) -> Dict[str, Any]:
    cap = _safe_limit(limit, default=10, minimum=1, maximum=50)
    pulls = get_recent_pull_requests(limit=cap, state="all")
    issues = get_recent_issues(limit=cap, state="all")
    commits = get_latest_commits(limit=cap)

    sources_used = _unique_nonempty(
        list(pulls.get("coral_sources_used") or [])
        + list(issues.get("coral_sources_used") or [])
        + list(commits.get("coral_sources_used") or [])
    )

    high_risk_rows: List[Dict[str, Any]] = []
    recommended_actions: List[str] = []
    pull_rows = list(pulls.get("data") or [])
    issue_rows = list(issues.get("data") or [])
    commit_rows = list(commits.get("data") or [])

    if pulls.get("status") in ("success", "partial"):
        open_prs = [p for p in pull_rows if _clean(p.get("state")).lower() == "open"]
        if open_prs:
            recommended_actions.append(f"Review {len(open_prs)} recently updated open pull requests")
    if issues.get("status") in ("success", "partial"):
        open_issues = [i for i in issue_rows if _clean(i.get("state")).lower() == "open"]
        if open_issues:
            recommended_actions.append(f"Triage {len(open_issues)} recently updated open issues")
    if commits.get("status") == "success" and commit_rows:
        recommended_actions.append("Check recent commits for changes touching blocked or high-risk roadmap projects")

    latest_pr_brief = ""
    gemini_brief_used = False
    if pull_rows:
        latest_pr_brief = _gemini_brief_latest_pr(pull_rows[0])
        gemini_brief_used = bool(latest_pr_brief)
        if not latest_pr_brief:
            latest_pr_brief = _fallback_latest_pr_brief(pull_rows[0])
        if gemini_brief_used:
            sources_used = _unique_nonempty(sources_used + ["gemini.summary"])

    data = {
        "latest_pull_requests": pull_rows,
        "latest_issues": issue_rows,
        "latest_commits": commit_rows,
        "pulls_status": pulls.get("status"),
        "issues_status": issues.get("status"),
        "commits_status": commits.get("status"),
        "pulls_summary": pulls.get("summary"),
        "issues_summary": issues.get("summary"),
        "commits_summary": commits.get("summary"),
        "pulls_fallback_used": bool(pulls.get("fallback_used")),
        "issues_fallback_used": bool(issues.get("fallback_used")),
        "pulls_data_origin": pulls.get("data_origin"),
        "issues_data_origin": issues.get("data_origin"),
        "latest_pr_brief": latest_pr_brief,
        "high_risk_projects": high_risk_rows,
        "recommended_actions": _unique_nonempty(recommended_actions),
    }
    overall_status = "success"
    if (
        pulls.get("status") in ("error", "unavailable")
        or issues.get("status") in ("error", "unavailable")
        or commits.get("status") in ("error", "unavailable")
    ):
        overall_status = "partial"
    return _status_payload(
        overall_status,
        "Compiled latest activity across PRs, issues, commits, and high-risk projects.",
        sources_used,
        data,
    )


def get_technical_evidence(include_queries: bool = True) -> Dict[str, Any]:
    main_mod = _get_main()
    health_payload = main_mod.health()

    try:
        reports = _load_reports(force_refresh=False)
        reports_error = None
    except Exception as e:
        reports = []
        reports_error = str(e)

    query_steps: List[str] = []
    sample_queries: List[str] = []
    if reports:
        for report in reports[:5]:
            flow = report.get("coral_query_flow_used") or {}
            query_steps.extend(flow.get("steps") or [])
            if include_queries:
                sample_queries.extend(flow.get("queries") or [])

    if not query_steps:
        query_steps = [
            "Coral retrieves planning rows from oppia_roadmap.projects.",
            "Backend groups rows into projects and extracts GitHub links.",
            "Coral retrieves GitHub evidence from github.issues/github.pulls.",
            "Backend scores risk and generates follow-up actions.",
        ]

    data = {
        "mode": health_payload.get("mode"),
        "source_health": health_payload.get("sources") or [],
        "connected_sources_count": health_payload.get("connected_sources_count"),
        "query_flow": _unique_nonempty(query_steps),
        "sample_queries": _unique_nonempty(sample_queries)[:20] if include_queries else [],
        "report_cache_present": bool(main_mod.REPORT_CACHE_FILE.exists()),
        "reports_cached": len(reports),
    }
    if reports_error:
        data["reports_error"] = reports_error

    coral_sources = [s.get("table") for s in (health_payload.get("sources") or []) if s.get("table")]
    if include_queries:
        coral_sources.append("information_schema.tables")
    return _status_payload(
        "success",
        "Collected Coral source health and query evidence.",
        _unique_nonempty(coral_sources),
        data,
    )


TOOL_REGISTRY = {
    "get_projects_summary": get_projects_summary,
    "get_project_details": get_project_details,
    "get_owner_risk_summary": get_owner_risk_summary,
    "get_recent_pull_requests": get_recent_pull_requests,
    "get_recent_issues": get_recent_issues,
    "get_latest_commits": get_latest_commits,
    "find_possible_regression_sources": find_possible_regression_sources,
    "get_reminder_candidates": get_reminder_candidates,
    "generate_project_reminder": generate_project_reminder,
    "get_latest_activity_summary": get_latest_activity_summary,
    "get_technical_evidence": get_technical_evidence,
}
