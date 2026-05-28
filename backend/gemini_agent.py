import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .agent_tools import TOOL_REGISTRY


SYSTEM_INSTRUCTION = """
You are OmniSprint Agent, an AI project delivery assistant for software teams.

You help engineering leads understand sprint/project health, recent engineering activity, delivery risk, possible regressions, and follow-up actions.

You do not directly access GitHub, Google Sheets, files, or APIs.
You can only call the tools provided to you.
Every tool retrieves data through Coral or through OmniSprint's Coral-backed backend.

When answering, use evidence returned by tools.
Do not invent project names, owners, PRs, issues, commits, dates, or logs.

When asked about regressions or failed tests:
- never claim certainty without explicit evidence
- say "likely suspect" or "possible cause"
- include confidence level
- recommend verification steps

When asked to draft reminders or emails:
- only generate for HIGH/CRITICAL risk projects unless user explicitly asks otherwise
- prefer concise, professional messages

Always produce concise, action-oriented answers.
Do not include internal project IDs (for example "proj-abc123") in user-facing responses unless explicitly requested.
""".strip()

FINAL_SCHEMA_PROMPT = """
Return ONLY valid JSON with this exact shape:
{
  "answer": "string",
  "confidence": "LOW|MEDIUM|HIGH",
  "evidence_summary": "string",
  "recommended_actions": ["string"],
  "reminder": null | object,
  "email_draft": null | object
}
Do NOT ask the user to ask another question.
Do NOT return generic readiness text like "I'm ready to help".
Answer the user's actual question using tool evidence.
""".strip()


_RESOLVED_GEMINI_MODEL: Optional[str] = None


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("none", "null", "nan"):
        return ""
    return text


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _max_tool_calls() -> int:
    return max(1, min(12, _safe_int(os.getenv("OMNISPRINT_AGENT_MAX_TOOL_CALLS", "5"), 5)))


def _error_text(exc: Exception) -> str:
    return _clean(str(exc))[:800]


