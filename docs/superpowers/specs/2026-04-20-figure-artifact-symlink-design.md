# Figure Artifact Flat-View Symlink Design

## Problem

`chat-response-figures` 链路把工具产出的图片落到 sandbox 目录：

```
<execution_workdir>/.matmaster/figures/<tool_call_id>/artifacts/<file>
```

`execution_workdir` 由 session_directory 决定（未设置则默认 `/share`）。前端侧边栏通过 OSS `asset_url` 渲染，这条路径用户不关心。但在以下场景用户需要直接访问文件：

- 用户 SSH 进 Bohrium 远端在 `/share/foo` 下继续工作，想把刚生成的图片引用到别的脚本/文档里
- 本地会话（任何 `Session` 实现，包括未来可能的 `LocalSession`）下同样想直接 `ls` 看到图片

当前目录层级 `.matmaster/figures/<tool_call_id>/artifacts/` 对用户不友好：隐藏目录三层嵌套，`tool_call_id` 是 `call_abcd1234` 这种无语义 ID，每次对话都在新的 `<tool_call_id>/` 下产出，用户无法在一个可预期位置看到所有图。

## Goals

- 在 `<workdir>/.matmaster/figures/` 下提供一个扁平化视图，文件名用 manifest 里的 `figure_id` + 原扩展名
- 保留现有 sandbox 隔离（`.matmaster/figures/<tool_call_id>/artifacts/`）和所有 manifest/上传/路径穿越校验语义
- 扁平视图对前端渲染、`payload.figures`、`response_figures` 事件零影响
- 本地 Session 与远端 Bohrium Session 对称工作

## Non-Goals

- 不改 manifest 契约（`ARTIFACT_DIR` / `MANIFEST_PATH` 注入、字段、校验规则）
- 不改 OSS 上传链路、`FigureDescriptor`、`response_figures` 事件
- 不改前端渲染（仍用 `asset_url`）
- 不引入扁平视图的清理/回收任务（随 sandbox 统一回收）
- 不引入新的 Session protocol 方法
- 不实现 LRU、数量上限等累积管理策略
- 不做 `bash_tool.py` 或 `exp.py` 层面的改动

## Approach

所有改动收敛在 `matmaster/tools/figure_artifacts.py`：

- 在 `collect_figures_from_session` 成功路径的末尾（OSS 上传成功之后、`result.figures.append` 之前），对每张成功上传的 figure 通过 `session.exec_bash` 执行：

  ```
  mkdir -p -- <flat_dir> && ln -s -- <rel_target> <link_path>
  ```

- `flat_dir = <workdir>/.matmaster/figures/`（`artifact_dir` 上两层）
- `link_path = <flat_dir>/<figure_id><suffix>`，`suffix` 取自 `resolved_path` 的扩展名并小写化
- `rel_target = posixpath.relpath(resolved_path, start=flat_dir)` = `<tool_call_id>/artifacts/<basename>`
- `ln -s` 不带 `-f`，利用 POSIX 默认"拒绝覆盖"行为实现跨 tool call 的 first-writer-wins

扁平视图只是"对外可见视图"，工具仍往 sandbox 写图，路径穿越校验、manifest 解析基准都不变。

## Data Flow

```
tool/script -> writes to <workdir>/.matmaster/figures/<call_id>/artifacts/<file>
             + writes manifest to <workdir>/.matmaster/figures/<call_id>/manifest.json
             |
             v
collect_figures_from_session:
  for each manifest entry:
    download + validate + upload_oss  (unchanged)
    [NEW] symlink into flat view
    append FigureDescriptor
  return FigureCollectionResult
```

扁平视图条目的生命周期严格 piggyback 在 sandbox 上：

- sandbox 当前"不立即删除，遵循现有 run/workspace 清理时机统一回收"（`chat-response-figures-design.md:200-202`）
- 本 spec 不引入单独的扁平视图清理。未来 workspace 清理逻辑如需扫悬空链接，使用标准 `find <flat_dir> -maxdepth 1 -xtype l -delete`
- 会话内无限累积，这是用户明确要的"都能找到"行为

