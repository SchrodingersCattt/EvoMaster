# Figure Artifact Flat-View Symlink Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给每张成功上传的 figure 在 `<workdir>/.matmaster/figures/` 下建一个以 `<figure_id>.<ext>` 命名的软链接，让用户可以在对话工作目录里直接 `ls` 找到图片。

**Architecture:** 所有改动收敛在 `matmaster/tools/figure_artifacts.py`。在 `collect_figures_from_session` 成功路径末尾（主循环 try 块内、`result.figures.append` 之前）通过 `session.exec_bash` 执行 `mkdir -p && [ -e ]/[ -L ] guard && ln -s` 复合命令。guard 用 stable exit code 73 + stdout marker `FIGURE_SYMLINK_EXISTS` 双保险识别"link_path 已占位"。symlink 诊断走 module logger 不进 `FigureCollectionResult.warnings`。manifest schema 补一层 `figure_id` 字符校验，warning 里用 `repr()` sanitize 非法 id 避免 NUL 污染 SSE/Postgres/JSON。

**Tech Stack:** Python 3.12、pytest、unittest.mock、现有 `matmaster.types.session.Session` protocol、`matmaster.sessions.local.LocalSession`（用于真实文件系统测试）。

**Spec reference:** 实施前先读 `docs/superpowers/specs/2026-04-20-figure-artifact-symlink-design.md`。本 plan 假设读过 spec；涉及详细行为时引用 spec 章节而不是复述。

**Existing code anchors（实施前必知）:**

- 主体文件 `matmaster/tools/figure_artifacts.py`——`collect_figures_from_session` 主循环有一个 `try/except Exception: result.failure_ids.append(...); continue` 包裹 download + validate + upload。`_link_figure_into_flat_view` 的调用必须放在 **try/except 块之外、`result.figures.append(FigureDescriptor(...))` 之前**——即只在"download + validate + upload 全部成功、控制流通过 except 的 `continue` 筛选后到达 append 行"的那一刻调用。放进 try 块内会让 `_link_figure_into_flat_view` 自身的 `exec_bash` 异常被 except 错误捕获并污染 `failure_ids`。放太后（append 之后）无实质差别，但语义应是"append 之前"。完整代码片段见 Task 3 的 "Critical insertion point"
- 既有 bash_tool figure 集成测试**真实名字**是 `test_bash_injects_figure_env_and_returns_payload_figures`（`tests/matmaster/tools/builtin/test_bash_tool.py:277`），作为新增集成测试的模板
- 既有 unit test fixture 模式：`MagicMock()` 充当 session，`_upload_cfg()` helper 构造 `FigureUploadConfig`，参见 `tests/matmaster/tools/test_figure_artifacts.py:21-29` 头部

**Execution note:** 实施过程中随时使用 @superpowers:test-driven-development（先红后绿）、@superpowers:verification-before-completion（commit 前跑完整 test suite）。所有 `pytest` 调用使用 `uv run pytest`（项目用 uv 管理虚拟环境）。

---

## Chunk 1: 全部实施任务

### Task 1: 模块前置——imports、logger、常量

**Files:**
- Modify: `matmaster/tools/figure_artifacts.py`（顶部 imports 区与常量区）

- [ ] **Step 1: 读当前 imports 与顶部常量区**

Run: `Read matmaster/tools/figure_artifacts.py:1-27`
目的：确认当前 stdlib import 顺序，找到 `_ALLOWED_SUFFIXES = ...` 这一行的位置（常量区起点）。

- [ ] **Step 2: 添加 imports、module logger 与常量**

在 `matmaster/tools/figure_artifacts.py` 顶部的 stdlib imports 区，按字典序插入：

```python
import logging
import shlex
```

（如果 `shlex` 或 `logging` 已存在，则跳过对应一行。）

在所有 import 之后、第一个常量 `_ALLOWED_SUFFIXES` 定义**之前**插入：

```python
logger = logging.getLogger(__name__)

_SYMLINK_EXISTS_MARKER = "FIGURE_SYMLINK_EXISTS"
_SYMLINK_EXISTS_EXIT_CODE = 73
_FIGURE_ID_MAX_REPR_LEN = 64
```

- [ ] **Step 3: 跑既有测试确认无回归**

Run: `uv run pytest tests/matmaster/tools/test_figure_artifacts.py -v`
Expected: 所有既有用例继续通过（未改动逻辑）。

- [ ] **Step 4: Commit**

```bash
git add matmaster/tools/figure_artifacts.py
git commit -m "chore(figure_artifacts): add logging + shlex imports and symlink marker constants"
```

---

### Task 2: Manifest `figure_id` 字符校验 + repr-sanitized warning

**Files:**
- Modify: `matmaster/tools/figure_artifacts.py::_load_manifest`
- Test: `tests/matmaster/tools/test_figure_artifacts.py`（追加三个用例）

覆盖 spec Testing 段的 test 11、12、13。

- [ ] **Step 1: 写三个 failing tests**

追加到 `tests/matmaster/tools/test_figure_artifacts.py` 末尾：

