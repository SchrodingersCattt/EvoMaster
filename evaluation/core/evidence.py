"""Evidence layer for MATTER evaluation.

This module defines the standardised evidence format (EvidenceBundle) and the
EvidenceExtractor that converts a raw trajectory JSON file into an
EvidenceBundle. The evaluator only depends on EvidenceBundle and does not
require any runtime-specific mapping file by default.

Design principles
-----------------
* ``EventRecord``   – abstract, agent-action-level events (used by rule-based checks)
* ``ToolCallRecord`` – raw tool call log with name + description (used by LLM judge)
* ``ArtifactRecord`` – output files / data produced during the run
* ``TokenUsage``     – scalar usage snapshot; on the bundle, ``token_usage_last_turn``
  holds the **last** model turn (max ``step_id``; tie-break by later record) and
  ``token_usage_run`` holds the sum over all turns.
* ``EvidenceBundle`` – single input to the evaluator
* ``EvidenceExtractor`` – converts trajectory JSON → EvidenceBundle

Runtime decoupling
------------------
`EvidenceExtractor` does not assume any default tool-name mapping. Runtime-
specific compatibility mappings can be injected by callers via `mapping_path`.
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    """Abstract event type used by rule-based evaluator checks.

    Deliberately tool-agnostic; the mapping from tool_name → EventType lives
    in ``evidence_mapping.yaml``.
    """

    STRUCTURE_RETRIEVAL = 'structure_retrieval'
    STRUCTURE_CONSTRUCTION = 'structure_construction'
    CALCULATION_EXECUTION = 'calculation_execution'
    SCRIPT_EXECUTION = 'script_execution'
    FILE_EDITING = 'file_editing'
    VALIDATION = 'validation'
    DATA_ANALYSIS = 'data_analysis'
    OTHER = 'other'


class SourceType(str, Enum):
    """Constraint-source type; records *where* a result came from."""

    DATABASE = 'database'
    SCIENTIFIC_LIBRARY = 'scientific_library'
    MCP_TOOL = 'mcp_tool'
    BASH_SCRIPT = 'bash_script'
    MODEL_ONLY = 'model_only'
    UNKNOWN = 'unknown'


class CallStatus(str, Enum):
    """Fine-grained tool-call outcome (replaces ``success: bool``)."""

    SUCCESS = 'success'
    EMPTY = 'empty'  # call succeeded but returned no data
    FAILED = 'failed'  # tool raised an error
    TIMEOUT = 'timeout'  # call timed out
    BLOCKED = 'blocked'  # blocked by ToolGuard
    INTERRUPTED = 'interrupted'  # cancelled by user / system


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class EventRecord(BaseModel):
    """One abstract scientific-process event.

    Used by deterministic rule-based evaluator checks (e.g.
    ``event_type_called``, ``source_type_used``).  Does **not** reference any
    specific tool name.
    """

    step: int = Field(description='Step index in the trajectory (1-based)')
    event_type: EventType = Field(description='Abstract event category')
    source_type: SourceType = Field(description='Where the result came from')
    succeeded: bool = Field(description='Whether the underlying call succeeded')
    detail: str = Field(default='', description='Short human-readable note')


class ToolCallRecord(BaseModel):
    """Raw tool-call log entry.

    Kept for LLM judge and human review.  Contains enough context for the
    judge to assess grounding and efficiency without access to the live runtime.
    """

    step: int = Field(description='Step index (1-based)')
    call_index: int = Field(
        default=0,
        description='Index within the step (a single step can issue multiple calls)',
    )
    tool_name: str = Field(description='Name of the tool that was called')
    tool_description: str = Field(
        default='',
        description='Description from ToolSpec (may be empty for legacy trajectories)',
    )
    args: dict[str, Any] = Field(
        default_factory=dict,
        description='Parsed tool arguments (JSON-decoded)',
    )
    status: CallStatus = Field(
        default=CallStatus.SUCCESS,
        description='Fine-grained call outcome',
    )
    observation_excerpt: str = Field(
        default='',
        description=(
            'First 500 chars of the tool observation, verbatim (no LLM summarisation). '
            'Used by the judge to assess whether the agent consumed the result.'
        ),
    )


class ArtifactRecord(BaseModel):
    """An output file or data artefact produced during the run."""

    path: str = Field(description='Relative path inside the workspace')
    artifact_type: str = Field(
        default='unknown',
        description="E.g. 'cif', 'csv', 'json', 'log', 'plot'",
    )
    size_bytes: int | None = Field(default=None)


class TokenUsage(BaseModel):
    """LLM token usage for the **last assistant / model turn** in the trajectory.

    ``EvidenceExtractor`` picks the step with the greatest ``step_id`` that carries
    non-empty ``assistant_message.meta.usage`` (tie: later occurrence wins).
    ``prompt_tokens`` is the input size for that call (OpenAI/Anthropic style).
    """

    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    cache_read_tokens: int = Field(default=0)

    def add(self, other: dict[str, int]) -> None:
        """Accumulate a per-step usage dict in-place."""
        self.prompt_tokens += other.get('prompt_tokens', 0)
        self.completion_tokens += other.get('completion_tokens', 0)
        self.total_tokens += other.get('total_tokens', 0)
        self.cache_read_tokens += other.get('cache_read_tokens', 0)

    @classmethod
    def from_usage_dict(cls, raw: dict[str, Any]) -> TokenUsage:
        """Normalise a single-turn ``usage`` dict from trajectory meta."""
        pt = int(raw.get('prompt_tokens') or 0)
        ct = int(raw.get('completion_tokens') or 0)
        tt = int(raw.get('total_tokens') or 0)
        cr = int(raw.get('cache_read_tokens') or 0)
        if not cr and raw.get('cache_read_input_tokens') is not None:
            try:
                cr = int(raw['cache_read_input_tokens'])
            except (TypeError, ValueError):
                cr = 0
        # Claude-style breakdown (if trajectory ever embeds CLI-shaped usage)
        if pt == 0 and (
            raw.get('input_tokens') is not None
            or raw.get('cache_creation_input_tokens') is not None
            or raw.get('cache_read_input_tokens') is not None
        ):
            try:
                inp = int(raw.get('input_tokens') or 0)
                ccreate = int(raw.get('cache_creation_input_tokens') or 0)
                cr2 = int(raw.get('cache_read_input_tokens') or 0)
            except (TypeError, ValueError):
                inp, ccreate, cr2 = 0, 0, 0
            pt = inp + ccreate + cr2
            cr = cr2 or cr
            if ct == 0 and raw.get('output_tokens') is not None:
                try:
                    ct = int(raw['output_tokens'])
                except (TypeError, ValueError):
                    pass
        if tt == 0 and (pt or ct):
            tt = pt + ct
        return cls(
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
            cache_read_tokens=cr,
        )

    @property
    def total_tokens_effective(self) -> int:
        """Cache-adjusted total: aligns with Claude Code's token accounting."""
        if self.cache_read_tokens > 0:
            return self.total_tokens - self.cache_read_tokens
        return self.total_tokens


