# ReadTool Optimization Design

## Problem

当前 ReadTool 存在三个问题：

1. **Agent 行为质量**：使用 `maybe_truncate` 做 16000 字符中间截断，截断是静默的。Agent 误以为看到了完整文件，基于残缺内容做错误的编辑决策。
2. **Token 效率**：大文件一次性全量返回，浪费 token 预算。
3. **与 evomaster 耦合**：从 `evomaster.agent.tools.builtin.editor` 导入 `MAX_OUTPUT_SIZE` 和 `maybe_truncate`，阻碍 matmaster builtin tools 独立化。

## Decision Summary

| 项 | 选择 |
|---|---|
| 截断策略 | 混合：超限报错 + 总行数 + 前 50 行预览 |
| 度量单位 | 行数为主 + 字符数兜底 |
| 默认限制 | 2000 行 |
| Session 层 | 不改动（全量读取后在 ReadTool 层切片） |
| 参数命名 | `offset` + `limit` 替换 `line_range` |
| 对其他工具影响 | 无（EditTool / WriteTool / ReadTracker 不变） |

## Design

### Constants

在 `matmaster/tools/builtin/read_tool.py` 顶部定义：

```python
MAX_READ_LINES = 2000       # 默认行数上限
MAX_READ_CHARS = 200_000    # 字符数硬上限（兜底：防 minified JSON / base64 等少行大内容）
PREVIEW_LINES = 50          # 超限报错时附带的预览行数
```

### Parameter Schema

旧参数 `line_range: [start, end]` 替换为两个独立整数参数：

```python
json_schema = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "Absolute path to the file to read.",
        },
        "offset": {
            "type": "integer",
            "description": "Line number to start reading from (1-indexed). Defaults to 1.",
        },
        "limit": {
            "type": "integer",
            "description": "Number of lines to read. Defaults to reading to end of file (up to 2000 lines).",
        },
    },
    "required": ["file_path"],
}
```

### Tool Description

```
Read file contents with line numbers (cat -n format).

Usage:
- ALWAYS use read_file to read files. NEVER use cat/head/tail via execute_bash.
- Files up to 2000 lines are returned in full. Larger files return an error with preview.
- Use offset and limit to read specific portions of large files.
- Always read a file before attempting to edit or overwrite it.
```

### Execution Logic

```
_execute(arguments):
  1. 提取参数: file_path, offset (可选), limit (可选)
  2. session.is_file(file_path) — 不存在则报错
  3. session.read_file(file_path) — 全量读取
  4. (mark_read 延迟到成功返回内容时，见下)
  5. lines = content.splitlines(), total = len(lines)
     (使用 splitlines() 统一处理 \n 和 \r\n，且不会因末尾换行多出空行)
  6. 分支:

  参数校验:
    offset 若提供则必须 >= 1, 否则报错
    limit 若提供则必须 >= 1, 否则报错

  无 offset/limit (全文读取模式):
    total <= MAX_READ_LINES → mark_read() + 正常返回全部内容 (带行号)
    total > MAX_READ_LINES  → 不 mark_read + 返回超限报错 + 总行数 + 前 PREVIEW_LINES 行预览

  有 offset 和/或 limit (范围读取模式):
    start = offset (默认 1)
    校验: 1 <= start <= total, 否则报错
    mark_read() (校验通过后标记，Agent 主动指定了范围视为有意读取)
    count = limit (默认 total - start + 1, 上限 MAX_READ_LINES)
    end = min(start + count - 1, total)
    actual_count = end - start + 1
    返回 lines[start-1:end] (带行号, init_line=start)
    如果 actual_count < 请求的 count (无论 count 来自显式 limit 还是隐式上限):
      在输出末尾附加: [Note: showing {actual_count} of {remaining} remaining lines. ...]

  字符数兜底 (适用于所有模式):
    对最终要返回的格式化内容检查总字符数
    如果超过 MAX_READ_CHARS, 按字符截断并附加提示:
      [Output truncated at {MAX_READ_CHARS} chars. Use offset/limit for smaller ranges.]
```

### Over-Limit Error Format

```
Error: file has {total} lines, exceeds read limit ({MAX_READ_LINES} lines).
Use offset and limit to read portions, e.g. offset=1, limit=2000 for the first 2000 lines.

Preview (first {PREVIEW_LINES} lines):
     1\t{line1}
     2\t{line2}
   ...
    50\t{line50}
```

