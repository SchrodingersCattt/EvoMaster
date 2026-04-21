"""Tests for score_devshell_tasks --submit argv wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from evaluation.devshell_agent.subprocess_runner import run_score_devshell_tasks_submit


def test_run_score_devshell_tasks_submit_includes_flags(tmp_path: Path) -> None:
    repo_root = tmp_path
    run_dir = tmp_path / "eval_out"
    run_dir.mkdir()
    eval_cfg = tmp_path / "evaluation" / "config.yaml"
    eval_cfg.parent.mkdir(parents=True)
    eval_cfg.write_text("{}", encoding="utf-8")

    with patch(
        "evaluation.scripts.devshell.score_devshell_tasks.score_devshell_tasks_for_agent_loop",
        autospec=True,
    ) as mock_loop:
        mock_loop.return_value = 0
        rc, out, err = run_score_devshell_tasks_submit(
            repo_root=repo_root,
            run_dir=run_dir,
            eval_config=eval_cfg,
            eval_ingest_timeout=99.0,
            score_jobs=4,
        )
        assert rc == 0
        assert out == ""
        assert err == ""
        mock_loop.assert_called_once_with(
            run_dir=run_dir,
            eval_config=eval_cfg,
            eval_ingest_timeout=99.0,
            score_jobs=4,
            parallel_checklist_workers=8,
            submit=True,
        )
