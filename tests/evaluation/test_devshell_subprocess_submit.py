"""Tests for score_devshell_tasks --submit argv wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from evaluation.devshell_agent.subprocess_runner import (
    DevshellEvalSubprocess,
    run_score_devshell_tasks_submit,
)


def test_run_score_devshell_tasks_submit_includes_flags(tmp_path: Path) -> None:
    repo_root = tmp_path
    run_dir = tmp_path / "eval_out"
    run_dir.mkdir()
    eval_cfg = tmp_path / "evaluation" / "config.yaml"
    eval_cfg.parent.mkdir(parents=True)
    eval_cfg.write_text("{}", encoding="utf-8")

    with patch.object(
        DevshellEvalSubprocess,
        "run_capture",
        autospec=True,
    ) as mock_cap:
        mock_cap.return_value = (0, "ok\n", "")
        rc, out, err = run_score_devshell_tasks_submit(
            repo_root=repo_root,
            run_dir=run_dir,
            eval_config=eval_cfg,
            eval_ingest_timeout=99.0,
        )
        assert rc == 0
        assert "ok" in out
        mock_cap.assert_called_once()
        argv = mock_cap.call_args[0][1]
        assert "--submit" in argv
        assert str(run_dir) in argv
        assert "99.0" in argv or "99" in argv
        assert str(eval_cfg) in argv
