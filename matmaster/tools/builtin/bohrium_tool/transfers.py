from __future__ import annotations

import json
import logging
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .errors import BohriumTransferError
from .models import BohriumDownloadTarget, BohriumInputSource

logger = logging.getLogger(__name__)


def _zip_local_dir(input_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in input_dir.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(input_dir))


def _prepare_remote_input_zip(
    *, source: BohriumInputSource, session, zip_path: Path
) -> None:
    remote_zip_path = f"/tmp/bohrium_input_{uuid4().hex}.zip"
    script = (
        "python3 - <<'PY'\n"
        "import pathlib, zipfile\n"
        f"source = pathlib.Path({json.dumps(source.resolved_path)})\n"
        f"archive = pathlib.Path({json.dumps(remote_zip_path)})\n"
        "with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zf:\n"
        "    for path in source.rglob('*'):\n"
        "        if path.is_file():\n"
        "            zf.write(path, path.relative_to(source))\n"
        "PY"
    )
    cleanup_cmd = f"rm -f {remote_zip_path}"
    try:
        result = session.exec_bash(script)
        if result.get("exit_code") != 0:
            detail = str(
                result.get("stderr")
                or result.get("output")
                or result.get("stdout")
                or "unknown error"
            ).strip()
            raise BohriumTransferError(
                f"Failed to package remote input_dir '{source.resolved_path}': {detail}"
            )
        try:
            zip_path.write_bytes(session.download(remote_zip_path))
        except Exception as exc:
            raise BohriumTransferError(
                f"Failed to download remote input_dir '{source.resolved_path}': {exc}"
            ) from exc
    finally:
        try:
            session.exec_bash(cleanup_cmd)
        except Exception:
            logger.warning(
                "Failed to clean up temporary remote input zip %s",
                remote_zip_path,
                exc_info=True,
            )


@contextmanager
def prepare_input_archive(source: BohriumInputSource, *, session):
    with tempfile.TemporaryDirectory(prefix="bohrium_submit_") as tmp_dir:
        zip_path = Path(tmp_dir) / "input.zip"
        if source.kind == "remote_share_dir":
            _prepare_remote_input_zip(source=source, session=session, zip_path=zip_path)
        else:
            _zip_local_dir(Path(source.resolved_path), zip_path)
        yield zip_path


def publish_download_target(target: BohriumDownloadTarget, *, session) -> str:
    if target.publish_mode == "direct":
        target.staging_dir.mkdir(parents=True, exist_ok=True)
        return target.resolved_path
    try:
        session.upload_directory(str(target.staging_dir), target.resolved_path)
        shutil.rmtree(target.staging_dir, ignore_errors=True)
        return target.resolved_path
    except Exception:
        logger.warning(
            "Failed to upload results to remote share %s",
            target.resolved_path,
            exc_info=True,
        )
        return str(target.staging_dir)
