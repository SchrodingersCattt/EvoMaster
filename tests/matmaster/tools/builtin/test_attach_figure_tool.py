"""tests/matmaster/tools/builtin/test_attach_figure_tool.py"""

import asyncio
from unittest.mock import MagicMock

from matmaster.tools.builtin.attach_figure_tool import AttachFigure
from matmaster.types.figures import FigureUploadConfig
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext
from matmaster.types.topology import ToolPlane

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _png(tag: bytes) -> bytes:
    """Distinct valid PNG bytes per tag (distinct figure_id)."""
    return b"\x89PNG\r\n\x1a\n" + tag + b"\x00" * 64


def validate(tool, args):
    return asyncio.run(tool.validate_input(args))


def make_session(path_bytes=None, default=_PNG):
    s = MagicMock()
    s.path_exists.return_value = True
    s.is_file.return_value = True
    if path_bytes is None:
        s.download.return_value = default
    else:
        s.download.side_effect = lambda p: path_bytes[p]
    return s


def make_upload_config(url="https://assets.test/u/fig.png"):
    return FigureUploadConfig(
        session_id="s",
        task_id="t",
        asset_key_prefix="figs",
        upload_bytes=lambda payload, key: url,
    )


def make_ctx(upload_config, tool_call_id="call-1"):
    state = ToolRunnerState()
    state.set("figure_upload_config", upload_config)
    return ToolExecutionContext(runner_state=state, tool_call_id=tool_call_id)


def run_ctx(tool, args, ctx):
    return asyncio.run(tool.execute_with_context(args, ctx))


class TestAttachFigureMetadata:
    def test_name(self):
        assert AttachFigure.name == "AttachFigure"

    def test_plane_is_external_service(self):
        assert AttachFigure.plane == ToolPlane.EXTERNAL_SERVICE

    def test_effect_level_external(self):
        assert AttachFigure.effect_level == "external_effect"

    def test_capabilities_workspace_read(self):
        assert AttachFigure.capabilities == frozenset({"workspace.read"})

    def test_resource_claim_shared_read(self):
        claims = AttachFigure.resource_claims
        assert len(claims) == 1
        assert claims[0].resource == "workspace"
        assert claims[0].mode == "shared_read"

    def test_schema_requires_figures_array(self):
        schema = AttachFigure.json_schema
        assert schema["required"] == ["figures"]
        assert schema["additionalProperties"] is False
        figures = schema["properties"]["figures"]
        assert figures["type"] == "array"
        assert figures["minItems"] == 1
        assert figures["maxItems"] == 20
        item = figures["items"]
        assert item["required"] == ["output_path", "caption"]
        assert item["additionalProperties"] is False
        assert "command" not in item["properties"]
        assert "timeout" not in item["properties"]

    def test_prompt_mentions_tool_and_workspace_root(self):
        text = AttachFigure(workdir="/share").prompt() or ""
        assert "AttachFigure" in text
        assert "/share" in text


class TestAttachFigureValidateInput:
    def test_empty_figures_denied(self):
        tool = AttachFigure(workdir="/share")
        d = validate(tool, {"figures": []})
        assert d is not None and d.decision == "deny"

    def test_too_many_figures_denied(self):
        tool = AttachFigure(workdir="/share")
        figs = [{"output_path": f"/share/f{i}.png", "caption": "c"} for i in range(21)]
        d = validate(tool, {"figures": figs})
        assert d is not None and d.decision == "deny"

    def test_missing_output_path_denied(self):
        tool = AttachFigure(workdir="/share")
        d = validate(tool, {"figures": [{"caption": "c"}]})
        assert d is not None and d.decision == "deny"

    def test_missing_caption_denied(self):
        tool = AttachFigure(workdir="/share")
        d = validate(tool, {"figures": [{"output_path": "/share/band.png"}]})
        assert d is not None and d.decision == "deny"

    def test_relative_path_denied(self):
        tool = AttachFigure(workdir="/share")
        d = validate(
            tool, {"figures": [{"output_path": "results/band.png", "caption": "c"}]}
        )
        assert d is not None and d.decision == "deny"

    def test_escape_path_denied(self):
        tool = AttachFigure(workdir="/share")
        d = validate(
            tool, {"figures": [{"output_path": "/etc/passwd.png", "caption": "c"}]}
        )
        assert d is not None and d.decision == "deny"

    def test_duplicate_output_path_denied(self):
        tool = AttachFigure(workdir="/share")
        d = validate(
            tool,
            {
                "figures": [
                    {"output_path": "/share/band.png", "caption": "a"},
                    {"output_path": "/share/band.png", "caption": "b"},
                ]
            },
        )
        assert d is not None and d.decision == "deny"

    def test_valid_absolute_batch_allowed(self):
        tool = AttachFigure(workdir="/share")
        d = validate(
            tool,
            {
                "figures": [
                    {"output_path": "/share/a.png", "caption": "A"},
                    {"output_path": "/share/results/b.png", "caption": "B"},
                ]
            },
        )
        assert d is None


