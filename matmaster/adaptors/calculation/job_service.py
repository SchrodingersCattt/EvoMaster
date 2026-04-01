# Placeholder: will be replaced in Task 2 with full implementation.
# Minimal exports to allow __init__.py to import.
from __future__ import annotations

from typing import Any

RUNNING_STATUSES: frozenset[str] = frozenset()


def query_job_status(*a: Any, **kw: Any) -> str:
    raise NotImplementedError


def get_job_results(*a: Any, **kw: Any) -> dict:
    raise NotImplementedError


def iterate_job_files(*a: Any, **kw: Any) -> list:
    raise NotImplementedError


def download_job_file(*a: Any, **kw: Any) -> Any:
    raise NotImplementedError


def download_job_directory(*a: Any, **kw: Any) -> list:
    raise NotImplementedError


def terminate_job(*a: Any, **kw: Any) -> tuple:
    raise NotImplementedError


def get_file_token(*a: Any, **kw: Any) -> tuple:
    raise NotImplementedError


def get_job_detail_raw(*a: Any, **kw: Any) -> dict:
    raise NotImplementedError