## Implementation

### `matmaster/tools/figure_artifacts.py`

**Imports 与模块 logger**：

- 新增 `import logging`
- 新增 `import shlex`
- 新增 module 顶层 `logger = logging.getLogger(__name__)`

该 logger 的 name 固定为 `matmaster.tools.figure_artifacts`，后续测试 caplog 断言依赖此 name。

**新增私有函数**：

```python
def _link_figure_into_flat_view(
    *,
    session: Session,
    artifact_dir: str,
    resolved_path: str,
    figure_id: str,
) -> None:
    """Symlink an uploaded artifact into the flat view directory.

    flat_dir = dirname(dirname(normpath(artifact_dir))) =
        <workdir>/.matmaster/figures/
    link_path = <flat_dir>/<figure_id><suffix>
    rel_target = relpath(resolved_path, start=flat_dir)

    On success: silent.
    On "File exists" (first-writer-wins): logger.warning with figure_symlink_exists:<id>.
    On other non-zero exit: logger.warning with figure_symlink_failed:<id>:<snippet>.
    On exception from session.exec_bash: logger.warning with figure_symlink_failed:<id>:<exc>.
    In all cases: the figure still enters payload.figures. This function
    never raises and never affects upload success accounting.

    Warnings are emitted via module logger rather than FigureCollectionResult.warnings
    because symlink outcomes are internal diagnostics, not manifest failures; writing
    them to result.warnings would cause bash_tool.py's "[Figure manifest ignored: ...]"
    text to misrepresent symlink issues as manifest problems.
    """
```

**调用点**：`collect_figures_from_session` 主循环内，在每张 figure 的 `asset_url = _upload_with_retry(...)` 成功之后、构造 `FigureDescriptor` 并 `result.figures.append` 之前调用：

```python
asset_url = _upload_with_retry(...)
_link_figure_into_flat_view(
    session=session,
    artifact_dir=artifact_dir,
    resolved_path=resolved_path,
    figure_id=entry.figure_id,
)
result.figures.append(FigureDescriptor(...))
```

放在 `try/except Exception` 捕获之外的成功分支。链接建立失败不再次抛出——完全在函数内部吞掉。

**命令构造**：

```python
flat_dir = posixpath.dirname(
    posixpath.dirname(posixpath.normpath(artifact_dir))
)
suffix = posixpath.splitext(resolved_path)[1].lower()
link_path = posixpath.join(flat_dir, f"{figure_id}{suffix}")
rel_target = posixpath.relpath(resolved_path, start=flat_dir)
cmd = (
    f"mkdir -p -- {shlex.quote(flat_dir)} && "
    f"ln -s -- {shlex.quote(rel_target)} {shlex.quote(link_path)}"
)
```

`posixpath.normpath` 自身已经规范化尾部斜杠，无需额外 `.rstrip("/")`。

**执行与判断**：`Session.exec_bash` 返回 `dict[str, Any]`，包含 `exit_code`、`stdout`、`stderr`、`output`、`working_dir`（见 `matmaster/sessions/ssh.py:197-202` 的参考实现）。实现必须用 `try/except Exception` 包裹 `session.exec_bash`，以覆盖 session 被关闭、底层 SSH 断连等实现特定异常：

```python
try:
    exec_result = session.exec_bash(cmd)
except Exception as exc:
    logger.warning("figure_symlink_failed:%s:%s", figure_id, exc)
    return

if exec_result.get("exit_code", 0) == 0:
    return

err = exec_result.get("stderr", "") or exec_result.get("stdout", "")
if "File exists" in err:
    logger.warning("figure_symlink_exists:%s", figure_id)
else:
    snippet = err[:200].strip()
    logger.warning("figure_symlink_failed:%s:%s", figure_id, snippet)
```

