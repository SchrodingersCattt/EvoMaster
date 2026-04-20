# Figure Artifact Flat-View Symlink Design

## Problem

`chat-response-figures` 链路把工具产出的图片落到 sandbox 目录：

```
<execution_workdir>/.matmaster/figures/<tool_call_id>/artifacts/<file>
```

`execution_workdir` 由 session_directory 决定（未设置则默认 `/share`）。前端侧边栏通过 OSS `asset_url` 渲染，这条路径用户不关心。但在以下场景用户需要直接访问文件：

- 用户 SSH 进 Bohrium 远端在 `/share/foo` 下继续工作，想把刚生成的图片引用到别的脚本/文档里
- 本地会话（`matmaster.sessions.local.LocalSession`）下同样想直接 `ls` 看到图片

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

- 在 `collect_figures_from_session` 成功路径的末尾（OSS 上传成功之后、`result.figures.append` 之前），对每张成功上传的 figure 通过 `session.exec_bash` 执行**带显式存在性 guard** 的复合命令（不使用 `ln -s` 对目的路径的隐式语义）：

  ```bash
  mkdir -p -- <flat_dir> && \
  if [ -e <link_path> ] || [ -L <link_path> ]; then \
    printf '%s\n' 'FIGURE_SYMLINK_EXISTS' && exit 73; \
  fi && \
  ln -s -- <rel_target> <link_path>
  ```

- `flat_dir = <workdir>/.matmaster/figures/`（`artifact_dir` 上两层）
- `link_path = <flat_dir>/<figure_id><suffix>`，`suffix` 取自 `resolved_path` 的扩展名并小写化
- `rel_target = posixpath.relpath(resolved_path, start=flat_dir)` = `<tool_call_id>/artifacts/<basename>`

guard 的两个 marker：

- **exit code `73`**：一个项目内统一的、任意但稳定的"link_path 已存在"信号；选 73 避免与常见 POSIX 退出码（如 `1`/`2`/`126`/`127`/`130`）冲突
- **stdout `FIGURE_SYMLINK_EXISTS`**：双保险，防止被 shell 自定义 `exit` 行为覆盖；locale-safe（不依赖英文 `File exists`）

**为什么不用裸 `ln -s`**：`ln -s target link_path` 对 `link_path` **是已存在目录**的情况不会 fail，而是会在该目录里创建 `<link_path>/<basename(target)>`——破坏 first-writer-wins、还可能污染已有目录。`[ -e ... ]` 捕获 regular file / dir / symlink-to-existing，`[ -L ... ]` 额外捕获悬空 symlink，组合后覆盖所有"link_path 已被占位"的情况。

扁平视图只是"对外可见视图"，工具仍往 sandbox 写图，路径穿越校验、manifest 解析基准都不变。

### 命名空间接受声明

扁平视图目录 `<workdir>/.matmaster/figures/` 与 sandbox 目录 `<workdir>/.matmaster/figures/<tool_call_id>/` 共享同一父目录。极端情况下 `figure_id + suffix` 可能撞上某个已存在的 `<tool_call_id>` 目录名——比如 `figure_id="call_abcd1234"` 且 `suffix=""`（虽然 suffix 非空约束见后文，但理论讨论）。本 spec 显式接受这个结构性命名空间，安全性由上面的 guard 托底（guard 发现 link_path 是已存在目录时走 `FIGURE_SYMLINK_EXISTS` 路径，绝不进入 `ln -s` 污染目录）。测试必须覆盖"link_path 是已存在目录"的场景（见 Testing）。

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

    Command uses explicit `[ -e ]`/`[ -L ]` guard before `ln -s` (NOT bare
    `ln -s`) to correctly reject every form of link_path preoccupation:
    regular file, directory, symlink-to-existing, dangling symlink.
    Guard signals "already exists" via stable exit code 73 AND stdout marker
    FIGURE_SYMLINK_EXISTS (double guard; locale-safe).

    On success: silent.
    On guard-triggered exists (first-writer-wins): logger.warning
        figure_symlink_exists:<id>. Detected by exit_code == 73 OR
        FIGURE_SYMLINK_EXISTS in stdout.
    On other non-zero exit: logger.warning figure_symlink_failed:<id>:<snippet>.
    On exception from session.exec_bash: logger.warning
        figure_symlink_failed:<id>:<exc>.
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
_SYMLINK_EXISTS_MARKER = "FIGURE_SYMLINK_EXISTS"
_SYMLINK_EXISTS_EXIT_CODE = 73  # 稳定、任意但不与常见 POSIX 码冲突

