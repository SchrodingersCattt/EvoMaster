from inspect import signature

from matmaster.bohrium import jobs as jobs_mod


def test_extract_bohr_job_id_keeps_numeric_suffix() -> None:
    assert jobs_mod._extract_bohr_job_id("123456/789") == "789"


def test_jobs_module_keeps_public_surface() -> None:
    public_names = [
        "RUNNING_STATUSES",
        "get_job_detail_raw",
        "get_file_token",
        "iterate_job_files",
        "download_job_file",
        "download_job_directory",
        "query_job_status",
        "get_job_results",
        "terminate_job",
    ]
    for name in public_names:
        assert hasattr(jobs_mod, name)
    assert "Running" in jobs_mod.RUNNING_STATUSES
    assert "bohr_job_id" in signature(jobs_mod.query_job_status).parameters