fallback 用 `stdout` 而不是 `output`，因为 `output` 是 `stdout + stderr` 拼接，可能带无关 stdout 噪声；`ln -s` 几乎不输出 stdout，`stdout` fallback 足够干净。

### Manifest Schema 补充校验

新增对 `figure_id` 的字符校验，防止扁平化后 `link_path` 跨目录或含 NUL。**该校验作为 `_load_manifest` 内部的字符串检查，不修改 `FigureManifestEntry` 的 pydantic 字段或 validator**——pydantic model 定义与现有 `matmaster/types/figures.py` 完全一致。

- `figure_id` 不得包含 `/`
- `figure_id` 不得包含 `\x00` (NUL)

加入 `_load_manifest`：对每个 `FigureManifestEntry`，若 `figure_id` 违反字符约束，返回 `invalid_manifest: invalid_figure_id:<id>` warning，整个 manifest 被拒（与现有 `invalid_figure_entry` 路径一致的失败策略）。

该校验对正常 figure_id（如 `band_structure`、`phonon-dispersion`）无影响。

**不过滤 `.` / `..` 的理由**：`figure_id = "."` + `suffix=".png"` 得到 `link_path = <flat_dir>/..png`，`figure_id = ".."` + `suffix=".png"` 得到 `link_path = <flat_dir>/...png`——这些都是 `<flat_dir>` 内的合法叶节点文件名（字面两点/三点加后缀），`ln -s` 不会把它们解释为目录引用；同时 `shlex.quote` 已阻止 shell 注入。路径穿越的真正风险载体是 `/`（分隔符）和 NUL（C 字符串终止符），已在上表列入。故不额外禁止点字符。

**推理依赖 `suffix` 非空**：`_link_figure_into_flat_view` 的调用点在 `_validate_image_bytes` 之后（上传成功路径），而 `_validate_image_bytes` 强制 `suffix in _ALLOWED_SUFFIXES = {.png, .jpg, .jpeg, .webp}`，所以运行时 `suffix` 必非空且以 `.` 开头。若未来 `_ALLOWED_SUFFIXES` 改为允许空扩展名，上述点字符安全性论证需要重做（`figure_id = "."` + 空 suffix 会得到 `<flat_dir>/.` 指向 flat_dir 自身的软链接）。

### 不改动的文件

- `matmaster/tools/builtin/bash_tool.py`
- `matmaster/tools/figure_artifacts.py::build_figure_env`
- `matmaster/tools/figure_artifacts.py::_resolve_artifact_path`
- `matmaster/types/figures.py` 的 `FigureDescriptor` / `FigureManifestEntry` 字段
- `matmaster/types/session.py` 的 `Session` protocol
- `src/services/response_figures_service.py`

## Error Handling

**核心不变量**：扁平视图 symlink 的成败不影响 figure 是否进入 `payload.figures`，也不影响回答主体。

| 场景 | symlink 行为 | `payload.figures` | Diagnostic |
|---|---|---|---|
| 下载失败 | 不尝试 | 不进入（记入 `failure_ids`） | 无 |
| 校验失败 | 不尝试 | 不进入（记入 `failure_ids`） | 无 |
| OSS 上传失败 | 不尝试 | 不进入（记入 `failure_ids`） | 无 |
| OSS 上传成功 + symlink 首建成功 | 创建 | 进入 | 无 |
| OSS 上传成功 + link_path 已存在（同名先占位 symlink 或用户手动放的同名 regular file） | 拒绝覆盖 | 进入 | `logger.warning("figure_symlink_exists:<id>")` |
| OSS 上传成功 + symlink 其他非零退出（权限/FS 不支持） | 失败 | 进入 | `logger.warning("figure_symlink_failed:<id>:<stderr_snippet>")` |
| OSS 上传成功 + `session.exec_bash` 抛异常（session 断连等） | 失败 | 进入 | `logger.warning("figure_symlink_failed:<id>:<exc>")` |