```python
def test_manifest_rejects_figure_id_with_slash() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"a/b","path":"plots/x.png","caption":"x"}]}'
    )

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert result.warnings == ["invalid_manifest: invalid_figure_id:'a/b'"]


def test_manifest_rejects_figure_id_with_nul_sanitizes_warning() -> None:
    # JSON `\u0000` 经 json.loads 解析回 Python `\x00` 单字节 NUL
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"a\\u0000b","path":"plots/x.png","caption":"x"}]}'
    )

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert len(result.warnings) == 1
    warning = result.warnings[0]
    # 核心安全断言：真实 NUL 字节绝不进入 warning 字符串（repr 转义为 \x00 可见文本）
    assert "\x00" not in warning
    assert warning == "invalid_manifest: invalid_figure_id:'a\\x00b'"


def test_manifest_rejects_figure_id_truncates_long_input() -> None:
    # 构造：长度 500、`/` 在位置 31（在前 64 字节截断窗口内）——确保截断后仍含 `/`
    # 触发 invalid_figure_id 校验；同时验证 repr 后的 warning 长度有界
    long_id = "x" * 31 + "/" + "y" * 500
    manifest_json = (
        '{"figures":[{"figure_id":"'
        + long_id
        + '","path":"plots/x.png","caption":"x"}]}'
    )
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = manifest_json

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert len(result.warnings) == 1
    # repr('x'*31 + '/' + 'y'*32) 纯 ASCII、repr 后 = 2 引号 + 64 内容 = 66 字符
    # 前缀 "invalid_manifest: invalid_figure_id:" = 36 字符
    # 总长 <= 36 + 66 + 小余量
    warning = result.warnings[0]
    prefix = "invalid_manifest: invalid_figure_id:"
    payload = warning.removeprefix(prefix)
    assert len(payload) <= 68, f"payload too long: {len(payload)} chars: {payload!r}"
    # 截断发生：原 id 长度 500+，warning 远短于原始
    assert len(warning) < 200
    # 语义覆盖：截断后的前 64 字节仍包含 `/`，用以触发校验
    assert "/" in payload
```

- [ ] **Step 2: 跑测试验证 fail**

Run: `uv run pytest tests/matmaster/tools/test_figure_artifacts.py::test_manifest_rejects_figure_id_with_slash tests/matmaster/tools/test_figure_artifacts.py::test_manifest_rejects_figure_id_with_nul_sanitizes_warning tests/matmaster/tools/test_figure_artifacts.py::test_manifest_rejects_figure_id_truncates_long_input -v`
Expected: 三个都 FAIL（当前 `_load_manifest` 对 `/` 和 NUL 无校验）。

- [ ] **Step 3: 在 `_load_manifest` 里加字符校验**

定位 `figure_artifacts.py` 里 `_load_manifest` 函数内的主循环体（现有代码在 `entry = FigureManifestEntry.model_validate(raw_entry)` 成功之后、`if entry.figure_id in seen_ids:` 之前）。在 `seen_ids` 重复检查之前插入：

```python
if "/" in entry.figure_id or "\x00" in entry.figure_id:
    truncated = entry.figure_id[:_FIGURE_ID_MAX_REPR_LEN]
    return _ManifestLoadResult(
        entries=None,
        warning=f"invalid_manifest: invalid_figure_id:{truncated!r}",
    )
```

`{truncated!r}` 触发 Python `repr()`：带引号、NUL 转义为 `\x00` 可见文本、其他控制字符也一并 escape——这是防 NUL 污染下游 SSE/Postgres/JSON 的核心机制。

- [ ] **Step 4: 跑测试验证 pass**

Run: `uv run pytest tests/matmaster/tools/test_figure_artifacts.py -v`
Expected: 新三个 PASS，既有 test_collect_figures_* 全部继续 PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/figure_artifacts.py tests/matmaster/tools/test_figure_artifacts.py
git commit -m "feat(figure_artifacts): reject figure_id with slash or NUL with sanitized warning"
```

---

### Task 3: 新增 `_link_figure_into_flat_view` 函数（happy path + 命令构造 + 路径计算）

**Files:**
- Modify: `matmaster/tools/figure_artifacts.py`（在 `collect_figures_from_session` 之前新增私有函数；在其主循环内新增一个调用）
- Test: `tests/matmaster/tools/test_figure_artifacts.py`

覆盖 spec Testing 段的 test 1、2、3。

**Critical insertion point（务必读完再动手）:**

`collect_figures_from_session` 主循环体已经被 `try: ... except Exception: result.failure_ids.append(...); continue` 包裹。调用 `_link_figure_into_flat_view` 的**准确位置**是：

```python
for entry, resolved_path in manifest_entries.entries:
    try:
        payload = _download_with_retry(...)
        _validate_image_bytes(...)
        asset_key = _build_asset_key(...)
        asset_url = _upload_with_retry(...)
    except Exception:
        result.failure_ids.append(entry.figure_id)
        continue

    # <<< _link_figure_into_flat_view(...) inserted here, AFTER the try block,
    # BEFORE result.figures.append. This is the "success branch" — exceptions
    # from upload/download have already been handled by the except clause above.
    result.figures.append(FigureDescriptor(...))