### Key Behaviors

1. **mark_read 时机**：仅在成功返回**未被截断**的文件内容时标记。以下情况**不标记**：全文超限（error + preview）、字符数兜底截断（`_apply_char_limit` 触发）。Agent 需要先用 offset/limit 读取到未被截断的内容，才能获得编辑权限。
2. **范围读取截断通知**：当实际返回行数少于请求范围（无论原因是 limit 超过 MAX_READ_LINES、还是 offset-only 模式命中上限），都在输出末尾附加提示，告知 Agent 还有多少行未读。避免任何形式的静默截断。
3. **只传 offset 不传 limit**：从 offset 行开始，读到文件末尾，但总量不超过 MAX_READ_LINES 行。如果被截断，附加通知。
4. **只传 limit 不传 offset**：从第 1 行开始，读取 limit 行（受 MAX_READ_LINES 上限约束）。
5. **参数校验**：offset 和 limit 若提供则必须 >= 1，否则返回明确错误。
6. **字符数兜底**：最终输出内容超过 MAX_READ_CHARS (200,000) 时，按字符截断并附加提示。防止 minified JSON、base64 等少行大内容撑爆 token 预算。
7. **移除 evomaster 依赖**：不再导入 `MAX_OUTPUT_SIZE` 和 `maybe_truncate`。保留 `_format_with_line_numbers` 方法但移除内部的 `maybe_truncate` 调用（行数限制 + 字符兜底已在上游完成）。

### Impact on Other Components

- **EditTool / WriteTool**：无需改动。依赖 `ReadTracker.has_been_read()`。新 ReadTool 在全文超限时不 mark_read，Agent 必须先用 offset/limit 读取目标区域才能获得编辑权限。这强化了 Read-Before-Modify 协议的语义：只有真正读到了内容才允许编辑。注意 EditTool 仍保留对 `evomaster.agent.tools.builtin.editor` 的 `maybe_truncate` / `MAX_OUTPUT_SIZE` / `SNIPPET_LINES` 依赖，这些在后续 EditTool 独立化时处理，不在本次范围内。
- **ReadTracker**：无需改动。
- **Exp 组装**（`matmaster/core/exp.py`）：`ReadTool(session=..., workdir=..., tracker=...)` 构造签名不变，无需改动。

## Test Plan

更新 `tests/matmaster/tools/test_read_tool.py`：

| 测试用例 | 验证点 |
|---------|--------|
| `test_read_full_within_limit` | <= 2000 行文件正常返回全部内容 |
| `test_read_exceeds_limit_returns_error_and_preview` | > 2000 行文件返回报错 + 总行数 + 前 50 行预览 |
| `test_read_with_offset_and_limit` | offset=100, limit=50 返回第 100-149 行 |
| `test_read_with_offset_only` | 只传 offset，读到末尾但不超过 MAX_READ_LINES |
| `test_read_with_limit_exceeds_max` | limit=5000 被截断为 MAX_READ_LINES 且输出包含截断通知 |
| `test_offset_only_truncated_with_notice` | 只传 offset 时被 MAX_READ_LINES 截断，输出包含通知 |
| `test_offset_out_of_range` | offset > 总行数时报错 |
| `test_tracker_not_marked_on_overlimit` | 全文超限时不 mark_read |
| `test_tracker_marked_on_ranged_read` | 范围读取时 mark_read |
| `test_read_with_only_limit` | 只传 limit 不传 offset，从第 1 行开始 |
| `test_read_empty_file` | 空文件正常返回，不报错 |
| `test_file_with_trailing_newline` | 末尾有 `\n` 的文件行数计算正确（splitlines 处理） |
| `test_offset_zero_rejected` | offset=0 报错 |
| `test_limit_negative_rejected` | limit=-1 报错 |
| `test_char_limit_truncation` | 少行但超 MAX_READ_CHARS 的内容被字符截断并附通知 |

## Files Changed

| 文件 | 变更 |
|------|------|
| `matmaster/tools/builtin/read_tool.py` | 重写：新常量、新 schema、新执行逻辑、移除 evomaster 导入 |
| `tests/matmaster/tools/test_read_tool.py` | 重写：覆盖所有新行为 |
