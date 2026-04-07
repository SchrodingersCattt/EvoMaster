"""Helper utilities for the builtin Bohrium tool package."""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple
from uuid import uuid4

from matmaster.tools.tool_result import ToolResult

ResolvePathFn = Callable[..., Any]


class _DownloadTargetDir(NamedTuple):
    local_dir: Path
    report_dir: str
    remote_dir: str | None


def _resolve_download_target_dir(
    *,
    raw_result_dir: str,
    workdir: Path | None,
    session: Any | None,
    resolve_path: ResolvePathFn,
) -> _DownloadTargetDir | ToolResult:
    decision = resolve_path(
        raw_path=raw_result_dir,
        execution_workdir=str(workdir or '.'),
        session=session,
    )
    if decision.requires_remote_session:
        return ToolResult(
            status='error',
            content=(
                f"result_dir '{raw_result_dir}' requires an active remote session "
                'but none is available. Use a local path instead.'
            ),
        )

    if decision.kind == 'remote_share':
        local_dir = Path(tempfile.mkdtemp(prefix='bohrium-download-'))
        return _DownloadTargetDir(
            local_dir=local_dir,
            report_dir=str(local_dir),
            remote_dir=raw_result_dir,
        )

    local_dir = Path(decision.normalized_path)
    return _DownloadTargetDir(
        local_dir=local_dir,
        report_dir=str(local_dir),
        remote_dir=None,
    )


def _finalize_download_target_dir(
    *,
    target: _DownloadTargetDir,
    session: Any | None,
    logger: Any,
) -> str:
    if target.remote_dir is None:
        return target.report_dir
    if session is None or not hasattr(session, 'upload_directory'):
        return target.report_dir
    try:
        session.upload_directory(str(target.local_dir), target.remote_dir)
        shutil.rmtree(target.local_dir, ignore_errors=True)
        return target.remote_dir
    except Exception:
        logger.warning(
            'Failed to upload results to remote share %s',
            target.remote_dir,
            exc_info=True,
        )
        return target.report_dir


def _resolve_bohrium_input_dir(
    *,
    input_dir: str,
    workdir: Path | None,
    session: Any | None,
    resolve_path: ResolvePathFn,
) -> tuple[str, Path | str]:
    """Resolve submit input_dir into a validated local or remote directory."""
    decision = resolve_path(
        raw_path=input_dir,
        execution_workdir=str(workdir or '.'),
        session=session,
    )

    if decision.kind == 'remote_share':
        if session is None:
            raise ValueError(
                f"input_dir '{input_dir}' requires an active remote session, "
                'but none is available'
            )
        if not getattr(session, 'is_open', False):
            raise ValueError(
                f"input_dir '{input_dir}' requires an open remote session, "
                'but the current session is not open'
            )

        remote_dir = decision.normalized_path
        if not session.path_exists(remote_dir):
            raise ValueError(f'Remote input_dir not found: {remote_dir}')
        if session.is_file(remote_dir):
            raise ValueError(f'Remote input_dir is not a directory: {remote_dir}')
        return decision.kind, remote_dir

    local_dir = Path(decision.normalized_path)
    if not local_dir.exists():
        raise ValueError(f'input_dir not found: {input_dir}')
    if not local_dir.is_dir():
        raise ValueError(f'input_dir is not a directory: {input_dir}')
    return decision.kind, local_dir


def _zip_local_input_dir(input_dir: Path, zip_path: Path) -> None:
    """Create input.zip from a local input directory."""
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file_path in input_dir.rglob('*'):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(input_dir))


def _remote_zip_command(input_dir: str, remote_zip_path: str) -> str:
    """Build a remote python3 command that packages input_dir into a zip file."""
    return (
        "python3 - <<'PY'\n"
        "import pathlib\n"
        "import zipfile\n\n"
        f"source = pathlib.Path({json.dumps(input_dir)})\n"
        f"archive = pathlib.Path({json.dumps(remote_zip_path)})\n"
        "archive.parent.mkdir(parents=True, exist_ok=True)\n"
        "with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zf:\n"
        "    for path in source.rglob('*'):\n"
        "        if path.is_file():\n"
        "            zf.write(path, path.relative_to(source))\n"
        "print(archive)\n"
        "PY"
    )


def _prepare_remote_input_zip(
    *,
    input_dir: str,
    session: Any,
    zip_path: Path,
    logger: Any,
) -> None:
    """Package a remote input directory and download the resulting zip locally."""
    remote_zip_path = f'/tmp/bohrium_input_{uuid4().hex}.zip'
    cleanup_cmd = f'rm -f {remote_zip_path}'

    try:
        result = session.exec_bash(_remote_zip_command(input_dir, remote_zip_path))
        if result.get('exit_code') != 0:
            detail = str(
                result.get('stderr')
                or result.get('output')
                or result.get('stdout')
                or 'unknown error'
            ).strip()
            raise RuntimeError(
                f"Failed to package remote input_dir '{input_dir}': {detail}"
            )

        try:
            zip_bytes = session.download(remote_zip_path)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to download remote input_dir '{input_dir}': {exc}"
            ) from exc
        zip_path.write_bytes(zip_bytes)
    finally:
        try:
            session.exec_bash(cleanup_cmd)
        except Exception:
            logger.warning(
                'Failed to clean up temporary remote input zip %s',
                remote_zip_path,
                exc_info=True,
            )


@contextmanager
def prepare_bohrium_input_zip(
    *,
    input_dir: str,
    workdir: Path | None,
    session: Any | None,
    resolve_path: ResolvePathFn,
    logger: Any,
) -> Iterator[Path]:
    """Yield a local input.zip for Bohrium submit from local or remote input_dir."""
    input_kind, resolved_input = _resolve_bohrium_input_dir(
        input_dir=input_dir,
        workdir=workdir,
        session=session,
        resolve_path=resolve_path,
    )

    with tempfile.TemporaryDirectory(prefix='bohrium_submit_') as tmp_dir:
        zip_path = Path(tmp_dir) / 'input.zip'
        if input_kind == 'remote_share':
            _prepare_remote_input_zip(
                input_dir=str(resolved_input),
                session=session,
                zip_path=zip_path,
                logger=logger,
            )
        else:
            _zip_local_input_dir(Path(resolved_input), zip_path)
        yield zip_path


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
