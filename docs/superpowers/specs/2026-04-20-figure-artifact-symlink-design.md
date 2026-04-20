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

**Imports**：新增 `import shlex`。

**新增私有函数**：

```python
def _link_figure_into_flat_view(
    *,
    session: Session,
    artifact_dir: str,
    resolved_path: str,
    figure_id: str,
    warnings: list[str],
) -> None:
    """Symlink an uploaded artifact into the flat view directory.

    flat_dir = dirname(dirname(artifact_dir)) =
        <workdir>/.matmaster/figures/
    link_path = <flat_dir>/<figure_id><suffix>
    rel_target = relpath(resolved_path, start=flat_dir)

    On success: no warning.
    On "File exists" (first-writer-wins): warning figure_symlink_exists:<id>.
    On other errors: warning figure_symlink_failed:<id>:<stderr_snippet>.
    In all cases: the figure still enters payload.figures. This function
    never raises and never affects upload success accounting.
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
    warnings=result.warnings,
)
result.figures.append(FigureDescriptor(...))
```

放在 `try/except Exception` 捕获之外的成功分支。链接建立失败不再次抛出——完全在函数内部处理，warning 追加到 `result.warnings`。

**命令构造**：

```python
flat_dir = posixpath.dirname(
    posixpath.dirname(posixpath.normpath(artifact_dir).rstrip("/"))
)
suffix = posixpath.splitext(resolved_path)[1].lower()
link_path = posixpath.join(flat_dir, f"{figure_id}{suffix}")
rel_target = posixpath.relpath(resolved_path, start=flat_dir)
cmd = (
    f"mkdir -p -- {shlex.quote(flat_dir)} && "
    f"ln -s -- {shlex.quote(rel_target)} {shlex.quote(link_path)}"
)
result = session.exec_bash(cmd)
```

**返回值约定**：`Session.exec_bash` 返回 `dict[str, Any]`，包含 `exit_code`、`stdout`、`stderr`、`output`、`working_dir`（见 `matmaster/sessions/ssh.py:197-202` 的参考实现）。判断方式：

```python
if result.get("exit_code", 0) == 0:
    return
stderr = result.get("stderr", "") or result.get("output", "")
if "File exists" in stderr:
    warnings.append(f"figure_symlink_exists:{figure_id}")
else:
    snippet = stderr[:200].strip()
    warnings.append(f"figure_symlink_failed:{figure_id}:{snippet}")
```

### Manifest Schema 补充校验

新增对 `figure_id` 的字符校验，防止扁平化后 `link_path` 跨目录或含 NUL：

- `figure_id` 不得包含 `/`
- `figure_id` 不得包含 `\x00` (NUL)

加入 `_load_manifest`：对每个 `FigureManifestEntry`，若 `figure_id` 违反字符约束，返回 `invalid_manifest: invalid_figure_id:<id>` warning，整个 manifest 被拒（与现有 `invalid_figure_entry` 路径一致的失败策略）。

该校验对正常 figure_id（如 `band_structure`、`phonon-dispersion`）无影响。

### 不改动的文件

- `matmaster/tools/builtin/bash_tool.py`
- `matmaster/tools/figure_artifacts.py::build_figure_env`
- `matmaster/tools/figure_artifacts.py::_resolve_artifact_path`
- `matmaster/types/figures.py` 的 `FigureDescriptor` / `FigureManifestEntry` 字段
- `matmaster/types/session.py` 的 `Session` protocol
- `src/services/response_figures_service.py`

## Error Handling

**核心不变量**：扁平视图 symlink 的成败不影响 figure 是否进入 `payload.figures`，也不影响回答主体。

| 场景 | symlink 行为 | `payload.figures` | Warning |
|---|---|---|---|
| 下载失败 | 不尝试 | 不进入（记入 `failure_ids`） | 无 |
| 校验失败 | 不尝试 | 不进入（记入 `failure_ids`） | 无 |
| OSS 上传失败 | 不尝试 | 不进入（记入 `failure_ids`） | 无 |
| OSS 上传成功 + symlink 首建成功 | 创建 | 进入 | 无 |
| OSS 上传成功 + symlink 撞名 | 拒绝覆盖 | 进入 | `figure_symlink_exists:<id>` |
| OSS 上传成功 + symlink 其他错误（权限/FS 不支持） | 失败 | 进入 | `figure_symlink_failed:<id>:<stderr>` |

