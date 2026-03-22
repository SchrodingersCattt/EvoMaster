"""Dialog history helpers for MatMasterAgent."""

from evomaster.utils.types import ToolMessage


def sanitize_dialog_history(
    messages: list,
    logger=None,
) -> list:
    """Ensure every AssistantMessage with tool_calls has matching ToolMessages.

    Claude/Bedrock requires that each ``tool_use`` block is immediately
    followed by a ``tool_result`` block.  When a session is interrupted
    mid-step the tool results may never have been recorded, leaving orphaned
    tool_calls in the history.  This function inserts placeholder ToolMessages
    for any such orphans so the dialog is structurally valid before it is
    sent to the LLM.

    Args:
        messages: Parsed message objects (UserMessage / AssistantMessage /
            ToolMessage).
        logger: Optional logger for warning about injected placeholders.

    Returns:
        A new list with placeholder ToolMessages inserted where needed.
    """
    sanitized: list = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        sanitized.append(msg)

        tool_calls = getattr(msg, 'tool_calls', None) or []
        if not tool_calls:
            i += 1
            continue

        expected_ids: dict[str, str] = {}  # id -> function name
        for tc in tool_calls:
            tc_id = getattr(tc, 'id', None) or ''
            tc_name = ''
            fn = getattr(tc, 'function', None)
            if fn is not None:
                tc_name = getattr(fn, 'name', '') or ''
            expected_ids[tc_id] = tc_name

        found_ids: set[str] = set()
        j = i + 1
        while j < len(messages):
            next_msg = messages[j]
            tc_id = getattr(next_msg, 'tool_call_id', None)
            if tc_id is None:
                break
            found_ids.add(tc_id)
            j += 1

        missing_ids = set(expected_ids.keys()) - found_ids
        if missing_ids:
            if logger is not None:
                logger.warning(
                    'sanitize_dialog_history: %d orphaned tool_call(s) '
                    'detected; injecting placeholder tool_result(s): %s',
                    len(missing_ids),
                    missing_ids,
                )
            for tc in tool_calls:
                tc_id = getattr(tc, 'id', None) or ''
                if tc_id not in missing_ids:
                    continue
                tc_name = expected_ids.get(tc_id, 'unknown')
                sanitized.append(
                    ToolMessage(
                        tool_call_id=tc_id,
                        name=tc_name,
                        content={
                            'status': 'interrupted',
                            'observation': (
                                'Tool call was interrupted before completion; '
                                'result is unavailable. Please retry if needed.'
                            ),
                        },
                    )
                )
        i += 1
    return sanitized
