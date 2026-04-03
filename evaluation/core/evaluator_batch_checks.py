"""Batch-processing check methods for BinaryEvaluator.

Extracted from evaluator.py to keep that file under the 1000-line limit.
All functions here are pure static utilities — no class state needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .evidence import EvidenceBundle
    from .schemas import ReferenceAnswer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_tool_names(tool_name: str) -> list[str]:
    if '|' in tool_name:
        return [n.strip() for n in tool_name.split('|')]
    return [str(tool_name)]


# ---------------------------------------------------------------------------
# Batch checks (static, no class state)
# ---------------------------------------------------------------------------


def check_batch_single_variable_sweep(
    *,
    tool_calls: list[dict[str, Any]],
    evidence: EvidenceBundle | None,
    ref: ReferenceAnswer,
) -> tuple[bool, str]:
    """Verify that across multiple calls, only one parameter varies.

    Reference format (tool_name is optional; if absent, matches all calls):
        - tool_name: the MCP tool being called (optional; also read from value.tool_name)
        - tool_arg: the parameter that should vary (optional; also read from value.sweep_param)
        - value: dict with sweep_param/expected_values keys, or a bare list of expected values
    """
    sweep_cfg = ref.value if isinstance(ref.value, dict) else {}
    tool_name = ref.tool_name or sweep_cfg.get('tool_name')
    sweep_var = ref.tool_arg or sweep_cfg.get('sweep_param')
    if not sweep_var:
        return (
            False,
            'batch_single_variable_sweep requires sweep_param '
            '(via tool_arg or value.sweep_param)',
        )
    sweep_var = str(sweep_var)

    if tool_name:
        names = _split_tool_names(str(tool_name))
        matching_calls = [c for c in tool_calls if c.get('tool_name') in names]
    else:
        # tool-name-agnostic: match all calls that have the sweep parameter
        names = None
        matching_calls = [
            c for c in tool_calls if sweep_var in (c.get('tool_args') or {})
        ]

    if len(matching_calls) < 2:
        label = names if names else f'any call with {sweep_var!r}'
        return (
            False,
            f'need at least 2 calls to {label} for sweep check, found {len(matching_calls)}',
        )

    all_args = [call.get('tool_args', {}) for call in matching_calls]
    if not all_args[0]:
        return False, 'first call has no arguments'

    all_param_names = set(all_args[0].keys())

    # Check that only sweep_var changes across calls
    for param in all_param_names:
        if param == sweep_var:
            continue
        values = [str(args.get(param, '<missing>')) for args in all_args]
        if len(set(values)) > 1:
            return (
                False,
                f"parameter '{param}' varies across calls (expected constant): {values}",
            )

    sweep_values = [args.get(sweep_var, '<missing>') for args in all_args]
    if len({str(v) for v in sweep_values}) < 2:
        return False, f"sweep parameter '{sweep_var}' does not vary: {sweep_values}"

    # Prefer value.expected_values (dict form); fall back to bare list ref.value
    expected_vals = sweep_cfg.get('expected_values') if sweep_cfg else None
    if (
        expected_vals is None
        and ref.value is not None
        and not isinstance(ref.value, dict)
    ):
        expected_vals = ref.value if isinstance(ref.value, list) else [ref.value]
    if expected_vals is not None:
        expected_strs = {str(v) for v in expected_vals}
        actual_strs = {str(v) for v in sweep_values}
        if actual_strs != expected_strs:
            return (
                False,
                f'sweep values {actual_strs} do not match expected {expected_strs}',
            )

    return (
        True,
        f'single variable sweep verified: {sweep_var} varies, other params constant',
    )


def check_batch_tool_args_constant(
    *,
    tool_calls: list[dict[str, Any]],
    evidence: EvidenceBundle | None,
    ref: ReferenceAnswer,
) -> tuple[bool, str]:
    """Verify that across multiple calls, specified parameters remain constant.

    Reference format (tool_name is optional; if absent, matches all calls):
        - tool_name: the MCP tool being called (optional; also read from value.tool_name)
        - tool_arg: comma-separated param names that must be constant
                    (optional; also read from value.param_names)
        - value: dict with param_names/expected_constant keys, or param_name->value map
    """
    val_cfg = ref.value if isinstance(ref.value, dict) else {}
    tool_name = ref.tool_name or val_cfg.get('tool_name')
    param_arg = ref.tool_arg or val_cfg.get('param_names')
    if not param_arg:
        return (
            False,
            'batch_tool_args_constant requires param_names '
            '(via tool_arg or value.param_names)',
        )

    param_names = [p.strip() for p in str(param_arg).split(',')]

    if tool_name:
        names = _split_tool_names(str(tool_name))
        matching_calls = [c for c in tool_calls if c.get('tool_name') in names]
    else:
        # tool-name-agnostic: match all calls that have any of the constant params
        names = None
        matching_calls = [
            c
            for c in tool_calls
            if any(p in (c.get('tool_args') or {}) for p in param_names)
        ]

    if len(matching_calls) < 2:
        label = names if names else f'any call with {param_names}'
        return (
            False,
            f'need at least 2 calls to {label}, found {len(matching_calls)}',
        )

    all_args = [call.get('tool_args', {}) for call in matching_calls]

    for param in param_names:
        values = [args.get(param, '<missing>') for args in all_args]
        if len({str(v) for v in values}) > 1:
            return False, f"parameter '{param}' varies across calls: {values}"

        # Determine expected value: check ref.value dict fields, then expected_constant
        expected = None
        if isinstance(ref.value, dict):
            if param in ref.value:
                expected = ref.value[param]
            elif 'expected_constant' in ref.value:
                expected = ref.value['expected_constant']
        if expected is not None:
            actual = values[0]
            if str(actual) != str(expected):
                return (
                    False,
                    f"parameter '{param}' is {actual}, expected {expected}",
                )

    return True, f"batch parameters constant: {', '.join(param_names)}"


def check_batch_consistent_calls(
    *,
    tool_calls: list[dict[str, Any]],
    evidence: EvidenceBundle | None,
    ref: ReferenceAnswer,
) -> tuple[bool, str]:
    """Verify that calls follow a consistent pattern (e.g., same tool, same order).

    Reference format:
        - tool_name: comma-separated tool names or pipe-separated variants (required)
        - value: expected structure:
            {
                'min_calls': int,
                'max_calls': int,
                'pattern': 'sequential' | 'grouped',
                'tools': ['tool1', 'tool2', ...]  (for grouped pattern)
            }
    """
    if not ref.tool_name:
        return False, 'batch_consistent_calls requires tool_name'

    if not isinstance(ref.value, dict):
        return (
            False,
            'batch_consistent_calls requires value as dict with pattern config',
        )

    min_calls = int(ref.value.get('min_calls', 1))
    max_calls = int(ref.value.get('max_calls', 9999))
    pattern = ref.value.get('pattern', 'sequential')
    pattern_tools = ref.value.get('tools', [])

    matching_calls = tool_calls
    if isinstance(ref.tool_name, str) and ref.tool_name.strip():
        names = _split_tool_names(ref.tool_name)
        matching_calls = [c for c in tool_calls if c.get('tool_name') in names]

    if not (min_calls <= len(matching_calls) <= max_calls):
        return (
            False,
            f'call count {len(matching_calls)} not in range [{min_calls}, {max_calls}]',
        )

    if pattern == 'grouped' and pattern_tools:
        actual_sequence = [c.get('tool_name') for c in tool_calls]
        expected_len = len(pattern_tools)
        if len(actual_sequence) % expected_len != 0:
            return (
                False,
                f'sequence length {len(actual_sequence)} not multiple of '
                f'pattern length {expected_len}',
            )
        for i, tool_name in enumerate(actual_sequence):
            expected_tool = pattern_tools[i % expected_len]
            if tool_name != expected_tool:
                return (
                    False,
                    f'position {i}: expected {expected_tool}, got {tool_name}',
                )

    return (
        True,
        f'batch calls consistent: {len(matching_calls)} calls, pattern={pattern}',
    )