flat_dir = posixpath.dirname(
    posixpath.dirname(posixpath.normpath(artifact_dir))
)
suffix = posixpath.splitext(resolved_path)[1].lower()
link_path = posixpath.join(flat_dir, f"{figure_id}{suffix}")
rel_target = posixpath.relpath(resolved_path, start=flat_dir)

q_flat = shlex.quote(flat_dir)
q_link = shlex.quote(link_path)
q_target = shlex.quote(rel_target)

cmd = (
    f"mkdir -p -- {q_flat} && "
    f"if [ -e {q_link} ] || [ -L {q_link} ]; then "
    f"printf '%s\\n' {shlex.quote(_SYMLINK_EXISTS_MARKER)} && "
    f"exit {_SYMLINK_EXISTS_EXIT_CODE}; "
    f"fi && "
    f"ln -s -- {q_target} {q_link}"
)
```

`posixpath.normpath` 自身已经规范化尾部斜杠，无需额外 `.rstrip("/")`。

**guard 必要性**：直接 `ln -s target link_path` 在 `link_path` 已经是**目录**时不会失败，POSIX `ln` 会把链接创建在该目录下（等价于 `ln -s target link_path/<basename(target)>`），从而破坏 first-writer-wins 并污染已存在目录。`[ -e ]` 覆盖 regular file / dir / symlink-to-existing，`[ -L ]` 额外捕获悬空 symlink。组合 guard 是 POSIX-safe、locale-safe 的。

**执行与判断**：`Session.exec_bash` 返回 `dict[str, Any]`，包含 `exit_code`、`stdout`、`stderr`、`output`、`working_dir`（见 `matmaster/sessions/ssh.py:226-232` 的参考实现）。实现必须用 `try/except Exception` 包裹 `session.exec_bash`，以覆盖 session 被关闭、底层 SSH 断连等实现特定异常：

```python
try:
    exec_result = session.exec_bash(cmd)
except Exception as exc:
    logger.warning("figure_symlink_failed:%s:%s", figure_id, exc)
    return

exit_code = exec_result.get("exit_code", 0)
if exit_code == 0:
    return

stdout = exec_result.get("stdout", "")
if exit_code == _SYMLINK_EXISTS_EXIT_CODE or _SYMLINK_EXISTS_MARKER in stdout:
    logger.warning("figure_symlink_exists:%s", figure_id)
    return

