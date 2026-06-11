"""Tests for ToolResult and normalize_tool_result."""

from __future__ import annotations

from matmaster.tools.tool_result import ToolResult, normalize_tool_result


class TestToolResult:
    def test_defaults(self) -> None:
        result = ToolResult()
        assert result.status == "success"
        assert result.content == ""
        assert result.payload == {}
        assert result.meta == {}

    def test_normalize_success_string(self) -> None:
        result = normalize_tool_result("hello")
        assert result.status == "success"
        assert result.content == "hello"
        assert result.payload == {}

    def test_normalize_error_prefixed_string(self) -> None:
        result = normalize_tool_result("Error: boom")
        assert result.status == "error"
        assert result.content == "Error: boom"

    def test_normalize_none(self) -> None:
        result = normalize_tool_result(None)
        assert result.status == "success"
        assert result.content == ""

    def test_explicit_tool_result_is_preserved(self) -> None:
        raw = ToolResult(
            status="success",
            content="Error: literal text",
            payload={"source": "explicit"},
        )
        result = normalize_tool_result(raw)
        assert result is raw
        assert result.status == "success"
        assert result.payload == {"source": "explicit"}


def test_tool_result_images_roundtrip() -> None:
    from matmaster.types.messages import ImageContentPart

    tr = ToolResult(
        content="Read image: a.png",
        images=[
            ImageContentPart(
                url="data:image/png;base64,aGVsbG8=", mime_type="image/png"
            )
        ],
    )
    restored = ToolResult.model_validate(tr.model_dump(mode="json"))
    assert restored.images[0].url == "data:image/png;base64,aGVsbG8="
    assert restored.images[0].mime_type == "image/png"
    assert ToolResult(content="no images").images == []
