from __future__ import annotations

import hashlib
import logging
import shlex
from unittest.mock import MagicMock

import pytest

from matmaster.tools.figure_artifacts import (
    FigureCollectionResult,
    build_figure_env,
    collect_figures_from_session,
)
from matmaster.types.figures import FigureUploadConfig


def test_build_figure_env_uses_tool_call_scoped_paths() -> None:
    artifact_dir, manifest_path = build_figure_env("/share", "call-1")

    assert artifact_dir == "/share/.matmaster/figures/call-1/artifacts"
    assert manifest_path == "/share/.matmaster/figures/call-1/manifest.json"


def _upload_cfg(
    upload_bytes=lambda data, key: f"https://oss.example/{key}",
) -> FigureUploadConfig:
    return FigureUploadConfig(
        session_id="sess-1",
        task_id="task-1",
        asset_key_prefix="matmaster/chat_figures",
        upload_bytes=upload_bytes,
    )


def test_collect_figures_missing_manifest_returns_empty_result() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = False

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result == FigureCollectionResult(figures=[], failure_ids=[], warnings=[])


def test_collect_figures_invalid_manifest_returns_warning_and_no_figures() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"../../etc/passwd","caption":"bad"}]}'
    )

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert result.failure_ids == []
    assert result.warnings == ["invalid_manifest: unsafe_path:../../etc/passwd"]


def test_collect_figures_duplicate_ids_returns_warning_and_no_figures() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = """
    {"figures":[
      {"figure_id":"band","path":"plots/band.png","caption":"band"},
      {"figure_id":"band","path":"plots/band-2.png","caption":"band2"}
    ]}
    """.strip()

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert result.failure_ids == []
    assert result.warnings == ["invalid_manifest: duplicate_figure_id:band"]


def test_collect_figures_rejects_unsupported_format_and_keeps_other_success() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = """
    {"figures":[
      {"figure_id":"band","path":"plots/band.png","caption":"band"},
      {"figure_id":"raw","path":"plots/raw.gif","caption":"raw"}
    ]}
    """.strip()
    fake_session.download.side_effect = [
        b"\x89PNG\r\n\x1a\n" + b"a" * 32,
        b"GIF89a" + b"b" * 32,
    ]

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert [fig.figure_id for fig in result.figures] == ["band"]
    assert result.failure_ids == ["raw"]


def test_collect_figures_rejects_oversized_image() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
    )
    fake_session.download.return_value = b"\x89PNG\r\n\x1a\n" + b"x" * (
        10 * 1024 * 1024
    )

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert result.failure_ids == ["band"]


def test_collect_figures_keeps_successful_entries_when_one_upload_fails() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = """
    {"figures":[
      {"figure_id":"band","path":"plots/band.png","caption":"band"},
      {"figure_id":"dos","path":"plots/dos.png","caption":"dos"}
    ]}
    """.strip()
    fake_session.download.side_effect = [
        b"\x89PNG\r\n\x1a\n" + b"a" * 32,
        b"\x89PNG\r\n\x1a\n" + b"b" * 32,
    ]

    def upload_bytes(data: bytes, key: str) -> str:
        if key.endswith("dos.png"):
            raise RuntimeError("upload failed")
        return f"https://oss.example/{key}"

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(upload_bytes=upload_bytes),
    )

    assert isinstance(result, FigureCollectionResult)
    assert [fig.figure_id for fig in result.figures] == ["band"]
    assert result.failure_ids == ["dos"]
    assert result.warnings == []


def test_collect_figures_retries_remote_download_once_before_failing() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
    )
    fake_session.download.side_effect = [
        TimeoutError("ssh hiccup"),
        TimeoutError("ssh still down"),
    ]

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert result.failure_ids == ["band"]
    assert fake_session.download.call_count == 2


def test_collect_figures_retries_upload_before_success() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
    )
    fake_session.download.return_value = b"\x89PNG\r\n\x1a\n" + b"x" * 64

    attempts = {"count": 0}

    def upload_bytes(data: bytes, key: str) -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("transient oss failure")
        return f"https://oss.example/{key}"

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(upload_bytes=upload_bytes),
    )

    assert [fig.figure_id for fig in result.figures] == ["band"]
    assert result.failure_ids == []
    assert attempts["count"] == 3
    assert result.figures[0].source_tool_call_id == "call-1"