class EvidenceBundle(BaseModel):
    """Standardised evidence format consumed by the evaluator.

    The evaluator depends **only** on this model; it has no direct dependency
    on the trajectory schema or any runtime detail.
    """

    task_id: str = Field(description='Task / question ID')
    final_answer: str = Field(
        default='',
        description='Final answer text produced by the agent',
    )
    events: list[EventRecord] = Field(
        default_factory=list,
        description='Abstract event log (rule-based checks)',
    )
    tool_calls: list[ToolCallRecord] = Field(
        default_factory=list,
        description='Raw tool-call log (LLM judge / human review)',
    )
    artifacts: list[ArtifactRecord] = Field(
        default_factory=list,
        description='Output files / artefacts',
    )
    model_name: str | None = Field(
        default=None,
        description='Base model name used during the run (from LLM config or API response)',
    )
    token_usage_last_turn: TokenUsage = Field(
        default_factory=TokenUsage,
        description='Token usage for the last model turn in the trajectory (max step_id)',
    )
    token_usage_run: TokenUsage = Field(
        default_factory=TokenUsage,
        description=(
            'Summed usage over all model turns in the trajectory, or the whole-run '
            'aggregate when the bundle is built from devshell summary only. '
            '``token_budget`` checklist compares ``total_tokens_effective`` here '
            '(cache-read deducted) to the reference ceiling.'
        ),
    )
    total_steps: int = Field(default=0, description='Total number of agent steps')
    run_status: str = Field(
        default='unknown',
        description="Terminal run status ('completed', 'failed', etc.)",
    )
    duration_ms: int = Field(
        default=0,
        description='Wall-clock time for the mat task run (set by runner, not trajectory).',
    )
    workspace_dir: str = Field(
        default='',
        description='Absolute path to the task workspace for artifact-based checks.',
    )
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description='Arbitrary extra metadata from the trajectory',
    )