def _strip_internal_project_ids(text: Any) -> str:
    value = _clean(text)
    if not value:
        return ""

    value = re.sub(r"\s*\(\s*proj-[a-z0-9]{6,}\s*\)", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\bproject\s*id\s*[:#]?\s*proj-[a-z0-9]{6,}\b", "project", value, flags=re.IGNORECASE)
    value = re.sub(r"\bproj-[a-z0-9]{6,}\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\(\s*\)", "", value)
    value = re.sub(r"\s+,", ",", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip()


def _is_placeholder_answer(text: str) -> bool:
    t = _clean(text).lower()
    if not t:
        return True
    placeholder_signals = [
        "i'm ready to help",
        "i am ready to help",
        "please ask your question",
        "respond in the requested json format",
        "how can i help",
        "please provide your question",
        "ready to assist",
    ]
    return any(signal in t for signal in placeholder_signals)


def _model_candidates(configured_model: str) -> List[str]:
    configured = _clean(configured_model)
    candidates: List[str] = []
    if configured:
        candidates.append(configured)
        if configured.startswith("models/"):
            candidates.append(configured[len("models/") :])
        else:
            candidates.append(f"models/{configured}")
    candidates.extend(
        [
            "gemini-2.5-flash",
            "models/gemini-2.5-flash",
            "gemini-1.5-flash-latest",
            "models/gemini-1.5-flash-latest",
            "gemini-1.5-flash",
        ]
    )
    out: List[str] = []
    seen = set()
    for item in candidates:
        text = _clean(item)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _is_model_not_supported_error(exc: Exception) -> bool:
    text = _error_text(exc).lower()
    return (
        "not found for api version" in text
        or "is not found" in text
        or "not supported for generatecontent" in text
        or "404" in text and "model" in text
        or "unknown model" in text
    )


def _fallback_used_response(
    answer: str,
    confidence: str,
    tool_calls: List[Dict[str, Any]],
    evidence_summary: str,
    recommended_actions: List[str],
    reminder: Optional[Dict[str, Any]] = None,
    email_draft: Optional[Dict[str, Any]] = None,
    fallback_reason: Optional[str] = None,
) -> Dict[str, Any]:
    cleaned_actions = [_strip_internal_project_ids(item) for item in (recommended_actions or [])]
    cleaned_actions = [item for item in cleaned_actions if item]
    payload = {
        "answer": _strip_internal_project_ids(answer),
        "confidence": confidence,
        "tool_calls": tool_calls,
        "evidence_summary": _strip_internal_project_ids(evidence_summary),
        "recommended_actions": cleaned_actions,
        "reminder": reminder,
        "email_draft": email_draft,
        "used_gemini": False,
        "fallback_used": True,
    }
    if _clean(fallback_reason):
        payload["fallback_reason"] = _clean(fallback_reason)
    return payload


def _tool_trace_entry(name: str, arguments: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": name,
        "arguments": arguments,
        "status": _clean(result.get("status")) or "unknown",
        "summary": _clean(result.get("summary")) or "",
        "coral_sources_used": list(result.get("coral_sources_used") or []),
    }


def _execute_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    fn = TOOL_REGISTRY.get(name)
    if not fn:
        return {
            "status": "error",
            "summary": f"Unknown tool: {name}",
            "coral_sources_used": [],
            "data": [],
        }
    try:
        if not isinstance(arguments, dict):
            arguments = {}
        return fn(**arguments)
    except TypeError as e:
        return {
            "status": "error",
            "summary": f"Tool arguments invalid for {name}: {e}",
            "coral_sources_used": [],
            "data": [],
        }
    except Exception:
        return {
            "status": "error",
            "summary": f"Tool execution failed for {name}.",
            "coral_sources_used": [],
            "data": [],
        }


def _recommendations_from_tool_data(result: Dict[str, Any]) -> List[str]:
    recs: List[str] = []
    data = result.get("data")
    if isinstance(data, dict):
        for key in ("recommended_actions", "recommendations"):
            value = data.get(key)
            if isinstance(value, list):
                for item in value:
                    text = _clean(item)
                    if text:
                        recs.append(text)
    elif isinstance(data, list):
        for row in data[:5]:
            if not isinstance(row, dict):
                continue
            for key in ("recommended_actions", "recommendations"):
                value = row.get(key)
                if isinstance(value, list):
                    for item in value:
                        text = _clean(item)
                        if text:
                            recs.append(text)
    # preserve order and dedupe
    out: List[str] = []
    seen = set()
    for item in recs:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _extract_reminder_and_email(tool_name: str, result: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    data = result.get("data")
    reminder = None
    email_draft = None
    if tool_name == "generate_project_reminder" and isinstance(data, dict):
        if isinstance(data.get("reminder"), dict):
            reminder = data.get("reminder")
        if _clean(data.get("email_subject")) or _clean(data.get("email_body")) or _clean(data.get("mailto_url")):
            email_draft = {
                "subject": data.get("email_subject"),
                "body": data.get("email_body"),
                "mailto_url": data.get("mailto_url"),
                "to": data.get("contributor_email"),
            }
    if tool_name == "get_reminder_candidates":
        rows = data if isinstance(data, list) else []
        if rows:
            first = rows[0]
            if isinstance(first, dict):
                reminder = first
                email_draft = {
                    "subject": first.get("email_subject"),
                    "body": first.get("email_body"),
                    "mailto_url": first.get("mailto_url"),
                    "to": first.get("contributor_email"),
                }
    return reminder, email_draft


def _fallback_tool_for_question(question: str) -> Tuple[str, Dict[str, Any]]:
    q = _clean(question).lower()
    if any(k in q for k in ("latest", "recent", "catch up", "catch-up", "changed", "activity")):
        return "get_latest_activity_summary", {"limit": 10}
    if any(k in q for k in ("commit", "regression", "broke", "failure", "failed test", "failing test", "ci")):
        return "find_possible_regression_sources", {"project_ref": None, "lookback_days": 14}
    if any(k in q for k in ("risk", "attention", "slipping", "highest risk", "at risk", "at-risk")):
        return "get_projects_summary", {"risk_filter": "HIGH_OR_CRITICAL", "limit": 20}
    if any(k in q for k in ("reminder", "email", "google chat", "draft")):
        return "get_reminder_candidates", {"owner_name": None, "limit": 10}
    if any(k in q for k in ("owner", "lead", "overloaded", "follow-up", "follow up")):
        return "get_owner_risk_summary", {"owner_name": None}
    if any(k in q for k in ("technical", "coral", "source", "query")):
        return "get_technical_evidence", {"include_queries": True}
    return "get_projects_summary", {"risk_filter": "ALL", "limit": 20}


def _build_fallback_answer(question: str, tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    status = _clean(result.get("status"))
    summary = _clean(result.get("summary"))
    data = result.get("data")
    recs = _recommendations_from_tool_data(result)
    reminder, email_draft = _extract_reminder_and_email(tool_name, result)

    if status in ("error", "unavailable"):
        answer = summary or "Unable to complete the requested analysis right now."
        confidence = "LOW"
    else:
        if tool_name == "get_projects_summary":
            rows = data if isinstance(data, list) else []
            if rows:
                top = rows[0]
                answer = (
                    f"{len(rows)} projects need attention. Highest risk: "
                    f"{_clean(top.get('project_name')) or 'project'} "
                    f"({_clean(top.get('risk_level')) or 'UNKNOWN'}, score {_safe_int(top.get('risk_score'), 0)})."
                )
            else:
                answer = "No projects matched the requested risk view."
            confidence = "MEDIUM"
        elif tool_name == "get_latest_activity_summary":
            payload = data if isinstance(data, dict) else {}
            prs = len(payload.get("latest_pull_requests") or [])
            issues = len(payload.get("latest_issues") or [])
            commits = len(payload.get("latest_commits") or [])
            risky = len(payload.get("high_risk_projects") or [])
            answer = (
                f"Latest activity: {prs} PRs, {issues} issues, {commits} commits, "
                f"and {risky} high-risk projects currently tracked."
            )
            confidence = "MEDIUM"
            if not recs and isinstance(payload.get("recommended_actions"), list):
                recs = [_clean(x) for x in payload.get("recommended_actions") if _clean(x)]
        elif tool_name == "find_possible_regression_sources":
            suspects = (data or {}).get("suspects") if isinstance(data, dict) else []
            count = len(suspects or [])
            answer = (
                "OmniSprint cannot prove causality from partial signals, "
                f"but found {count} likely regression suspects."
            )
            confidence = "LOW"
        elif tool_name == "get_owner_risk_summary":
            rows = data if isinstance(data, list) else []
            if rows:
                top = rows[0]
                answer = (
                    f"Owner lead needing the most attention: {_clean(top.get('owner_lead')) or 'Unknown'} "
                    f"({top.get('high_risk_projects', 0)} high-risk projects)."
                )
            else:
                answer = "No owner risk profiles were returned."
            confidence = "MEDIUM"
        elif tool_name in ("get_reminder_candidates", "generate_project_reminder"):
            if reminder:
                answer = "Generated targeted high-risk follow-up content."
            else:
                answer = "No reminder needed based on the current risk threshold."
            confidence = "MEDIUM"
        elif tool_name == "get_technical_evidence":
            answer = "Collected Coral source and query-flow evidence for this workspace."
            confidence = "HIGH"
        else:
            answer = summary or "Completed request using Coral-backed OmniSprint tools."
            confidence = "MEDIUM"

    return _fallback_used_response(
        answer=answer,
        confidence=confidence,
        tool_calls=[_tool_trace_entry(tool_name, {}, result)],
        evidence_summary=summary or "Evidence retrieved through Coral-backed OmniSprint tools.",
        recommended_actions=recs[:8],
        reminder=reminder,
        email_draft=email_draft,
    )


def _parse_json_block(text: str) -> Optional[Dict[str, Any]]:
    raw = _clean(text)
    if not raw:
        return None
    # direct parse
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    # fenced code block fallback
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue
    return None


def _tool_declarations_for_gemini_types(types_mod: Any) -> List[Any]:
    raw_decls = _tool_declarations()
    out = []
    for decl in raw_decls:
        out.append(
            types_mod.FunctionDeclaration(
                name=decl["name"],
                description=decl["description"],
                parameters=decl["parameters"],
            )
        )
    return out


def _tool_declarations() -> List[Dict[str, Any]]:
    return [
        {
            "name": "get_projects_summary",
            "description": "Get normalized projects with risk scores.",
            "parameters": {
                "type": "object",
                "properties": {
                    "risk_filter": {
                        "type": "string",
                        "enum": ["ALL", "HIGH", "CRITICAL", "HIGH_OR_CRITICAL", "LOW_OR_MEDIUM"],
                    },
                    "limit": {"type": "integer"},
                },
            },
        },
        {
            "name": "get_project_details",
            "description": "Get full project evidence by project id or project name reference.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_ref": {"type": "string"},
                },
                "required": ["project_ref"],
            },
        },
        {
            "name": "get_owner_risk_summary",
            "description": "Get owner lead delivery-risk summary.",
            "parameters": {
                "type": "object",
                "properties": {"owner_name": {"type": "string"}},
            },
        },
        {
            "name": "get_recent_pull_requests",
            "description": "Get recently updated pull requests from Coral github.pulls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer"},
                    "state": {"type": "string", "enum": ["all", "open", "closed"]},
                },
            },
        },
        {
            "name": "get_recent_issues",
            "description": "Get recently updated issues from Coral github.issues.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer"},
                    "state": {"type": "string", "enum": ["all", "open", "closed"]},
                },
            },
        },
        {
            "name": "get_latest_commits",
            "description": "Get latest commits from Coral github.commits if available.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
            },
        },
        {
            "name": "find_possible_regression_sources",
            "description": "Find likely PR/commit suspects for a regression with confidence levels.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_ref": {"type": "string"},
                    "lookback_days": {"type": "integer"},
                },
            },
        },
        {
            "name": "get_reminder_candidates",
            "description": "Get high-risk reminder candidates with Google Chat and email drafts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner_name": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
        {
            "name": "generate_project_reminder",
            "description": "Generate reminder for one project if it is HIGH/CRITICAL risk.",
            "parameters": {
                "type": "object",
                "properties": {"project_ref": {"type": "string"}},
                "required": ["project_ref"],
            },
        },
        {
            "name": "get_latest_activity_summary",
            "description": "Get compact latest activity summary across PRs/issues/commits/high-risk projects.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
            },
        },
        {
            "name": "get_technical_evidence",
            "description": "Get Coral source health and query evidence.",
            "parameters": {
                "type": "object",
                "properties": {"include_queries": {"type": "boolean"}},
            },
        },
    ]


