import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .models import Project, Subtask


_ISSUE_RE = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/issues/(\d+)", re.IGNORECASE)
_PR_RE = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/pull/?(?:s/)?(\d+)", re.IGNORECASE)
_TARGET_REPO_SLUG = (
    f"{(os.getenv('GITHUB_OWNER') or 'oppia').strip().lower()}/"
    f"{(os.getenv('GITHUB_REPO') or 'oppia').strip().lower()}"
)
_NON_PROJECT_TITLE_PHRASES = [
    "projects below are blocked on the other teams",
    "leads need to collaborate in order to get unblock these projects",
    "leads need to collaborate to unblock",
    "projects below are blocked",
    "other teams",
    "for reference only",
]
_GEMINI_TITLE_DECISION_CACHE: Dict[str, bool] = {}
_GEMINI_TITLE_CALL_COUNT = 0
_GEMINI_TITLE_MAX_CALLS = max(0, int(os.getenv("OMNISPRINT_TITLE_GEMINI_MAX_CALLS", "2") or "2"))


def _clean(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", "null"):
        return ""
    return s


def _to_bool(val: Any) -> bool:
    text = _clean(val).lower()
    return text in ("1", "true", "yes", "on")


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


def _row_signature(row: Dict[str, Any]) -> Tuple[Tuple[str, str], ...]:
    items: List[Tuple[str, str]] = []
    for k, v in row.items():
        items.append((_normalize_key(str(k)), _clean(v)))
    return tuple(sorted(items))


def _subtask_signature(st: Subtask) -> Tuple[Any, ...]:
    issue_nums: List[int] = []
    for n in (st.github_issue_numbers or []):
        try:
            val = int(n)
        except Exception:
            continue
        if val > 0:
            issue_nums.append(val)
    pr_nums: List[int] = []
    for n in (st.github_pr_numbers or []):
        try:
            val = int(n)
        except Exception:
            continue
        if val > 0:
            pr_nums.append(val)
    return (
        _clean(st.subtask),
        _clean(st.status),
        _clean(st.assignee),
        _clean(st.estimated_completion_date),
        _clean(st.notes),
        tuple(sorted(set(issue_nums))),
        tuple(sorted(set(pr_nums))),
    )


def _collect_issue_pr_numbers(row: Dict[str, Any]) -> tuple[list[int], list[int]]:
    issues: List[int] = []
    prs: List[int] = []
    for v in row.values():
        if v is None:
            continue
        text = str(v)
        for owner, repo, issue_num in _ISSUE_RE.findall(text):
            if f"{owner}/{repo}".lower() != _TARGET_REPO_SLUG:
                continue
            try:
                val = int(issue_num)
            except Exception:
                continue
            if val > 0:
                issues.append(val)
        for owner, repo, pr_num in _PR_RE.findall(text):
            if f"{owner}/{repo}".lower() != _TARGET_REPO_SLUG:
                continue
            try:
                val = int(pr_num)
            except Exception:
                continue
            if val > 0:
                prs.append(val)
    return sorted(set(issues)), sorted(set(prs))


def _looks_like_github_work_item_reference(text: str) -> bool:
    value = _clean(text)
    if not value:
        return False
    issue_match = _ISSUE_RE.search(value)
    if issue_match and f"{issue_match.group(1)}/{issue_match.group(2)}".lower() == _TARGET_REPO_SLUG:
        return True
    pr_match = _PR_RE.search(value)
    if pr_match and f"{pr_match.group(1)}/{pr_match.group(2)}".lower() == _TARGET_REPO_SLUG:
        return True
    return False


def _looks_like_non_project_heading_title(title: str) -> bool:
    text = _clean(title)
    if not text:
        return False
    low = text.lower()
    if _looks_like_github_work_item_reference(text):
        return True
    if any(phrase in low for phrase in _NON_PROJECT_TITLE_PHRASES):
        return True
    if low.startswith("projects below"):
        return True
    words = re.findall(r"[a-z0-9]+", low)
    word_count = len(words)
    sentence_like = (
        ("need to" in low or "should " in low or "please " in low or "in order to" in low)
        and word_count >= 8
    )
    blocked_collab = (
        word_count >= 7
        and "blocked" in low
        and ("team" in low or "teams" in low)
        and ("lead" in low or "leads" in low or "collaborate" in low)
    )
    return sentence_like or blocked_collab


def _is_ambiguous_project_title(
    title: str,
    description: str,
    lead: str,
    contributor: str,
    planned: str,
    row: Dict[str, Any],
) -> bool:
    text = _clean(title)
    if not text:
        return False
    low = text.lower()
    words = re.findall(r"[a-z0-9]+", low)
    word_count = len(words)
    if word_count < 7:
        return False
    has_owner_or_date = any([_clean(lead), _clean(contributor), _clean(planned)])
    if has_owner_or_date:
        return False
    issue_nums, pr_nums = _collect_issue_pr_numbers(row)
    has_github_signal = bool(issue_nums or pr_nums)
    if has_github_signal:
        return False
    # Long sentence-like titles with little project metadata are ambiguous.
    sentence_markers = ["need to", "should", "please", "in order to", "below", "blocked", "teams"]
    marker_hits = sum(1 for m in sentence_markers if m in low)
    if marker_hits >= 2:
        return True
    if _clean(description) and _clean(description) != text:
        return False
    return word_count >= 11


def _gemini_title_is_real_project(
    title: str,
    description: str,
    lead: str,
    contributor: str,
    planned: str,
) -> Optional[bool]:
    global _GEMINI_TITLE_CALL_COUNT
    enabled = _to_bool(os.getenv("OMNISPRINT_TITLE_GEMINI_ENABLED", "1"))
    api_key = _clean(os.getenv("GEMINI_API_KEY"))
    if not enabled or not api_key:
        return None
    if _GEMINI_TITLE_CALL_COUNT >= _GEMINI_TITLE_MAX_CALLS:
        return None

    model = _clean(os.getenv("GEMINI_MODEL")) or "gemini-2.5-flash"
    cache_key = " | ".join([_clean(title), _clean(description), _clean(lead), _clean(contributor), _clean(planned)])
    if cache_key in _GEMINI_TITLE_DECISION_CACHE:
        return _GEMINI_TITLE_DECISION_CACHE[cache_key]

    prompt = (
        "Classify whether this row title is a real software project title or a section/header sentence. "
        "Return ONLY JSON: {\"is_project\": true|false}. "
        f"title={_clean(title)!r}; description={_clean(description)!r}; "
        f"owner_lead={_clean(lead)!r}; owner_contributor={_clean(contributor)!r}; planned_date={_clean(planned)!r}"
    )
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        try:
            cfg = types.GenerateContentConfig(
                max_output_tokens=20,
                temperature=0.0,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            )
        except Exception:
            cfg = types.GenerateContentConfig(
                max_output_tokens=20,
                temperature=0.0,
            )
        _GEMINI_TITLE_CALL_COUNT += 1
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=cfg,
        )
        text = _clean(getattr(resp, "text", ""))
        if not text:
            return None
        parsed = None
        try:
            parsed = json.loads(text)
        except Exception:
            if "```" in text:
                for block in text.split("```"):
                    candidate = block.strip()
                    if candidate.startswith("json"):
                        candidate = candidate[4:].strip()
                    try:
                        parsed = json.loads(candidate)
                        break
                    except Exception:
                        continue
        if not isinstance(parsed, dict):
            return None
        decision = bool(parsed.get("is_project"))
        _GEMINI_TITLE_DECISION_CACHE[cache_key] = decision
        return decision
    except Exception:
        return None


