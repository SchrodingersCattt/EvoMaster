from matmaster.types.current_input import (
    CurrentInputContext,
    build_current_instruction_block,
)


def test_current_input_context_round_trips_payload() -> None:
    ctx = CurrentInputContext.from_values(
        user_text="  analyze  ",
        files=["https://oss.example.com/chat/a.cif", ""],
        images=["https://oss.example.com/chat/fig.png"],
        workspace_paths=[" /share/case/POSCAR "],
        pre_query_scope_event_id=42,
    )

    assert ctx.user_text == "analyze"
    assert ctx.files == ("https://oss.example.com/chat/a.cif",)
    assert ctx.images == ("https://oss.example.com/chat/fig.png",)
    assert ctx.workspace_paths == ("/share/case/POSCAR",)
    assert CurrentInputContext.from_payload(ctx.to_payload()) == ctx
    assert ctx.has_effective_input() is True


def test_build_current_instruction_block_lists_only_current_inputs() -> None:
    ctx = CurrentInputContext.from_values(
        user_text="Use only the new file",
        files=["https://oss.example.com/chat/new.cif"],
        images=["https://oss.example.com/chat/current.png"],
        workspace_paths=["/share/current/POSCAR"],
        pre_query_scope_event_id=12,
    )

    block = build_current_instruction_block(ctx)

    assert block.startswith("<current_instruction>")
    assert "Use only the new file" in block
    assert "file_1 new.cif https://oss.example.com/chat/new.cif" in block
    assert "workspace_1 /share/current/POSCAR" in block
    assert "image_1 current.png https://oss.example.com/chat/current.png" in block
    assert "old.cif" not in block
    assert block.endswith("</current_instruction>")


def test_attachment_only_and_empty_current_instruction() -> None:
    with_file = CurrentInputContext.from_values(
        user_text="",
        files=["https://oss.example.com/chat/only-file.cif"],
    )
    empty = CurrentInputContext()

    assert "file_1 only-file.cif https://oss.example.com/chat/only-file.cif" in (
        build_current_instruction_block(with_file)
    )
    assert empty.has_effective_input() is False
    assert build_current_instruction_block(empty) == ""