def _extract_text_from_response(resp: Any) -> str:
    text = _clean(getattr(resp, "text", ""))
    if text:
        return text
    candidates = []
    for field in ("candidates",):
        value = getattr(resp, field, None)
        if value:
            candidates = value
            break
    for candidate in candidates or []:
        content = getattr(candidate, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", []) or []:
            part_text = _clean(getattr(part, "text", ""))
            if part_text:
                return part_text
    return ""


def _extract_function_calls_from_response(resp: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    fn_calls = getattr(resp, "function_calls", None)
    if isinstance(fn_calls, list):
        for call in fn_calls:
            name = _clean(getattr(call, "name", ""))
            args = getattr(call, "args", {}) or {}
            if name:
                out.append({"name": name, "args": dict(args) if isinstance(args, dict) else {}})
        if out:
            return out

    # fallback: inspect candidate parts
    candidates = getattr(resp, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", []) or []:
            fc = getattr(part, "function_call", None)
            if not fc:
                continue
            name = _clean(getattr(fc, "name", ""))
            args = getattr(fc, "args", {}) or {}
            if name:
                out.append({"name": name, "args": dict(args) if isinstance(args, dict) else {}})
    return out


def _coerce_final_response(obj: Dict[str, Any], fallback_answer: str, fallback_evidence: str) -> Dict[str, Any]:
    answer = _clean(obj.get("answer")) or fallback_answer
    answer = _strip_internal_project_ids(answer)
    confidence = (_clean(obj.get("confidence")) or "MEDIUM").upper()
    if confidence not in ("LOW", "MEDIUM", "HIGH"):
        confidence = "MEDIUM"
    evidence_summary = _clean(obj.get("evidence_summary")) or fallback_evidence
    evidence_summary = _strip_internal_project_ids(evidence_summary)
    actions = obj.get("recommended_actions")
    if not isinstance(actions, list):
        actions = []
    actions = [_strip_internal_project_ids(x) for x in actions if _clean(x)]
    actions = [x for x in actions if x]
    reminder = obj.get("reminder")
    if reminder is not None and not isinstance(reminder, dict):
        reminder = None
    email_draft = obj.get("email_draft")
    if email_draft is not None and not isinstance(email_draft, dict):
        email_draft = None
    return {
        "answer": answer,
        "confidence": confidence,
        "evidence_summary": evidence_summary,
        "recommended_actions": actions[:8],
        "reminder": reminder,
        "email_draft": email_draft,
    }


def _try_gemini_function_calling(question: str) -> Dict[str, Any]:
    try:
        from google import genai
        from google.genai import types
    except Exception as e:
        raise RuntimeError(f"Gemini SDK import failed: {e}")

    api_key = _clean(os.getenv("GEMINI_API_KEY"))
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    model = _clean(os.getenv("GEMINI_MODEL")) or "gemini-2.5-flash"
    max_calls = _max_tool_calls()
    client = genai.Client(api_key=api_key)

    function_decls = _tool_declarations_for_gemini_types(types)
    tools = [types.Tool(function_declarations=function_decls)]
    try:
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=tools,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
    except Exception:
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=tools,
        )

    contents: List[Any] = [
        types.Content(role="user", parts=[types.Part.from_text(text=question)]),
    ]

    tool_calls: List[Dict[str, Any]] = []
    all_sources: List[str] = []
    reminder: Optional[Dict[str, Any]] = None
    email_draft: Optional[Dict[str, Any]] = None
    collected_actions: List[str] = []
    last_summary = "No evidence collected."
    global _RESOLVED_GEMINI_MODEL
    selected_model = _RESOLVED_GEMINI_MODEL if _clean(_RESOLVED_GEMINI_MODEL) else model
    candidate_models = _model_candidates(model)
    if selected_model not in candidate_models:
        selected_model = candidate_models[0]

    for _ in range(max_calls):
        try:
            resp = client.models.generate_content(model=selected_model, contents=contents, config=config)
        except Exception as e:
            if not _is_model_not_supported_error(e):
                raise RuntimeError(
                    f"Gemini request failed before tool-calling ({selected_model}): {_error_text(e)}"
                )

            switched = False
            errors: List[str] = [f"{selected_model}: {_error_text(e)}"]
            for candidate_model in candidate_models:
                if candidate_model == selected_model:
                    continue
                try:
                    resp = client.models.generate_content(model=candidate_model, contents=contents, config=config)
                    selected_model = candidate_model
                    _RESOLVED_GEMINI_MODEL = candidate_model
                    switched = True
                    break
                except Exception as inner:
                    errors.append(f"{candidate_model}: {_error_text(inner)}")
                    if not _is_model_not_supported_error(inner):
                        raise RuntimeError(
                            f"Gemini request failed before tool-calling ({candidate_model}): {_error_text(inner)}"
                        )
            if not switched:
                raise RuntimeError(
                    "Unable to resolve a working Gemini model. Tried: " + "; ".join(errors[:6])
                )
        else:
            _RESOLVED_GEMINI_MODEL = selected_model
        requested_calls = _extract_function_calls_from_response(resp)

        if not requested_calls:
            # If Gemini skipped tool calls entirely, force one deterministic Coral-backed
            # tool execution to avoid generic/non-actionable placeholder responses.
            if not tool_calls:
                forced_tool, forced_args = _fallback_tool_for_question(question)
                forced_result = _execute_tool(forced_tool, forced_args)
                last_summary = _clean(forced_result.get("summary")) or last_summary
                all_sources.extend(forced_result.get("coral_sources_used") or [])
                collected_actions.extend(_recommendations_from_tool_data(forced_result))
                r, e = _extract_reminder_and_email(forced_tool, forced_result)
                if r and reminder is None:
                    reminder = r
                if e and email_draft is None:
                    email_draft = e
                forced_trace = _tool_trace_entry(forced_tool, forced_args, forced_result)
                tool_calls.append(forced_trace)
                forced_payload = {
                    "name": forced_tool,
                    "status": forced_trace["status"],
                    "summary": forced_trace["summary"],
                    "coral_sources_used": forced_trace["coral_sources_used"],
                    "data": forced_result.get("data"),
                }
                try:
                    function_response_part = types.Part.from_function_response(
                        name=forced_tool,
                        response=forced_payload,
                    )
                    contents.append(types.Content(role="user", parts=[function_response_part]))
                except Exception:
                    contents.append(
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_text(
                                    text=(
                                        "Tool result for "
                                        f"{forced_tool}:\n{json.dumps(forced_payload, ensure_ascii=True, default=str)}"
                                    )
                                )
                            ],
                        )
                    )
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(
                                text=(
                                    "Use this evidence to answer the user's question directly. "
                                    "Do not return generic readiness statements."
                                )
                            )
                        ],
                    )
                )
                continue

            # Gemini is done with tool calls; ask for strict final JSON based on gathered context.
            contents.append(types.Content(role="model", parts=[types.Part.from_text(text=_extract_text_from_response(resp) or "")]))
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=FINAL_SCHEMA_PROMPT)]))
            final_resp = client.models.generate_content(model=selected_model, contents=contents, config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION))
            parsed = _parse_json_block(_extract_text_from_response(final_resp))
            if not parsed:
                # one retry with harder constraint
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text="Return only JSON. No markdown. No prose. Use the exact keys.")],
                    )
                )
                retry_resp = client.models.generate_content(
                    model=selected_model,
                    contents=contents,
                    config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION),
                )
                parsed = _parse_json_block(_extract_text_from_response(retry_resp))

            final = _coerce_final_response(
                parsed or {},
                fallback_answer=_extract_text_from_response(resp) or "Completed analysis using OmniSprint tools.",
                fallback_evidence=last_summary,
            )
            if _is_placeholder_answer(final.get("answer") or "") and tool_calls:
                # Force a concrete answer when Gemini returns boilerplate.
                final["answer"] = (
                    f"{last_summary} "
                    "Use the recommended actions below for immediate follow-up."
                ).strip()
                final["confidence"] = "MEDIUM"
            final.update(
                {
                    "tool_calls": tool_calls,
                    "used_gemini": True,
                    "fallback_used": False,
                    "gemini_model": selected_model,
                }
            )
            if not final.get("reminder"):
                final["reminder"] = reminder
            if not final.get("email_draft"):
                final["email_draft"] = email_draft
            if not final.get("recommended_actions"):
                final["recommended_actions"] = collected_actions[:8]
            if not final.get("evidence_summary"):
                final["evidence_summary"] = last_summary
            return final

        for call in requested_calls:
            name = call.get("name")
            arguments = call.get("args") if isinstance(call.get("args"), dict) else {}
            result = _execute_tool(name, arguments)
            last_summary = _clean(result.get("summary")) or last_summary
            all_sources.extend(result.get("coral_sources_used") or [])
            collected_actions.extend(_recommendations_from_tool_data(result))
            r, e = _extract_reminder_and_email(name, result)
            if r and reminder is None:
                reminder = r
            if e and email_draft is None:
                email_draft = e

            trace = _tool_trace_entry(name, arguments, result)
            tool_calls.append(trace)

            tool_response_payload = {
                "name": name,
                "status": trace["status"],
                "summary": trace["summary"],
                "coral_sources_used": trace["coral_sources_used"],
                "data": result.get("data"),
            }
            try:
                function_response_part = types.Part.from_function_response(
                    name=name,
                    response=tool_response_payload,
                )
                contents.append(
                    types.Content(
                        role="user",
                        parts=[function_response_part],
                    )
                )
            except Exception:
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(
                                text=(
                                    "Tool result for "
                                    f"{name}:\n{json.dumps(tool_response_payload, ensure_ascii=True, default=str)}"
                                )
                            )
                        ],
                    )
                )

    # max tool calls reached: ask for final answer with current trace.
    evidence_summary = (
        f"Executed {len(tool_calls)} tool calls; most recent result: {last_summary}"
        if tool_calls
        else "No tool calls executed."
    )
    return {
        "answer": "Reached the tool-call safety limit. Here is the best available summary from retrieved evidence.",
        "confidence": "LOW",
        "tool_calls": tool_calls,
        "evidence_summary": evidence_summary,
        "recommended_actions": list(dict.fromkeys([_clean(a) for a in collected_actions if _clean(a)]))[:8],
        "reminder": reminder,
        "email_draft": email_draft,
        "used_gemini": True,
        "fallback_used": False,
        "gemini_model": selected_model,
    }


