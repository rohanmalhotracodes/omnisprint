import os
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
from urllib.parse import quote


LEVEL_ORDER = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


def _parse_date(s: str):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d-%b-%Y", "%d %b", "%d %B"):
        try:
            dt = datetime.strptime(str(s).strip(), fmt)
            if "%Y" not in fmt:
                dt = dt.replace(year=datetime.utcnow().year)
            return dt.date()
        except Exception:
            continue
    try:
        return datetime.fromisoformat(str(s)).date()
    except Exception:
        return None


def _threshold_allows(risk_level: str, risk_threshold: str) -> bool:
    a = LEVEL_ORDER.get((risk_level or "").upper(), 0)
    b = LEVEL_ORDER.get((risk_threshold or "HIGH").upper(), 3)
    return a >= b


def _build_links(report: Dict[str, Any]) -> tuple[list[str], list[str]]:
    issue_links: List[str] = []
    pr_links: List[str] = []
    repo_slug = os.getenv("GITHUB_REPO_FULL_NAME") or "oppia/oppia"
    if "/" not in repo_slug:
        owner = os.getenv("GITHUB_OWNER", "oppia")
        repo_slug = f"{owner}/{repo_slug}"

    for i in report.get("github_issue_evidence") or []:
        url = i.get("html_url")
        if url:
            issue_links.append(url)
    for p in report.get("github_pr_evidence") or []:
        url = p.get("html_url")
        if url:
            pr_links.append(url)

    if not issue_links:
        for n in report.get("all_github_issue_numbers") or []:
            issue_links.append(f"https://github.com/{repo_slug}/issues/{n}")
    if not pr_links:
        for n in report.get("all_github_pr_numbers") or []:
            pr_links.append(f"https://github.com/{repo_slug}/pull/{n}")

    return sorted(set(issue_links)), sorted(set(pr_links))


def _extract_email(text: str) -> Optional[str]:
    if not text:
        return None
    match = EMAIL_RE.search(str(text))
    if not match:
        return None
    return match.group(0).strip()


def _resolve_contributor_email(report: Dict[str, Any]) -> Optional[str]:
    explicit = _extract_email(str(report.get("contributor_email") or ""))
    if explicit:
        return explicit
    return _extract_email(str(report.get("project_owner_contributor") or ""))


def _build_mailto(project_name: str, message: str, contributor_email: str) -> str:
    subject = quote(f"Sprint Tracker follow-up: {project_name}")
    body = quote(message or "")
    return f"mailto:{contributor_email}?subject={subject}&body={body}"


def _needs_reminder(report: Dict[str, Any], risk_threshold: str = "HIGH") -> bool:
    risk_level = (report.get("risk_level") or "").upper()
    threshold = (risk_threshold or "HIGH").upper()
    if _threshold_allows(risk_level, risk_threshold):
        return True

    # For default/high-threshold reminders, do not notify LOW/MEDIUM projects
    # unless caller explicitly lowers the threshold.
    if threshold in ("HIGH", "CRITICAL"):
        return False

    if int(report.get("blocked_subtasks", 0)) > 0:
        return True
    if int(report.get("stale_open_pr_count", 0)) > 0 or int(report.get("stale_open_issue_count", 0)) > 0:
        return True

    planned = _parse_date(report.get("planned_completion_date"))
    unfinished = int(report.get("total_subtasks", 0)) - int(report.get("completed_subtasks", 0))
    if planned and planned < datetime.utcnow().date() and unfinished > 0:
        return True
    return False


def _message_text(report: Dict[str, Any], issue_links: List[str], pr_links: List[str]) -> str:
    contributor = report.get("project_owner_contributor") or "Contributor"
    project_name = report.get("project_name") or "this project"
    risk_level = report.get("risk_level") or "HIGH"
    drivers = (report.get("risk_drivers") or [])[:2]
    while len(drivers) < 2:
        drivers.append("Risk signal detected in roadmap/GitHub evidence")

    issue_part = ", ".join(issue_links[:6]) if issue_links else "None"
    pr_part = ", ".join(pr_links[:6]) if pr_links else "None"

    return (
        f"Hi {contributor}, quick reminder on {project_name}.\n\n"
        f"Sprint Tracker flagged this project as {risk_level} because:\n"
        f"- {drivers[0]}\n"
        f"- {drivers[1]}\n\n"
        "Could you please share:\n"
        "1. Current progress\n"
        "2. Any blockers\n"
        "3. Updated expected completion date\n"
        "4. Whether any reviewer/help is needed\n\n"
        "Linked items:\n"
        f"- Issues: {issue_part}\n"
        f"- PRs: {pr_part}\n\n"
        "Thanks!"
    )


def generate_reminders(
    project_reports: List[Dict[str, Any]],
    risk_threshold: str = "HIGH",
    project_id: Optional[str] = None,
    owner_lead: Optional[str] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    threshold = (risk_threshold or "HIGH").upper()

    for report in project_reports:
        if project_id and report.get("project_id") != project_id:
            continue
        if owner_lead and (report.get("project_owner_lead") or "").lower() != owner_lead.lower():
            continue
        if not _needs_reminder(report, threshold):
            continue

        issue_links, pr_links = _build_links(report)
        text = _message_text(report, issue_links, pr_links)
        reason = "; ".join((report.get("risk_drivers") or [])[:3]) or "High-risk/blocked/slipping project"
        contributor_email = _resolve_contributor_email(report)
        can_email = bool(contributor_email)
        mailto_url = _build_mailto(report.get("project_name") or "project", text, contributor_email) if contributor_email else None

        out.append(
            {
                "project_id": report.get("project_id"),
                "project_name": report.get("project_name"),
                "project_owner_lead": report.get("project_owner_lead"),
                "project_owner_contributor": report.get("project_owner_contributor"),
                "risk_level": report.get("risk_level"),
                "risk_score": report.get("risk_score"),
                "reason": reason,
                "google_chat_text": text,
                "risk_drivers": report.get("risk_drivers") or [],
                "contributor_email": contributor_email,
                "can_email": can_email,
                "mailto_url": mailto_url,
            }
        )

    return out
