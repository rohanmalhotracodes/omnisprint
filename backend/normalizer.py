import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from .models import Project, Subtask


_ISSUE_RE = re.compile(r"github\.com/[^/\s]+/[^/\s]+/issues/(\d+)", re.IGNORECASE)
_PR_RE = re.compile(r"github\.com/[^/\s]+/[^/\s]+/pull/?(?:s/)?(\d+)", re.IGNORECASE)


def _clean(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", "null"):
        return ""
    return s


def _extract_numbers_from_value(val: Any, pattern: re.Pattern[str]) -> List[int]:
    if val is None:
        return []
    return [int(m) for m in pattern.findall(str(val))]


def _normalize_key(k: str) -> str:
    if not k:
        return ""
    s = str(k).lower().strip()
    s = re.sub(r"[\n\r]+", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _normalized_items(row: Dict[str, Any]) -> List[Tuple[str, str, Any]]:
    return [(k, _normalize_key(k), v) for k, v in row.items()]


def _find_field(row: Dict[str, Any], candidates: List[str]) -> Any:
    items = _normalized_items(row)
    normalized_candidates = [_normalize_key(c) for c in candidates if _normalize_key(c)]

    # Exact key match (prefer non-empty values).
    for candidate in normalized_candidates:
        for _, nk, v in items:
            if nk == candidate and _clean(v):
                return v
    for candidate in normalized_candidates:
        for _, nk, v in items:
            if nk == candidate:
                return v

    # Partial match handles verbose Google Sheet headers.
    for candidate in normalized_candidates:
        for _, nk, v in items:
            if not nk:
                continue
            if candidate in nk or nk in candidate:
                if _clean(v):
                    return v
    for candidate in normalized_candidates:
        for _, nk, v in items:
            if not nk:
                continue
            if candidate in nk or nk in candidate:
                return v

    return None


def _project_id_from_name(name: str, salt: str = "") -> str:
    digest = hashlib.md5(f"{name}|{salt}".encode("utf-8")).hexdigest()[:10]
    return f"proj-{digest}"


def _collect_issue_pr_numbers(row: Dict[str, Any]) -> tuple[list[int], list[int]]:
    issues: List[int] = []
    prs: List[int] = []
    for v in row.values():
        issues.extend(_extract_numbers_from_value(v, _ISSUE_RE))
        prs.extend(_extract_numbers_from_value(v, _PR_RE))
    return sorted(set(issues)), sorted(set(prs))


def _guess_project_title(explicit_title: str, description: str) -> str:
    title = _clean(explicit_title)
    if title:
        return title.splitlines()[0].strip()
    desc = _clean(description)
    if not desc:
        return "-"
    return desc.splitlines()[0].strip() or "-"


def _is_instruction_row(row: Dict[str, Any]) -> bool:
    combined = " ".join(_clean(v).lower() for v in row.values())
    return (
        "project description" in combined
        and "project owner" in combined
        and "planned completion date" in combined
        and "subtasks" in combined
    )


def _is_legacy_flattened_row(row: Dict[str, Any]) -> bool:
    keys = {_normalize_key(k) for k in row.keys()}
    return "project_name" in keys or "project id" in keys or "subtask" in keys


def _build_subtask(row: Dict[str, Any]) -> Optional[Subtask]:
    subtask = _clean(
        _find_field(
            row,
            [
                "subtasks",
                "subtask",
                "sub tasks",
                "task",
                "notes / links for subtasks",
            ],
        )
    )
    status = _clean(_find_field(row, ["status", "state"]))
    assignee = _clean(_find_field(row, ["assignee", "assigned to", "assigned"]))
    estimated = _clean(
        _find_field(
            row,
            [
                "est completion date",
                "est. completion date",
                "estimated completion date",
                "estimated_completion_date",
                "estimated date",
            ],
        )
    )
    notes = _clean(
        _find_field(
            row,
            [
                "notes / links for subtasks",
                "notes",
                "notes links",
                "links",
                "comments",
            ],
        )
    )

    issue_nums, pr_nums = _collect_issue_pr_numbers(row)
    has_any_signal = any([subtask, status, assignee, estimated, notes, issue_nums, pr_nums])
    if not has_any_signal:
        return None

    return Subtask(
        subtask=subtask or None,
        status=status or None,
        assignee=assignee or None,
        estimated_completion_date=estimated or None,
        notes=notes or None,
        github_issue_numbers=issue_nums,
        github_pr_numbers=pr_nums,
        raw_row=row,
    )


def group_roadmap_rows(raw_rows: List[Dict[str, Any]]) -> List[Project]:
    projects: List[Project] = []
    current: Optional[Project] = None
    legacy_mode: Optional[bool] = None

    for idx, raw in enumerate(raw_rows):
        row = {k: v for k, v in raw.items()}
        if not row:
            continue
        if _is_instruction_row(row):
            continue

        if legacy_mode is None:
            legacy_mode = _is_legacy_flattened_row(row)

        explicit_title = _clean(
            _find_field(
                row,
                [
                    "project",
                    "project title",
                    "project name",
                    "project_name",
                    "key goals",
                    "goal",
                    "target",
                ],
            )
        )
        description = _clean(
            _find_field(
                row,
                [
                    "project description",
                    "description",
                    "project_description",
                    "impact",
                ],
            )
        )
        lead = _clean(
            _find_field(
                row,
                [
                    "project owner leads",
                    "project owner lead",
                    "project_owner_lead",
                    "owner leads",
                    "lead",
                ],
            )
        )
        contributor = _clean(
            _find_field(
                row,
                [
                    "project owner contributor",
                    "project_owner_contributor",
                    "owner contributor",
                    "contributor",
                ],
            )
        )
        planned = _clean(
            _find_field(
                row,
                [
                    "planned completion date",
                    "planned completion",
                    "planned_date",
                    "planned",
                ],
            )
        )
        debugging = _clean(
            _find_field(
                row,
                [
                    "link to active debugging doc",
                    "debugging_doc_link",
                    "debugging doc",
                    "debugging link",
                ],
            )
        )
        source_mode = _clean(_find_field(row, ["source_mode"]))

        has_project_level_signal = any([description, lead, contributor, planned, debugging])
        has_strong_project_signal = any([description, lead, contributor, debugging])
        starts_new_project = False
        if explicit_title:
            if current is None:
                starts_new_project = True
            elif legacy_mode:
                # Legacy flattened snapshot may carry planned dates in subtask
                # rows, so use stronger project-level anchors for boundaries.
                starts_new_project = has_strong_project_signal
            else:
                # Raw sheet mode: any non-empty project title starts a new project.
                starts_new_project = True

        if starts_new_project:
            if current is not None:
                projects.append(current)
            project_name = _guess_project_title(explicit_title, description)
            explicit_id = _clean(_find_field(row, ["project_id", "id"]))
            project_id = explicit_id or _project_id_from_name(project_name, str(idx))
            current = Project(
                project_id=project_id,
                project_name=project_name,
                project_description=description or project_name,
                project_owner_lead=lead or None,
                project_owner_contributor=contributor or None,
                planned_completion_date=planned or None,
                debugging_doc_link=debugging or None,
                source_mode=source_mode or "LIVE",
            )
        elif current is None:
            # Ignore noise rows before the first detected project.
            continue

        if current is None:
            continue

        # Carry project-level context forward.
        if not current.project_description and description:
            current.project_description = description
        if not current.project_owner_lead and lead:
            current.project_owner_lead = lead
        if not current.project_owner_contributor and contributor:
            current.project_owner_contributor = contributor
        if not current.planned_completion_date and planned:
            current.planned_completion_date = planned
        if not current.debugging_doc_link and debugging:
            current.debugging_doc_link = debugging
        if source_mode:
            current.source_mode = source_mode

        current.raw_project_rows.append(row)
        subtask = _build_subtask(row)
        if subtask is not None:
            current.subtasks.append(subtask)

    if current is not None:
        projects.append(current)

    for project in projects:
        all_issue_numbers: List[int] = []
        all_pr_numbers: List[int] = []
        for subtask in project.subtasks:
            all_issue_numbers.extend(subtask.github_issue_numbers or [])
            all_pr_numbers.extend(subtask.github_pr_numbers or [])
        for raw_row in project.raw_project_rows:
            iss, prs = _collect_issue_pr_numbers(raw_row)
            all_issue_numbers.extend(iss)
            all_pr_numbers.extend(prs)
        project.all_github_issue_numbers = sorted(set(all_issue_numbers))
        project.all_github_pr_numbers = sorted(set(all_pr_numbers))

    return projects


def normalize_row(raw: Dict[str, Any], fallback_id_prefix: str = "proj") -> Project:
    grouped = group_roadmap_rows([raw])
    if grouped:
        return grouped[0]
    name = _clean(_find_field(raw, ["project", "project_name", "project name"])) or "-"
    return Project(
        project_id=_project_id_from_name(name or fallback_id_prefix),
        project_name=name or "-",
        raw_project_rows=[raw],
    )