```

调用**不包进 try 里**——`_link_figure_into_flat_view` 内部已有 `try/except Exception` 处理 `session.exec_bash` 的异常，且链接失败不应影响已成功上传的 figure 计入 `failure_ids`。

- [ ] **Step 1: 写三个 failing tests**

追加到 `tests/matmaster/tools/test_figure_artifacts.py`。用一个 helper 做通用 fake session 上传链路：

```python
def _make_session_for_single_figure(
    *,
    png_bytes: bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 32,
    exec_bash_return: dict | None = None,
) -> MagicMock:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
    )
    fake_session.download.return_value = png_bytes
    fake_session.exec_bash.return_value = exec_bash_return or {
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
    }
    return fake_session


def _get_exec_bash_command(call_args) -> str:
    """session.exec_bash is always called with command=... kwarg per our impl."""
    return call_args.kwargs.get("command") or call_args.args[0]


def test_flat_view_symlink_created_on_success() -> None:
    fake_session = _make_session_for_single_figure()

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert len(result.figures) == 1
    assert result.warnings == []
    fake_session.exec_bash.assert_called_once()
    cmd = _get_exec_bash_command(fake_session.exec_bash.call_args)
    # guard 关键元素都出现（substring 匹配，"exit 73" 后实际带 ";"）
    assert "mkdir -p --" in cmd
    assert "[ -e " in cmd
    assert "[ -L " in cmd
    assert "ln -s --" in cmd
    assert "FIGURE_SYMLINK_EXISTS" in cmd
    assert "exit 73" in cmd


def test_flat_view_symlink_path_uses_figure_id_and_ext() -> None:
    fake_session = _make_session_for_single_figure()

    collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    cmd = _get_exec_bash_command(fake_session.exec_bash.call_args)
    # link_path 形态 <workdir>/.matmaster/figures/<figure_id>.<ext>
    assert "/share/.matmaster/figures/band.png" in cmd


def test_flat_view_symlink_relative_target() -> None:
    fake_session = _make_session_for_single_figure()

    collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    cmd = _get_exec_bash_command(fake_session.exec_bash.call_args)
    # rel_target 为相对路径 <call_id>/artifacts/<basename>
    assert "call-1/artifacts/band.png" in cmd
    # 严格断言：ln -s -- 紧随其后的第一个参数是相对路径
    ln_idx = cmd.index("ln -s --")
    ln_tail = cmd[ln_idx + len("ln -s --"):].strip()
    first_token = ln_tail.split()[0]
    assert not first_token.startswith("/"), (
        f"ln target must be relative, got {first_token!r}"
    )
```

- [ ] **Step 2: 跑测试验证 fail**

Run: `uv run pytest tests/matmaster/tools/test_figure_artifacts.py::test_flat_view_symlink_created_on_success -v`
Expected: FAIL（`exec_bash` 从未被 `collect_figures_from_session` 调用）。

- [ ] **Step 3: 实现 `_link_figure_into_flat_view` 函数**

在 `matmaster/tools/figure_artifacts.py` 里，`collect_figures_from_session` 定义**之前**新增私有函数：

```python
def _link_figure_into_flat_view(
    *,
    session: Session,
    artifact_dir: str,
    resolved_path: str,
    figure_id: str,
) -> None:
    """Create a flat-view symlink for a successfully uploaded figure.

    Uses explicit [ -e ]/[ -L ] guard BEFORE ln -s (NOT bare ln -s) to
    correctly reject every form of link_path preoccupation: regular file,
    directory, symlink-to-existing, dangling symlink. Guard signals
    "already exists" via stable exit code 73 AND stdout marker
    FIGURE_SYMLINK_EXISTS (double guard; locale-safe).

    Diagnostics emitted via module logger, NOT via FigureCollectionResult.warnings
    (which would be misrepresented as "manifest ignored" by bash_tool).

    Full contract: see docs/superpowers/specs/2026-04-20-figure-artifact-symlink-design.md
    """

    flat_dir = posixpath.dirname(
        posixpath.dirname(posixpath.normpath(artifact_dir))
    )
    suffix = posixpath.splitext(resolved_path)[1].lower()
    link_path = posixpath.join(flat_dir, f"{figure_id}{suffix}")
    rel_target = posixpath.relpath(resolved_path, start=flat_dir)

    q_flat = shlex.quote(flat_dir)
    q_link = shlex.quote(link_path)
    q_target = shlex.quote(rel_target)
    q_marker = shlex.quote(_SYMLINK_EXISTS_MARKER)

    cmd = (
        f"mkdir -p -- {q_flat} && "
        f"if [ -e {q_link} ] || [ -L {q_link} ]; then "
        f"printf '%s\\n' {q_marker} && "
        f"exit {_SYMLINK_EXISTS_EXIT_CODE}; "
        f"fi && "
        f"ln -s -- {q_target} {q_link}"
    )

    try:
        exec_result = session.exec_bash(command=cmd)
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

在 `collect_figures_from_session` 主循环中插入调用（位置严格遵循本 Task 顶部"Critical insertion point"）：

```python
_link_figure_into_flat_view(
    session=session,
    artifact_dir=artifact_dir,
    resolved_path=resolved_path,
    figure_id=entry.figure_id,
)
```

- [ ] **Step 4: 跑测试验证 pass**