def _should_start_project_for_title(
    title: str,
    description: str,
    lead: str,
    contributor: str,
    planned: str,
    row: Dict[str, Any],
) -> bool:
    if not _clean(title):
        return False
    if _looks_like_non_project_heading_title(title):
        return False
    if _is_ambiguous_project_title(title, description, lead, contributor, planned, row):
        llm = _gemini_title_is_real_project(title, description, lead, contributor, planned)
        if llm is not None:
            return llm
    return True


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

        has_strong_project_signal = any([description, lead, contributor, debugging])
        title_is_project_candidate = _should_start_project_for_title(
            explicit_title,
            description,
            lead,
            contributor,
            planned,
            row,
        ) if explicit_title else False

        if explicit_title and not title_is_project_candidate:
            # Section/header rows should not become projects.
            # If the row still carries subtask evidence, keep it under current project.
            separator_subtask = _build_subtask(row)
            if current is not None and separator_subtask is not None:
                current.raw_project_rows.append(row)
                current.subtasks.append(separator_subtask)
            continue

        starts_new_project = False
        if title_is_project_candidate:
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

    # Merge accidental duplicate project blocks with the same project_id.
    merged_projects: Dict[str, Project] = {}
    ordered_project_ids: List[str] = []
    for project in projects:
        pid = _clean(project.project_id)
        if not pid:
            continue
        existing = merged_projects.get(pid)
        if existing is None:
            merged_projects[pid] = project
            ordered_project_ids.append(pid)
            continue

        # Keep first non-empty project-level values.
        if not _clean(existing.project_name) and _clean(project.project_name):
            existing.project_name = project.project_name
        if not _clean(existing.project_description) and _clean(project.project_description):
            existing.project_description = project.project_description
        if not _clean(existing.project_owner_lead) and _clean(project.project_owner_lead):
            existing.project_owner_lead = project.project_owner_lead
        if not _clean(existing.project_owner_contributor) and _clean(project.project_owner_contributor):
            existing.project_owner_contributor = project.project_owner_contributor
        if not _clean(existing.planned_completion_date) and _clean(project.planned_completion_date):
            existing.planned_completion_date = project.planned_completion_date
        if not _clean(existing.debugging_doc_link) and _clean(project.debugging_doc_link):
            existing.debugging_doc_link = project.debugging_doc_link
        if not _clean(existing.source_mode) and _clean(project.source_mode):
            existing.source_mode = project.source_mode

        existing.raw_project_rows.extend(project.raw_project_rows or [])
        existing.subtasks.extend(project.subtasks or [])

    projects = [merged_projects[pid] for pid in ordered_project_ids]

    for project in projects:
        # Deduplicate raw rows within each project.
        seen_rows = set()
        uniq_rows: List[Dict[str, Any]] = []
        for raw_row in project.raw_project_rows or []:
            sig = _row_signature(raw_row)
            if sig in seen_rows:
                continue
            seen_rows.add(sig)
            uniq_rows.append(raw_row)
        project.raw_project_rows = uniq_rows

        # Deduplicate subtasks within each project.
        seen_subtasks = set()
        uniq_subtasks: List[Subtask] = []
        for subtask in project.subtasks or []:
            sig = _subtask_signature(subtask)
            if sig in seen_subtasks:
                continue
            seen_subtasks.add(sig)
            uniq_subtasks.append(subtask)
        project.subtasks = uniq_subtasks

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