class TestAttachFigurePublish:
    def test_publishes_single_image(self):
        session = make_session()
        tool = AttachFigure(session=session, workdir="/share")
        result = run_ctx(
            tool,
            {"figures": [{"output_path": "/share/band.png", "caption": "Band"}]},
            make_ctx(make_upload_config()),
        )
        assert result.status == "success"
        assert len(result.payload["figures"]) == 1
        fig = result.payload["figures"][0]
        assert fig["caption"] == "Band"
        assert f"[[fig:{fig['figure_id']}]]" in result.content

    def test_does_not_exec_shell(self):
        session = make_session()
        tool = AttachFigure(session=session, workdir="/share")
        run_ctx(
            tool,
            {"figures": [{"output_path": "/share/band.png", "caption": "c"}]},
            make_ctx(make_upload_config()),
        )
        session.exec_bash.assert_not_called()

    def test_publishes_batch_n(self):
        path_bytes = {"/share/a.png": _png(b"a"), "/share/b.png": _png(b"b")}
        session = make_session(path_bytes=path_bytes)
        tool = AttachFigure(session=session, workdir="/share")
        result = run_ctx(
            tool,
            {
                "figures": [
                    {"output_path": "/share/a.png", "caption": "A"},
                    {"output_path": "/share/b.png", "caption": "B"},
                ]
            },
            make_ctx(make_upload_config()),
        )
        assert result.status == "success"
        ids = [f["figure_id"] for f in result.payload["figures"]]
        assert len(ids) == 2 and len(set(ids)) == 2
        assert result.content.count("[[fig:") == 2

    def test_phase_a_failure_missing_file_zero_upload(self):
        session = make_session()
        session.path_exists.return_value = False
        uploads = []
        cfg = FigureUploadConfig(
            session_id="s",
            task_id="t",
            asset_key_prefix="figs",
            upload_bytes=lambda payload, key: uploads.append(key) or "https://a/x.png",
        )
        tool = AttachFigure(session=session, workdir="/share")
        result = run_ctx(
            tool,
            {"figures": [{"output_path": "/share/band.png", "caption": "c"}]},
            make_ctx(cfg),
        )
        assert result.status == "error"
        assert not result.payload.get("figures")
        assert "file_not_found" in result.content
        assert uploads == []  # zero upload on Phase A failure

    def test_same_basename_gets_distinct_suffix(self):
        # Same basename in distinct paths -> response-unique ids band / band-2,
        # both published (the old content-hash duplicate rejection is gone).
        path_bytes = {"/share/x/band.png": _png(b"x"), "/share/y/band.png": _png(b"y")}
        session = make_session(path_bytes=path_bytes)
        tool = AttachFigure(session=session, workdir="/share")
        result = run_ctx(
            tool,
            {
                "figures": [
                    {"output_path": "/share/x/band.png", "caption": "first"},
                    {"output_path": "/share/y/band.png", "caption": "second"},
                ]
            },
            make_ctx(make_upload_config()),
        )
        assert result.status == "success"
        ids = [f["figure_id"] for f in result.payload["figures"]]
        assert ids == ["band", "band-2"]
        assert result.content.count("[[fig:") == 2

    def test_basename_unique_across_calls_in_one_run(self):
        # The figure_id registry lives on runner_state, shared across calls in
        # one response, so a repeated basename in a later call becomes band-2.
        session = make_session()
        tool = AttachFigure(session=session, workdir="/share")
        ctx = make_ctx(make_upload_config())  # one runner_state, reused below
        first = run_ctx(
            tool,
            {"figures": [{"output_path": "/share/band.png", "caption": "a"}]},
            ctx,
        )
        second = run_ctx(
            tool,
            {"figures": [{"output_path": "/share/sub/band.png", "caption": "b"}]},
            ctx,
        )
        assert first.payload["figures"][0]["figure_id"] == "band"
        assert second.payload["figures"][0]["figure_id"] == "band-2"

    def test_phase_b_failure_yields_no_figures(self):
        path_bytes = {
            "/share/a.png": _png(b"a"),
            "/share/b.png": _png(b"b"),
            "/share/c.png": _png(b"c"),
        }
        session = make_session(path_bytes=path_bytes)

        def upload_bytes(payload, key):
            if key.endswith("c.png"):
                raise RuntimeError("upload down")
            return "https://a/" + key

        cfg = FigureUploadConfig(
            session_id="s",
            task_id="t",
            asset_key_prefix="figs",
            upload_bytes=upload_bytes,
        )
        tool = AttachFigure(session=session, workdir="/share")
        result = run_ctx(
            tool,
            {
                "figures": [
                    {"output_path": "/share/a.png", "caption": "A"},
                    {"output_path": "/share/b.png", "caption": "B"},
                    {"output_path": "/share/c.png", "caption": "C"},
                ]
            },
            make_ctx(cfg),
        )
        assert result.status == "error"
        assert not result.payload.get("figures")
        assert "c.png" in result.content

    def test_missing_upload_config_returns_error(self):
        session = make_session()
        tool = AttachFigure(session=session, workdir="/share")
        state = ToolRunnerState()
        ctx = ToolExecutionContext(runner_state=state, tool_call_id="call-1")
        result = run_ctx(
            tool,
            {"figures": [{"output_path": "/share/band.png", "caption": "c"}]},
            ctx,
        )
        assert result.status == "error"
        assert "not configured" in result.content

    def test_missing_tool_call_id_returns_error(self):
        session = make_session()
        tool = AttachFigure(session=session, workdir="/share")
        result = run_ctx(
            tool,
            {"figures": [{"output_path": "/share/band.png", "caption": "c"}]},
            make_ctx(make_upload_config(), tool_call_id=None),
        )
        assert result.status == "error"


def test_exported_from_builtin_package():
    from matmaster.tools.builtin import AttachFigure as Exported

    assert Exported is AttachFigure


def test_in_session_requiring_names():
    from matmaster.core.exp import _SESSION_REQUIRING_TOOL_NAMES

    assert "AttachFigure" in _SESSION_REQUIRING_TOOL_NAMES


def test_in_external_effect_names():
    from matmaster.core.exp import _EXTERNAL_EFFECT_TOOL_NAMES

    assert "AttachFigure" in _EXTERNAL_EFFECT_TOOL_NAMES
