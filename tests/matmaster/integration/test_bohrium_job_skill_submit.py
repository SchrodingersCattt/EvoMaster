from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT_PATH = Path("matmaster/skills/playground-skills/bohrium-job/scripts/submit_job.py")


def test_submit_job_script_delegates_to_shared_upload_workflow(
    monkeypatch,
) -> None:
    spec = importlib.util.spec_from_file_location("bohrium_submit_job_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    called = {}

    def fake_submit_job(*args, **kwargs):
        called["args"] = args
        called["kwargs"] = kwargs
        return ("job-123", "bohr-456")

    monkeypatch.setattr(module, "submit_job_via_runtime", fake_submit_job)

    job_id, bohr_job_id = module.submit_job(
        input_dir=Path("/tmp/inputs"),
        image="demo:latest",
        cmd="python run.py > log 2>&1",
        machine="c32_m128_cpu",
        job_name="demo",
        disk_size=50,
    )

    assert (job_id, bohr_job_id) == ("job-123", "bohr-456")
    assert called["kwargs"]["image"] == "demo:latest"