Run: `uv run pytest tests/matmaster/tools/test_figure_artifacts.py -v`
Expected: 三个新用例 PASS。既有用例通过——特别注意 `test_collect_figures_keeps_successful_entries_when_one_upload_fails` 应保持 PASS（symlink 只对成功上传触发）。

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/figure_artifacts.py tests/matmaster/tools/test_figure_artifacts.py
git commit -m "feat(figure_artifacts): add flat-view symlink with guard on success path"
```

---

### Task 4: 失败路径不触发 symlink 尝试

**Files:**
- Test: `tests/matmaster/tools/test_figure_artifacts.py`

覆盖 spec Testing 段的 test 7、8。

- [ ] **Step 1: 写两个 tests（Task 3 实现如果正确，这两个会直接 PASS；这里主要做防回归固化）**

追加：

```python
def test_flat_view_symlink_not_attempted_on_upload_failure() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
    )
    fake_session.download.return_value = b"\x89PNG\r\n\x1a\n" + b"x" * 32

    def always_fail(data: bytes, key: str) -> str:
        raise RuntimeError("upload dead")

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(upload_bytes=always_fail),
    )

    assert result.figures == []
    assert result.failure_ids == ["band"]
    # 关键断言：symlink 尝试完全未发生
    fake_session.exec_bash.assert_not_called()


def test_flat_view_symlink_not_attempted_on_download_failure() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
    )
    fake_session.download.side_effect = TimeoutError("ssh dead")

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert result.failure_ids == ["band"]
    fake_session.exec_bash.assert_not_called()
```

- [ ] **Step 2: 跑测试验证 pass**

Run: `uv run pytest tests/matmaster/tools/test_figure_artifacts.py::test_flat_view_symlink_not_attempted_on_upload_failure tests/matmaster/tools/test_figure_artifacts.py::test_flat_view_symlink_not_attempted_on_download_failure -v`
Expected: PASS。如果 FAIL，说明 `_link_figure_into_flat_view` 被放错位置（放到了 try 外部或 except 分支外），必须回到 Task 3 修正位置。

- [ ] **Step 3: Commit**

```bash
git add tests/matmaster/tools/test_figure_artifacts.py
git commit -m "test(figure_artifacts): assert symlink not attempted on download/upload failure"
```

---

### Task 5: Exists 检测——exit 73 与 stdout marker 两条识别路径

**Files:**
- Test: `tests/matmaster/tools/test_figure_artifacts.py`

覆盖 spec Testing 段的 test 4、5。

- [ ] **Step 0: 追加 imports 到 `tests/matmaster/tools/test_figure_artifacts.py` 顶部**

如果尚未 import，在文件顶部 imports 区追加（按字典序）：

```python
import logging

import pytest
```

追加后运行 `uv run pytest tests/matmaster/tools/test_figure_artifacts.py --collect-only` 确认收集无错（`NameError` / `ImportError` 会在收集阶段暴露）。

- [ ] **Step 1: 写两个 failing tests**

追加：

```python
def _run_with_exec_bash_returns(
    exec_bash_returns: list[dict],
    figure_id: str = "band",
) -> tuple[FigureCollectionResult, MagicMock]:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"'
        + figure_id
        + '","path":"plots/'
        + figure_id
        + '.png","caption":"x"}]}'
    )
    fake_session.download.return_value = b"\x89PNG\r\n\x1a\n" + b"x" * 32
    fake_session.exec_bash.side_effect = exec_bash_returns

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )
    return result, fake_session


def test_flat_view_symlink_first_writer_wins_via_exit_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")

    result, _session = _run_with_exec_bash_returns(
        [
            {
                "exit_code": 73,
                "stdout": "FIGURE_SYMLINK_EXISTS\n",
                "stderr": "",
            }
        ],
        figure_id="band",
    )

    assert len(result.figures) == 1
    assert result.warnings == []
    assert any(
        "figure_symlink_exists:band" in record.getMessage()
        for record in caplog.records
    )


def test_flat_view_symlink_first_writer_wins_via_stdout_marker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")

    # exit code 被 remap 为 1（模拟某些 wrapper 把 73 替换成别的值），
    # 但 stdout marker 仍作为识别凭证
    result, _session = _run_with_exec_bash_returns(
        [
            {
                "exit_code": 1,
                "stdout": "FIGURE_SYMLINK_EXISTS\n",
                "stderr": "",
            }
        ],
        figure_id="band",
    )

    assert len(result.figures) == 1
    assert result.warnings == []
    exists_msgs = [
        r.getMessage()
        for r in caplog.records
        if "figure_symlink_exists" in r.getMessage()
    ]
    failed_msgs = [
        r.getMessage()
        for r in caplog.records
        if "figure_symlink_failed" in r.getMessage()
    ]
    assert exists_msgs and not failed_msgs, (
        f"should classify as exists, not failed; "
        f"exists={exists_msgs} failed={failed_msgs}"
    )
