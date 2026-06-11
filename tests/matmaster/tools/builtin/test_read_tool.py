"""tests/matmaster/tools/builtin/test_read_tool.py"""

import asyncio
import dataclasses
from unittest.mock import MagicMock

from matmaster.tools.builtin.read_tool import ReadTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.session import SessionFileStat
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext
from tests.matmaster.tools.filesystem_semantics.test_image_resolution import (
    make_png,
    make_png_header_only,
)


def make_session(content="line1\nline2\nline3", is_file=True):
    return make_download_session(content.encode("utf-8"), is_file=is_file)


def make_download_session(raw: bytes, is_file: bool = True, mtime: float = 1.0):
    session = MagicMock()
    session.is_file.return_value = is_file
    session.download.return_value = raw
    session.stat_file.return_value = SessionFileStat(size=len(raw), mtime=mtime)
    return session


class TestReadToolMetadata:
    def test_name(self):
        assert ReadTool.name == "Read"

    def test_effect_level(self):
        assert ReadTool.effect_level == "none"

    def test_fast_path(self):
        assert ReadTool.fast_path_eligible is True

    def test_schema_allows_explicit_encoding(self):
        assert "encoding" in ReadTool.json_schema["properties"]


class TestReadToolExecution:
    def test_file_not_found(self):
        tool = ReadTool(session=make_session(is_file=False))
        result = asyncio.run(tool.execute({"file_path": "/workspace/nope"}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"

    def test_full_read_small_file(self):
        content = "\n".join(f"line {i}" for i in range(10))
        tool = ReadTool(session=make_session(content=content))
        result = asyncio.run(tool.execute({"file_path": "/workspace/f.py"}))
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert "1\t" in result.content  # cat -n format

    def test_full_read_marks_read(self):
        content = "hello"
        tool = ReadTool(session=make_session(content=content))
        result = asyncio.run(tool.execute({"file_path": "/workspace/f.py"}))
        assert isinstance(result, ToolResult)
        assert result.meta.get("mark_read") is True

    def test_overlimit_returns_error(self):
        content = "\n".join(f"line {i}" for i in range(2500))
        tool = ReadTool(session=make_session(content=content))
        result = asyncio.run(tool.execute({"file_path": "/workspace/big.py"}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "offset" in result.content.lower()

    def test_ranged_read(self):
        content = "\n".join(f"line {i}" for i in range(100))
        tool = ReadTool(session=make_session(content=content))
        result = asyncio.run(
            tool.execute(
                {
                    "file_path": "/workspace/f.py",
                    "offset": 10,
                    "limit": 5,
                }
            )
        )
        assert isinstance(result, ToolResult)
        assert result.meta.get("mark_read") is True

    def test_ranged_read_marks_read(self):
        content = "\n".join(f"line {i}" for i in range(100))
        tool = ReadTool(session=make_session(content=content))
        result = asyncio.run(
            tool.execute(
                {
                    "file_path": "/workspace/f.py",
                    "offset": 0,
                    "limit": 5,
                }
            )
        )
        assert isinstance(result, ToolResult)
        assert result.meta.get("mark_read") is True

    def test_read_tool_recovers_utf16_bom(self):
        tool = ReadTool(session=make_download_session(b"\xff\xfeh\x00i\x00\n\x00"))
        result = asyncio.run(tool.execute({"file_path": "/workspace/f.txt"}))
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert "hi" in result.content
        assert result.meta["encoding_used"] == "utf-16"

    def test_read_tool_returns_structured_error_for_candidate_text(self):
        raw = "第一行\n第二行\n".encode("gb18030")
        tool = ReadTool(session=make_download_session(raw))
        result = asyncio.run(tool.execute({"file_path": "/workspace/f.txt"}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert result.meta["diagnostic"]["kind"] == "candidate_text"


@dataclasses.dataclass
class _Stat:
    size: int
    mtime: float = 0.0


class FakeImageSession:
    def __init__(self, data: bytes, size: int | None = None):
        self._data = data
        self._size = len(data) if size is None else size

    def is_file(self, path: str) -> bool:
        return True

    def download(self, path: str, timeout=None) -> bytes:
        return self._data

    def stat_file(self, path: str) -> _Stat:
        return _Stat(size=self._size)


class TestReadToolImageBranch:
    def test_read_image_returns_image_payload(self):
        raw = make_png(2, 2)
        tool = ReadTool(
            session=FakeImageSession(raw), vision_enabled=True, vision_detail="high"
        )
        result = tool._execute({"file_path": "/ws/plot.png"})
        assert result.status == "success"
        assert "Read image: /ws/plot.png" in result.content
        assert len(result.images) == 1
        assert result.images[0].url.startswith("data:image/png;base64,")
        assert result.images[0].mime_type == "image/png"
        assert result.images[0].detail == "high"
        assert "mark_read" not in result.meta
        assert "snapshot_seed" not in result.payload

    def test_read_image_vision_disabled_errors(self):
        tool = ReadTool(session=FakeImageSession(make_png(2, 2)))
        result = tool._execute({"file_path": "/ws/plot.png"})
        assert result.status == "error"
        assert "does not support image input" in result.content

    def test_read_image_stat_precheck_skips_download(self):
        class ExplodingSession(FakeImageSession):
            def download(self, path, timeout=None):
                raise AssertionError("must not download oversize image")

        tool = ReadTool(
            session=ExplodingSession(b"", size=4 * 1024 * 1024),
            vision_enabled=True,
        )
        result = tool._execute({"file_path": "/ws/huge.png"})
        assert result.status == "error"
        assert "3 MiB" in result.content

    def test_read_image_oversize_png_dimensions_error(self):
        tool = ReadTool(
            session=FakeImageSession(make_png_header_only(9000, 100)),
            vision_enabled=True,
        )
        result = tool._execute({"file_path": "/ws/wide.png"})
        assert result.status == "error"
        assert "8000px" in result.content

    def test_read_image_magic_wins_over_extension(self):
        tool = ReadTool(session=FakeImageSession(make_png(2, 2)), vision_enabled=True)
        result = tool._execute({"file_path": "/ws/data.dat"})
        assert result.status == "success"
        assert result.images

    def test_read_gif_falls_through_to_text_path(self):
        gif = b"GIF89a" + b"\x00\xff" * 64
        tool = ReadTool(session=FakeImageSession(gif), vision_enabled=True)
        result = tool._execute({"file_path": "/ws/anim.gif"})
        assert result.images == []


class TestReadToolRunnerState:
    def test_execute_with_context_updates_runner_state(self):
        content = "hello"
        tool = ReadTool(session=make_session(content=content))
        state = ToolRunnerState()
        ctx = ToolExecutionContext(runner_state=state)
        asyncio.run(tool.execute_with_context({"file_path": "/workspace/f.py"}, ctx))
        assert "/workspace/f.py" in state.get("read_files", set())

    def test_execute_with_context_writes_snapshot(self):
        tool = ReadTool(session=make_download_session(b"hello\n"))
        state = ToolRunnerState()
        ctx = ToolExecutionContext(runner_state=state)
        asyncio.run(tool.execute_with_context({"file_path": "/workspace/f.txt"}, ctx))
        assert "/workspace/f.txt" in state.get("read_files", set())
        snapshot = state.get("file_semantics", {})["/workspace/f.txt"]
        assert snapshot.fingerprint.size == 6
        assert snapshot.fingerprint.mtime == 1.0
