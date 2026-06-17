from matmaster.context.ports import WorkspaceJobs
from matmaster.context.workspace_jobs_compute import (
    CSV_COLUMNS,
    PREVIEW_COLUMNS,
    build_csv_rows,
    build_csv_text,
    compute_inline_chars,
    compute_summary,
    render_csv_block,
    render_inline_lines,
    select_delivery_preview_rows,
    select_observation_preview_rows,
    trim_preview_rows_to_char_limit,
)


def _job(job_id: str, status: str, **extra) -> dict:
    return {"job_id": job_id, "job_name": f"n-{job_id}", "status": status, **extra}


def test_compute_summary_counts_groups_and_statuses() -> None:
    active = (_job("a1", "running"), _job("a2", "running"))
    unhandled = (_job("p1", "failed"), _job("p2", "finished"))
    handled_recent = (_job("r1", "finished"),)
    s = compute_summary(active, unhandled, handled_recent)
    assert s.total == 5
    assert (s.active, s.unhandled_terminal, s.handled_recent_terminal) == (2, 2, 1)
    assert s.by_status == {"running": 2, "failed": 1, "finished": 2}
    assert (s.failed, s.stopped, s.lost) == (1, 0, 0)
    assert s.unhandled_action == 1


def test_render_inline_lines_columnar_with_flags_and_chars_consistent() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        active_jobs=(_job("a1", "running"),),
        mode="workspace_observation",
        summary=compute_summary((_job("a1", "running"),), (), ()),
        required_truncated=False,
        handled_recent_has_more=True,
        handled_recent_unavailable=True,
    )
    lines = render_inline_lines(jobs)
    assert lines[0] == "workspace /share/p"
    assert lines[1] == "mode workspace_observation"
    assert lines[2].startswith("summary {")
    assert "required_truncated false" in lines
    assert "handled_recent_has_more true" in lines
    assert "handled_recent_hint " in "\n".join(lines)
    assert "handled_recent_unavailable true" in lines
    assert "handled_recent_unavailable_hint " in "\n".join(lines)
    assert "active job_id,job_name,status" in lines
    assert "a1,n-a1,running" in lines
    assert compute_inline_chars(jobs) == len("\n".join(lines))


def test_render_inline_required_error_is_visible() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        mode="workspace_observation",
        required_error={"reason": "query_failed"},
    )

    text = "\n".join(render_inline_lines(jobs))

    assert "required_context_error " in text
    assert '"reason": "query_failed"' in text


def test_render_csv_block_header_then_escaped_values() -> None:
    rows = [{"job_id": "1", "status": "failed", "input_dir": "a,b"}]
    block = render_csv_block(
        "unhandled_terminal", ("job_id", "status", "input_dir"), rows
    )
    assert block[0] == "unhandled_terminal job_id,status,input_dir"
    assert block[1] == '1,failed,"a,b"'


def test_select_observation_preview_rows_order_and_group_tags() -> None:
    active = (_job("a1", "running"),)
    unhandled = (_job("p1", "failed"), _job("p2", "finished"))
    handled_recent = (_job("r1", "finished"),)
    rows = select_observation_preview_rows(
        active=active,
        unhandled_terminal=unhandled,
        handled_recent_terminal=handled_recent,
        limit=10,
    )
    # order: unhandled action -> active -> unhandled other -> handled recent
    assert [r["job_id"] for r in rows] == ["p1", "a1", "p2", "r1"]
    assert rows[0]["group"] == "unhandled_terminal"
    assert rows[1]["group"] == "active"
    assert rows[2]["group"] == "unhandled_terminal"
    assert rows[3]["group"] == "handled_recent_terminal"


def test_select_observation_preview_rows_respects_limit() -> None:
    unhandled = tuple(_job(f"f{i}", "failed") for i in range(5))
    rows = select_observation_preview_rows(
        active=(), unhandled_terminal=unhandled, handled_recent_terminal=(), limit=2
    )
    assert [r["job_id"] for r in rows] == ["f0", "f1"]


def test_select_delivery_preview_rows_action_only_no_group() -> None:
    rows = (
        _job("f1", "failed"),
        _job("t1", "finished"),
        _job("l1", "lost"),
    )
    selected = select_delivery_preview_rows(rows, limit=10)
    assert [r["job_id"] for r in selected] == ["f1", "l1"]
    assert "group" not in selected[0]


def test_build_csv_rows_adds_group_and_total_matches() -> None:
    active = (_job("a1", "running"),)
    unhandled = (_job("p1", "failed"),)
    handled_recent = (_job("r1", "finished"),)
    rows = build_csv_rows(active, unhandled, handled_recent)
    assert len(rows) == 3
    assert rows[0]["group"] == "active"
    assert rows[1]["group"] == "unhandled_terminal"
    assert rows[2]["group"] == "handled_recent_terminal"


def test_preview_columns_prefixes_group() -> None:
    assert PREVIEW_COLUMNS == ("group", "job_id", "job_name", "status")


def test_trim_preview_rows_to_char_limit_truncates_and_bounds_rendered_preview() -> (
    None
):
    rows = (
        {
            "group": "unhandled_terminal",
            "job_id": "p1",
            "job_name": "n" * 20000,
            "status": "failed",
        },
    )

    trimmed = trim_preview_rows_to_char_limit(
        rows,
        columns=PREVIEW_COLUMNS,
        char_limit=12000,
    )

    assert len(trimmed) == 1
    assert str(trimmed[0]["job_name"]).endswith("...<truncated>")
    rendered = "\n".join(render_csv_block("preview_rows", PREVIEW_COLUMNS, trimmed))
    assert len(rendered) <= 12000


def test_build_csv_text_fixed_header_bool_none_and_extras_dropped() -> None:
    rows = [
        {
            "group": "unhandled_terminal",
            "job_id": "1",
            "job_name": "n",
            "status": "failed",
            "sandbox": True,
            "result_dir": None,
            "user_id": "SECRET",
            "org_id": "SECRET",
        }
    ]
    text = build_csv_text(rows)
    header = text.splitlines()[0]
    assert header == ",".join(CSV_COLUMNS)
    assert "SECRET" not in text
    body = text.splitlines()[1]
    assert "true" in body
    assert body.count(",") == len(CSV_COLUMNS) - 1
