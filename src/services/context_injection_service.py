"""会话上下文注入服务：从历史事件抽取关键信息并拼接到新一轮 prompt。"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from src.utils.constant import (
    CTX_EVENT_WINDOW,
    CTX_FILTER_NOISE_ENABLED,
    CTX_HISTORY_MAX_CHARS,
    CTX_HISTORY_MAX_LINES,
    CTX_INJECTION_ENABLED,
    CTX_MAX_TOKENS_LIMIT,
    CTX_TOTAL_PROMPT_MAX_CHARS,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.services.events_service import ChatEventsService

_NOISE_TYPES = frozenset({'log_line', 'ping', 'session_status', 'status'})
_LOW_VALUE_SUCCESS_TOOLS = frozenset(
    {'use_skill', 'str_replace_editor', 'execute_bash', 'peek_file', 'think', 'finish'}
)
_LIKELY_FILE_EXTS = frozenset(
    {
        '.cif',
        '.xyz',
        '.vasp',
        '.xsf',
        '.poscar',
        '.contcar',
        '.json',
        '.yaml',
        '.yml',
        '.md',
        '.txt',
        '.csv',
        '.pdf',
        '.png',
        '.jpg',
        '.jpeg',
        '.zip',
        '.tar',
        '.gz',
        '.log',
        '.out',
        '.err',
    }
)

_DEFAULT_STATE = {
    'intent': '',
    'file_memory': [],
}
_LOCAL_SESSION_STATE_STORE: dict[str, dict[str, Any]] = {}
_SESSION_HISTORY_HEADER = '[Session history]\n'
_SESSION_HISTORY_HINT = (
    'Use this prior context for continuity, and prioritize latest user request on conflicts.\n'
)
_SESSION_HISTORY_PREFIX = _SESSION_HISTORY_HEADER + _SESSION_HISTORY_HINT


def _to_one_line(text: str) -> str:
    return ' '.join((text or '').strip().split())


def _normalize_mode(mode: str | None) -> str:
    return (mode or 'direct').strip().lower() or 'direct'


def _clip(text: str, limit: int) -> str:
    if limit <= 0:
        return ''
    s = text or ''
    if len(s) <= limit:
        return s
    if limit <= 3:
        return s[:limit]
    return s[: limit - 3] + '...'


def _normalize_event_content(content: Any) -> str:
    if content is None:
        return ''
    if isinstance(content, str):
        return _to_one_line(content)
    if isinstance(content, dict):
        base = _to_one_line(str(content.get('content', '')))
        files = content.get('files')
        if isinstance(files, list) and files:
            return _to_one_line(f'{base} files={len(files)}')
        try:
            return _to_one_line(json.dumps(content, ensure_ascii=False))
        except Exception:
            return _to_one_line(str(content))
    try:
        return _to_one_line(json.dumps(content, ensure_ascii=False))
    except Exception:
        return _to_one_line(str(content))


def _parse_json_dict(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return None
    return payload if isinstance(payload, dict) else None


def _is_noise_event(event: dict) -> bool:
    if not CTX_FILTER_NOISE_ENABLED:
        return False
    event_type = str(event.get('type', '')).strip()
    return event_type in _NOISE_TYPES


def _is_high_value_success_tool(name: str) -> bool:
    n = (name or '').strip()
    return bool(n) and n not in _LOW_VALUE_SUCCESS_TOOLS and (
        n.startswith(('mat_', 'run_')) or n in {'monitor_job', 'submit_job'}
    )


def _event_to_memory_line(event: dict) -> str | None:
    source = str(event.get('source', '')).strip() or 'System'
    event_type = str(event.get('type', '')).strip()
    if not event_type or _is_noise_event(event):
        return None
    content = event.get('content')

    if source == 'User' and event_type in {'query', 'planner_reply', 'ask_human_reply'}:
        text = _normalize_event_content(content)
        return f'User({event_type}): {text}' if text else None

    if event_type in {'error', 'cancelled', 'finish'}:
        text = _normalize_event_content(content)
        return f'{source}({event_type}): {text}' if text else None

# tool_result 由 _build_history_block 统一做结构化提炼，避免重复和噪声。
    return None


def _estimate_tokens(text: str) -> int:
    # 近似估算：英文约 4 chars/token，中文更密但这里按统一策略做安全控制。
    return max(1, len(text) // 4)


def _extract_urls(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for token in text.split():
        t = token.strip('()[]{}<>,;:\'\"')
        if not (t.startswith('http://') or t.startswith('https://')):
            continue
        # 清理常见的转义尾巴，避免如 ".cif\\n" 被误判为非文件 URL
        t = _canonicalize_url(t)
        out.append(t)
    # 去重保序
    dedup: list[str] = []
    seen: set[str] = set()
    for u in out:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup


def _merge_file_memory(current: list[str], existing: list[str], limit: int = 20) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw in list(current or []) + list(existing or []):
        if not isinstance(raw, str):
            continue
        ref = _canonicalize_url(raw)
        if not ref or ref in seen:
            continue
        seen.add(ref)
        merged.append(ref)
        if len(merged) >= max(1, limit):
            break
    return merged


def _is_likely_file_url(url: str) -> bool:
    u = _canonicalize_url(url).lower()
    if not (u.startswith('http://') or u.startswith('https://')):
        return False
    if '/workspace/download' in u:
        return True
    if '/chat_workspace/' in u or '/finish_reports/' in u:
        return True
    path = u.split('?', 1)[0]
    for ext in _LIKELY_FILE_EXTS:
        if path.endswith(ext):
            return True
    return False


def _canonicalize_url(url: str) -> str:
    t = (url or '').strip()
    t = t.replace('\\n', '').replace('\\r', '').replace('\\"', '')
    t = t.rstrip('\\')
    t = t.rstrip('.,;:')
    if t.endswith('-'):
        t = t[:-1].rstrip()
    return t


def _looks_like_error(text: str) -> bool:
    low = (text or '').lower()
    # 成功信号优先：避免 “Failed steps: 无” 这类文案把成功结果误判为 error
    success_markers = (
        '"status": "success"',
        "'status': 'success'",
        '"status":"success"',
        "'status':'success'",
        '"task_completed": "true"',
        '"task_completed":"true"',
        '"task_completed": "partial"',
        '"task_completed":"partial"',
        '"task_completed": true',
        '"task_completed":true',
    )
    if any(m in low for m in success_markers):
        return False
    markers = (
        'error',
        'failed',
        'unknown tool',
        'invalid parameter',
        'no such file',
        'traceback',
        'exception',
    )
    return any(m in low for m in markers)


def _parse_tool_result_payload(content: Any) -> dict[str, Any] | None:
    payload = _parse_json_dict(content)
    if not payload:
        return None
    name = str(payload.get('name', '')).strip()
    if not name:
        return None
    result = payload.get('result')
    result_text = _normalize_event_content(result)
    low = result_text.lower()
    urls = _extract_urls(result_text)
    has_error = _looks_like_error(result_text)
    has_job_id = ('bohr_job_id' in low) or ('job_id' in low)
    return {
        'name': name,
        'result_text': result_text,
        'has_error': has_error,
        'has_job_id': has_job_id,
        'urls': urls,
    }


def _is_structure_url(url: str) -> bool:
    u = _canonicalize_url(url).lower().split('?', 1)[0]
    return u.endswith(('.cif', '.xyz', '.vasp', '.xsf', '.poscar', '.contcar'))


class ContextInjectionService:
    """组装传给 agent 的增强 prompt（原始用户输入 + 历史关键上下文）。"""

    def __init__(self, events_service: 'ChatEventsService | None' = None):
        if events_service is not None:
            self._events_service = events_service
        else:
            from src.services.events_service import get_events_service

            self._events_service = get_events_service()

    @staticmethod
    def _session_store() -> dict[str, dict[str, Any]]:
        try:
            from src.services.sessions_service import SESSIONS

            return SESSIONS
        except Exception:
            # 测试环境下若 sessions_service 依赖未满足，降级到本地 store。
            return _LOCAL_SESSION_STATE_STORE

    def _get_or_init_state(self, session_id: str) -> dict[str, Any]:
        session = self._session_store().setdefault(session_id, {})
        state = session.get('state')
        if not isinstance(state, dict):
            state = {}
        merged = dict(_DEFAULT_STATE)
        merged.update(state)
        if not isinstance(merged.get('file_memory'), list):
            merged['file_memory'] = []
        session['state'] = merged
        return merged

    @staticmethod
    def _select_recent_events(events: list[dict]) -> list[dict]:
        window = max(1, CTX_EVENT_WINDOW)
        return events[-window:] if len(events) > window else events

    @staticmethod
    def _extract_file_memory(events: list[dict], attached_files: list[str] | None) -> list[str]:
        file_refs: list[tuple[str, int, int]] = []  # (url, score, recency_idx)
        if attached_files:
            for idx, x in enumerate(attached_files):
                if isinstance(x, str) and x.strip():
                    cx = _canonicalize_url(x)
                    score = 50 if _is_structure_url(cx) else 30
                    file_refs.append((cx, score, idx))
        for idx, event in enumerate(events):
            content = event.get('content')
            if isinstance(content, dict):
                files = content.get('files')
                if isinstance(files, list):
                    for x in files:
                        if isinstance(x, str) and x.strip():
                            cx = _canonicalize_url(x)
                            score = 90 if _is_structure_url(cx) else 35
                            file_refs.append((cx, score, idx))
                maybe_urls = _extract_urls(_normalize_event_content(content))
                for u in maybe_urls:
                    score = 90 if _is_structure_url(u) else 35
                    file_refs.append((u, score, idx))
                if str(event.get('type', '')).strip() == 'tool_result':
                    parsed = _parse_tool_result_payload(content)
                    if parsed:
                        for u in parsed['urls']:
                            score = 95 if _is_structure_url(u) else 40
                            file_refs.append((u, score, idx))
            elif isinstance(content, str):
                for u in _extract_urls(content):
                    score = 90 if _is_structure_url(u) else 35
                    file_refs.append((u, score, idx))
        best: dict[str, tuple[int, int]] = {}
        for ref, score, recency_idx in file_refs:
            c_ref = _canonicalize_url(ref)
            if not _is_likely_file_url(c_ref):
                continue
            prev = best.get(c_ref)
            if prev is None or (score, recency_idx) > prev:
                best[c_ref] = (score, recency_idx)
        ranked = sorted(best.items(), key=lambda x: (x[1][0], x[1][1]), reverse=True)
        return [x[0] for x in ranked[:20]]

    @staticmethod
    def _file_ref_label(ref: str) -> str:
        text = _canonicalize_url(str(ref))
        name = text.rsplit('/', 1)[-1] if '/' in text else text
        return f'{name}: {text}'

    def _update_state_for_turn(
        self,
        session_id: str,
        events: list[dict],
        user_prompt: str,
        mode: str,
        attached_files: list[str] | None,
    ) -> dict[str, Any]:
        state = self._get_or_init_state(session_id)
        _ = mode
        state['intent'] = _to_one_line(user_prompt)[:300]
        file_memory = self._extract_file_memory(events, attached_files)
        state['file_memory'] = _merge_file_memory(
            file_memory,
            state.get('file_memory', []),
            limit=20,
        )
        return state

    @staticmethod
    def _build_history_block(
        events: list[dict],
        current_user_prompt: str,
        mode: str = 'direct',
    ) -> list[str]:
        records: list[tuple[int, str]] = []
        _ = mode
        normalized_current = _to_one_line(current_user_prompt)
        for idx, event in enumerate(events):
            line = _event_to_memory_line(event)
            if line:
                line = _clip(_to_one_line(line), 240)
                if line.startswith('User(query):') and _to_one_line(
                    line.split(':', 1)[-1]
                ) == normalized_current:
                    line = None
            if line:
                records.append((idx, line))
            if str(event.get('type', '')).strip() == 'tool_result':
                parsed = _parse_tool_result_payload(event.get('content'))
                if not parsed:
                    continue
                name = parsed['name']
                # 只保留高价值“成功工具调用”的简短摘要，不注入错误 JSON 噪声。
                if parsed['has_error'] or (not _is_high_value_success_tool(name)):
                    continue
                succ_line = f'ToolSuccess({name})'
                records.append((idx, succ_line))

        if not records:
            return []

        max_lines = max(1, CTX_HISTORY_MAX_LINES)
        selected_lines = [line for _, line in records[-max_lines:]]
        header_len = len(_SESSION_HISTORY_PREFIX)
        kept: list[str] = []
        for line in selected_lines:
            candidate = kept + [line]
            block_len = header_len + len('\n'.join(f'- {x}' for x in candidate))
            if block_len <= max(1, CTX_HISTORY_MAX_CHARS):
                kept.append(line)
            else:
                break
        return kept

    @staticmethod
    def _render_history_block(lines: list[str]) -> str:
        if not lines:
            return ''
        return _SESSION_HISTORY_PREFIX + '\n'.join(f'- {x}' for x in lines)

    @staticmethod
    def _build_state_block(state: dict[str, Any]) -> str:
        if not state:
            return ''
        intent = _clip(_to_one_line(str(state.get('intent', ''))), 300)
        if not intent:
            return ''
        return '[Session intent]\n' + intent

    @staticmethod
    def _build_file_block(file_memory: list[str]) -> str:
        if not file_memory:
            return ''
        lines = '\n'.join(
            f"- {ContextInjectionService._file_ref_label(x)}" for x in file_memory[:10]
        )
        return '[Session files]\nReferenced files/urls from this session:\n' + lines

    def _fit_context_to_budget(
        self,
        user_prompt: str,
        history_lines: list[str],
        state: dict[str, Any],
        file_refs: list[str],
    ) -> tuple[str, dict[str, Any]]:
        max_total = max(1, CTX_TOTAL_PROMPT_MAX_CHARS)
        max_tokens = max(1, CTX_MAX_TOKENS_LIMIT)

        cur_history = list(history_lines)
        cur_files = list(file_refs[:10])
        state_included = True
        truncated = False

        while True:
            history_block = self._render_history_block(cur_history)
            state_block = self._build_state_block(state) if state_included else ''
            file_block = self._build_file_block(cur_files)
            blocks = [b for b in [history_block, state_block, file_block] if b]
            if not blocks:
                return user_prompt, {
                    'enabled': True,
                    'history_lines_count': 0,
                    'state_injected': False,
                    'file_refs_count': 0,
                    'context_truncated': truncated,
                    'fallback': True,
                }
            context_block = '\n\n'.join(blocks)
            candidate = f'{user_prompt}\n\n{context_block}'
            if len(candidate) <= max_total and _estimate_tokens(candidate) <= max_tokens:
                return candidate, {
                    'enabled': True,
                    'history_lines_count': len(cur_history),
                    'state_injected': bool(state_block),
                    'file_refs_count': len(cur_files),
                    'context_truncated': truncated,
                }

            truncated = True
            # 收缩优先级：history(逐条减) -> state(full->minimal->none) -> files
            if len(cur_history) > 1:
                cur_history = cur_history[:-1]
                continue
            # 保底：仅保留一条历史，再尝试一次极限截断
            if len(cur_history) == 1:
                clipped = _clip(cur_history[0], 80)
                if clipped == cur_history[0]:
                    cur_history = []
                else:
                    cur_history = [clipped]
                continue
            if state_included:
                state_included = False
                continue
            if len(cur_files) > 1:
                cur_files = cur_files[: max(1, len(cur_files) // 2)]
                continue
            if len(cur_files) == 1:
                cur_files = []
                continue

    async def build_augmented_prompt(
        self,
        session_id: str,
        user_prompt: str,
        mode: str = 'direct',
        attached_files: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        if not CTX_INJECTION_ENABLED:
            return user_prompt, {'enabled': False, 'history_lines_count': 0}
        if not session_id or not user_prompt:
            return user_prompt, {'enabled': True, 'history_lines_count': 0}

        try:
            events = self._events_service.get_session_events(session_id) or []
            events = self._select_recent_events(events)
            normalized_mode = _normalize_mode(mode)
            state = self._update_state_for_turn(
                session_id=session_id,
                events=events,
                user_prompt=user_prompt,
                mode=normalized_mode,
                attached_files=attached_files,
            )
            history_lines = self._build_history_block(
                events,
                user_prompt,
                mode=normalized_mode,
            )
            has_state = bool(state.get('intent'))
            if not history_lines and not state.get('file_memory') and not has_state:
                return user_prompt, {'enabled': True, 'history_lines_count': 0}
            prompt, meta = self._fit_context_to_budget(
                user_prompt=user_prompt,
                history_lines=history_lines,
                state=state,
                file_refs=state.get('file_memory', []),
            )
            return prompt, meta
        except Exception as e:
            logger.warning(
                'build_augmented_prompt failed, fallback to raw prompt: session_id=%s err=%s',
                session_id,
                e,
            )
            return user_prompt, {'enabled': True, 'history_lines_count': 0, 'fallback': True}


@lru_cache
def get_context_injection_service() -> ContextInjectionService:
    from src.services.events_service import get_events_service

    return ContextInjectionService(events_service=get_events_service())