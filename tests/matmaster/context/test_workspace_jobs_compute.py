from matmaster.context.ports import WorkspaceJobs
from matmaster.context.workspace_jobs_compute import (
    CSV_COLUMNS,
    build_csv_rows,
    build_csv_text,
    compute_inline_chars,
    compute_summary,
    render_csv_block,
    render_inline_lines,
    select_priority_samples,
)


def _job(job_id: str, status: str, **extra) -> dict:
    return {"job_id": job_id, "job_name": f"n-{job_id}", "status": status, **extra}


def test_compute_summary_counts_groups_and_statuses() -> None:
    active = (_job("a1", "running"), _job("a2", "running"))
    pending = (_job("p1", "failed"), _job("p2", "finished"))
    recent = (_job("r1", "finished"),)
    s = compute_summary(active, pending, recent)
    assert s.total == 5
    assert (s.active, s.pending_terminal, s.recent_terminal) == (2, 2, 1)
    assert s.by_status == {"running": 2, "failed": 1, "finished": 2}
    assert (s.failed, s.stopped, s.lost) == (1, 0, 0)


def test_render_inline_lines_columnar_and_chars_consistent() -> None:
    jobs = WorkspaceJobs(
        workspace="/share/p",
        active_jobs=(_job("a1", "running"),),
        mode="workspace_observation",
        summary=compute_summary((_job("a1", "running"),), (), ()),
    )
    lines = render_inline_lines(jobs)
    assert lines[0] == "workspace /share/p"
    assert lines[1] == "mode workspace_observation"
    assert lines[2].startswith("summary {")
    assert lines[3] == "active job_id,job_name,status"
    assert lines[4] == "a1,n-a1,running"
    assert compute_inline_chars(jobs) == len("\n".join(lines))


def test_render_csv_block_header_then_escaped_values() -> None:
    rows = [{"job_id": "1", "status": "failed", "input_dir": "a,b"}]
    block = render_csv_block(
        "pending_terminal", ("job_id", "status", "input_dir"), rows
    )
    assert block[0] == "pending_terminal job_id,status,input_dir"
    assert block[1] == '1,failed,"a,b"'


def test_select_priority_samples_action_first_then_fill() -> None:
    pending = (
        _job("p1", "failed"),
        _job("p2", "lost"),
        _job("p3", "finished"),
    )
    active = (_job("a1", "running"),)
    recent = (_job("r1", "finished"),)
    samples = select_priority_samples(
        active, pending, recent, action_limit=200, fill_limit=20
    )
    # 前两条是 action（failed/lost）
    assert samples[0]["job_id"] == "p1"
    assert samples[1]["job_id"] == "p2"
    # fill 含其余 pending(finished) + active + recent
    fill_ids = {s["job_id"] for s in samples[2:]}
    assert fill_ids == {"p3", "a1", "r1"}


def test_select_priority_samples_action_limit_truncates() -> None:
    pending = tuple(_job(f"f{i}", "failed") for i in range(5))
    samples = select_priority_samples((), pending, (), action_limit=2, fill_limit=20)
    # 只前 2 条 failed；其余 failed 既不在 action 也不在 fill
    assert len(samples) == 2
    assert [s["job_id"] for s in samples] == ["f0", "f1"]


def test_build_csv_rows_adds_group_and_total_matches() -> None:
    active = (_job("a1", "running"),)
    pending = (_job("p1", "failed"),)
    recent = (_job("r1", "finished"),)
    rows = build_csv_rows(active, pending, recent)
    assert len(rows) == 3
    assert rows[0]["group"] == "active"
    assert rows[1]["group"] == "pending_terminal"
    assert rows[2]["group"] == "recent_terminal"


def test_build_csv_text_fixed_header_bool_none_and_extras_dropped() -> None:
    rows = [
        {
            "group": "pending_terminal",
            "job_id": "1",
            "job_name": "n",
            "status": "failed",
            "sandbox": True,
            "result_dir": None,
            "user_id": "SECRET",
            "org_id": "SECRET",  # 列集外，必须被丢弃
        }
    ]
    text = build_csv_text(rows)
    header = text.splitlines()[0]
    assert header == ",".join(CSV_COLUMNS)
    assert "SECRET" not in text
    body = text.splitlines()[1]
    assert "true" in body  # bool 小写
    # result_dir=None 与缺失列均为空串：尾部连续逗号
    assert body.count(",") == len(CSV_COLUMNS) - 1