err = exec_result.get("stderr", "") or stdout
snippet = err[:200].strip()
logger.warning("figure_symlink_failed:%s:%s", figure_id, snippet)
```

`FIGURE_SYMLINK_EXISTS` 检查两层（exit code 73 + stdout 文本）作为双保险：即使 guard 脚本在某些极端 shell 环境里 `exit 73` 被截断或被包装器 remap，stdout marker 仍能识别。fallback 到 `stdout` 而不是 `output`，因为 `output` 是 `stdout + stderr` 拼接，可能混入 marker 噪声。

### Manifest Schema 补充校验

新增对 `figure_id` 的字符校验，防止扁平化后 `link_path` 跨目录或含 NUL。**该校验作为 `_load_manifest` 内部的字符串检查，不修改 `FigureManifestEntry` 的 pydantic 字段或 validator**——pydantic model 定义与现有 `matmaster/types/figures.py` 完全一致。

- `figure_id` 不得包含 `/`
- `figure_id` 不得包含 `\x00` (NUL)

加入 `_load_manifest`：对每个 `FigureManifestEntry`，若 `figure_id` 违反字符约束，返回 warning 并拒收整个 manifest（与现有 `invalid_figure_entry` 路径一致的失败策略）。

**Warning 载荷必须 sanitize**：`result.warnings` 会被 `bash_tool.py:241` 拼进 tool result 文本，再经 SSE 推送、Postgres 持久化、前端 JSON 序列化。**绝对不能把原始非法 `figure_id` 字节（尤其是 NUL）直接写进 warning 字符串**——Postgres `text` 拒绝 NUL、很多 JSON 路径与日志管道会被 NUL 截断或破坏编码。

Warning 格式固定为：

```
invalid_manifest: invalid_figure_id:<repr>
```

其中 `<repr>` 是 Python `repr(figure_id)` 的结果（带引号、C-style 转义）。例如：

| 原始 `figure_id` | repr 结果 | warning 实际字符串 |
|---|---|---|
| `a/b` | `'a/b'` | `invalid_manifest: invalid_figure_id:'a/b'` |
| `a\x00b` | `'a\\x00b'` | `invalid_manifest: invalid_figure_id:'a\\x00b'` |
| `very_long_id_...` | 截断前取 `figure_id[:64]` 再 `repr` | 同上，但字符串长度可控 |

实现时对 `figure_id` 先截断到前 64 字节再取 `repr`，避免恶意 manifest 通过超长 id 污染 tool result 文本。

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
| OSS 上传成功 + link_path 已存在（同名 symlink / regular file / **目录** / 悬空 symlink） | guard 命中，不创建 | 进入 | `logger.warning("figure_symlink_exists:<id>")`（通过 exit 73 或 stdout marker `FIGURE_SYMLINK_EXISTS` 识别） |
| OSS 上传成功 + `ln -s` 其他非零退出（mkdir 权限不足、FS 不支持 symlink 等） | 失败 | 进入 | `logger.warning("figure_symlink_failed:<id>:<stderr_snippet>")` |
| OSS 上传成功 + `session.exec_bash` 抛异常（session 断连等） | 失败 | 进入 | `logger.warning("figure_symlink_failed:<id>:<exc>")` |

**Diagnostic 走向**：所有 symlink 诊断信息仅通过 `matmaster.tools.figure_artifacts` 模块 logger 输出，**不进入** `FigureCollectionResult.warnings`。这样做的原因：

- `bash_tool.py:250-254` 会把 `collection.warnings` 拼成 `[Figure manifest ignored: ...]` 追加到 tool_result 文本。若 symlink 的 warning 混入该字段，agent 会看到"manifest ignored"的误导性措辞——而实际上 manifest 正确、figure 已成功上传、只是便利视图未建成
- symlink 是 post-upload 便利视图，诊断信息对 agent 没有决策价值（first-writer-wins 的撞名图仍在 `payload.figures` 中），对运维有价值 → logger 是正确的归宿
- 保持 `collection.warnings` 的语义纯净：只承载"manifest 被拒"的真正降级信息

## Concurrency

同一回答内多个 tool call 并行产图：

- 各 tool call 的 sandbox `<workdir>/.matmaster/figures/<call_id>/` 独立，互不干扰
- 扁平视图 `<workdir>/.matmaster/figures/` 是所有 tool call 共享目录
- 跨 tool call 的 `figure_id` 冲突由 guard 脚本的 `[ -e ]`/`[ -L ]` 检查托底。注意：该 guard **不是原子的**——`[ -e ]` 检查和后续 `ln -s` 之间存在时间窗，理论上两个并发 tool call 都可能通过 guard 再同时创建链接。但在这个失败模式下，`ln -s` 自身对"link_path 已被 regular file 或 symlink 占位"的行为仍是 fail（返回 `File exists` 类错误到 stderr），所以第二个进入的 tool call 会走到 "其他非零退出" 分支并落入 `figure_symlink_failed` warning——figure 依然进入 `payload.figures`，只是诊断分类从 exists 落到 generic failure。真实影响：极罕见的并发 race 场景里，先到者赢、后到者见 failed（而不是 exists），行为上仍是 first-writer-wins，只是 diagnostic 分类不精确。本 spec 接受该并发 race——若未来需要严格分类，可升级 guard 为 `ln -s target link_path.tmp.$$ && mv -n link_path.tmp.$$ link_path` 之类原子原语，不在本次范围

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

1. `test_flat_view_symlink_created_on_success` — 单 figure 成功上传后，`session.exec_bash` 被调用一次；命令含 `mkdir -p --`、guard 的 `[ -e` 和 `[ -L`、`ln -s --`、stdout marker `FIGURE_SYMLINK_EXISTS`、exit `73` 三个常量都出现；`result.warnings` 为空
2. `test_flat_view_symlink_path_uses_figure_id_and_ext` — link_path 形态 `<workdir>/.matmaster/figures/<figure_id>.<ext>`，扩展名小写
3. `test_flat_view_symlink_relative_target` — rel_target 为相对路径 `<call_id>/artifacts/<basename>`
4. `test_flat_view_symlink_first_writer_wins_via_exit_code` — Setup：连续两次调用 `collect_figures_from_session`，**相同 workdir**、**不同 `tool_call_id`**、**相同 `figure_id`**。Mock 第二次 `session.exec_bash` 返回 `exit_code=73`、stdout=`FIGURE_SYMLINK_EXISTS\n`、stderr=`""`。断言：第二次 caplog 含 `figure_symlink_exists:<id>`，两张 figure 都进入各自 `result.figures`，两次 `result.warnings` 均为空
5. `test_flat_view_symlink_first_writer_wins_via_stdout_marker` — 同 4 的 setup，但 mock 第二次返回 `exit_code=1`（某种包装器 remap 了 73）、stdout 含 `FIGURE_SYMLINK_EXISTS`、stderr=`""`。断言：仍识别为 exists，caplog 含 `figure_symlink_exists:<id>`，不被归为 generic failure
6. `test_flat_view_symlink_generic_failure_does_not_fail_figure` — `exec_bash` 返回 `exit_code=1`、stderr=`Permission denied`、stdout 不含 marker；figure 仍进入 `result.figures`，caplog 含 `figure_symlink_failed:<id>:...Permission denied...`，**不**含 `figure_symlink_exists`，`result.warnings` 为空
7. `test_flat_view_symlink_not_attempted_on_upload_failure` — mock `upload_bytes` 抛异常重试耗尽；验证 `session.exec_bash` **完全未被调用**（`collect_figures_from_session` 自身不调用 `exec_bash` 做任何 mkdir，guard+`ln -s` 只在上传成功后才触发，所以零调用断言精确可行）
8. `test_flat_view_symlink_not_attempted_on_download_failure` — 同上，`session.download` 抛异常，`session.exec_bash` 完全未被调用
9. `test_flat_view_symlink_shell_quoting` — workdir 含空格（如 `/share/foo bar`）、figure_id 含连字符等边界字符，`exec_bash` 收到的命令通过 `shlex.split` 还原后 link_path、rel_target、marker 各段与预期一致；断言 marker 字面量 `FIGURE_SYMLINK_EXISTS` 也被 `shlex.quote` 包裹
10. `test_flat_view_symlink_exec_bash_raises_does_not_fail_figure` — mock `session.exec_bash` 在 `ln -s` 调用时抛 `RuntimeError("session closed")`；figure 仍进入 `result.figures`，caplog 含 `figure_symlink_failed:<id>:session closed`，`result.warnings` 为空
11. `test_manifest_rejects_figure_id_with_slash` — manifest 含 `figure_id: "a/b"` → `result.warnings` 含字面量 `invalid_manifest: invalid_figure_id:'a/b'`（注意 repr 带引号）且 `result.figures` 为空
12. `test_manifest_rejects_figure_id_with_nul` — manifest 含 `figure_id: "a\x00b"` → `result.warnings` 含字面量 `invalid_manifest: invalid_figure_id:'a\\x00b'`（NUL 被 repr 转义为 `\x00` 可见文本）；**断言 warning 字符串本身不含真实 NUL 字节**（`"\x00" not in warnings[0]`），确保经 SSE/Postgres 传输安全
13. `test_manifest_rejects_figure_id_truncates_long_input` — manifest `figure_id` 长度 >64 字节时，warning 里的 repr 载荷截断在 64 字节内；整 warning 字符串长度有界

### Real filesystem tests (tests/matmaster/tools/test_figure_artifacts_real_fs.py)

新文件，用 `matmaster.sessions.local.LocalSession` + `tmp_path` fixture 做真实 symlink 行为验证。**不 mock `session.exec_bash`**，真正调用 `subprocess` 执行 guard+ln 脚本。每个用例在 tmp_path 下搭建最小 `<workdir>/.matmaster/figures/<call_id>/artifacts/` 结构，放一个真实 PNG 文件（最小 1x1 PNG bytes），手动构造一个 manifest，调用 `collect_figures_from_session`（`upload_bytes` 用一个返回假 URL 的 stub，不需要真网）。

14. `test_real_fs_creates_symlink` — 空 flat_dir；调用后 `<flat_dir>/<figure_id>.png` 是一个软链接，`os.readlink` 返回相对路径 `<call_id>/artifacts/<basename>`，链接目标可读且内容等同原 artifact
15. `test_real_fs_rejects_existing_regular_file` — 预先在 `<flat_dir>/<figure_id>.png` 放一个内容不同的 regular file；调用后**不改动该文件**（stat 前后一致，内容不被覆盖），figure 仍进入 `result.figures`，caplog 含 `figure_symlink_exists:<id>`
16. `test_real_fs_rejects_existing_directory` — 预先在 `<flat_dir>/<figure_id>.png` 建一个同名目录（模拟"如果 figure_id 巧合撞上某个 tool_call_id 目录名"的极端场景）；调用后该目录**结构不被修改**（无子链接写入），figure 仍进入 `result.figures`，caplog 含 `figure_symlink_exists:<id>`。**这条测试直接验证本 spec 不用裸 `ln -s` 的主要理由**
17. `test_real_fs_rejects_existing_dangling_symlink` — 预先在 `<flat_dir>/<figure_id>.png` 建一个指向不存在目标的 symlink；调用后该 symlink 保持不变，caplog 含 `figure_symlink_exists:<id>`
18. `test_real_fs_success_then_collision_same_workdir` — 连续两次 `collect_figures_from_session`（不同 call_id、相同 figure_id、相同 workdir）；第一次 symlink 建成功、第二次 guard 命中，两张 figure 都进入各自 `result.figures`

Real-fs 测试跳过条件：如运行环境不支持 symlink（罕见，但 CI 跨平台时可能发生），用 `pytest.importorskip` 风格或 `sys.platform` / 运行时探测跳过，不使测试套件整体失败。

### Integration (tests/matmaster/tools/builtin/test_bash_tool.py)

12. `test_bash_tool_figure_flow_creates_flat_view_symlink` — BashTool 走完 figure 流程后，FakeSession 观察到 `exec_bash` 调用含扁平视图的 `ln -s` 命令；已有 `test_bash_tool_collects_figures_from_manifest` 类型用例基础上扩展。额外断言：tool result 文本里 `[Figure manifest ignored: ...]` 行**不出现**（因为 symlink 成功且无 manifest 问题），确保 bash_tool 文本行为无回归

### Regression

- 既有 `test_build_figure_env_uses_tool_call_scoped_paths` 不变
- 无图片对话路径不变（`exec_bash` 不会被扁平视图逻辑调用）
- `failure_ids`、`result.figures` 现有形状不变
- `FigureDescriptor` / `payload.figures` JSON 形状不变

### 不写的测试

- 真实 Bohrium `/share` 挂载下的 symlink 行为（依赖远端集群，`LocalSession` tmp_path 已覆盖 POSIX 核心语义）
- 清理/累积测试（清理不在本次范围）

## Rollout Scope

本 spec 覆盖：

- `matmaster/tools/figure_artifacts.py` 顶部新增 `import logging`、`import shlex`，module 顶层新增 `logger = logging.getLogger(__name__)`
- `collect_figures_from_session` 内新增 `_link_figure_into_flat_view` 函数及其调用，guard 用显式 `[ -e ]`/`[ -L ]` + stdout marker `FIGURE_SYMLINK_EXISTS` + 稳定 exit code `73`，不使用裸 `ln -s`
- `_load_manifest` 补充 `figure_id` 字符校验（禁 `/` 与 NUL），违反时返回 `invalid_manifest: invalid_figure_id:<repr>` warning（进 `result.warnings`），repr 前对 figure_id 截断到 64 字节避免污染 tool result 文本；**warning 字符串里绝不包含真实 NUL 字节**，所有不可打印字符都被 Python `repr()` 转义
- symlink 类诊断信息走模块 logger（`matmaster.tools.figure_artifacts`），**不进** `result.warnings`
- 对应单元、集成与 real-fs（`LocalSession` + tmp_path）测试
- 前后端协议、`response_figures` 事件、manifest schema 公开字段、上传链路、session protocol、`FigureCollectionResult` / `FigureDescriptor` 的 dataclass/pydantic 形状全部零改动
- `bash_tool.py` 拼到 tool result 文本里的 `[Figure manifest ignored: ...]` 行为零改动（因 symlink warning 不流入 `collection.warnings`）

不在本次范围内（明确延后）：

- 悬空 symlink 清理
- 基于 symlink 的增强用户体验（如在 tool_result 文本里提示 `ls .matmaster/figures/`）
- 从 `.matmaster` 切换到显性 `figures/` 目录
- 将扁平视图移到 `.matmaster/figures/_flat/` 子目录隔离（本 spec 接受命名空间共享风险，由 guard 托底并通过 real-fs test 16 回归验证）
