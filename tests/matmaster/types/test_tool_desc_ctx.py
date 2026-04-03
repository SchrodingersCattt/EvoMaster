from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.topology import RuntimeTopology


def test_tool_description_context_is_frozen() -> None:
    topo = RuntimeTopology(session_kind="local", control_root="/c", workspace_root="/w")
    ctx = ToolDescriptionContext(
        session_kind="local",
        workspace_root="/w",
        topology=topo,
    )

    assert ctx.session_kind == "local"
    assert ctx.workspace_root == "/w"