```

- [ ] **Step 2: 跑测试验证 pass（Task 3 实现已覆盖）**

Run: `uv run pytest tests/matmaster/tools/test_figure_artifacts.py -k "first_writer_wins" -v`
Expected: PASS。如果 FAIL，回到 Task 3 Step 3 检查 `if exit_code == _SYMLINK_EXISTS_EXIT_CODE or _SYMLINK_EXISTS_MARKER in stdout` 判断是否正确实现。

- [ ] **Step 3: Commit**

```bash
git add tests/matmaster/tools/test_figure_artifacts.py
git commit -m "test(figure_artifacts): cover first-writer-wins via exit 73 and stdout marker"
```

---

### Task 6: Generic failure、exec_bash 异常、Shell quoting

**Files:**
- Test: `tests/matmaster/tools/test_figure_artifacts.py`

覆盖 spec Testing 段的 test 6、9、10。

- [ ] **Step 0: 确保 `import shlex` 存在于测试文件顶部**

如果 Task 5 没加过，追加到 imports 区。运行 `uv run pytest tests/matmaster/tools/test_figure_artifacts.py --collect-only` 确认无 `NameError`。

- [ ] **Step 1: 写三个 failing tests**

追加：

```python
def test_flat_view_symlink_generic_failure_does_not_fail_figure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")

    result, _session = _run_with_exec_bash_returns(
        [
            {
                "exit_code": 1,
                "stdout": "",
                "stderr": "ln: cannot create symbolic link 'x.png': Permission denied\n",
            }
        ],
        figure_id="band",
    )

    assert len(result.figures) == 1
    assert result.warnings == []
    failed = [
        r.getMessage()
        for r in caplog.records
        if "figure_symlink_failed:band" in r.getMessage()
    ]
    assert failed and "Permission denied" in failed[0]
    # 不能被误分类为 exists
    assert not any(
        "figure_symlink_exists" in r.getMessage() for r in caplog.records
    )


def test_flat_view_symlink_exec_bash_raises_does_not_fail_figure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")

    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
    )
    fake_session.download.return_value = b"\x89PNG\r\n\x1a\n" + b"x" * 32
    fake_session.exec_bash.side_effect = RuntimeError("session closed")

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert len(result.figures) == 1
    assert result.warnings == []
    assert any(
        "figure_symlink_failed:band" in r.getMessage()
        and "session closed" in r.getMessage()
        for r in caplog.records
    )


def test_flat_view_symlink_shell_quoting() -> None:
    # workdir 含空格，figure_id 含连字符
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band-alpha","path":"plots/band.png","caption":"x"}]}'
    )
    fake_session.download.return_value = b"\x89PNG\r\n\x1a\n" + b"x" * 32
    fake_session.exec_bash.return_value = {
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
    }

    collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/foo bar/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/foo bar/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    cmd = _get_exec_bash_command(fake_session.exec_bash.call_args)
    tokens = shlex.split(cmd)
    # 带空格路径被正确还原为单个 token
    assert "/share/foo bar/.matmaster/figures" in tokens  # flat_dir
    assert "/share/foo bar/.matmaster/figures/band-alpha.png" in tokens  # link_path
    assert "call-1/artifacts/band.png" in tokens  # rel_target
    # marker 作为单独 token 存在
    assert "FIGURE_SYMLINK_EXISTS" in tokens
```

- [ ] **Step 2: 跑测试验证 pass**

Run: `uv run pytest tests/matmaster/tools/test_figure_artifacts.py -v`
Expected: 全部 PASS。

- [ ] **Step 3: Commit**

```bash
git add tests/matmaster/tools/test_figure_artifacts.py
git commit -m "test(figure_artifacts): cover generic failure, exec_bash exception, shell quoting"
```

---

### Task 7: BashTool integration test（tool_result 文本不被污染）

**Files:**
- Test: `tests/matmaster/tools/builtin/test_bash_tool.py`

覆盖 spec Testing 段的 test 14（原编号 12 在 spec 里）。

- [ ] **Step 1: 读既有 figure 集成测试模板**

Run: `Read tests/matmaster/tools/builtin/test_bash_tool.py:260-340`
目的：读现有 `test_bash_injects_figure_env_and_returns_payload_figures`（line 277）的 setup，复用相同的 session mock 结构与 manifest json。

- [ ] **Step 2: 写 failing test（紧贴既有模板）**

追加到 `tests/matmaster/tools/builtin/test_bash_tool.py`（放在 `test_bash_injects_figure_env_and_returns_payload_figures` 后面）：

```python
def test_bash_tool_figure_flow_creates_flat_view_symlink(self) -> None:
    """Parallel setup to test_bash_injects_figure_env_and_returns_payload_figures.

    Verifies BashTool's figure flow drives _link_figure_into_flat_view and
    does NOT pollute tool_result text with [Figure manifest ignored: ...].
    """
    # ... fixture setup mirrors test_bash_injects_figure_env_and_returns_payload_figures ...
    # (copy session mock + manifest + png bytes + execute_with_context + figure_cfg construction)

    result = asyncio.run(
        tool.execute_with_context(
            {"command": "python render.py"},
            context={"tool_call_id": "call-xyz", "figure_cfg": figure_cfg},
        )
    )

    # 1. symlink 调用：exec_bash call_args_list 里找含 "ln -s --" 的命令
    ln_calls = [
        call for call in session.exec_bash.call_args_list
        if "ln -s --" in (
            call.kwargs.get("command") or (call.args[0] if call.args else "")
        )
    ]
    assert len(ln_calls) == 1
    ln_cmd = ln_calls[0].kwargs.get("command") or ln_calls[0].args[0]
    assert "FIGURE_SYMLINK_EXISTS" in ln_cmd
    assert "/share/.matmaster/figures/band.png" in ln_cmd  # link_path

    # 2. tool_result.content 不含 "[Figure manifest ignored:" —— bash_tool 文本零污染
    assert "[Figure manifest ignored:" not in result.content

    # 3. payload 图片数正确
    assert len(result.payload["figures"]) == 1