def test_collect_figures_extension_header_mismatch_fails_without_warning() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
    )
    fake_session.download.return_value = b"\xff\xd8\xff" + b"x" * 64

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert result.failure_ids == ["band"]
    assert result.warnings == []


def test_collect_figures_asset_key_is_deterministic_and_preserves_basename() -> None:
    captured_keys: list[str] = []

    def upload_bytes(data: bytes, key: str) -> str:
        captured_keys.append(key)
        return f"https://oss.example/{key}"

    manifest = '{"figures":[{"figure_id":"band","path":"plots/band-plot.png","caption":"band"}]}'
    artifact_dir = "/share/.matmaster/figures/call-1/artifacts"
    manifest_path = "/share/.matmaster/figures/call-1/manifest.json"

    for _ in range(2):
        fake_session = MagicMock()
        fake_session.path_exists.return_value = True
        fake_session.read_file.return_value = manifest
        fake_session.download.return_value = b"\x89PNG\r\n\x1a\n" + b"x" * 64
        result = collect_figures_from_session(
            session=fake_session,
            artifact_dir=artifact_dir,
            manifest_path=manifest_path,
            tool_call_id="call-1",
            upload_config=_upload_cfg(upload_bytes=upload_bytes),
        )

        assert [fig.figure_id for fig in result.figures] == ["band"]

    assert captured_keys[0] == captured_keys[1]
    assert captured_keys[0].endswith("/band-plot.png")


def test_collect_figures_asset_key_uses_stable_sanitized_segments() -> None:
    captured_keys: list[str] = []

    def upload_bytes(data: bytes, key: str) -> str:
        captured_keys.append(key)
        return f"https://oss.example/{key}"

    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = '{"figures":[{"figure_id":"Band Figure 01","path":"plots/final image.png","caption":"band"}]}'
    payload = b"\x89PNG\r\n\x1a\n" + b"x" * 64
    fake_session.download.return_value = payload

    collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call 1/alpha",
        upload_config=FigureUploadConfig(
            session_id="sess 1/main",
            task_id="task:1",
            asset_key_prefix="matmaster/chat_figures",
            upload_bytes=upload_bytes,
        ),
    )

    expected_digest = hashlib.sha256(payload).hexdigest()[:16]
    assert captured_keys == [
        "matmaster/chat_figures/sess-1-main/task-1/call-1-alpha/Band-Figure-01/"
        f"{expected_digest}/final image.png"
    ]


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


def test_manifest_rejects_figure_id_with_control_char_sanitizes_warning() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"line\\nbreak","path":"plots/x.png","caption":"x"}]}'
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
    assert "\n" not in warning
    assert warning == "invalid_manifest: invalid_figure_id:'line\\nbreak'"


def test_manifest_rejects_figure_id_truncates_long_input() -> None:
    # 构造：长度 500、`/` 在位置 31（在前 64 字符截断窗口内）——确保截断后仍含 `/`
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
    # 语义覆盖：截断后的前 64 字符仍包含 `/`，用以触发校验
    assert "/" in payload


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
    # rel_target 为从 flat_dir 到 resolved_path 的相对路径，保留 manifest 子目录
    assert "call-1/artifacts/plots/band.png" in cmd
    # 严格断言：ln -s -- 紧随其后的第一个参数是相对路径
    ln_idx = cmd.index("ln -s --")
    ln_tail = cmd[ln_idx + len("ln -s --") :].strip()
    first_token = ln_tail.split()[0]
    assert not first_token.startswith("/"), (
        f"ln target must be relative, got {first_token!r}"
    )


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
        "figure_symlink_exists:'band'" in record.getMessage()
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
        if "figure_symlink_failed:'band'" in r.getMessage()
    ]
    assert failed and "Permission denied" in failed[0]
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
        "figure_symlink_failed:'band'" in r.getMessage()
        and "session closed" in r.getMessage()
        for r in caplog.records
    )


def test_flat_view_symlink_shell_quoting() -> None:
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
    assert "/share/foo bar/.matmaster/figures" in tokens
    assert "/share/foo bar/.matmaster/figures/band-alpha.png" in tokens
    assert "call-1/artifacts/plots/band.png" in tokens
    assert "FIGURE_SYMLINK_EXISTS" in tokens