def ask_agent(question: str) -> Dict[str, Any]:
    q = _clean(question)
    if not q:
        return _fallback_used_response(
            answer="Please provide a question.",
            confidence="LOW",
            tool_calls=[],
            evidence_summary="No question provided.",
            recommended_actions=[],
            fallback_reason="empty_question",
        )

    api_key = _clean(os.getenv("GEMINI_API_KEY"))
    if not api_key:
        tool_name, args = _fallback_tool_for_question(q)
        result = _execute_tool(tool_name, args)
        fallback = _build_fallback_answer(q, tool_name, result)
        fallback["tool_calls"] = [_tool_trace_entry(tool_name, args, result)]
        fallback["fallback_reason"] = "GEMINI_API_KEY is missing or blank"
        return fallback

    try:
        out = _try_gemini_function_calling(q)
        if _is_placeholder_answer(out.get("answer") or ""):
            tool_name, args = _fallback_tool_for_question(q)
            result = _execute_tool(tool_name, args)
            fallback = _build_fallback_answer(q, tool_name, result)
            fallback["tool_calls"] = [_tool_trace_entry(tool_name, args, result)]
            fallback["fallback_reason"] = "Gemini returned a generic placeholder response"
            return fallback
        return out
    except Exception as e:
        tool_name, args = _fallback_tool_for_question(q)
        result = _execute_tool(tool_name, args)
        fallback = _build_fallback_answer(q, tool_name, result)
        fallback["tool_calls"] = [_tool_trace_entry(tool_name, args, result)]
        fallback["fallback_reason"] = _error_text(e)
        return fallback