## Concurrency

同一回答内多个 tool call 并行产图：

- 各 tool call 的 sandbox `<workdir>/.matmaster/figures/<call_id>/` 独立，互不干扰
- 扁平视图 `<workdir>/.matmaster/figures/` 是所有 tool call 共享目录
- 跨 tool call 的 `figure_id` 冲突由 `ln -s` 原子性托底：先完成的 tool call 占位，后到者的 `ln` 返回非零（`File exists`），wrapper 记 warning 并跳过。first-writer-wins 自然成立，无需额外同步原语

manifest 内部（单 tool call 内）的 `figure_id` 唯一性仍由 `_load_manifest` 里现有的 `seen_ids` 逻辑保证。

## Lifecycle

- 扁平视图 symlink 与 sandbox 同生命周期
- 不引入单独的清理任务或扫描器
- 会话内无 cap，累积由 `.matmaster` 隐藏目录自然隔离，不污染用户 `ls` 默认视图
- 跨机器迁移（`rsync`/`tar` 整个 `.matmaster/`）时，相对路径链接仍有效

## Testing

### Unit (tests/matmaster/tools/test_figure_artifacts.py)

1. `test_flat_view_symlink_created_on_success` — 单 figure 成功上传后，`exec_bash` 被调用一次，命令含 `ln -s` 和正确 rel_target
2. `test_flat_view_symlink_path_uses_figure_id_and_ext` — link_path 形态 `<workdir>/.matmaster/figures/<figure_id>.<ext>`，扩展名小写
3. `test_flat_view_symlink_relative_target` — rel_target 为相对路径 `<call_id>/artifacts/<basename>`
4. `test_flat_view_symlink_first_writer_wins` — 连续两次 `collect_figures_from_session`（不同 tool_call_id、相同 figure_id）：第二次 `exec_bash` 返回 `exit_code != 0` 且 stderr 含 `File exists`，warning 含 `figure_symlink_exists:<id>`，第二张 figure 仍进入 `result.figures`
5. `test_flat_view_symlink_generic_failure_does_not_fail_figure` — `exec_bash` 返回 `Permission denied`，figure 仍进入 `result.figures`，warning 含 `figure_symlink_failed:<id>:...`
6. `test_flat_view_symlink_not_attempted_on_upload_failure` — mock 上传抛异常，`exec_bash` 未被调用（除已有的 `mkdir -p` 等无关调用外）
7. `test_flat_view_symlink_not_attempted_on_download_failure` — 同上，`session.download` 抛异常
8. `test_flat_view_symlink_shell_quoting` — workdir 含空格（如 `/share/foo bar`）、figure_id 含连字符等边界字符，`exec_bash` 收到的命令通过 `shlex.split` 还原后路径正确
9. `test_manifest_rejects_figure_id_with_slash` — manifest 含 `figure_id: "a/b"` → `invalid_manifest` warning 且无图片返回
10. `test_manifest_rejects_figure_id_with_nul` — manifest 含 `figure_id: "a\x00b"` → `invalid_manifest` warning

### Integration (tests/matmaster/tools/builtin/test_bash_tool.py)

11. `test_bash_tool_figure_flow_creates_flat_view_symlink` — BashTool 走完 figure 流程后，FakeSession 观察到 `exec_bash` 调用含扁平视图的 `ln -s` 命令；已有 `test_bash_tool_collects_figures_from_manifest` 类型用例基础上扩展

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

- `collect_figures_from_session` 内新增 `_link_figure_into_flat_view`
- `_load_manifest` 补充 `figure_id` 字符校验
- 对应单元与集成测试
- 前后端协议、response_figures 事件、manifest 契约、上传链路、session protocol 全部零改动

不在本次范围内（明确延后）：

- 悬空 symlink 清理
- 基于 symlink 的增强用户体验（如在 tool_result 文本里提示 `ls .matmaster/figures/`）
- LocalSession 实现本身（本 spec 只保证"当 LocalSession 存在时对称工作"）
- 从 `.matmaster` 切换到显性 `figures/` 目录
