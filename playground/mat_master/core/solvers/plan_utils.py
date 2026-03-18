"""plan_utils.py — Stateless plan/JSON utility functions for ResearchPlanner.

Extracted from research_planner.py to keep that file focused on the
ResearchPlanner class and its state-machine logic.

Includes:
- Config helpers: _get_async_registry, _load_pre_check_system_prompt, _get_mat_master_config
- Plan schema helpers: _is_deg_plan, _normalize_step, _normalize_plan, _plan_to_external_schema
- JSON parsing/repair: _extract_json_from_content, _try_parse_json,
  _complete_truncated_json, _strip_last_incomplete_step
- Text helpers: _to_thought_tag, _normalize_planner_thought
- Edit helpers: _str_replace_in_text, _STR_REPLACE_TOOL_SPEC
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

from ..async_tool_registry import AsyncToolRegistry

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _get_async_registry(config) -> AsyncToolRegistry:
    """Create AsyncToolRegistry from config (handles model_dump)."""
    try:
        if hasattr(config, 'model_dump'):
            d = config.model_dump()
        else:
            d = dict(config) if config else {}
    except Exception as exc:
        logging.getLogger(__name__).warning(
            'Failed to parse config for AsyncToolRegistry: %s', exc
        )
        d = {}
    return AsyncToolRegistry(d)


def _load_pre_check_system_prompt(config_dir=None) -> str:
    """Load pre-check system prompt from file, falling back to minimal inline template."""
    candidates = []
    if config_dir is not None:
        playground_base = Path(str(config_dir).replace('configs', 'playground', 1))
        candidates.append(
            (playground_base / 'prompts' / 'pre_check_system_prompt.txt').resolve()
        )
        candidates.append(
            (Path(config_dir) / 'prompts' / 'pre_check_system_prompt.txt').resolve()
        )
    local_base = Path(__file__).resolve().parent.parent.parent
    candidates.append(
        (local_base / 'prompts' / 'pre_check_system_prompt.txt').resolve()
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding='utf-8')
    # Fallback: minimal inline template
    return (
        'You are a pre-planning readiness assessor. Output JSON with keys: '
        'ready_to_plan (bool), prerequisites (list), reasoning (str). '
        'Do NOT generate the plan itself. Only assess readiness.'
    )


def _get_mat_master_config(config) -> dict:
    try:
        if hasattr(config, 'model_dump'):
            d = config.model_dump()
        else:
            d = dict(config) if config else {}
        return d.get('mat_master') or {}
    except Exception as exc:
        logging.getLogger(__name__).warning(
            'Failed to parse mat_master config: %s', exc
        )
        return {}


# ---------------------------------------------------------------------------
# Plan schema helpers
# ---------------------------------------------------------------------------


def _is_deg_plan(plan: Any) -> bool:
    """True if plan is a DEG (has 'steps' or 'execution_graph' with step_id)."""
    if not isinstance(plan, dict):
        return False
    steps = plan.get('steps') or plan.get('execution_graph')
    return (
        isinstance(steps, list)
        and len(steps) > 0
        and isinstance(steps[0].get('step_id'), int)
    )


def _normalize_step(step: dict[str, Any]) -> dict[str, Any]:
    """Map execution_graph schema to internal steps schema (goal-oriented)."""
    intensity = (
        step.get('compute_intensity') or step.get('compute_cost') or 'MEDIUM'
    ).upper()
    if intensity == 'LOW':
        cost = 'Low'
    elif intensity == 'HIGH':
        cost = 'High'
    else:
        cost = 'Medium'
    step_type = (step.get('step_type') or 'normal').lower()
    if step_type not in ('normal', 'skill_evolution'):
        step_type = (
            'skill_evolution'
            if step.get('tool_name') == 'skill_evolution'
            else 'normal'
        )
    intent = step.get('goal') or step.get('scientific_intent') or step.get('intent', '')
    return {
        'step_id': step.get('step_id'),
        'step_type': step_type,
        'tool_name': (
            'skill_evolution' if step_type == 'skill_evolution' else ''
        ),  # only for executor branch
        'intent': intent,
        'compute_cost': cost,
        'requires_human_confirm': step.get(
            'requires_confirmation', step.get('requires_human_confirm', False)
        ),
        'fallback_logic': step.get('fallback_strategy')
        or step.get('fallback_logic', 'None'),
        'status': step.get('status', 'pending'),
        'conditional_branch': step.get(
            'conditional_branch'
        ),  # optional: {"if_success": <step_id>, "if_fail": <step_id>}
        'depends_on': step.get(
            'depends_on', []
        ),  # optional: [step_id, ...] that must complete first
    }


def _normalize_plan(plan: dict[str, Any], max_steps: int = 999) -> dict[str, Any]:
    """Ensure plan has 'steps' with internal field names; cap length."""
    graph = plan.get('execution_graph') or plan.get('steps') or []
    plan['steps'] = [_normalize_step(s) for s in graph][:max_steps]
    for s in plan['steps']:
        s.setdefault('status', 'pending')
    return plan


def _plan_to_external_schema(plan: dict[str, Any]) -> dict[str, Any]:
    """Convert internal plan (steps) to prompt schema (execution_graph) for revision."""
    steps = plan.get('steps', [])
    intensity_map = {'Low': 'LOW', 'Medium': 'MEDIUM', 'High': 'HIGH'}
    execution_graph = []
    for s in steps:
        entry = {
            'step_id': s.get('step_id'),
            'step_type': s.get('step_type', 'normal'),
            'goal': s.get('intent', ''),
            'compute_intensity': intensity_map.get(s.get('compute_cost'), 'MEDIUM'),
            'requires_confirmation': s.get('requires_human_confirm', False),
            'fallback_strategy': s.get('fallback_logic', 'None'),
            'status': s.get('status', 'pending'),
        }
        if s.get('conditional_branch'):
            entry['conditional_branch'] = s['conditional_branch']
        if s.get('depends_on'):
            entry['depends_on'] = s['depends_on']
        execution_graph.append(entry)
    out = {
        'plan_id': plan.get('plan_id'),
        'status': plan.get('status'),
        'refusal_reason': plan.get('refusal_reason'),
        'strategy_name': plan.get('strategy_name'),
        'fidelity_level': plan.get('fidelity_level', 'Production'),
        'execution_graph': execution_graph,
    }
    if plan.get('plan_report'):
        out['plan_report'] = plan['plan_report']
    return out


# ---------------------------------------------------------------------------
# JSON parsing / repair
# ---------------------------------------------------------------------------


def _extract_json_from_content(content: str) -> str | None:
    """Extract first {...} or ```json ... ``` from LLM output.

    Uses string-aware brace matching to correctly handle braces inside
    JSON string values (e.g. goal text containing '{tool_name}').
    """
    text = (content or '').strip()
    if '```json' in text:
        start = text.find('```json') + 7
        end = text.rfind('```')
        if end > start:
            return text[start:end].strip()
    if '```' in text:
        start = text.find('```') + 3
        end = text.rfind('```')
        if end > start:
            return text[start:end].strip()
    start = text.find('{')
    if start < 0:
        return None
    # String-aware brace matching: skip braces inside quoted strings
    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _try_parse_json(raw: str, logger: logging.Logger | None = None) -> dict:
    """Attempt to parse JSON with multi-stage repair for common LLM output errors.

    Repair stages (applied in order until one succeeds):
    1. Direct json.loads() — no repair needed
    2. Fix unescaped newlines/tabs inside string values
    3. Remove trailing commas before ] or }
    4. Replace single-quoted strings with double-quoted strings
    5. Strip JavaScript-style line comments (// ...)
    6. Strip JavaScript-style block comments (/* ... */)
    7. Truncate at last valid closing brace (handles truncated output)

    Raises json.JSONDecodeError if all stages fail.
    """
    _log = logger or logging.getLogger(__name__)

    # Stage 1: direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    repaired = raw

    # Stage 1.5: strip markdown code fences (```json ... ``` or ``` ... ```)
    try:
        stripped = repaired
        if stripped.lstrip().startswith('```'):
            # Remove opening fence line
            first_nl = stripped.find('\n')
            if first_nl >= 0:
                stripped = stripped[first_nl + 1 :]
            # Remove closing fence
            last_fence = stripped.rfind('```')
            if last_fence >= 0:
                stripped = stripped[:last_fence]
            stripped = stripped.strip()
            if stripped:
                result = json.loads(stripped)
                return result
    except json.JSONDecodeError:
        # If fence-stripped version also has issues, continue to other stages
        # but use the stripped version as the base for subsequent repairs
        if stripped and stripped.strip():
            repaired = stripped

    # Stage 2: fix unescaped control characters inside string values.
    # Replace literal newlines/tabs/carriage-returns inside JSON strings.
    # We do this by scanning character-by-character to stay string-aware.
    try:
        chars: list[str] = []
        in_str = False
        esc = False
        for ch in repaired:
            if esc:
                chars.append(ch)
                esc = False
                continue
            if ch == '\\' and in_str:
                chars.append(ch)
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                chars.append(ch)
                continue
            if in_str and ch == '\n':
                chars.append('\\n')
                continue
            if in_str and ch == '\r':
                chars.append('\\r')
                continue
            if in_str and ch == '\t':
                chars.append('\\t')
                continue
            chars.append(ch)
        stage2 = ''.join(chars)
        return json.loads(stage2)
    except json.JSONDecodeError:
        pass

    # Stage 3: remove trailing commas before ] or }
    try:
        stage3 = re.sub(r',\s*([}\]])', r'\1', repaired)
        return json.loads(stage3)
    except json.JSONDecodeError:
        pass

    # Stage 4: combine stage 2 + stage 3
    try:
        stage4 = re.sub(r',\s*([}\]])', r'\1', stage2)  # type: ignore[possibly-undefined]
        return json.loads(stage4)
    except (json.JSONDecodeError, UnboundLocalError):
        pass

    # Stage 5: strip JS-style line comments
    try:
        stage5 = re.sub(r'//[^\n]*', '', repaired)
        stage5 = re.sub(r',\s*([}\]])', r'\1', stage5)
        return json.loads(stage5)
    except json.JSONDecodeError:
        pass

    # Stage 6: strip JS-style block comments
    try:
        stage6 = re.sub(r'/\*.*?\*/', '', repaired, flags=re.DOTALL)
        stage6 = re.sub(r',\s*([}\]])', r'\1', stage6)
        return json.loads(stage6)
    except json.JSONDecodeError:
        pass

    # Stage 7: truncate at last valid closing brace (handles truncated LLM output)
    try:
        last_brace = repaired.rfind('}')
        if last_brace > 0:
            truncated = repaired[: last_brace + 1]
            # Also fix trailing commas in truncated version
            truncated = re.sub(r',\s*([}\]])', r'\1', truncated)
            return json.loads(truncated)
    except json.JSONDecodeError:
        pass

    # All stages failed — re-raise original error for caller to handle
    _log.debug('_try_parse_json: all repair stages failed for input (len=%d)', len(raw))
    raise json.JSONDecodeError('All JSON repair stages failed', raw, 0)


def _complete_truncated_json(raw: str) -> str | None:
    """Complete a truncated JSON string by appending missing closing brackets/braces.

    Uses a string-aware stack scan to find unclosed { and [, then appends
    the missing closing chars in reverse order.

    Returns the completed string if the input was unbalanced (i.e. truncated),
    or None if the input is already balanced or empty.
    """
    if not raw:
        return None
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in raw:
        if esc:
            esc = False
            continue
        if ch == '\\' and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == '{':
            stack.append('}')
        elif ch == '[':
            stack.append(']')
        elif ch in ('}', ']'):
            if stack and stack[-1] == ch:
                stack.pop()
            # mismatched closer — leave stack as-is; _try_parse_json will catch it
    if not stack:
        # Already balanced — caller should use _try_parse_json directly
        return None
    # Strip trailing comma/whitespace before appending closers
    tail = raw.rstrip()
    if tail.endswith(','):
        tail = tail[:-1].rstrip()
    return tail + ''.join(reversed(stack))


def _strip_last_incomplete_step(raw: str) -> str | None:
    """Strip the last (likely incomplete) step object from a truncated plan JSON.

    Finds the last '{' that is NOT inside a string (i.e. the start of the
    last step object), truncates before it, and strips trailing comma/whitespace.

    Returns the stripped string, or None if no such position is found.
    """
    if not raw:
        return None
    last_open = -1
    in_str = False
    esc = False
    for i, ch in enumerate(raw):
        if esc:
            esc = False
            continue
        if ch == '\\' and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == '{':
            last_open = i
    if last_open <= 0:
        return None
    stripped = raw[:last_open].rstrip()
    if stripped.endswith(','):
        stripped = stripped[:-1].rstrip()
    return stripped


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _to_thought_tag(label: str) -> str:
    """Normalize bracket labels like 'Pre-check' into stable snake_case tags."""
    t = (label or '').strip().lower()
    t = re.sub(r'[^a-z0-9]+', '_', t)
    return t.strip('_')


def _normalize_planner_thought(content: Any) -> dict[str, Any]:
    """Convert planner thought payloads to structured JSON for frontend readability."""
    if isinstance(content, dict):
        return content
    if content is None:
        return {'message': ''}
    if not isinstance(content, str):
        return {'data': content}

    raw_text = content
    text = raw_text.strip()
    payload: dict[str, Any] = {'message': raw_text}
    if not text:
        return payload

    # Parse leading bracket tags, e.g. "[Pre-check] ...", "[CRP] ..."
    tag_match = re.match(r'^\[([^\]]+)\]\s*([\s\S]*)$', text)
    if tag_match:
        payload['tag'] = _to_thought_tag(tag_match.group(1))
        payload['message'] = tag_match.group(2).strip()

    # If thought includes embedded JSON, expose it explicitly under `data`.
    embedded = _extract_json_from_content(text)
    if embedded:
        try:
            parsed = _try_parse_json(embedded)
            payload['data'] = parsed
            # When structured data is successfully parsed, suppress the raw message
            # to avoid showing raw JSON text in the chat alongside the rendered card.
            # Only keep message if it contains meaningful non-JSON text (e.g. preamble).
            msg_text = str(payload.get('message', '')).strip()
            if msg_text:
                # Remove the embedded JSON block from the message text
                msg_without_json = msg_text.replace(embedded, '').strip()
                if not msg_without_json:
                    # Message was purely the JSON — drop it entirely
                    payload.pop('message', None)
                else:
                    # Keep only the non-JSON preamble/suffix
                    payload['message'] = msg_without_json
        except Exception:
            pass
    return payload


# ---------------------------------------------------------------------------
# Edit helpers
# ---------------------------------------------------------------------------

# Tool spec for str_replace_editor (str_replace command only).
# Used by _revise_plan_from_file to give the LLM a single, scoped tool so it
# can make targeted edits to current_plan.json via function calling without
# having access to any other tools (prevents over-execution).
_STR_REPLACE_TOOL_SPEC: dict = {
    'type': 'function',
    'function': {
        'name': 'str_replace_editor',
        'description': (
            'Make a targeted edit to current_plan.json by replacing an exact '
            "substring with new text. Use this to apply the user's revision "
            'request with minimal changes — do NOT rewrite the whole plan.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'command': {
                    'type': 'string',
                    'enum': ['str_replace'],
                    'description': 'Must be "str_replace".',
                },
                'path': {
                    'type': 'string',
                    'description': 'File path — always "current_plan.json".',
                },
                'old_str': {
                    'type': 'string',
                    'description': (
                        'Exact substring to find in current_plan.json. '
                        'Must be unique in the file. Include enough context '
                        '(2-3 surrounding lines) to ensure uniqueness.'
                    ),
                },
                'new_str': {
                    'type': 'string',
                    'description': 'Replacement text for old_str.',
                },
            },
            'required': ['command', 'path', 'old_str', 'new_str'],
        },
    },
}


def _str_replace_in_text(
    text: str,
    old_str: str,
    new_str: str,
    logger: logging.Logger | None = None,
) -> tuple[str, str | None]:
    """Apply a single str_replace operation to *text* (no file I/O).

    Mirrors the strip-fallback logic from ``EditorTool._str_replace``:

    1. Try exact match of *old_str*.
    2. If not found, retry with ``old_str.strip()`` / ``new_str.strip()``.
    3. Return ``(original_text, error_message)`` if still not found or if
       multiple matches exist.

    Returns ``(new_text, None)`` on success, or ``(text, error_message)`` on
    failure (original text is returned unchanged so the caller can decide
    whether to abort or continue with remaining tool calls).
    """
    _log = logger or logging.getLogger(__name__)

    def _do_replace(content: str, search: str, replacement: str) -> str:
        matches = list(re.finditer(re.escape(search), content))
        if not matches:
            raise ValueError(f'old_str not found: {search[:80]!r}')
        if len(matches) > 1:
            lines = sorted({content.count('\n', 0, m.start()) + 1 for m in matches})
            raise ValueError(
                f'old_str matches multiple locations (lines {lines}); make it more unique'
            )
        m = matches[0]
        return content[: m.start()] + replacement + content[m.end() :]

    try:
        return _do_replace(text, old_str, new_str), None
    except ValueError:
        pass

    # Strip fallback (mirrors EditorTool._str_replace lines 290-301)
    old_stripped = old_str.strip()
    new_stripped = new_str.strip()
    if old_stripped == old_str:
        # No whitespace to strip — already failed with exact match
        err = f'old_str not found verbatim in plan text: {old_str[:80]!r}'
        _log.warning('[Planner] _str_replace_in_text: %s', err)
        return text, err
    try:
        result = _do_replace(text, old_stripped, new_stripped)
        _log.debug(
            '[Planner] _str_replace_in_text: used strip fallback for %r', old_str[:60]
        )
        return result, None
    except ValueError as e:
        err = str(e)
        _log.warning('[Planner] _str_replace_in_text: %s', err)
        return text, err
