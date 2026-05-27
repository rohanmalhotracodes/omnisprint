#!/usr/bin/env python3
import argparse
import csv
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


def _clean(value: Optional[str]) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("nan", "none", "null"):
        return ""
    return text


def _normalize(text: str) -> str:
    s = _clean(text).lower().replace("\n", " ").replace("\r", " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _split_multi(value: str) -> List[str]:
    text = _clean(value)
    if not text:
        return []
    normalized = re.sub(r"\s+(and|&)\s+", "+", text, flags=re.IGNORECASE)
    normalized = normalized.replace("/", "+").replace("|", "+").replace(";", "+")
    parts = re.split(r"\+|,", normalized)
    return [p.strip() for p in parts if p.strip()]


def _extract_email(value: str) -> Optional[str]:
    if not value:
        return None
    m = EMAIL_RE.search(value)
    if not m:
        return None
    return m.group(0).strip()


def _detect_header_row(rows: Sequence[Sequence[str]]) -> int:
    for idx, row in enumerate(rows):
        joined = " ".join(_normalize(cell) for cell in row if _clean(cell))
        if "email" in joined and any(token in joined for token in ("name", "contributor", "owner", "member")):
            return idx
    # Fallback to first row.
    return 0


def _find_column_index(headers: Sequence[str], candidates: Sequence[str]) -> int:
    nh = [_normalize(h) for h in headers]
    nc = [_normalize(c) for c in candidates if _normalize(c)]

    for candidate in nc:
        for idx, header in enumerate(nh):
            if header == candidate:
                return idx

    for candidate in nc:
        for idx, header in enumerate(nh):
            if candidate in header or header in candidate:
                return idx
    return -1


def _value_at(row: Sequence[str], idx: int) -> str:
    if idx < 0 or idx >= len(row):
        return ""
    return _clean(row[idx])


def _zip_names_emails(raw_name: str, raw_email: str) -> List[Tuple[str, str]]:
    names = _split_multi(raw_name)
    emails = []
    for chunk in _split_multi(raw_email):
        em = _extract_email(chunk)
        if em:
            emails.append(em)

    if not names and not emails:
        return []
    if names and not emails:
        return [(name, "") for name in names]
    if emails and not names:
        return [("", email) for email in emails]

    # Pair one-to-one when lengths match; otherwise reuse first email.
    if len(names) == len(emails):
        return list(zip(names, emails))
    if len(emails) == 1:
        return [(name, emails[0]) for name in names]
    return list(zip(names, emails[: len(names)]))


def build_jsonl(input_csv: Path, output_jsonl: Path) -> int:
    rows: List[List[str]] = []
    with input_csv.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append([_clean(c) for c in row])

    if not rows:
        output_jsonl.write_text("", encoding="utf-8")
        return 0

    header_idx = _detect_header_row(rows)
    headers = rows[header_idx]

    idx_name = _find_column_index(
        headers,
        [
            "project owner contributor",
            "contributor",
            "name",
            "member name",
            "owner",
            "person",
        ],
    )
    idx_email = _find_column_index(
        headers,
        [
            "email",
            "contributor email",
            "project owner contributor email",
            "mail",
            "email address",
        ],
    )
    idx_team = _find_column_index(headers, ["team", "group"])
    idx_role = _find_column_index(headers, ["role", "responsibility", "ownership"])
    idx_github = _find_column_index(headers, ["github handle", "github", "github username", "username"])

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    count = 0

    with output_jsonl.open("w", encoding="utf-8") as out:
        for row in rows[header_idx + 1 :]:
            if len(row) < len(headers):
                row = row + [""] * (len(headers) - len(row))

            raw_name = _value_at(row, idx_name)
            raw_email = _value_at(row, idx_email)
            team = _value_at(row, idx_team)
            role = _value_at(row, idx_role)
            github_handle = _value_at(row, idx_github)

            # Fallback if no dedicated email column: scan row for any email.
            if not raw_email:
                for cell in row:
                    m = _extract_email(cell)
                    if m:
                        raw_email = m
                        break

            pairs = _zip_names_emails(raw_name, raw_email)
            if not pairs and (raw_name or raw_email):
                pairs = [(raw_name, raw_email)]

            for name, email in pairs:
                clean_name = _clean(name)
                clean_email = _extract_email(email or "") or ""
                if not clean_name and not clean_email:
                    continue

                dedupe_key = (_normalize(clean_name), clean_email.lower())
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                record: Dict[str, str] = {
                    "name": clean_name,
                    "team": team,
                    "role": role,
                    "github_handle": github_handle,
                    "email": clean_email,
                }
                out.write(json.dumps(record, ensure_ascii=True) + "\n")
                count += 1

    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build team member JSONL snapshot from CSV export.")
    parser.add_argument("--input", required=True, help="Path to team CSV")
    parser.add_argument("--output", required=True, help="Path to output JSONL")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_csv = Path(args.input).expanduser().resolve()
    output_jsonl = Path(args.output).expanduser().resolve()

    if not input_csv.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_csv}")

    rows = build_jsonl(input_csv, output_jsonl)
    print(f"Wrote {rows} team rows to {output_jsonl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
