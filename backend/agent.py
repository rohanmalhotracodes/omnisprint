from typing import Any, Dict, List

from .coral_client import CoralClient
from .reminder_generator import generate_reminders


def _load_reports() -> List[Dict[str, Any]]:
    # Reuse the same grouped project-level data pipeline used by REST endpoints.
    from .main import _build_project_reports

    return _build_project_reports(1200)


def handle_agent_query(question: str, coral: CoralClient) -> Dict[str, Any]:
    del coral
    q = (question or "").lower().strip()

    try:
        reports = _load_reports()
    except Exception as e:
        return {"error": f"Unable to query Coral/project pipeline: {e}"}

    if not reports:
        return {"answer": "No projects found in the connected planning source."}

    if "reminder" in q and ("need" in q or "which" in q):
        reminders = generate_reminders(reports, risk_threshold="HIGH")
        return {
            "answer": f"{len(reminders)} projects currently need reminder follow-up.",
            "reminders": reminders,
        }

    if "generate" in q and "reminder" in q:
        reminders = generate_reminders(reports, risk_threshold="HIGH")
        return {
            "answer": f"Generated {len(reminders)} copy-ready Google Chat reminders for high-risk projects.",
            "reminders": reminders,
        }

    if "contributors" in q and ("follow-up" in q or "follow up" in q or "need" in q):
        reminders = generate_reminders(reports, risk_threshold="HIGH")
        contribs = sorted(
            {
                (r.get("project_owner_contributor") or "(unassigned contributor)")
                for r in reminders
            }
        )
        return {
            "answer": f"{len(contribs)} contributors currently need follow-up.",
            "contributors": contribs,
        }

    if "owners" in q and ("follow-up" in q or "follow up" in q or "need" in q):
        reminders = generate_reminders(reports, risk_threshold="HIGH")
        owners = sorted(
            {
                (r.get("project_owner_lead") or "(unassigned lead)")
                for r in reminders
            }
        )
        return {
            "answer": f"{len(owners)} project leads currently need follow-up actions.",
            "owners": owners,
        }

    if "should not be bothered" in q or ("on track" in q and "contributors" in q):
        risky_ids = {r.get("project_id") for r in generate_reminders(reports, risk_threshold="HIGH")}
        on_track = sorted(
            {
                (p.get("project_owner_contributor") or "(unassigned contributor)")
                for p in reports
                if p.get("project_id") not in risky_ids
            }
        )
        return {
            "answer": f"{len(on_track)} contributors currently look on-track and do not need follow-up.",
            "contributors": on_track,
        }

    if ("owner lead" in q or "lead" in q) and ("most risky" in q or "highest" in q):
        lead_map: Dict[str, Dict[str, Any]] = {}
        for r in reports:
            lead = (r.get("project_owner_lead") or "").strip() or "(unassigned lead)"
            lead_map.setdefault(lead, {"owner_lead": lead, "high_risk_projects": 0, "max_risk": 0})
            if r.get("risk_level") in ("HIGH", "CRITICAL"):
                lead_map[lead]["high_risk_projects"] += 1
            lead_map[lead]["max_risk"] = max(lead_map[lead]["max_risk"], int(r.get("risk_score", 0)))

        ranked = sorted(
            list(lead_map.values()),
            key=lambda x: (x["high_risk_projects"], x["max_risk"]),
            reverse=True,
        )
        top = ranked[0]
        return {
            "answer": f"Owner lead with the most risky projects: {top['owner_lead']}",
            "leaderboard": ranked[:10],
        }

    if "message" in q and "highest-risk" in q:
        reminders = generate_reminders(reports, risk_threshold="LOW")
        if not reminders:
            return {"answer": "No reminder candidates found."}
        top = sorted(reminders, key=lambda r: int(r.get("risk_score", 0)), reverse=True)[0]
        return {
            "answer": f"Here is the message for the highest-risk project: {top.get('project_name')}",
            "reminder": top,
        }

    if "most at risk" in q or "most risky" in q or "most risky project" in q:
        top = sorted(reports, key=lambda r: int(r.get("risk_score", 0)), reverse=True)[0]
        return {
            "answer": f"Most at-risk project: {top.get('project_name')}",
            "project": top,
        }

    if "blocked" in q:
        blocked = [r for r in reports if int(r.get("blocked_subtasks", 0)) > 0]
        return {
            "answer": f"Found {len(blocked)} blocked projects.",
            "projects": blocked[:20],
        }

    if "owner" in q and ("highest" in q or "risk" in q):
        owner_scores: Dict[str, List[int]] = {}
        for r in reports:
            owner = (
                (r.get("project_owner_lead") or "").strip()
                or (r.get("project_owner_contributor") or "").strip()
                or "(unassigned)"
            )
            owner_scores.setdefault(owner, []).append(int(r.get("risk_score", 0)))
        ranked = sorted(
            [
                {"owner": k, "average_risk": int(sum(v) / len(v)), "project_count": len(v)}
                for k, v in owner_scores.items()
            ],
            key=lambda x: x["average_risk"],
            reverse=True,
        )
        top = ranked[0]
        return {
            "answer": f"Highest delivery risk owner: {top['owner']} (avg risk {top['average_risk']})",
            "owners": ranked[:10],
        }

    if "pr" in q and ("delay" in q or "delaying" in q or "block" in q):
        impacted = [r for r in reports if int(r.get("linked_pr_count", 0)) > 0]
        impacted_sorted = sorted(impacted, key=lambda p: int(p.get("risk_score", 0)), reverse=True)
        return {
            "answer": f"Found {len(impacted)} projects with linked PRs.",
            "projects": impacted_sorted[:10],
        }

    if "evidence" in q and "highest-risk" in q:
        top = sorted(reports, key=lambda r: int(r.get("risk_score", 0)), reverse=True)[0]
        return {
            "answer": f"Evidence for highest-risk project: {top.get('project_name')}",
            "project_name": top.get("project_name"),
            "risk_level": top.get("risk_level"),
            "risk_score": top.get("risk_score"),
            "risk_drivers": top.get("risk_drivers", []),
            "github_issue_evidence": top.get("github_issue_evidence", []),
            "github_pr_evidence": top.get("github_pr_evidence", []),
            "coral_query_flow_used": top.get("coral_query_flow_used", {}),
        }

    if "lead" in q or "fix first" in q or "what should" in q:
        top = sorted(reports, key=lambda r: int(r.get("risk_score", 0)), reverse=True)[:3]
        return {
            "answer": "Top engineering lead priorities are the highest-risk projects below.",
            "priorities": [
                {
                    "project_name": r.get("project_name"),
                    "risk_score": r.get("risk_score"),
                    "risk_level": r.get("risk_level"),
                    "recommended_actions": r.get("recommendations", []),
                }
                for r in top
            ],
        }

    return {
        "answer": "Try: 'Which projects are most at risk?', 'Which owners need follow-up?', 'Generate Google Chat reminders for high-risk projects.', 'Which contributors should not be bothered because they are on track?', or 'Show evidence for the highest-risk project.'"
    }