**Diagnostic 走向**：所有 symlink 诊断信息仅通过 `matmaster.tools.figure_artifacts` 模块 logger 输出，**不进入** `FigureCollectionResult.warnings`。这样做的原因：

- `bash_tool.py:250-254` 会把 `collection.warnings` 拼成 `[Figure manifest ignored: ...]` 追加到 tool_result 文本。若 symlink 的 warning 混入该字段，agent 会看到"manifest ignored"的误导性措辞——而实际上 manifest 正确、figure 已成功上传、只是便利视图未建成
- symlink 是 post-upload 便利视图，诊断信息对 agent 没有决策价值（first-writer-wins 的撞名图仍在 `payload.figures` 中），对运维有价值 → logger 是正确的归宿
- 保持 `collection.warnings` 的语义纯净：只承载"manifest 被拒"的真正降级信息

## Concurrency

同一回答内多个 tool call 并行产图：

- 各 tool call 的 sandbox `<workdir>/.matmaster/figures/<call_id>/` 独立，互不干扰
- 扁平视图 `<workdir>/.matmaster/figures/` 是所有 tool call 共享目录
- 跨 tool call 的 `figure_id` 冲突由 `ln -s` 原子性托底：先完成的 tool call 占位，后到者的 `ln` 返回非零（`File exists`），wrapper 记 logger.warning 并跳过。first-writer-wins 自然成立，无需额外同步原语

manifest 内部（单 tool call 内）的 `figure_id` 唯一性仍由 `_load_manifest` 里现有的 `seen_ids` 逻辑保证。

## Lifecycle

- 扁平视图 symlink 与 sandbox 同生命周期
- 不引入单独的清理任务或扫描器
- 会话内无 cap，累积由 `.matmaster` 隐藏目录自然隔离，不污染用户 `ls` 默认视图
- 跨机器迁移（`rsync`/`tar` 整个 `.matmaster/`）时，相对路径链接仍有效

## Testing

### Unit (tests/matmaster/tools/test_figure_artifacts.py)

所有 symlink 诊断断言通过 pytest `caplog` fixture 捕获模块 logger 输出（logger 名 `matmaster.tools.figure_artifacts`），而非检查 `result.warnings`。

**caplog fixture 约定**：在每个依赖 caplog 的测试（test 4/5/9）里，显式调用 `caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")`，并在断言前要求 `logger.propagate` 保持默认 `True`。这避免项目未来若引入 `logging.conf` 改变 propagate 行为时导致测试静默失败。

1. `test_flat_view_symlink_created_on_success` — 单 figure 成功上传后，`session.exec_bash` 被调用一次，命令含 `ln -s` 和正确 rel_target；`result.warnings` 为空
2. `test_flat_view_symlink_path_uses_figure_id_and_ext` — link_path 形态 `<workdir>/.matmaster/figures/<figure_id>.<ext>`，扩展名小写
3. `test_flat_view_symlink_relative_target` — rel_target 为相对路径 `<call_id>/artifacts/<basename>`
4. `test_flat_view_symlink_first_writer_wins` — Setup：连续两次调用 `collect_figures_from_session`，两次使用**相同 workdir**（因此 derive 出同一 `<workdir>/.matmaster/figures/` 扁平目录），但 **不同 `tool_call_id`** 构造的 `artifact_dir`，两份 manifest 使用**相同 `figure_id`**。Mock 的 `session.exec_bash` 在第一次 `ln -s` 调用返回 `exit_code=0`，第二次返回 `exit_code=1` 且 `stderr` 含 `File exists`。断言：第二次调用的 caplog 含 `figure_symlink_exists:<id>`，两张 figure 都进入各自调用的 `result.figures`，两次 `result.warnings` 均为空
5. `test_flat_view_symlink_generic_failure_does_not_fail_figure` — `exec_bash` 返回 `exit_code=1`、stderr=`Permission denied`，figure 仍进入 `result.figures`，caplog 含 `figure_symlink_failed:<id>:...Permission denied...`，`result.warnings` 为空
6. `test_flat_view_symlink_not_attempted_on_upload_failure` — mock `upload_bytes` 抛异常重试耗尽；验证 `session.exec_bash` **完全未被调用**（`collect_figures_from_session` 自身不调用 `exec_bash` 做任何 mkdir，`ln -s` 只在上传成功后才触发，所以零调用断言精确可行）
7. `test_flat_view_symlink_not_attempted_on_download_failure` — 同上，`session.download` 抛异常，`session.exec_bash` 完全未被调用
8. `test_flat_view_symlink_shell_quoting` — workdir 含空格（如 `/share/foo bar`）、figure_id 含连字符等边界字符，`exec_bash` 收到的命令通过 `shlex.split` 还原后 link_path 和 rel_target 各段与预期一致
9. `test_flat_view_symlink_exec_bash_raises_does_not_fail_figure` — mock `session.exec_bash` 在 `ln -s` 调用时抛 `RuntimeError("session closed")`；figure 仍进入 `result.figures`，caplog 含 `figure_symlink_failed:<id>:session closed`，`result.warnings` 为空
10. `test_manifest_rejects_figure_id_with_slash` — manifest 含 `figure_id: "a/b"` → `result.warnings` 含 `invalid_manifest: invalid_figure_id:a/b` 且 `result.figures` 为空
11. `test_manifest_rejects_figure_id_with_nul` — manifest 含 `figure_id: "a\x00b"` → `result.warnings` 含 `invalid_manifest: invalid_figure_id:...`