```

实现细节（fixture、imports、`figure_cfg` 构造）严格复制 `test_bash_injects_figure_env_and_returns_payload_figures` 里的对应片段，**修改**点仅限：
- `session.exec_bash.return_value` 或 `.side_effect` 增加对 `ln -s --` 子串命令的分支返回 `{"exit_code": 0, "stdout": "", "stderr": "", "output": "", "working_dir": "/share"}`，其他现有 exec_bash 分支保持。
- 如果既有测试的 mock 是"所有 exec_bash 都返回同一结果"，把 `return_value` 直接改成上述 `exit_code=0` 的形态即可兼容。

- [ ] **Step 3: 跑测试验证**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bash_tool.py::TestBashTool::test_bash_tool_figure_flow_creates_flat_view_symlink -v`
Expected: PASS（Task 3 已实现 `_link_figure_into_flat_view`，BashTool 自然走通）。如果 FAIL 且错误是"exec_bash 没被 ln -s 调用"，说明 `collect_figures_from_session` 里插入点有问题，回到 Task 3 修。

- [ ] **Step 4: 跑完整 figure 相关套件**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bash_tool.py -v -k figure`
Expected: 既有 bash_tool figure 用例全部继续 PASS，新用例 PASS。

- [ ] **Step 5: Commit**

```bash
git add tests/matmaster/tools/builtin/test_bash_tool.py
git commit -m "test(bash_tool): verify figure flow creates flat-view symlink without polluting tool result"
```

---

### Task 8: Real-FS 测试（LocalSession + tmp_path）

**Files:**
- Create: `tests/matmaster/tools/test_figure_artifacts_real_fs.py`

覆盖 spec Testing 段的 test 15、16、17、18、19。

**Critical LocalSession API（务必先读）:** `LocalSession.__init__(workspace_path: Path | str, *, timeout=300, encoding="utf-8")`——**`workspace_path` 是必需位置参数**。fixture 必须接受 `tmp_path` 并传入。

- [ ] **Step 1: 读 LocalSession 定义**

Run: `Read matmaster/sessions/local.py:26-80`
目的：确认构造签名、`open()`/`close()` 行为、`exec_bash` 返回结构。

- [ ] **Step 2: 创建新测试文件 skeleton**

Create `tests/matmaster/tools/test_figure_artifacts_real_fs.py`：

```python
"""Real-filesystem tests for figure flat-view symlinks.

使用 LocalSession + tmp_path 真实调用 subprocess 跑 guard+ln 脚本，
对 MagicMock 无法验证的 symlink 真实行为做回归兜底。
尤其针对 spec 第 P1.2 条的风险："ln -s target existing_dir/" 会
把链接创建在已存在目录内，本文件的 test_real_fs_rejects_existing_directory
就是直接回归这个风险。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from matmaster.sessions.local import LocalSession
from matmaster.tools.figure_artifacts import (
    build_figure_env,
    collect_figures_from_session,
)
from matmaster.types.figures import FigureUploadConfig

pytestmark = pytest.mark.skipif(
    not hasattr(os, "symlink"),
    reason="filesystem does not support symlink",
)

# 最小 PNG：合法 PNG signature + IHDR + IDAT + IEND。通过 _validate_image_bytes
# 所需：前 8 字节 `\x89PNG\r\n\x1a\n`（PNG magic） + suffix `.png` ∈ _ALLOWED_SUFFIXES
# + size < 10 MiB。本字节流约 67 字节，全部满足。
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff\x3f\x00\x05\xfe\x02\xfe\xdc\xccY\xe7"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _upload_cfg() -> FigureUploadConfig:
    return FigureUploadConfig(
        session_id="sess-1",
        task_id="task-1",
        asset_key_prefix="matmaster/chat_figures",
        upload_bytes=lambda data, key: f"https://oss.example/{key}",
    )


def _setup_artifact(
    workdir: Path, call_id: str, figure_id: str
) -> tuple[str, str]:
    """Write artifact file + manifest on disk. Return (artifact_dir, manifest_path)."""
    artifact_dir, manifest_path = build_figure_env(str(workdir), call_id)
    artifact_path = Path(artifact_dir) / f"{figure_id}.png"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(_TINY_PNG)
    Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(manifest_path).write_text(
        '{"figures":[{"figure_id":"'
        + figure_id
        + '","path":"'
        + figure_id
        + '.png","caption":"c"}]}'
    )
    return artifact_dir, manifest_path


@pytest.fixture
def local_session(tmp_path: Path):
    """Opened LocalSession with tmp_path as workspace. Closes on teardown."""
    session = LocalSession(tmp_path)
    session.open()
    try:
        yield session
    finally:
        session.close()
```

- [ ] **Step 3: 跑 skeleton 确认 fixture 工作**

Run: `uv run pytest tests/matmaster/tools/test_figure_artifacts_real_fs.py -v`
Expected: 无测试函数，pytest 收集到 0 用例并成功退出。如果 `LocalSession(tmp_path)` 构造失败，回到 Step 1 核对签名。

- [ ] **Step 4: 写 test 15（happy path）**

追加：

```python
def test_real_fs_creates_symlink(
    local_session: LocalSession, tmp_path: Path
) -> None:
    workdir = tmp_path
    artifact_dir, manifest_path = _setup_artifact(workdir, "call-1", "band")

    result = collect_figures_from_session(
        session=local_session,
        artifact_dir=artifact_dir,
        manifest_path=manifest_path,
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert len(result.figures) == 1
    assert result.warnings == []

    link_path = workdir / ".matmaster" / "figures" / "band.png"
    assert link_path.is_symlink()
    # 相对链接目标
    assert os.readlink(link_path) == "call-1/artifacts/band.png"
    # 链接能解析到原 artifact 字节
    assert link_path.read_bytes() == _TINY_PNG
```

Run: `uv run pytest tests/matmaster/tools/test_figure_artifacts_real_fs.py::test_real_fs_creates_symlink -v`
Expected: PASS。

- [ ] **Step 5: 写 test 16（regular file 占位拒绝）**

```python
def test_real_fs_rejects_existing_regular_file(
    local_session: LocalSession,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")

    workdir = tmp_path
    artifact_dir, manifest_path = _setup_artifact(workdir, "call-1", "band")

    # 预先放一个同名 regular file
    flat_dir = workdir / ".matmaster" / "figures"
    flat_dir.mkdir(parents=True, exist_ok=True)
    squatter = flat_dir / "band.png"
    squatter.write_bytes(b"SQUATTER")

    result = collect_figures_from_session(
        session=local_session,
        artifact_dir=artifact_dir,
        manifest_path=manifest_path,
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert len(result.figures) == 1
    # 占位文件没被触碰
    assert squatter.read_bytes() == b"SQUATTER"
    assert not squatter.is_symlink()
    assert any(
        "figure_symlink_exists:band" in r.getMessage() for r in caplog.records
    )
```

Run: `uv run pytest tests/matmaster/tools/test_figure_artifacts_real_fs.py::test_real_fs_rejects_existing_regular_file -v`
Expected: PASS。

- [ ] **Step 6: 写 test 17（目录占位拒绝）——核心 P1.2 回归**

```python
def test_real_fs_rejects_existing_directory(
    local_session: LocalSession,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")

    workdir = tmp_path
    artifact_dir, manifest_path = _setup_artifact(workdir, "call-1", "band")

    # 预先把 link_path 建成一个目录（模拟 figure_id 撞上 tool_call_id 子目录的极端场景）
    flat_dir = workdir / ".matmaster" / "figures"
    flat_dir.mkdir(parents=True, exist_ok=True)
    squatter_dir = flat_dir / "band.png"
    squatter_dir.mkdir()
    (squatter_dir / "untouched").write_text("keep me")

    result = collect_figures_from_session(
        session=local_session,
        artifact_dir=artifact_dir,
        manifest_path=manifest_path,
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert len(result.figures) == 1
    # 目录结构不变；特别是没有在 squatter_dir 里创建额外链接
    assert squatter_dir.is_dir()
    assert not squatter_dir.is_symlink()
    assert (squatter_dir / "untouched").read_text() == "keep me"
    # squatter 内部应仅有原文件，没有新条目
    entries = sorted(p.name for p in squatter_dir.iterdir())
    assert entries == ["untouched"], (
        f"guard should reject directory; found extra entries: {entries}"
    )
    assert any(
        "figure_symlink_exists:band" in r.getMessage() for r in caplog.records
    )
```

Run: `uv run pytest tests/matmaster/tools/test_figure_artifacts_real_fs.py::test_real_fs_rejects_existing_directory -v`
Expected: PASS。如果 FAIL（目录里出现额外 `band.png` 条目），说明 guard 没生效——这是 spec P1.2 的核心风险，必须回到 Task 3 Step 3 检查 shell 命令构造。

- [ ] **Step 7: 写 test 18（悬空 symlink 占位拒绝）**

```python
def test_real_fs_rejects_existing_dangling_symlink(
    local_session: LocalSession,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")

    workdir = tmp_path
    artifact_dir, manifest_path = _setup_artifact(workdir, "call-1", "band")

    # 预先建一个指向不存在目标的 symlink
    flat_dir = workdir / ".matmaster" / "figures"
    flat_dir.mkdir(parents=True, exist_ok=True)
    dangling = flat_dir / "band.png"
    os.symlink("nowhere-to-be-seen", dangling)
    assert dangling.is_symlink()
    assert not dangling.exists()  # 悬空确认

    result = collect_figures_from_session(
        session=local_session,
        artifact_dir=artifact_dir,
        manifest_path=manifest_path,
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert len(result.figures) == 1
    # 悬空 symlink 保持指向错误目标（未被覆盖）
    assert os.readlink(dangling) == "nowhere-to-be-seen"
    assert any(
        "figure_symlink_exists:band" in r.getMessage() for r in caplog.records
    )
```

Run: `uv run pytest tests/matmaster/tools/test_figure_artifacts_real_fs.py::test_real_fs_rejects_existing_dangling_symlink -v`
Expected: PASS（`[ -L ]` 捕获悬空 symlink）。

- [ ] **Step 8: 写 test 19（跨 call 同 workdir 连续场景）**

```python
def test_real_fs_success_then_collision_same_workdir(
    local_session: LocalSession,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="matmaster.tools.figure_artifacts")

    workdir = tmp_path
    # 第一次 call
    ad1, mp1 = _setup_artifact(workdir, "call-1", "band")
    r1 = collect_figures_from_session(
        session=local_session,
        artifact_dir=ad1,
        manifest_path=mp1,
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )
    assert len(r1.figures) == 1

    link_path = workdir / ".matmaster" / "figures" / "band.png"
    assert link_path.is_symlink()
    assert os.readlink(link_path) == "call-1/artifacts/band.png"

    # 第二次 call：不同 call_id、相同 figure_id
    ad2, mp2 = _setup_artifact(workdir, "call-2", "band")
    caplog.clear()
    r2 = collect_figures_from_session(
        session=local_session,
        artifact_dir=ad2,
        manifest_path=mp2,
        tool_call_id="call-2",
        upload_config=_upload_cfg(),
    )
    assert len(r2.figures) == 1  # figure 仍进入 payload

    # 链接仍指向 call-1（first-writer-wins）
    assert os.readlink(link_path) == "call-1/artifacts/band.png"
    assert any(
        "figure_symlink_exists:band" in r.getMessage() for r in caplog.records
    )
```

Run: `uv run pytest tests/matmaster/tools/test_figure_artifacts_real_fs.py::test_real_fs_success_then_collision_same_workdir -v`
Expected: PASS。

- [ ] **Step 9: 跑完整 real-fs + figure 受影响套件**

Run: `uv run pytest tests/matmaster/tools/test_figure_artifacts_real_fs.py tests/matmaster/tools/test_figure_artifacts.py tests/matmaster/tools/builtin/test_bash_tool.py -v`
Expected: 全部 PASS。

- [ ] **Step 10: Commit**

```bash
git add tests/matmaster/tools/test_figure_artifacts_real_fs.py
git commit -m "test(figure_artifacts): real-fs LocalSession tests for symlink guard behavior"
```

---

### Task 9: 最终集成验证

**Files:** 无新增（仅执行验证命令）

- [ ] **Step 1: 跑完整受影响测试套件**

Run: `uv run pytest tests/matmaster/ -v --tb=short 2>&1 | tail -80`
Expected: 全部 PASS，无新增 warning/xfail/skip（除明确 skip 条件：非 symlink 文件系统）。

- [ ] **Step 2: 跑 linter 与 type checker**

Run: `uv run ruff check matmaster/tools/figure_artifacts.py tests/matmaster/tools/`
Run: `uv run mypy matmaster/tools/figure_artifacts.py`（若 mypy 已在项目配置中）
Expected: 无新增告警。若 mypy 未配置可跳过。

- [ ] **Step 3: 验证改动范围收敛**

Run: `git diff main --stat -- matmaster/`
Expected: **只有** `matmaster/tools/figure_artifacts.py` 一个 source 文件变化。若多出其他 matmaster/ 下文件改动，需要复核。

Run: `git diff main --stat -- tests/`
Expected: 仅 `tests/matmaster/tools/test_figure_artifacts.py`、`tests/matmaster/tools/builtin/test_bash_tool.py` 修改，以及新增 `tests/matmaster/tools/test_figure_artifacts_real_fs.py`。

- [ ] **Step 4: 对照 Rollout Scope 做代码层面事实核查**

逐条执行验证命令：

- [ ] `grep -n '^import logging' matmaster/tools/figure_artifacts.py` 应返回 1 行
- [ ] `grep -n '^import shlex' matmaster/tools/figure_artifacts.py` 应返回 1 行
- [ ] `grep -n 'logger = logging.getLogger' matmaster/tools/figure_artifacts.py` 应返回 1 行
- [ ] `grep -nc '_link_figure_into_flat_view' matmaster/tools/figure_artifacts.py` 应 ≥ 2（定义 + 调用）
- [ ] `grep -n 'FIGURE_SYMLINK_EXISTS' matmaster/tools/figure_artifacts.py` 应返回 marker 常量 + 判断条件两处
- [ ] `grep -n 'invalid_figure_id' matmaster/tools/figure_artifacts.py` 应返回 warning 字符串
- [ ] `grep -n 'invalid_figure_id' matmaster/types/figures.py` 应无命中（pydantic model 零改动）
- [ ] `grep -n 'result.warnings' matmaster/tools/figure_artifacts.py` 命中数应不大于改动前（symlink 诊断未污染）

- [ ] **Step 5: 最终 commit（如有收尾小 fix）或跳过**

如 Step 1-4 发现小 fix，commit 一次作为收尾：

```bash
git commit -m "chore(figure_artifacts): final verification fixes"
```

---

## Done Criteria

全部以下条件同时满足才算 plan 完成：

1. Task 1-9 每个 step 的 checkbox 都已 check off
2. `uv run pytest tests/matmaster/` 全绿（Task 9 Step 1 为准）
3. Task 9 Step 4 checklist 全通过
4. spec `docs/superpowers/specs/2026-04-20-figure-artifact-symlink-design.md` Rollout Scope 的每一条在 code 层面可被验证
