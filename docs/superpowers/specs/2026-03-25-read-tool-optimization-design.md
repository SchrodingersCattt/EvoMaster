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
| 度量单位 | 纯行数 |
| 默认限制 | 2000 行 |
| Session 层 | 不改动（全量读取后在 ReadTool 层切片） |
| 参数命名 | `offset` + `limit` 替换 `line_range` |
| 对其他工具影响 | 无（EditTool / WriteTool / ReadTracker 不变） |

## Design

### Constants

在 `matmaster/tools/builtin/read_tool.py` 顶部定义：

```python
MAX_READ_LINES = 2000       # 无 offset/limit 时的默认行数上限
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
Read the contents of a file with line numbers (cat -n format).

Usage:
- By default reads up to 2000 lines from the beginning.
- For large files, use offset and limit to read specific portions.
- When you already know which part of the file you need, only read that part.
- Always read a file before attempting to edit or overwrite it.
```

### Execution Logic

```
_execute(arguments):
  1. 提取参数: file_path, offset (可选), limit (可选)
  2. session.is_file(file_path) — 不存在则报错
  3. session.read_file(file_path) — 全量读取
  4. tracker.mark_read(file_path) — 无论后续是否超限都标记
  5. lines = content.splitlines(), total = len(lines)
     (使用 splitlines() 统一处理 \n 和 \r\n，且不会因末尾换行多出空行)
  6. 分支:

  无 offset/limit (全文读取模式):
    total <= MAX_READ_LINES → 正常返回全部内容 (带行号)
    total > MAX_READ_LINES  → 返回超限报错 + 总行数 + 前 PREVIEW_LINES 行预览

  有 offset 和/或 limit (范围读取模式):
    start = offset (默认 1)
    count = limit (默认 total - start + 1, 上限 MAX_READ_LINES)
    校验: 1 <= start <= total, 否则报错
    end = min(start + count - 1, total)
    返回 lines[start-1:end] (带行号, init_line=start)
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

1. **mark_read 时机**：`session.read_file()` 成功后立即标记，不管是否超限。确保 Agent 后续可以用 edit_file 修改该文件（Read-Before-Modify 协议不受影响）。
2. **limit 隐式截断 + 通知**：如果 Agent 显式传入的 `limit` 超过 MAX_READ_LINES，实际返回 MAX_READ_LINES 行，并在输出末尾附加提示：`[Note: requested {limit} lines, capped at {MAX_READ_LINES}. Use offset to continue reading.]`。避免 Agent 误以为已读到全部请求内容。
3. **只传 offset 不传 limit**：从 offset 行开始，读到文件末尾，但总量不超过 MAX_READ_LINES 行。
4. **只传 limit 不传 offset**：从第 1 行开始，读取 limit 行（受 MAX_READ_LINES 上限约束）。
5. **移除 evomaster 依赖**：不再导入 `MAX_OUTPUT_SIZE` 和 `maybe_truncate`。保留 `_format_with_line_numbers` 方法但移除内部的 `maybe_truncate` 调用（行数限制已在上游完成）。

### Impact on Other Components

- **EditTool / WriteTool**：无需改动。依赖 `ReadTracker.has_been_read()`，新 ReadTool 无论超限与否都 `mark_read()`。注意 EditTool 仍保留对 `evomaster.agent.tools.builtin.editor` 的 `maybe_truncate` / `MAX_OUTPUT_SIZE` / `SNIPPET_LINES` 依赖，这些在后续 EditTool 独立化时处理，不在本次范围内。
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
| `test_offset_out_of_range` | offset > 总行数时报错 |
| `test_tracker_marked_on_overlimit` | 超限时仍然 mark_read |
| `test_read_with_only_limit` | 只传 limit 不传 offset，从第 1 行开始 |
| `test_read_empty_file` | 空文件正常返回，不报错 |
| `test_file_with_trailing_newline` | 末尾有 `\n` 的文件行数计算正确（splitlines 处理） |

## Files Changed

| 文件 | 变更 |
|------|------|
| `matmaster/tools/builtin/read_tool.py` | 重写：新常量、新 schema、新执行逻辑、移除 evomaster 导入 |
| `tests/matmaster/tools/test_read_tool.py` | 重写：覆盖所有新行为 |