# ---------------------------------------------------------------------------
# Evidence Extractor
# ---------------------------------------------------------------------------

_OBSERVATION_EXCERPT_LEN = 500


class EvidenceExtractor:
    """Convert a trajectory JSON file into an :class:`EvidenceBundle`.

    This class accepts an optional ``mapping_path`` that determines how tool
    calls are classified into EventTypes. When ``mapping_path`` is omitted, the
    extractor stays runtime-agnostic and simply skips tool-name classification.

    Parameters
    ----------
    mapping_path:
        Optional path to an evidence mapping YAML file (tool → event type
        mappings). When omitted, no runtime-specific event mapping is loaded.
    agent_name_filter:
        If set, only process trajectory entries whose ``agent_name`` matches.
        Useful in multi-agent runs (planner + solver).
    """

    def __init__(
        self,
        mapping_path: Path | str | None = None,
        agent_name_filter: str | None = None,
    ) -> None:
        self._mapping_path = Path(mapping_path) if mapping_path else None
        self._agent_name_filter = agent_name_filter
        self._mapping: list[dict[str, Any]] = []
        if self._mapping_path is not None:
            self._load_mapping()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(
        self,
        trajectory_path: Path | str,
        task_id: str = '',
        final_answer: str = '',
    ) -> EvidenceBundle:
        """Extract an :class:`EvidenceBundle` from a trajectory JSON file.

        Parameters
        ----------
        trajectory_path:
            Path to the trajectory ``.json`` file written by the agent.
        task_id:
            Override the task_id (defaults to value read from trajectory).
        final_answer:
            Final answer text (injected from the runner; not stored in traj).
        """
        traj_path = Path(trajectory_path)
        if not traj_path.exists():
            logger.warning('Trajectory file not found: %s', traj_path)
            return EvidenceBundle(
                task_id=task_id,
                final_answer=final_answer,
                duration_ms=0,
                workspace_dir='',
            )

        try:
            raw: list[dict[str, Any]] = json.loads(
                traj_path.read_text(encoding='utf-8')
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning('Failed to read trajectory %s: %s', traj_path, exc)
            return EvidenceBundle(
                task_id=task_id,
                final_answer=final_answer,
                duration_ms=0,
                workspace_dir='',
            )

        return self._build_bundle(raw, task_id=task_id, final_answer=final_answer)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_mapping(self) -> None:
        if self._mapping_path is None:
            self._mapping = []
            return
        if not self._mapping_path.exists():
            logger.warning(
                'evidence_mapping.yaml not found at %s; event classification will be empty',
                self._mapping_path,
            )
            self._mapping = []
            return
        try:
            data = yaml.safe_load(self._mapping_path.read_text(encoding='utf-8'))
            self._mapping = data.get('mappings', []) if isinstance(data, dict) else []
        except Exception as exc:  # noqa: BLE001
            logger.warning('Failed to load evidence_mapping.yaml: %s', exc)
            self._mapping = []

    def _build_bundle(
        self,
        raw: list[dict[str, Any]],
        task_id: str,
        final_answer: str,
    ) -> EvidenceBundle:
        # Determine task_id from first entry if not provided
        if not task_id and raw:
            traj = raw[0].get('trajectory', {})
            task_id = traj.get('task_id', '')

        # Build tool-description map from dialogs
        tool_desc_map = self._build_tool_description_map(raw)

        # Determine model_name from first step's assistant_message meta
        model_name = self._extract_model_name(raw)

        # Last model turn only: max step_id with usage (tie: later in file wins)
        best_usage_key: tuple[int, int] = (-1, -1)
        best_usage: dict[str, Any] | None = None
        run_usage = TokenUsage()
        step_serial = 0

        events: list[EventRecord] = []
        tool_calls: list[ToolCallRecord] = []

        total_steps = 0
        run_status = 'unknown'

        for entry in raw:
            traj = entry.get('trajectory', {})
            agent_name = traj.get('agent_name', '')
            if self._agent_name_filter and agent_name != self._agent_name_filter:
                continue

            run_status = entry.get('status', run_status)

            for step_dict in traj.get('steps', []):
                step_id = step_dict.get('step_id', 0)
                total_steps = max(total_steps, step_id)
                step_serial += 1

                # Keep usage from the latest step_id; same id → last occurrence
                asst_msg = step_dict.get('assistant_message', {})
                meta = asst_msg.get('meta', {}) if isinstance(asst_msg, dict) else {}
                usage = meta.get('usage', {})
                if isinstance(usage, dict) and usage:
                    key = (step_id, step_serial)
                    if best_usage is None or key > best_usage_key:
                        best_usage_key = key
                        best_usage = usage
                    tu_step = TokenUsage.from_usage_dict(usage)
                    run_usage.add(
                        {
                            'prompt_tokens': tu_step.prompt_tokens,
                            'completion_tokens': tu_step.completion_tokens,
                            'total_tokens': tu_step.total_tokens,
                            'cache_read_tokens': tu_step.cache_read_tokens,
                        }
                    )

                # Collect tool responses indexed by call_id
                tool_responses = step_dict.get('tool_responses', [])
                resp_by_id: dict[str, dict[str, Any]] = {}
                for tr in tool_responses:
                    if isinstance(tr, dict):
                        cid = tr.get('tool_call_id', '')
                        if cid:
                            resp_by_id[cid] = tr

                # Process tool calls
                raw_tool_calls = (
                    asst_msg.get('tool_calls', []) if isinstance(asst_msg, dict) else []
                ) or []

                for call_idx, tc in enumerate(raw_tool_calls):
                    if not isinstance(tc, dict):
                        continue
                    func = tc.get('function', {})
                    tool_name = func.get('name', '')
                    if not tool_name:
                        continue

                    # Parse arguments
                    raw_args = func.get('arguments', '{}')
                    args = self._parse_args(raw_args)

                    # Determine call status
                    call_id = tc.get('id', '')
                    resp = resp_by_id.get(call_id, {})
                    status = self._parse_call_status(resp)

                    # Build observation excerpt
                    observation_excerpt = self._make_excerpt(resp)

                    # Look up tool description
                    tool_description = tool_desc_map.get(tool_name, '')

                    tcr = ToolCallRecord(
                        step=step_id,
                        call_index=call_idx,
                        tool_name=tool_name,
                        tool_description=tool_description,
                        args=args,
                        status=status,
                        observation_excerpt=observation_excerpt,
                    )
                    tool_calls.append(tcr)

                    # Map to EventRecord
                    event = self._map_tool_to_event(
                        tool_name=tool_name,
                        args=args,
                        step=step_id,
                        status=status,
                    )
                    if event:
                        events.append(event)

        last_turn_usage = (
            TokenUsage.from_usage_dict(best_usage)
            if best_usage is not None
            else TokenUsage()
        )

        return EvidenceBundle(
            task_id=task_id,
            final_answer=final_answer,
            events=events,
            tool_calls=tool_calls,
            model_name=model_name,
            token_usage_last_turn=last_turn_usage,
            token_usage_run=run_usage,
            total_steps=total_steps,
            run_status=run_status,
        )

    def _build_tool_description_map(self, raw: list[dict[str, Any]]) -> dict[str, str]:
        """Build tool_name → description map from the dialogs in the trajectory.

        The first entry's ``dialogs[0].tools`` list contains ToolSpec objects
        serialised as ``{type: function, function: {name, description, parameters}}``.
        """
        desc_map: dict[str, str] = {}
        if not raw:
            return desc_map
        traj = raw[0].get('trajectory', {})
        for dialog in traj.get('dialogs', []):
            if not isinstance(dialog, dict):
                continue
            for tool_spec in dialog.get('tools', []):
                if not isinstance(tool_spec, dict):
                    continue
                func = tool_spec.get('function', {})
                name = func.get('name', '')
                desc = func.get('description', '')
                if name:
                    desc_map[name] = desc
        return desc_map

    def _extract_model_name(self, raw: list[dict[str, Any]]) -> str | None:
        """Extract model name from the first step's assistant_message meta."""
        for entry in raw:
            traj = entry.get('trajectory', {})
            # Check top-level meta first (if populated by Phase 0 fix)
            meta = traj.get('meta', {})
            if isinstance(meta, dict) and meta.get('model_name'):
                return str(meta['model_name'])
            # Fall back to first step's assistant_message.meta
            for step in traj.get('steps', []):
                asst = step.get('assistant_message', {})
                if isinstance(asst, dict):
                    ameta = asst.get('meta', {})
                    model = ameta.get('model')
                    if model:
                        return str(model)
        return None

    def _parse_args(self, raw_args: Any) -> dict[str, Any]:
        """Parse tool arguments to a dict (JSON string or already a dict)."""
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        return {}

    def _parse_call_status(self, response: dict[str, Any]) -> CallStatus:
        """Determine fine-grained CallStatus from a tool response dict."""
        if not response:
            return CallStatus.SUCCESS  # no response recorded → assume success

        content = response.get('content', '')
        meta_info = (response.get('meta') or {}).get('info', {})

        # Explicit success field in meta.info
        if isinstance(meta_info, dict):
            if 'success' in meta_info:
                if not meta_info['success']:
                    return CallStatus.FAILED
                # check for empty success
                if not content or (isinstance(content, str) and not content.strip()):
                    return CallStatus.EMPTY
                return CallStatus.SUCCESS

        # Detect guard-blocked calls
        if isinstance(content, str):
            lower = content.lower()
            if 'blocked' in lower or 'loop detected' in lower or 'guard' in lower:
                return CallStatus.BLOCKED
            if 'timeout' in lower or 'timed out' in lower:
                return CallStatus.TIMEOUT
            if 'interrupted' in lower or 'cancelled' in lower:
                return CallStatus.INTERRUPTED
            # Try JSON status field
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    status_str = str(parsed.get('status', '')).lower()
                    if status_str == 'success':
                        result = parsed.get(
                            'result', parsed.get('data', parsed.get('content'))
                        )
                        if (
                            result is None
                            or result == ''
                            or result == []
                            or result == {}
                        ):
                            return CallStatus.EMPTY
                        return CallStatus.SUCCESS
                    elif status_str in ('error', 'failed', 'failure'):
                        return CallStatus.FAILED
                    elif status_str == 'timeout':
                        return CallStatus.TIMEOUT
            except (json.JSONDecodeError, ValueError):
                pass
            # Empty content → EMPTY
            if not content.strip():
                return CallStatus.EMPTY

        return CallStatus.SUCCESS

    def _make_excerpt(self, response: dict[str, Any]) -> str:
        """Return first ``_OBSERVATION_EXCERPT_LEN`` chars of observation (verbatim)."""
        if not response:
            return ''
        content = response.get('content', '')
        if not isinstance(content, str):
            try:
                content = json.dumps(content, ensure_ascii=False)
            except Exception:  # noqa: BLE001
                content = str(content)
        return content[:_OBSERVATION_EXCERPT_LEN]

    def _map_tool_to_event(
        self,
        tool_name: str,
        args: dict[str, Any],
        step: int,
        status: CallStatus,
    ) -> EventRecord | None:
        """Look up tool_name in the mapping table and return an EventRecord.

        Returns ``None`` if the tool is not in the mapping (it will still
        appear in ``tool_calls`` for the LLM judge).
        """
        for rule in self._mapping:
            # Pattern match: prefix or exact
            pattern: str = rule.get('pattern', '')
            if not self._name_matches(tool_name, pattern):
                continue

            # Optional arg-condition check
            when: dict[str, str] = rule.get('when_args_contains', {})
            if when and not self._args_match(args, when):
                continue

            event_type = EventType(rule.get('event_type', EventType.OTHER.value))
            source_type = SourceType(rule.get('source_type', SourceType.UNKNOWN.value))

            return EventRecord(
                step=step,
                event_type=event_type,
                source_type=source_type,
                succeeded=status == CallStatus.SUCCESS,
                detail=rule.get('detail', tool_name),
            )
        return None

    @staticmethod
    def _name_matches(tool_name: str, pattern: str) -> bool:
        """Return True if tool_name matches a pattern.

        Supports:
        - exact match: ``mat_struct_db_fetch_structures_from_db``
        - prefix match ending with ``*``: ``mat_struct_db_*``
        - substring match wrapping with ``*``: ``*fetch*``
        """
        if pattern.endswith('*') and pattern.startswith('*'):
            return pattern[1:-1] in tool_name
        if pattern.endswith('*'):
            return tool_name.startswith(pattern[:-1])
        if pattern.startswith('*'):
            return tool_name.endswith(pattern[1:])
        return tool_name == pattern

    @staticmethod
    def _args_match(args: dict[str, Any], when: dict[str, str]) -> bool:
        """Return True if all when_args_contains conditions are satisfied.

        Each condition is ``{arg_key: substring_to_find_in_value}``.
        """
        for key, substring in when.items():
            val = args.get(key, '')
            if substring not in str(val):
                return False
        return True