### Integration (tests/matmaster/tools/builtin/test_bash_tool.py)

12. `test_bash_tool_figure_flow_creates_flat_view_symlink` — BashTool 走完 figure 流程后，FakeSession 观察到 `exec_bash` 调用含扁平视图的 `ln -s` 命令；已有 `test_bash_tool_collects_figures_from_manifest` 类型用例基础上扩展。额外断言：tool result 文本里 `[Figure manifest ignored: ...]` 行**不出现**（因为 symlink 成功且无 manifest 问题），确保 bash_tool 文本行为无回归

### Regression

- 既有 `test_build_figure_env_uses_tool_call_scoped_paths` 不变
- 无图片对话路径不变（`exec_bash` 不会被扁平视图逻辑调用）
- `failure_ids`、`result.figures` 现有形状不变
- `FigureDescriptor` / `payload.figures` JSON 形状不变

### 不写的测试

- 文件系统真实 symlink 行为（依赖 OS/Bohrium FS，单测用 mock 更可靠）
- 清理/累积测试（清理不在本次范围）
- 跨 Session 实现的对称性测试（Session protocol 已经覆盖）

## Rollout Scope

本 spec 覆盖：

- `matmaster/tools/figure_artifacts.py` 顶部新增 `import shlex`
- `collect_figures_from_session` 内新增 `_link_figure_into_flat_view` 函数及其调用
- `_load_manifest` 补充 `figure_id` 字符校验（禁 `/` 与 NUL），违反时返回新的 `invalid_manifest: invalid_figure_id:<id>` warning（进 `result.warnings`）
- symlink 类诊断信息走模块 logger（`matmaster.tools.figure_artifacts`），**不进** `result.warnings`
- 对应单元与集成测试
- 前后端协议、`response_figures` 事件、manifest schema 公开字段、上传链路、session protocol、`FigureCollectionResult` / `FigureDescriptor` 的 dataclass/pydantic 形状全部零改动
- `bash_tool.py` 拼到 tool result 文本里的 `[Figure manifest ignored: ...]` 行为零改动（因 symlink warning 不流入 `collection.warnings`）

不在本次范围内（明确延后）：

- 悬空 symlink 清理
- 基于 symlink 的增强用户体验（如在 tool_result 文本里提示 `ls .matmaster/figures/`）
- LocalSession 实现本身（本 spec 只保证"当 LocalSession 存在时对称工作"）
- 从 `.matmaster` 切换到显性 `figures/` 目录
