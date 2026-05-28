from datetime import datetime
from typing import List, Dict, Any

from .models import Project, Subtask, RiskReport


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


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except Exception:
        return 0


def _is_done_status(status: str) -> bool:
    s = (status or "").lower()
    return any(x in s for x in ("done", "complete", "completed", "closed"))


def _has_blocker_signal(status: str, notes: str) -> bool:
    txt = f"{status or ''} {notes or ''}".lower()
    return any(
        kw in txt
        for kw in (
            "blocked",
            "stuck",
            "waiting",
            "delayed",
            "dependency",
            "needs approval",
            "review required",
            "failing",
        )
    )


def _collect_high_risk_subtasks(subtasks: List[Subtask]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for st in subtasks:
        reasons: List[str] = []
        if _has_blocker_signal(st.status or "", st.notes or ""):
            reasons.append("blocked_or_delayed")
        if (st.notes or "").lower().find("review") >= 0:
            reasons.append("needs_review_attention")
        if (st.notes or "").lower().find("approval") >= 0:
            reasons.append("awaiting_approval")
        if not reasons:
            continue
        out.append(
            {
                "subtask": st.subtask,
                "status": st.status,
                "assignee": st.assignee,
                "estimated_completion_date": st.estimated_completion_date,
                "notes": st.notes,
                "reasons": reasons,
            }
        )
    return out


def _subtask_key(st: Any) -> str:
    if isinstance(st, dict):
        subtask = st.get("subtask")
        status = st.get("status")
        assignee = st.get("assignee")
        estimated = st.get("estimated_completion_date")
        notes = st.get("notes")
    else:
        subtask = getattr(st, "subtask", None)
        status = getattr(st, "status", None)
        assignee = getattr(st, "assignee", None)
        estimated = getattr(st, "estimated_completion_date", None)
        notes = getattr(st, "notes", None)
    return "|".join(
        [
            str(subtask or ""),
            str(status or ""),
            str(assignee or ""),
            str(estimated or ""),
            str(notes or ""),
        ]
    )


def _is_failed_ci_signal(status: str, failed_tests: int) -> bool:
    return status in ("failed", "failure", "error", "timed_out", "cancelled") or failed_tests > 0


def score_project(project: Project, extra_context: Dict[str, Any] = None) -> RiskReport:
    score = 0
    drivers: List[str] = []
    recs: List[str] = []
    evidence: Dict[str, Any] = {}

    today = datetime.utcnow().date()
    subtasks = project.subtasks or []
    total_subtasks = len(subtasks)
    completed_subtasks = len([s for s in subtasks if _is_done_status(s.status or "")])
    unfinished_subtasks = max(0, total_subtasks - completed_subtasks)
    blocked_subtasks = [s for s in subtasks if _has_blocker_signal(s.status or "", s.notes or "")]

    high_risk_subtasks = _collect_high_risk_subtasks(subtasks)

    # Schedule risk at project level.
    planned = _parse_date(project.planned_completion_date or "")
    if planned:
        days_to_plan = (planned - today).days
        if days_to_plan < 0 and unfinished_subtasks > 0:
            score += 25
            drivers.append("Planned completion date has passed and project still has unfinished subtasks")
            evidence["planning_sheet"] = f"Planned completion {project.planned_completion_date} is in the past"
        elif days_to_plan <= 7 and unfinished_subtasks > 0:
            score += 10
            drivers.append("Planned completion date is within 7 days with unfinished subtasks")
            evidence["planning_sheet"] = f"Planned completion date soon: {project.planned_completion_date}"

    # Subtask execution risk.
    if unfinished_subtasks >= 3:
        score += 10
        drivers.append(f"Multiple unfinished subtasks ({unfinished_subtasks})")
    if unfinished_subtasks >= 6:
        score += 5

    if blocked_subtasks:
        score += min(25, 10 + 3 * (len(blocked_subtasks) - 1))
        drivers.append(f"{len(blocked_subtasks)} blocked/stuck/delayed subtasks detected")

    # Notes risk.
    all_notes = " ".join([(s.notes or "") for s in subtasks]).lower()
    if any(kw in all_notes for kw in ("flaky", "merge conflict", "dependency", "pending", "failing")):
        score += 8
        drivers.append("High-risk notes found across subtasks (flaky/dependency/merge conflicts/pending)")

    # Ownership risk.
    if not (project.project_owner_contributor or "").strip():
        score += 12
        drivers.append("Missing Project Owner (Contributor)")

    linked_issue_count = len(project.all_github_issue_numbers or [])
    linked_pr_count = len(project.all_github_pr_numbers or [])
    if linked_issue_count:
        score += min(12, linked_issue_count * 2)
        drivers.append(f"Project links to {linked_issue_count} GitHub issues")
    if linked_pr_count:
        score += min(15, linked_pr_count * 2)
        drivers.append(f"Project links to {linked_pr_count} GitHub PRs")

    github_issue_evidence = list(extra_context.get("issues") or []) if extra_context else []
    github_pr_evidence = list(extra_context.get("prs") or []) if extra_context else []

    open_issue_count = 0
    stale_open_issue_count = 0
    for iss in github_issue_evidence:
        st = (iss.get("state") or "").lower()
        if st == "open":
            open_issue_count += 1
            updated = iss.get("updated_at") or iss.get("updated")
            dt = _parse_dt(updated)
            if dt is not None and (datetime.utcnow() - dt).days > 14:
                stale_open_issue_count += 1

    open_pr_count = 0
    stale_open_pr_count = 0
    for pr in github_pr_evidence:
        st = (pr.get("state") or "").lower()
        if st == "open":
            open_pr_count += 1
            updated = pr.get("updated_at") or pr.get("updated")
            dt = _parse_dt(updated)
            if dt is not None and (datetime.utcnow() - dt).days > 14:
                stale_open_pr_count += 1

    if open_issue_count:
        score += min(14, open_issue_count * 2)
        drivers.append(f"{open_issue_count} open linked issues")
    if open_pr_count:
        score += min(16, open_pr_count * 3)
        drivers.append(f"{open_pr_count} open linked PRs")
    if stale_open_issue_count:
        score += min(12, stale_open_issue_count * 4)
        drivers.append(f"{stale_open_issue_count} stale open linked issues (>14 days)")
    if stale_open_pr_count:
        score += min(15, stale_open_pr_count * 5)
        drivers.append(f"{stale_open_pr_count} stale open linked PRs (>14 days)")

    ci_signals_by_pr = dict(extra_context.get("ci_signals_by_pr") or {}) if extra_context else {}
    ci_evidence: List[Dict[str, Any]] = []
    failing_ci_pr_count = 0
    flaky_ci_pr_count = 0
    failed_tests_total = 0
    flaky_tests_total = 0
    stale_ci_signal_count = 0
    failing_ci_pr_numbers = set()

    for pr in github_pr_evidence:
        pr_num = _to_int(pr.get("number") or pr.get("id"))
        if pr_num <= 0:
            continue
        sig = ci_signals_by_pr.get(pr_num)
        if not sig:
            continue

        entry = dict(sig)
        entry["pr_number"] = pr_num
        ci_evidence.append(entry)

        status = str(sig.get("ci_status") or "").strip().lower()
        failed_tests = _to_int(sig.get("failed_tests"))
        flaky_tests = _to_int(sig.get("flaky_tests"))

        failed_tests_total += failed_tests
        flaky_tests_total += flaky_tests

        if _is_failed_ci_signal(status, failed_tests):
            failing_ci_pr_count += 1
            failing_ci_pr_numbers.add(pr_num)
        if "flake" in status or flaky_tests > 0:
            flaky_ci_pr_count += 1

        last_run = _parse_dt(sig.get("last_run"))
        if last_run is not None and (datetime.utcnow() - last_run).days > 7:
            stale_ci_signal_count += 1

    if failing_ci_pr_count:
        score += min(18, 8 + 4 * (failing_ci_pr_count - 1))
        drivers.append(f"{failing_ci_pr_count} linked PRs have failing CI checks")
    if failed_tests_total > 0:
        score += min(12, failed_tests_total)
        drivers.append(f"{failed_tests_total} failing tests across linked CI signals")
    if flaky_ci_pr_count:
        score += min(10, 3 + 2 * (flaky_ci_pr_count - 1))
        drivers.append(f"{flaky_ci_pr_count} linked PRs show flaky CI signals")
    if stale_ci_signal_count:
        score += min(8, stale_ci_signal_count * 2)
        drivers.append(f"{stale_ci_signal_count} linked PR CI signals are stale (>7 days)")

    issue_pr_links = list(extra_context.get("issue_pr_links") or []) if extra_context else []
    issue_to_related_prs: Dict[int, set[int]] = {}
    for link in issue_pr_links:
        issue_num = _to_int(link.get("issue_number"))
        if issue_num <= 0:
            continue
        related_nums = {
            _to_int(n)
            for n in (link.get("related_pr_numbers") or [])
            if _to_int(n) > 0
        }
        primary_pr_num = _to_int(link.get("primary_pr_number"))
        if primary_pr_num > 0:
            related_nums.add(primary_pr_num)
        if related_nums:
            issue_to_related_prs[issue_num] = related_nums

    subtasks_with_failing_linked_ci = 0
    linked_issue_pr_failures: List[Dict[str, Any]] = []
    for st in subtasks:
        issue_nums = {
            _to_int(n)
            for n in (st.github_issue_numbers or [])
            if _to_int(n) > 0
        }
        direct_pr_nums = {
            _to_int(n)
            for n in (st.github_pr_numbers or [])
            if _to_int(n) > 0
        }
        derived_pr_nums = set(direct_pr_nums)
        for issue_num in issue_nums:
            derived_pr_nums.update(issue_to_related_prs.get(issue_num, set()))
        if not derived_pr_nums:
            continue
        failing_related_prs = sorted(derived_pr_nums.intersection(failing_ci_pr_numbers))
        if not failing_related_prs:
            continue
        subtasks_with_failing_linked_ci += 1
        linked_issue_pr_failures.append(
            {
                "subtask": st.subtask,
                "status": st.status,
                "assignee": st.assignee,
                "issue_numbers": sorted(issue_nums),
                "linked_pr_numbers": sorted(derived_pr_nums),
                "failing_pr_numbers": failing_related_prs,
            }
        )

    if subtasks_with_failing_linked_ci:
        score += min(16, 6 + 3 * (subtasks_with_failing_linked_ci - 1))
        drivers.append(
            f"{subtasks_with_failing_linked_ci} subtasks are linked to issues/PRs with failing CI/tests"
        )
        evidence["issue_pr_ci_links"] = linked_issue_pr_failures

        risk_subtask_by_key = {_subtask_key(st): st for st in high_risk_subtasks}
        for st in subtasks:
            issue_nums = {
                _to_int(n)
                for n in (st.github_issue_numbers or [])
                if _to_int(n) > 0
            }
            direct_pr_nums = {
                _to_int(n)
                for n in (st.github_pr_numbers or [])
                if _to_int(n) > 0
            }
            derived_pr_nums = set(direct_pr_nums)
            for issue_num in issue_nums:
                derived_pr_nums.update(issue_to_related_prs.get(issue_num, set()))
            failing_related_prs = sorted(derived_pr_nums.intersection(failing_ci_pr_numbers))
            if not failing_related_prs:
                continue
            key = _subtask_key(st)
            if key not in risk_subtask_by_key:
                risk_subtask_by_key[key] = {
                    "subtask": st.subtask,
                    "status": st.status,
                    "assignee": st.assignee,
                    "estimated_completion_date": st.estimated_completion_date,
                    "notes": st.notes,
                    "reasons": [],
                }
            reasons = risk_subtask_by_key[key].get("reasons") or []
            if "linked_pr_ci_failed" not in reasons:
                reasons.append("linked_pr_ci_failed")
            risk_subtask_by_key[key]["reasons"] = reasons
        high_risk_subtasks = list(risk_subtask_by_key.values())

    contributor_high_risk_projects = int(extra_context.get("contributor_high_risk_projects", 0)) if extra_context else 0
    if contributor_high_risk_projects >= 2:
        score += 8
        drivers.append(
            f"Project Owner (Contributor) is attached to {contributor_high_risk_projects} high-risk projects"
        )

    score = min(100, int(score))
    if score >= 80:
        level = "CRITICAL"
    elif score >= 60:
        level = "HIGH"
    elif score >= 35:
        level = "MEDIUM"
    else:
        level = "LOW"

    if blocked_subtasks:
        recs.append("Unblock blocked subtasks first and assign clear owners for each blocker")
    if failing_ci_pr_count:
        recs.append("Fix failing CI checks and reduce failing tests on linked pull requests")
    if subtasks_with_failing_linked_ci:
        recs.append("Prioritize issue-to-PR chains with failing CI/tests for contributor follow-up")
    if stale_open_pr_count:
        recs.append("Escalate stale open PRs for review and merge decision")
    if stale_open_issue_count:
        recs.append("Triage stale issues and update ownership/action dates")
    if unfinished_subtasks >= 3:
        recs.append("Break remaining subtasks into owner-tagged milestones with deadlines")
    if not (project.project_owner_contributor or "").strip():
        recs.append("Assign a Project Owner (Contributor) for day-to-day execution follow-ups")
    if not recs:
        recs.append("Project is on track; continue routine monitoring")

    evidence["github_issues"] = github_issue_evidence
    evidence["github_prs"] = github_pr_evidence
    evidence["ci_signals"] = ci_evidence

    return RiskReport(
        project_id=project.project_id,
        project_name=project.project_name,
        risk_score=score,
        risk_level=level,
        risk_drivers=drivers,
        recommendations=recs,
        high_risk_subtasks=high_risk_subtasks,
        github_issue_evidence=github_issue_evidence,
        github_pr_evidence=github_pr_evidence,
        evidence_by_source=evidence,
        open_linked_issue_count=open_issue_count,
        open_linked_pr_count=open_pr_count,
        stale_open_issue_count=stale_open_issue_count,
        stale_open_pr_count=stale_open_pr_count,
        failing_ci_pr_count=failing_ci_pr_count,
        flaky_ci_pr_count=flaky_ci_pr_count,
        failed_tests_total=failed_tests_total,
        flaky_tests_total=flaky_tests_total,
        stale_ci_signal_count=stale_ci_signal_count,
        ci_evidence=ci_evidence,
        issue_pr_links=issue_pr_links,
    )
