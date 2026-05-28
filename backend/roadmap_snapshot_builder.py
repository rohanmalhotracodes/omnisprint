#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import os
import re
from contextlib import nullcontext
from pathlib import Path
from typing import Dict, List, Optional, Sequence


GITHUB_URL_RE = re.compile(r"https?://github\.com/[^\s,)\]]+", re.IGNORECASE)
ISSUE_URL_RE = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/issues/(\d+)", re.IGNORECASE)
PR_URL_RE = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/pull/?(?:s/)?(\d+)", re.IGNORECASE)
TARGET_REPO_SLUG = (
    f"{(os.getenv('GITHUB_OWNER') or 'oppia').strip().lower()}/"
    f"{(os.getenv('GITHUB_REPO') or 'oppia').strip().lower()}"
)
NON_PROJECT_TITLE_PHRASES = [
    "projects below are blocked on the other teams",
    "leads need to collaborate in order to get unblock these projects",
    "leads need to collaborate to unblock",
    "projects below are blocked",
]


def _clean(value: Optional[str]) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("nan", "none", "null"):
        return ""
    return text


def _normalize(text: str) -> str:
    s = _clean(text).lower()
    s = s.replace("\n", " ").replace("\r", " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _detect_header_row(rows: Sequence[Sequence[str]]) -> int:
    for idx, row in enumerate(rows):
        combined = " ".join(_normalize(cell) for cell in row if _clean(cell))
        if (
            "project description" in combined
            and "project owner" in combined
            and "planned completion date" in combined
            and "subtasks" in combined
        ):
            return idx
    raise ValueError("Could not find roadmap header row in CSV")


def _find_column_index(headers: Sequence[str], candidates: Sequence[str]) -> int:
    normalized_headers = [_normalize(h) for h in headers]
    normalized_candidates = [_normalize(c) for c in candidates if _normalize(c)]

    for candidate in normalized_candidates:
        for idx, header in enumerate(normalized_headers):
            if header == candidate:
                return idx

    for candidate in normalized_candidates:
        for idx, header in enumerate(normalized_headers):
            if candidate in header or header in candidate:
                return idx

    return -1


def _value_at(row: Sequence[str], idx: int) -> str:
    if idx < 0 or idx >= len(row):
        return ""
    return _clean(row[idx])


def _extract_project_name(project_description: str) -> str:
    lines = [ln.strip() for ln in str(project_description or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    return lines[0]


def _looks_like_non_project_heading(project_name: str) -> bool:
    text = _clean(project_name)
    if not text:
        return False
    low = text.lower()
    if any(phrase in low for phrase in NON_PROJECT_TITLE_PHRASES):
        return True
    if low.startswith("projects below"):
        return True
    words = re.findall(r"[a-z0-9]+", low)
    if len(words) >= 8 and ("need to" in low or "in order to" in low):
        return True
    return False


def _extract_github_links(cells: Sequence[str]) -> List[str]:
    urls: List[str] = []
    for cell in cells:
        urls.extend(GITHUB_URL_RE.findall(str(cell)))
    # Preserve order while de-duplicating.
    seen = set()
    out: List[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _extract_issue_pr_numbers(cells: Sequence[str]) -> tuple[List[int], List[int]]:
    issues: List[int] = []
    prs: List[int] = []
    for cell in cells:
        text = str(cell or "")
        for owner, repo, issue_num in ISSUE_URL_RE.findall(text):
            if f"{owner}/{repo}".lower() != TARGET_REPO_SLUG:
                continue
            issues.append(int(issue_num))
        for owner, repo, pr_num in PR_URL_RE.findall(text):
            if f"{owner}/{repo}".lower() != TARGET_REPO_SLUG:
                continue
            prs.append(int(pr_num))
    return sorted(set(issues)), sorted(set(prs))


def _project_id(project_name: str, project_counter: int) -> str:
    token = f"{project_name}|{project_counter}"
    digest = hashlib.md5(token.encode("utf-8")).hexdigest()[:10]
    return f"proj-{digest}"


def build_jsonl(input_csv: Path, output_jsonl: Path, links_output_jsonl: Optional[Path] = None) -> tuple[int, int]:
    raw_rows: List[List[str]] = []
    with input_csv.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            raw_rows.append([_clean(cell) for cell in row])

    header_idx = _detect_header_row(raw_rows)
    headers = raw_rows[header_idx]

    idx_project_description = _find_column_index(headers, ["project description", "project", "key goals"])
    idx_owner_lead = _find_column_index(headers, ["project owner leads", "project owner lead"])
    idx_owner_contributor = _find_column_index(headers, ["project owner contributor", "contributor"])
    idx_planned = _find_column_index(headers, ["planned completion date", "planned completion"])
    idx_subtask = _find_column_index(headers, ["subtasks", "subtask"])
    idx_estimated = _find_column_index(headers, ["est completion date", "estimated completion date"])
    idx_status = _find_column_index(headers, ["status", "state"])
    idx_assignee = _find_column_index(headers, ["assignee", "assigned to"])
    idx_notes = _find_column_index(headers, ["notes links for subtasks", "notes / links for subtasks", "notes"])
    idx_debugging_doc = _find_column_index(
        headers,
        ["link to active debugging doc", "debugging doc", "debugging document"],
    )

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    if links_output_jsonl is not None:
        links_output_jsonl.parent.mkdir(parents=True, exist_ok=True)

    current_project_id = ""
    current_project_name = ""
    project_counter = 0
    written = 0
    links_written = 0
    seen_records = set()
    seen_links = set()

    links_context = (
        links_output_jsonl.open("w", encoding="utf-8")
        if links_output_jsonl is not None
        else nullcontext()
    )
    with output_jsonl.open("w", encoding="utf-8") as out, links_context as links_out:
        for row in raw_rows[header_idx + 1 :]:
            # Normalize width.
            if len(row) < len(headers):
                row = row + [""] * (len(headers) - len(row))

            if not any(_clean(cell) for cell in row):
                continue

            project_description = _value_at(row, idx_project_description)
            project_name = _extract_project_name(project_description)
            if _looks_like_non_project_heading(project_name):
                project_name = ""
            owner_lead = _value_at(row, idx_owner_lead)
            owner_contributor = _value_at(row, idx_owner_contributor)
            planned_completion_date = _value_at(row, idx_planned)
            subtask = _value_at(row, idx_subtask)
            estimated_completion_date = _value_at(row, idx_estimated)
            status = _value_at(row, idx_status)
            assignee = _value_at(row, idx_assignee)
            notes = _value_at(row, idx_notes)
            debugging_doc_link = _value_at(row, idx_debugging_doc)

            starts_project = bool(project_name)
            if starts_project:
                project_counter += 1
                current_project_id = _project_id(project_name, project_counter)
                current_project_name = project_name
            if not current_project_id:
                continue

            github_links_list = _extract_github_links(row)
            github_links = ", ".join(github_links_list)
            issue_numbers, pr_numbers = _extract_issue_pr_numbers(row)
            record: Dict[str, str] = {
                "project_id": current_project_id,
                "project_name": project_name if starts_project else "",
                "project_description": project_description if starts_project else "",
                "project_owner_lead": owner_lead if starts_project else "",
                "project_owner_contributor": owner_contributor if starts_project else "",
                "planned_completion_date": planned_completion_date,
                "subtask": subtask,
                "estimated_completion_date": estimated_completion_date,
                "status": status,
                "assignee": assignee,
                "notes": notes,
                "github_links": github_links,
                "debugging_doc_link": debugging_doc_link if starts_project else "",
                "source_mode": "SNAPSHOT",
            }

            record_key = (
                record["project_id"],
                record["project_name"],
                record["project_description"],
                record["project_owner_lead"],
                record["project_owner_contributor"],
                record["planned_completion_date"],
                record["subtask"],
                record["estimated_completion_date"],
                record["status"],
                record["assignee"],
                record["notes"],
                record["github_links"],
                record["debugging_doc_link"],
            )
            if record_key in seen_records:
                continue
            seen_records.add(record_key)
            out.write(json.dumps(record, ensure_ascii=True) + "\n")
            written += 1

            if links_out is not None:
                for issue_num in issue_numbers:
                    link_record = {
                        "project_id": current_project_id,
                        "project_name": current_project_name,
                        "subtask": subtask,
                        "link_type": "issue",
                        "link_number": int(issue_num),
                        "source_mode": "SNAPSHOT",
                    }
                    link_key = (
                        link_record["project_id"],
                        link_record["subtask"],
                        link_record["link_type"],
                        int(link_record["link_number"]),
                    )
                    if link_key in seen_links:
                        continue
                    seen_links.add(link_key)
                    links_out.write(json.dumps(link_record, ensure_ascii=True) + "\n")
                    links_written += 1
                for pr_num in pr_numbers:
                    link_record = {
                        "project_id": current_project_id,
                        "project_name": current_project_name,
                        "subtask": subtask,
                        "link_type": "pr",
                        "link_number": int(pr_num),
                        "source_mode": "SNAPSHOT",
                    }
                    link_key = (
                        link_record["project_id"],
                        link_record["subtask"],
                        link_record["link_type"],
                        int(link_record["link_number"]),
                    )
                    if link_key in seen_links:
                        continue
                    seen_links.add(link_key)
                    links_out.write(json.dumps(link_record, ensure_ascii=True) + "\n")
                    links_written += 1

    return written, links_written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Coral JSONL roadmap snapshot from CSV export.")
    parser.add_argument("--input", required=True, help="Path to CSV file exported from Google Sheets")
    parser.add_argument("--output", required=True, help="Path to output JSONL file")
    parser.add_argument(
        "--links-output",
        required=False,
        default="",
        help="Optional path to output project GitHub links JSONL file",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_csv = Path(args.input).expanduser().resolve()
    output_jsonl = Path(args.output).expanduser().resolve()
    links_output_jsonl = Path(args.links_output).expanduser().resolve() if args.links_output else None

    if not input_csv.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_csv}")

    rows, link_rows = build_jsonl(input_csv, output_jsonl, links_output_jsonl)
    print(f"Wrote {rows} rows to {output_jsonl}")
    if links_output_jsonl is not None:
        print(f"Wrote {link_rows} project-link rows to {links_output_jsonl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
