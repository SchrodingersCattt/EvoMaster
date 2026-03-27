"""按 tool-transaction 边界截取对话尾部。"""

from __future__ import annotations

from evomaster.utils.types import Message


def safe_tail(
    other_messages: list[Message],
    n_turns: int,
) -> list[Message]:
    """从 other_messages（非 system 消息列表）的尾部取 n_turns 个完整 tool-transaction。

    一个 tool-transaction 定义为：
      1 个 AssistantMessage（可能带 tool_calls）+ 其后紧跟的所有 ToolMessage（0 个或多个）

    规则：
    - 从尾部向前扫描，以 AssistantMessage 为 transaction 起点。
    - 每找到一个完整 transaction（assistant + 其所有 tool results），计数 +1。
    - 收集到 n_turns 个 transaction 后停止，返回这段消息。
    - 若 other_messages 中 assistant 消息不足 n_turns 个，返回全部 other_messages。
    - 保证返回的片段：
        * 不以孤立 ToolMessage 开头
        * 每个 AssistantMessage 的所有 tool_call_id 都有对应 ToolMessage
    """
    if not other_messages:
        return []

    # 从尾部向前找 transaction 边界
    # 先把消息按 transaction 分组（从前往后）
    transactions: list[list[Message]] = []
    i = 0
    while i < len(other_messages):
        msg = other_messages[i]
        if msg.role.value == 'assistant':
            # 收集这个 assistant 及其后续所有 tool 消息
            tx: list[Message] = [msg]
            j = i + 1
            while j < len(other_messages) and other_messages[j].role.value == 'tool':
                tx.append(other_messages[j])
                j += 1
            transactions.append(tx)
            i = j
        else:
            # user 消息或其他非 assistant/tool 消息：单独作为一个 transaction
            transactions.append([msg])
            i += 1

    # 取最后 n_turns 个 transaction
    tail_transactions = (
        transactions[-n_turns:] if len(transactions) >= n_turns else transactions
    )
    result: list[Message] = []
    for tx in tail_transactions:
        result.extend(tx)
    return result
