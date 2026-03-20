"""StreamingMatMasterAgent: MatMasterAgent that emits thought/tool_call/tool_result via callback."""

import json
import logging
import sys
import traceback as _tb
from typing import Any, Callable

from evomaster.utils.types import AssistantMessage, ToolMessage
from src.utils.chat_event_source import normalize_event_source

from ..core.agent import MatMasterAgent


def _extract_think_content(args_str: str) -> str | None:
    """If args parse as { \"thought\": \"...\" }, return the thought text."""
    if not args_str or not args_str.strip():
        return None
    try:
        obj = json.loads(args_str)
        if isinstance(obj, dict) and 'thought' in obj:
            t = obj['thought']
            return str(t) if t is not None else None
    except (json.JSONDecodeError, TypeError):
        pass
    return None


class StreamingMatMasterAgent(MatMasterAgent):
    """
    MatMasterAgent that reports state in real time via event_callback.
    Overrides _on_assistant_message, _on_tool_call_start, and _on_tool_message
    to emit events.
    LLM 原生文本通过 thought 事件推送；think 工具的参数也作为 thought 推送，便于前端展示推理。
    tool_call 事件在 _on_tool_call_start 中推送（before-callback 修补参数之后），
    确保前端看到的是 callback 处理后的真实参数。
    """

    def __init__(
        self, event_callback: Callable[[str, str, Any], None] | None = None, **kwargs
    ):
        super().__init__(**kwargs)
        self.event_callback = event_callback
        # Inject LLM token streaming callback into the underlying agent
        self._on_llm_token = self._on_llm_token_cb
        self._current_stream_id: str | None = None
        self._stream_token_count: int = 0
        # inject compact lifecycle event callback into ContextManager
        if self.event_callback is not None:
            self.context_manager.set_compact_event_callback(
                lambda payload: self._emit('System', 'context_compaction', payload)
            )

    def _emit(self, source: str, event_type: str, content: Any, **extra: Any) -> None:
        if self.event_callback:
            self.event_callback(source, event_type, content, **extra)

    def _on_llm_token_cb(self, delta: str) -> None:
        """Emit streamed thought deltas for each token from the LLM."""
        agent_name = normalize_event_source(getattr(self, '_agent_name', None))
        if self._current_stream_id is None:
            self._begin_llm_stream(agent_name)
        self._emit(
            agent_name,
            'thought',
            delta,
            stream_state='streaming',
            stream_id=self._current_stream_id,
        )
        self._stream_token_count += len(delta) if isinstance(delta, str) else 0

    def _begin_llm_stream(
        self, agent_name: str, context: str = 'step_execution'
    ) -> None:
        """Emit streamed thought start marker and track stream state."""
        import uuid as _uuid

        self._current_stream_id = f"str_{_uuid.uuid4().hex[:12]}"
        self._stream_token_count = 0
        self._emit(
            agent_name,
            'thought',
            '',
            stream_state='start',
            context=context,
            stream_id=self._current_stream_id,
        )

    def _end_llm_stream(self, agent_name: str) -> None:
        """Emit streamed thought end marker and clear stream state."""
        if self._current_stream_id is not None:
            self._emit(
                agent_name,
                'thought',
                '',
                stream_state='end',
                stream_id=self._current_stream_id,
                token_count=self._stream_token_count,
            )
            self._current_stream_id = None
            self._stream_token_count = 0

    def _step(self) -> bool:
        """Override _step to wrap the MatMasterAgent LLM call with thought stream markers.

        _query_with_context_recovery() already calls self.llm.query_stream() directly when
        self._on_llm_token is set, so we only need to emit the stream boundary markers here.
        The previous monkey-patch approach (self.llm.query = _streaming_query) caused infinite
        recursion: _streaming_query → query_stream → BaseLLM.query_stream → self.query()
        → monkey-patched _streaming_query → ... when dialog.tools is present (OpenAILLM falls
        back to super().query_stream() which calls self.query()).
        """
        _stack_depth = len(_tb.extract_stack())
        logging.info(
            '[DIAG] StreamingMatMasterAgent._step() entered: stack_depth=%d, recursion_limit=%d',
            _stack_depth,
            sys.getrecursionlimit(),
        )
        if _stack_depth > sys.getrecursionlimit() - 100:
            logging.error(
                '[DIAG] NEAR RECURSION LIMIT in _step()! stack_depth=%d\nStack:\n%s',
                _stack_depth,
                ''.join(_tb.format_stack(limit=40)),
            )
        agent_name = normalize_event_source(getattr(self, '_agent_name', None))
        self._begin_llm_stream(agent_name, context='step_execution')
        try:
            return super()._step()
        finally:
            self._end_llm_stream(agent_name)

    def _on_assistant_message(self, msg: AssistantMessage) -> None:
        agent_name = normalize_event_source(getattr(self, '_agent_name', None))
        # 始终推送 LLM 原生文本（含空字符串），前端可区分空与有内容
        native_text = msg.content if msg.content is not None else ''
        self._emit(agent_name, 'thought', native_text)
        if msg.tool_calls:
            for tc in msg.tool_calls:
                # think 工具的参数作为"思考"再推一条，方便前端当作文本展示
                if tc.function.name == 'think':
                    thought_text = _extract_think_content(tc.function.arguments or '')
                    if thought_text:
                        self._emit(agent_name, 'thought', thought_text)
                # skill_hit 跟踪（skill_name 不被 before-callback 修改，此处可安全读取）
                if tc.function.name == 'use_skill':
                    try:
                        args = json.loads(tc.function.arguments or '{}')
                        if isinstance(args, dict) and args.get('skill_name'):
                            name = args.get('skill_name')
                            # 只把"真实技能"记为 skill_hit，排除工具名（如 mat_sn_*、mat_sg_* 等）
                            registry = getattr(self, 'skill_registry', None)
                            if registry is not None and getattr(
                                registry, 'get_skill', None
                            ):
                                if registry.get_skill(name) is not None:
                                    self._emit('MatMaster', 'skill_hit', name)
                    except (json.JSONDecodeError, TypeError):
                        pass
        # NOTE: tool_call 事件不再从此处推送。
        # 改为在 _on_tool_call_start() 中推送，此时 before-callback 已完成参数修补
        # （如 DPA 模型别名 -> OSS URL、bohr_job_id 自动补全等），
        # 前端看到的是 callback 处理后的真实参数。

    def _on_tool_call_start(self, tool_call) -> None:
        """Emit tool_call event AFTER before-callbacks have patched the args.

        This ensures the frontend displays the resolved arguments (e.g. DPA
        model alias resolved to OSS URL, auto-filled bohr_job_id, etc.)
        rather than the raw LLM output.
        """
        args_raw = tool_call.function.arguments or ''
        try:
            args_payload = json.loads(args_raw) if args_raw.strip() else {}
        except (json.JSONDecodeError, TypeError):
            args_payload = args_raw
        self._emit(
            'MatMaster',
            'tool_call',
            {'id': tool_call.id, 'name': tool_call.function.name, 'args': args_payload},
        )

    def _on_tool_message(self, msg: ToolMessage) -> None:
        logging.info(
            '[flow] StreamingMatMasterAgent._on_tool_message entered name=%s id=%s',
            msg.name,
            getattr(msg, 'tool_call_id', None),
        )
        result = (
            msg.content
            if isinstance(msg.content, dict)
            else {'message': msg.content or ''}
        )
        meta = getattr(msg, 'meta', None) or {}
        info = meta.get('info') or {}
        payload: dict = {
            'id': msg.tool_call_id,
            'name': msg.name,
            'result': result,
            'info': info,
        }
        # report_url 已在 result 内（agent 写入 observation['report_url']），不再重复写顶层
        self._emit('MatMaster', 'tool_result', payload)

        # Model-visible hinting: when webpage tool indicates blocked domains,
        # emit an extra thought to push the agent to stop retrying and conclude.
        try:
            if msg.name == 'extract_info_from_webpage':
                guidance = None
                if isinstance(result, dict):
                    guidance = result.get('web_fetch_guidance')
                if guidance:
                    agent_name = normalize_event_source(
                        getattr(self, '_agent_name', None)
                    )
                    self._emit(agent_name, 'thought', str(guidance))
        except Exception:
            pass
        logging.info(
            '[flow] StreamingMatMasterAgent._on_tool_message done name=%s',
            msg.name,
        )
