# Bohrium SDK-Free Transfer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace builtin Bohrium submit/download data transfer with a standalone, SDK-free large-file transfer package while preserving current behavior through staged, reversible phases.

**Architecture:** Keep Bohrium control-plane code in `matmaster-evo`; move transfer data-plane code into a dedicated `matmaster_bohrium_transfer` package under `packages/bohrium-transfer`. Deliver four independent phases: package extraction, remote preinstalled CLI, SDK-free upload, and SDK-free download plus dependency removal.

**Tech Stack:** Python 3.11+, uv workspace/path package, `requests`, stdlib `zipfile`, `hashlib`, `concurrent.futures`, `os.open` secure file writes, pytest/monkeypatch fakes, existing SSH/session protocol.

---

## File Structure

- `packages/bohrium-transfer/pyproject.toml`: standalone transfer runtime package metadata; depends only on `requests` plus stdlib.
- `packages/bohrium-transfer/src/matmaster_bohrium_transfer/version.py`: package/protocol/schema version and capability advertisement.
- `packages/bohrium-transfer/src/matmaster_bohrium_transfer/errors.py`: structured, redacted transfer exceptions shared by upload/download/CLI.
- `packages/bohrium-transfer/src/matmaster_bohrium_transfer/security.py`: secret redaction and local atomic 0600 JSON writes.
- `packages/bohrium-transfer/src/matmaster_bohrium_transfer/manifest.py`: same-session transfer manifest read/write/GC.
- `packages/bohrium-transfer/src/matmaster_bohrium_transfer/progress.py`: progress event dataclass plus rate-limited logging sink.
- `packages/bohrium-transfer/src/matmaster_bohrium_transfer/archive.py`: `ZIP_STORED` input archive creation and directory fingerprinting.
- `packages/bohrium-transfer/src/matmaster_bohrium_transfer/client.py`: direct StoreHost HTTP client for multipart upload and object listing.
- `packages/bohrium-transfer/src/matmaster_bohrium_transfer/multipart.py`: concurrent multipart upload, manifest resume, retry/backoff.
- `packages/bohrium-transfer/src/matmaster_bohrium_transfer/download.py`: Range probing, concurrent Range download, sandbox fallback chain, safe zip extraction, atomic result publish.
- `packages/bohrium-transfer/src/matmaster_bohrium_transfer/remote.py`: remote CLI entry point for `version`, `upload-submit`, and `download-results`.
- `matmaster/bohrium/upload.py`: main-project submit adapter; constructs Bohrium object key/download URL and delegates upload to the transfer package.
- `matmaster/bohrium/artifacts.py`: main-project download adapter; preserves existing public API and sandbox fallback semantics while reusing transfer helpers.
- `matmaster/tools/builtin/bohrium_tool/remote_runner.py`: SSH/session runner for preinstalled remote package; probes version and executes CLI without copying source.
- `matmaster/tools/builtin/bohrium_tool/transfers.py`: builtin tool bridge; keeps `paths.py`/`models.py` as control-plane path resolution and calls the new runner.
- `Dockerfile.remote`: optional wheel install hook for `matmaster_bohrium_transfer`; final phase removes `bohrium-sdk`.
- `scripts/build_bohrium_transfer_bundle.py`: reproducible wheel build metadata and SHA256 generation for remote image builds.

---

## Phase A: Package Extraction And Install Chain

### Task 1: Scaffold Standalone Transfer Package

**Files:**
- Create: `packages/bohrium-transfer/pyproject.toml`
- Create: `packages/bohrium-transfer/src/matmaster_bohrium_transfer/__init__.py`
- Create: `packages/bohrium-transfer/src/matmaster_bohrium_transfer/version.py`
- Create: `packages/bohrium-transfer/src/matmaster_bohrium_transfer/errors.py`
- Create: `tests/matmaster_bohrium_transfer/test_version.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Write failing package import/version test**

Create `tests/matmaster_bohrium_transfer/test_version.py`:

```python
from __future__ import annotations

from matmaster_bohrium_transfer.version import (
    CAPABILITIES,
    PACKAGE_NAME,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    version_payload,
)


def test_version_payload_exposes_protocol_and_capabilities() -> None:
    payload = version_payload()

    assert payload["ok"] is True
    assert payload["package"] == PACKAGE_NAME
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["protocol_version"] == PROTOCOL_VERSION
    assert "zip_stored" in payload["capabilities"]
    assert "redacted_errors" in payload["capabilities"]
    assert sorted(payload["capabilities"]) == sorted(CAPABILITIES)
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
uv run pytest tests/matmaster_bohrium_transfer/test_version.py -q
```

Expected before implementation: import failure for `matmaster_bohrium_transfer`.

- [ ] **Step 3: Add package metadata and version module**

Create `packages/bohrium-transfer/pyproject.toml`:

```toml
[project]
name = "matmaster-bohrium-transfer"
version = "0.1.0"
description = "MatMaster Bohrium large-file transfer runtime"
requires-python = ">=3.11"
dependencies = [
    "requests",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/matmaster_bohrium_transfer"]
```

Create `packages/bohrium-transfer/src/matmaster_bohrium_transfer/__init__.py`:

```python
from __future__ import annotations

from .version import PACKAGE_NAME, PACKAGE_VERSION, PROTOCOL_VERSION, SCHEMA_VERSION

__all__ = [
    "PACKAGE_NAME",
    "PACKAGE_VERSION",
    "PROTOCOL_VERSION",
    "SCHEMA_VERSION",
]
```

Create `packages/bohrium-transfer/src/matmaster_bohrium_transfer/version.py`:

```python
from __future__ import annotations

import platform
from importlib.metadata import PackageNotFoundError, version

PACKAGE_NAME = "matmaster-bohrium-transfer"
SCHEMA_VERSION = "v1"
PROTOCOL_VERSION = "1.0"
GIT_COMMIT = "unknown"

CAPABILITIES = (
    "multipart_upload",
    "upload_concurrency",
    "manifest_resume",
    "range_resume",
    "range_download_concurrency",
    "sandbox_iterate",
    "zip_stored",
    "secure_payload_file",
    "redacted_errors",
)


def _package_version() -> str:
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "0.1.0+local"


PACKAGE_VERSION = _package_version()


def version_payload() -> dict[str, object]:
    return {
        "ok": True,
        "package": PACKAGE_NAME,
        "package_version": PACKAGE_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "git_commit": GIT_COMMIT,
        "capabilities": list(CAPABILITIES),
        "python_version": platform.python_version(),
    }
```

Create `packages/bohrium-transfer/src/matmaster_bohrium_transfer/errors.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TransferError(Exception):
    stage: str
    safe_message: str
    retryable: bool = False
    transfer_id: str = ""
    bytes_done: int | None = None
    bytes_total: int | None = None
    resume_available: bool = False
    redacted_detail: str = ""

    def __str__(self) -> str:
        return self.safe_message

    def to_payload(self) -> dict[str, object]:
        return {
            "ok": False,
            "stage": self.stage,
            "retryable": self.retryable,
            "safe_message": self.safe_message,
            "transfer_id": self.transfer_id,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "resume_available": self.resume_available,
            "redacted_detail": self.redacted_detail,
        }


class ArchiveError(TransferError):
    pass


class StorageInitError(TransferError):
    pass


class StoragePartUploadError(TransferError):
    pass


class StorageCompleteError(TransferError):
    pass


class ManifestError(TransferError):
    pass


class ResumeValidationError(TransferError):
    pass


class RangeProbeError(TransferError):
    pass


class DownloadError(TransferError):
    pass


class ExtractError(TransferError):
    pass


class PublishError(TransferError):
    pass


class RemoteVersionError(TransferError):
    pass


class RemoteExecutionError(TransferError):
    pass
```

Modify root `pyproject.toml` to make uv install the workspace package:

```toml
dependencies = [
    "matmaster-bohrium-transfer",
    # keep the existing project dependencies unchanged after this new entry
]

[tool.uv.workspace]
members = ["packages/bohrium-transfer"]

[tool.uv.sources]
matmaster-bohrium-transfer = { workspace = true }
```

- [ ] **Step 4: Refresh uv lock**

Run:

```bash
uv lock
```

Expected: `uv.lock` includes the workspace package and remains resolvable.

- [ ] **Step 5: Verify package import**

Run:

```bash
uv run pytest tests/matmaster_bohrium_transfer/test_version.py -q
```

Expected after implementation: test passes.

- [ ] **Step 6: Commit Phase A scaffold**

Run:

```bash
git add pyproject.toml uv.lock packages/bohrium-transfer tests/matmaster_bohrium_transfer/test_version.py
git commit -m "feat: scaffold bohrium transfer package"
```

### Task 2: Add Remote CLI Version Command

**Files:**
- Create: `packages/bohrium-transfer/src/matmaster_bohrium_transfer/remote.py`
- Test: `tests/matmaster_bohrium_transfer/test_remote_cli.py`

- [ ] **Step 1: Write failing CLI version test**

Create `tests/matmaster_bohrium_transfer/test_remote_cli.py`:

```python
from __future__ import annotations

import json

from matmaster_bohrium_transfer.remote import main


def test_remote_cli_version_outputs_json(capsys) -> None:
    exit_code = main(["version", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["protocol_version"] == "1.0"
    assert "multipart_upload" in payload["capabilities"]
    assert captured.err == ""
```

- [ ] **Step 2: Run the failing CLI test**

Run:

```bash
uv run pytest tests/matmaster_bohrium_transfer/test_remote_cli.py -q
```

Expected before implementation: import failure for `matmaster_bohrium_transfer.remote`.

- [ ] **Step 3: Implement `remote.py` version command**

Create `packages/bohrium-transfer/src/matmaster_bohrium_transfer/remote.py`:

```python
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .version import version_payload


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matmaster-bohrium-transfer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    version_parser = subparsers.add_parser("version")
    version_parser.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "version":
        payload = version_payload()
        if args.as_json:
            _print_json(payload)
        else:
            print(
                f"{payload['package']} {payload['package_version']} "
                f"protocol={payload['protocol_version']}"
            )
        return 0
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Verify CLI version**

Run:

```bash
uv run pytest tests/matmaster_bohrium_transfer/test_remote_cli.py -q
uv run python -m matmaster_bohrium_transfer.remote version --json
```

Expected: pytest passes; command prints JSON containing `"ok": true`.

- [ ] **Step 5: Commit CLI version**

Run:

```bash
git add packages/bohrium-transfer/src/matmaster_bohrium_transfer/remote.py tests/matmaster_bohrium_transfer/test_remote_cli.py
git commit -m "feat: add bohrium transfer remote version cli"
```

### Task 3: Build Wheel Bundle And Dockerfile Install Hook

**Files:**
- Create: `scripts/build_bohrium_transfer_bundle.py`
- Modify: `Dockerfile.remote`
- Test: `tests/scripts/test_build_bohrium_transfer_bundle.py`

- [ ] **Step 1: Write failing bundle script test**

Create `tests/scripts/test_build_bohrium_transfer_bundle.py`:

```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_build_bohrium_transfer_bundle_dry_run_outputs_metadata() -> None:
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/build_bohrium_transfer_bundle.py",
            "--dry-run",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    assert payload["package"] == "matmaster-bohrium-transfer"
    assert payload["protocol_version"] == "1.0"
    assert payload["wheel_path"].endswith(".whl")
    assert payload["sha256_path"].endswith(".sha256")
```

- [ ] **Step 2: Run the failing bundle test**

Run:

```bash
uv run pytest tests/scripts/test_build_bohrium_transfer_bundle.py -q
```

Expected before implementation: script file missing.

- [ ] **Step 3: Implement bundle script**

Create `scripts/build_bohrium_transfer_bundle.py`:

```python
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from matmaster_bohrium_transfer.version import (
    CAPABILITIES,
    PACKAGE_NAME,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
)

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "packages" / "bohrium-transfer"
DIST_DIR = PACKAGE_DIR / "dist"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_bundle(*, dry_run: bool = False) -> dict[str, object]:
    wheel_path = DIST_DIR / "matmaster_bohrium_transfer-0.1.0-py3-none-any.whl"
    sha_path = wheel_path.with_suffix(wheel_path.suffix + ".sha256")
    if not dry_run:
        subprocess.run(
            ["uv", "build", "--package", PACKAGE_NAME, "--wheel"],
            cwd=ROOT,
            check=True,
        )
        wheels = sorted(DIST_DIR.glob("matmaster_bohrium_transfer-*.whl"))
        if not wheels:
            raise RuntimeError(f"no wheel found in {DIST_DIR}")
        wheel_path = wheels[-1]
        sha_path = wheel_path.with_suffix(wheel_path.suffix + ".sha256")
        sha_path.write_text(f"{_sha256(wheel_path)}  {wheel_path.name}\n")
    return {
        "package": PACKAGE_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "capabilities": list(CAPABILITIES),
        "wheel_path": str(wheel_path),
        "sha256_path": str(sha_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build_bundle(dry_run=args.dry_run), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Modify `Dockerfile.remote` by adding the install hook after the existing `pip install` block:

```dockerfile
ARG MATMASTER_BOHRIUM_TRANSFER_URL=""
ARG MATMASTER_BOHRIUM_TRANSFER_SHA256=""

RUN if [ -n "$MATMASTER_BOHRIUM_TRANSFER_URL" ]; then \
      wget -O /tmp/matmaster_bohrium_transfer.whl "$MATMASTER_BOHRIUM_TRANSFER_URL" && \
      echo "$MATMASTER_BOHRIUM_TRANSFER_SHA256  /tmp/matmaster_bohrium_transfer.whl" | sha256sum -c - && \
      pip install --no-cache-dir /tmp/matmaster_bohrium_transfer.whl && \
      rm -f /tmp/matmaster_bohrium_transfer.whl ; \
    fi
```

- [ ] **Step 4: Verify bundle script and Dockerfile syntax**

Run:

```bash
uv run pytest tests/scripts/test_build_bohrium_transfer_bundle.py -q
docker build -f Dockerfile.remote --target not-a-real-target . >/tmp/mm-transfer-docker-check.log 2>&1 || true
rg -n "MATMASTER_BOHRIUM_TRANSFER_URL|matmaster_bohrium_transfer.whl" Dockerfile.remote
```

Expected: pytest passes; `rg` finds the install hook. The docker command may fail because `not-a-real-target` is invalid; it is only a quick Dockerfile parse smoke check and should not be treated as a release gate.

- [ ] **Step 5: Commit bundle and Dockerfile hook**

Run:

```bash
git add scripts/build_bohrium_transfer_bundle.py tests/scripts/test_build_bohrium_transfer_bundle.py Dockerfile.remote
git commit -m "feat: add bohrium transfer bundle install hook"
```

## Phase B: Remove Runtime Source Copy

### Task 4: Implement Remote Version Probe In Main Project

**Files:**
- Modify: `matmaster/tools/builtin/bohrium_tool/remote_runner.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py`

- [ ] **Step 1: Add failing version-probe tests**

Append to `tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py`:

```python
def test_remote_version_probe_uses_preinstalled_package() -> None:
    session = RunnerSession(
        helper_stdout=json.dumps(
            {
                "schema_version": "v1",
                "protocol_version": "1.0",
                "ok": True,
                "package": "matmaster-bohrium-transfer",
                "capabilities": ["multipart_upload", "zip_stored"],
            }
        )
    )

    from matmaster.tools.builtin.bohrium_tool.remote_runner import probe_remote_transfer

    payload = probe_remote_transfer(session)

    assert payload["ok"] is True
    assert any(
        "python3 -m matmaster_bohrium_transfer.remote version --json" in cmd
        for cmd in session.exec_calls
    )


def test_remote_version_probe_rejects_non_json() -> None:
    session = RunnerSession(helper_stdout="not json", helper_exit_code=1)

    from matmaster.tools.builtin.bohrium_tool.remote_runner import probe_remote_transfer

    with pytest.raises(BohriumTransferError, match="remote transfer version probe"):
        probe_remote_transfer(session)
```

- [ ] **Step 2: Run the failing probe tests**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py::test_remote_version_probe_uses_preinstalled_package tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py::test_remote_version_probe_rejects_non_json -q
```

Expected before implementation: import failure for `probe_remote_transfer`.

- [ ] **Step 3: Implement version probe and Python discovery**

In `matmaster/tools/builtin/bohrium_tool/remote_runner.py`, replace the legacy helper import:

```python
from matmaster.bohrium.remote_transfer_helper import SCHEMA_VERSION, redact_secrets
```

with transfer package imports:

```python
from matmaster_bohrium_transfer.security import redact_secrets
from matmaster_bohrium_transfer.version import SCHEMA_VERSION
```

Then add:

```python
REMOTE_PROTOCOL_MAJOR = "1"


def _remote_transfer_python_binary() -> str:
    return (
        os.environ.get("BOHRIUM_TRANSFER_REMOTE_PYTHON")
        or os.environ.get("BOHRIUM_REMOTE_HELPER_PYTHON")
        or "python3"
    ).strip()


def _parse_json_stdout(stdout: str, *, purpose: str) -> dict[str, Any]:
    try:
        parsed = json.loads(stdout.strip())
    except json.JSONDecodeError as exc:
        raise BohriumTransferError(
            f"{purpose} stdout is not JSON: {redact_secrets(stdout)}"
        ) from exc
    if not isinstance(parsed, dict):
        raise BohriumTransferError(f"{purpose} JSON output must be an object")
    return parsed


def probe_remote_transfer(session) -> dict[str, Any]:
    python_binary = _remote_transfer_python_binary()
    quoted_python = shlex.quote(python_binary)
    command = f"{quoted_python} -m matmaster_bohrium_transfer.remote version --json"
    result = session.exec_bash(command, timeout=30)
    stdout = str(result.get("stdout") or "").strip()
    if result.get("exit_code") != 0:
        detail = stdout or result.get("stderr") or result.get("output") or ""
        raise BohriumTransferError(
            "remote transfer version probe failed: "
            f"{redact_secrets(detail)}"
        )
    payload = _parse_json_stdout(stdout, purpose="remote transfer version probe")
    protocol = str(payload.get("protocol_version") or "")
    if protocol.split(".", 1)[0] != REMOTE_PROTOCOL_MAJOR:
        raise BohriumTransferError(
            "remote transfer protocol mismatch: "
            f"expected major {REMOTE_PROTOCOL_MAJOR}, got {protocol!r}"
        )
    return payload
```

- [ ] **Step 4: Verify probe tests**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py -q
```

Expected after implementation: all remote runner tests pass.

- [ ] **Step 5: Commit version probe**

Run:

```bash
git add matmaster/tools/builtin/bohrium_tool/remote_runner.py tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py
git commit -m "feat: probe remote bohrium transfer package"
```

### Task 5: Replace Helper Source Copy With Remote Package CLI

**Files:**
- Modify: `matmaster/tools/builtin/bohrium_tool/remote_runner.py`
- Modify: `matmaster/tools/builtin/bohrium_tool/transfers.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py`

- [ ] **Step 1: Add failing runner execution tests**

Append to `tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py`:

```python
def test_run_remote_transfer_uses_package_cli_not_source_copy() -> None:
    session = RunnerSession(
        helper_stdout=json.dumps(
            {
                "schema_version": "v1",
                "protocol_version": "1.0",
                "ok": True,
                "oss_key": "prefix/input.zip",
            }
        )
    )

    from matmaster.tools.builtin.bohrium_tool.remote_runner import run_remote_transfer

    result = run_remote_transfer(
        session,
        subcommand="upload-submit",
        payload={"input_dir": "/share/input", "token": "secret-token"},
    )

    assert result["oss_key"] == "prefix/input.zip"
    assert not any(path.endswith("remote_transfer_helper.py") for path, _ in session.writes)
    assert any(
        "-m matmaster_bohrium_transfer.remote upload-submit --payload-file" in cmd
        for cmd in session.exec_calls
    )
    assert not any("secret-token" in cmd for cmd in session.exec_calls)
```

- [ ] **Step 2: Run the failing execution test**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py::test_run_remote_transfer_uses_package_cli_not_source_copy -q
```

Expected before implementation: import failure for `run_remote_transfer`.

- [ ] **Step 3: Implement `run_remote_transfer` with exit-code triage**

In `matmaster/tools/builtin/bohrium_tool/remote_runner.py`, add:

```python
def _parse_remote_transfer_result(result: dict, *, purpose: str) -> dict[str, Any]:
    stdout = str(result.get("stdout") or "").strip()
    exit_code = int(result.get("exit_code") or 0)
    if stdout:
        try:
            parsed = _parse_json_stdout(stdout, purpose=purpose)
        except BohriumTransferError:
            if exit_code == 0:
                raise
            detail = result.get("stderr") or result.get("output") or stdout
            raise BohriumTransferError(
                f"{purpose} failed without JSON: {redact_secrets(detail)}"
            )
        if exit_code != 0 or parsed.get("ok") is False:
            safe = parsed.get("safe_message") or parsed.get("error") or "unknown error"
            raise BohriumTransferError(f"{purpose} failed: {redact_secrets(safe)}")
        return parsed
    if exit_code != 0:
        detail = result.get("stderr") or result.get("output") or "empty stdout"
        raise BohriumTransferError(
            f"{purpose} failed without JSON: {redact_secrets(detail)}"
        )
    raise BohriumTransferError(f"{purpose} produced empty stdout")


def run_remote_transfer(
    session,
    *,
    subcommand: str,
    payload: dict[str, Any],
    timeout: int = 3600,
) -> dict[str, Any]:
    if session is None or not getattr(session, "is_open", False):
        raise BohriumTransferError("remote transfer requires an open remote session")

    probe_remote_transfer(session)
    python_binary = _remote_transfer_python_binary()
    quoted_python = shlex.quote(python_binary)
    temp_dir = ""
    try:
        mktemp = _run_checked(
            session,
            "mktemp -d /tmp/matmaster_bohrium_transfer.XXXXXX",
            purpose="remote temp directory creation",
            timeout=15,
        )
        temp_dir = str(mktemp.get("stdout") or "").strip().splitlines()[-1]
        q_temp_dir = shlex.quote(temp_dir)
        _run_checked(
            session,
            f"chmod 700 {q_temp_dir}",
            purpose="remote temp directory permission setup",
            timeout=15,
        )
        payload_path = f"{temp_dir}/payload.json"
        q_payload_path = shlex.quote(payload_path)
        payload_with_schema = dict(payload)
        payload_with_schema.setdefault("schema_version", SCHEMA_VERSION)
        payload_with_schema.setdefault("protocol_version", "1.0")
        _run_checked(
            session,
            f"umask 077; : > {q_payload_path}",
            purpose="remote payload secure create",
            timeout=15,
        )
        session.write_file(
            payload_path,
            json.dumps(payload_with_schema, ensure_ascii=False),
        )
        _run_checked(
            session,
            f"chmod 600 {q_payload_path}",
            purpose="remote payload permission verification",
            timeout=15,
        )
        command = (
            f"{quoted_python} -m matmaster_bohrium_transfer.remote "
            f"{shlex.quote(subcommand)} --payload-file {q_payload_path}"
        )
        result = session.exec_bash(command, timeout=timeout)
        parsed = _parse_remote_transfer_result(
            result,
            purpose=f"remote transfer {subcommand}",
        )
        parsed.setdefault("remote_helper_temp_dir", temp_dir)
        return parsed
    finally:
        if temp_dir:
            session.exec_bash(f"rm -rf {shlex.quote(temp_dir)}", timeout=30)
```

Keep `run_remote_helper()` as a legacy wrapper during Phase B:

```python
def run_remote_helper(
    session,
    *,
    subcommand: str,
    payload: dict[str, Any],
    timeout: int = 3600,
) -> dict[str, Any]:
    if os.environ.get("BOHRIUM_TRANSFER_USE_LEGACY") == "1":
        return _run_legacy_remote_helper(
            session,
            subcommand=subcommand,
            payload=payload,
            timeout=timeout,
        )
    return run_remote_transfer(
        session,
        subcommand=subcommand,
        payload=payload,
        timeout=timeout,
    )
```

Rename the current implementation body to `_run_legacy_remote_helper()`.

- [ ] **Step 4: Wire `transfers.py` to call `run_remote_transfer`**

In `matmaster/tools/builtin/bohrium_tool/transfers.py`, change the import and calls:

```python
from .remote_runner import run_remote_transfer
```

Replace:

```python
result = run_remote_helper(
    session,
    subcommand="upload-submit",
    payload=payload,
)
```

with:

```python
result = run_remote_transfer(
    session,
    subcommand="upload-submit",
    payload=payload,
)
```

Apply the same replacement to the existing `download-results` call:

```python
result = run_remote_transfer(
    session,
    subcommand="download-results",
    payload=payload,
)
```

- [ ] **Step 5: Verify Phase B focused tests**

Run:

```bash
uv run pytest \
  tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py \
  tests/matmaster/integration/test_runtime_credential_bridge_e2e.py \
  -q
```

Expected: tests pass; no test expects writing `remote_transfer_helper.py` unless `BOHRIUM_TRANSFER_USE_LEGACY=1`.

- [ ] **Step 6: Commit remote package CLI integration**

Run:

```bash
git add matmaster/tools/builtin/bohrium_tool/remote_runner.py matmaster/tools/builtin/bohrium_tool/transfers.py tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py tests/matmaster/integration/test_runtime_credential_bridge_e2e.py
git commit -m "feat: use preinstalled bohrium transfer cli"
```

## Phase C: SDK-Free Upload

### Task 6: Add Redaction, Secure File, Manifest, And Progress Primitives

**Files:**
- Create: `packages/bohrium-transfer/src/matmaster_bohrium_transfer/security.py`
- Create: `packages/bohrium-transfer/src/matmaster_bohrium_transfer/manifest.py`
- Create: `packages/bohrium-transfer/src/matmaster_bohrium_transfer/progress.py`
- Test: `tests/matmaster_bohrium_transfer/test_security_manifest_progress.py`

- [ ] **Step 1: Write failing primitive tests**

Create `tests/matmaster_bohrium_transfer/test_security_manifest_progress.py`:

```python
from __future__ import annotations

import json
import os
from pathlib import Path

from matmaster_bohrium_transfer.manifest import ManifestStore
from matmaster_bohrium_transfer.progress import LoggingProgressSink, TransferProgressEvent
from matmaster_bohrium_transfer.security import redact_secrets, secure_write_json


def test_redact_secrets_masks_headers_json_and_urls() -> None:
    raw = {
        "Authorization": "Bearer secret-token",
        "token": "abc123",
        "url": "https://store/api/download/a?token=secret-token",
    }

    redacted = redact_secrets(raw)

    assert "secret-token" not in redacted
    assert "abc123" not in redacted
    assert "<redacted>" in redacted


def test_secure_write_json_uses_0600(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"

    secure_write_json(path, {"token": "secret"})

    mode = path.stat().st_mode & 0o777
    assert mode == 0o600
    assert json.loads(path.read_text())["token"] == "secret"


def test_manifest_store_round_trip_with_lock(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "transfers")
    manifest = {"schema_version": "v1", "transfer_id": "t1", "token": "secret"}

    store.write("t1", manifest)

    loaded = store.read("t1")
    assert loaded == manifest
    assert (tmp_path / "transfers" / "t1" / "manifest.json").stat().st_mode & 0o777 == 0o600


def test_logging_progress_sink_limits_chunk_events(caplog) -> None:
    sink = LoggingProgressSink(min_bytes=32 * 1024 * 1024, min_seconds=1.0)
    event = TransferProgressEvent(
        event_type="download_chunk_completed",
        transfer_id="t1",
        phase="download",
        direction="download",
        bytes_done=1024,
        bytes_total=None,
    )

    sink.emit(event)
    sink.emit(event)

    assert len(caplog.records) <= 1
```

- [ ] **Step 2: Run the failing primitive tests**

Run:

```bash
uv run pytest tests/matmaster_bohrium_transfer/test_security_manifest_progress.py -q
```

Expected before implementation: import failures for new modules.

- [ ] **Step 3: Implement security and manifest primitives**

Create `security.py`:

```python
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

SECRET_KEYS = {"token", "access_key", "accessKey", "authorization", "Authorization"}
TOKEN_QUERY_RE = re.compile(r"(?i)(token|access_key|accessKey)=([^&\\s]+)")
BEARER_RE = re.compile(r"(?i)(Bearer\\s+)[^&\\s]+")
PATH_TOKEN_RE = re.compile(r"(?<=/)[A-Za-z0-9_\\-=]{24,}(?=/|$)")


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<redacted>" if str(key) in SECRET_KEYS else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        text = BEARER_RE.sub(r"\\1<redacted>", value)
        text = TOKEN_QUERY_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)
        return PATH_TOKEN_RE.sub("<redacted>", text)
    return value


def redact_secrets(value: Any) -> str:
    sanitized = _sanitize(value)
    if isinstance(sanitized, str):
        return sanitized
    return json.dumps(sanitized, ensure_ascii=False, sort_keys=True)


def secure_write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, sort_keys=True)
    except Exception:
        target.unlink(missing_ok=True)
        raise
```

Create `manifest.py`:

```python
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from .security import secure_write_json


class ManifestStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def transfer_dir(self, transfer_id: str) -> Path:
        return self.root / transfer_id

    def read(self, transfer_id: str) -> dict[str, Any]:
        path = self.transfer_dir(transfer_id) / "manifest.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, transfer_id: str, manifest: dict[str, Any]) -> None:
        directory = self.transfer_dir(transfer_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / "manifest.json"
        tmp = directory / f"manifest.{time.time_ns()}.tmp"
        secure_write_json(tmp, manifest)
        tmp.replace(path)
        path.chmod(0o600)

    def gc(self, *, older_than_seconds: int = 7 * 24 * 3600) -> list[Path]:
        now = time.time()
        removed: list[Path] = []
        if not self.root.exists():
            return removed
        for child in self.root.iterdir():
            if not child.is_dir() or (child / "lock").exists():
                continue
            if now - child.stat().st_mtime > older_than_seconds:
                shutil.rmtree(child, ignore_errors=True)
                removed.append(child)
        return removed
```

Create `progress.py`:

```python
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransferProgressEvent:
    event_type: str
    transfer_id: str
    phase: str
    direction: str
    bytes_done: int
    bytes_total: int | None
    parts_done: int | None = None
    parts_total: int | None = None
    rate_mbps: float | None = None
    resume_supported: bool | None = None
    location: str | None = None
    package_version: str | None = None
    protocol_version: str | None = None


class ProgressSink:
    def emit(self, event: TransferProgressEvent) -> None:
        raise NotImplementedError


class NoopProgressSink(ProgressSink):
    def emit(self, event: TransferProgressEvent) -> None:
        del event


class LoggingProgressSink(ProgressSink):
    def __init__(self, *, min_bytes: int = 32 * 1024 * 1024, min_seconds: float = 1.0) -> None:
        self.min_bytes = min_bytes
        self.min_seconds = min_seconds
        self._last_bytes: dict[str, int] = {}
        self._last_time: dict[str, float] = {}

    def emit(self, event: TransferProgressEvent) -> None:
        now = time.monotonic()
        last_bytes = self._last_bytes.get(event.transfer_id, 0)
        last_time = self._last_time.get(event.transfer_id, 0.0)
        byte_delta = event.bytes_done - last_bytes
        time_delta = now - last_time
        if event.event_type.endswith("_chunk_completed"):
            if byte_delta < self.min_bytes and time_delta < self.min_seconds:
                return
        self._last_bytes[event.transfer_id] = event.bytes_done
        self._last_time[event.transfer_id] = now
        logger.info(
            "transfer_progress type=%s id=%s bytes=%s/%s",
            event.event_type,
            event.transfer_id,
            event.bytes_done,
            event.bytes_total,
        )
```

- [ ] **Step 4: Verify primitive tests**

Run:

```bash
uv run pytest tests/matmaster_bohrium_transfer/test_security_manifest_progress.py -q
```

Expected: tests pass.

- [ ] **Step 5: Commit primitives**

Run:

```bash
git add packages/bohrium-transfer/src/matmaster_bohrium_transfer tests/matmaster_bohrium_transfer/test_security_manifest_progress.py
git commit -m "feat: add bohrium transfer security and manifest primitives"
```

### Task 7: Add ZIP_STORED Archive And Fingerprint

**Files:**
- Create: `packages/bohrium-transfer/src/matmaster_bohrium_transfer/archive.py`
- Test: `tests/matmaster_bohrium_transfer/test_archive.py`

- [ ] **Step 1: Write failing archive tests**

Create `tests/matmaster_bohrium_transfer/test_archive.py`:

```python
from __future__ import annotations

import zipfile
from pathlib import Path

from matmaster_bohrium_transfer.archive import create_zip_store, directory_fingerprint


def test_create_zip_store_uses_no_compression_and_allows_empty_dir(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    archive = tmp_path / "input.zip"

    result = create_zip_store(input_dir, archive)

    assert result.archive_path == archive
    with zipfile.ZipFile(archive) as zf:
        assert zf.namelist() == []
        assert zf.comment == b""


def test_create_zip_store_preserves_non_ascii_names_and_stored_method(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "结构.in").write_text("data", encoding="utf-8")
    archive = tmp_path / "input.zip"

    create_zip_store(input_dir, archive)

    with zipfile.ZipFile(archive) as zf:
        info = zf.getinfo("结构.in")
        assert info.compress_type == zipfile.ZIP_STORED
        assert zf.read("结构.in") == b"data"


def test_directory_fingerprint_changes_when_file_mtime_changes(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    file_path = input_dir / "a.txt"
    file_path.write_text("aa", encoding="utf-8")
    first = directory_fingerprint(input_dir)

    file_path.write_text("bb", encoding="utf-8")
    second = directory_fingerprint(input_dir)

    assert first != second
```

- [ ] **Step 2: Run failing archive tests**

Run:

```bash
uv run pytest tests/matmaster_bohrium_transfer/test_archive.py -q
```

Expected before implementation: import failure for `archive.py`.

- [ ] **Step 3: Implement archive helpers**

Create `archive.py`:

```python
from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArchiveResult:
    archive_path: Path
    archive_size: int
    archive_mtime_ns: int
    source_fingerprint: str
    archive_format: str = "zip"
    archive_compression: str = "stored"


def directory_fingerprint(input_dir: str | Path) -> str:
    root = Path(input_dir)
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        st = path.lstat()
        kind = "symlink" if path.is_symlink() else "file"
        entries.append(
            {
                "rel_path": path.relative_to(root).as_posix(),
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "mode": stat.S_IMODE(st.st_mode),
                "kind": kind,
            }
        )
    raw = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def create_zip_store(input_dir: str | Path, archive_path: str | Path) -> ArchiveResult:
    root = Path(input_dir)
    target = Path(archive_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    source_fingerprint = directory_fingerprint(root)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                zf.write(path, path.relative_to(root).as_posix())
    st = target.stat()
    return ArchiveResult(
        archive_path=target,
        archive_size=st.st_size,
        archive_mtime_ns=st.st_mtime_ns,
        source_fingerprint=source_fingerprint,
    )
```

- [ ] **Step 4: Verify archive tests**

Run:

```bash
uv run pytest tests/matmaster_bohrium_transfer/test_archive.py -q
```

Expected: tests pass.

- [ ] **Step 5: Commit archive helpers**

Run:

```bash
git add packages/bohrium-transfer/src/matmaster_bohrium_transfer/archive.py tests/matmaster_bohrium_transfer/test_archive.py
git commit -m "feat: add zip-stored bohrium transfer archives"
```

### Task 8: Add StoreHost Client And Multipart Upload

**Files:**
- Create: `packages/bohrium-transfer/src/matmaster_bohrium_transfer/client.py`
- Create: `packages/bohrium-transfer/src/matmaster_bohrium_transfer/multipart.py`
- Test: `tests/matmaster_bohrium_transfer/test_client_multipart.py`
- Test: `tests/matmaster_bohrium_transfer/test_storehost_contract.py`

- [ ] **Step 1: Write failing client/multipart tests**

Create `tests/matmaster_bohrium_transfer/test_client_multipart.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from matmaster_bohrium_transfer.client import StoreHostClient, decode_storage_param
from matmaster_bohrium_transfer.multipart import upload_file_multipart
from matmaster_bohrium_transfer.manifest import ManifestStore


class FakeResponse:
    def __init__(self, *, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {"code": 0, "data": {}}
        self.text = json.dumps(self._payload)

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if not self.ok:
            raise RuntimeError(self.text)


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, url, *, headers=None, json=None, data=None, timeout=None):
        self.calls.append((url, headers or {}, {"json": json, "data": data, "timeout": timeout}))
        if url.endswith("/api/upload/multipart/init"):
            return FakeResponse(payload={"code": 0, "data": {"initialKey": "init-1"}})
        if url.endswith("/api/upload/multipart/upload"):
            param = decode_storage_param((headers or {})["X-Storage-Param"])
            return FakeResponse(payload={"code": 0, "data": {"partString": f"part-{param['number']}"}})
        if url.endswith("/api/upload/multipart/complete"):
            return FakeResponse(payload={"code": 0, "data": {"done": True}})
        raise AssertionError(url)


def test_store_host_upload_part_sends_tiefblue_compatible_header() -> None:
    session = FakeSession()
    client = StoreHostClient("https://store.example", "token-1", session=session)

    result = client.upload_part(
        object_key="prefix/input.zip",
        initial_key="init-1",
        number=2,
        part_size=5,
        data=b"abcde",
    )

    assert result == "part-2"
    _, headers, _ = session.calls[-1]
    decoded = decode_storage_param(headers["X-Storage-Param"])
    assert decoded["initialKey"] == "init-1"
    assert decoded["number"] == 2
    assert decoded["partSize"] == 5
    assert decoded["objectKey"] == "prefix/input.zip"
    assert headers["Authorization"] == "Bearer token-1"


def test_upload_file_multipart_writes_manifest_and_completes(tmp_path: Path) -> None:
    file_path = tmp_path / "input.zip"
    file_path.write_bytes(b"a" * 10)
    session = FakeSession()
    client = StoreHostClient("https://store.example", "token-1", session=session)
    store = ManifestStore(tmp_path / "manifest")

    summary = upload_file_multipart(
        client=client,
        file_path=file_path,
        object_key="prefix/input.zip",
        manifest_store=store,
        transfer_id="t1",
        part_size=4,
        concurrency=2,
        part_retries=1,
    )

    assert summary["ok"] is True
    assert summary["parts_total"] == 3
    manifest = store.read("t1")
    assert [part["part_string"] for part in manifest["parts"]] == ["part-1", "part-2", "part-3"]


def test_upload_file_multipart_resumes_completed_manifest_parts(tmp_path: Path) -> None:
    file_path = tmp_path / "input.zip"
    file_path.write_bytes(b"abcdefghij")
    session = FakeSession()
    client = StoreHostClient("https://store.example", "token-1", session=session)
    store = ManifestStore(tmp_path / "manifest")
    store.write(
        "t1",
        {
            "schema_version": "v1",
            "transfer_id": "t1",
            "object_key": "prefix/input.zip",
            "initial_key": "init-resume",
            "token": "token-1",
            "part_size": 4,
            "file_size": 10,
            "file_mtime_ns": file_path.stat().st_mtime_ns,
            "parts": [
                {
                    "number": 1,
                    "offset": 0,
                    "size": 4,
                    "part_string": "part-1-old",
                    "status": "completed",
                },
                {"number": 2, "offset": 4, "size": 4, "status": "pending"},
                {"number": 3, "offset": 8, "size": 2, "status": "pending"},
            ],
        },
    )

    summary = upload_file_multipart(
        client=client,
        file_path=file_path,
        object_key="prefix/input.zip",
        manifest_store=store,
        transfer_id="t1",
        part_size=4,
        concurrency=2,
        part_retries=1,
    )

    uploaded_part_numbers = [
        decode_storage_param(headers["X-Storage-Param"])["number"]
        for url, headers, _ in session.calls
        if url.endswith("/api/upload/multipart/upload")
    ]
    complete_call = [
        call for call in session.calls if call[0].endswith("/api/upload/multipart/complete")
    ][0]
    assert sorted(uploaded_part_numbers) == [2, 3]
    assert complete_call[2]["json"]["initialKey"] == "init-resume"
    assert complete_call[2]["json"]["partString"] == [
        "part-1-old",
        "part-2",
        "part-3",
    ]
    assert summary["resume_used"] is True
```

- [ ] **Step 2: Run failing client/multipart tests**

Run:

```bash
uv run pytest tests/matmaster_bohrium_transfer/test_client_multipart.py -q
```

Expected before implementation: import failures for `client.py` and `multipart.py`.

- [ ] **Step 3: Implement StoreHost client**

Create `client.py`:

```python
from __future__ import annotations

import base64
import json
from typing import Any

import requests

from .errors import StorageCompleteError, StorageInitError, StoragePartUploadError


def encode_storage_param(parameter: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(parameter).encode()).decode()


def decode_storage_param(encoded: str) -> dict[str, Any]:
    return json.loads(base64.b64decode(encoded).decode("utf-8"))


class StoreHostClient:
    def __init__(self, store_host: str, token: str, *, session=None) -> None:
        self.store_host = store_host.rstrip("/")
        self.token = token
        self.session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def init_multipart(self, object_key: str) -> str:
        response = self.session.post(
            f"{self.store_host}/api/upload/multipart/init",
            headers=self._headers(),
            json={"path": object_key},
            timeout=30,
        )
        if not getattr(response, "ok", False):
            raise StorageInitError("init", "multipart init failed", redacted_detail=getattr(response, "text", ""))
        data = response.json().get("data") or {}
        initial_key = str(data.get("initialKey") or "")
        if not initial_key:
            raise StorageInitError("init", "multipart init response missing initialKey")
        return initial_key

    def upload_part(
        self,
        *,
        object_key: str,
        initial_key: str,
        number: int,
        part_size: int,
        data: bytes,
    ) -> str:
        param = {
            "initialKey": initial_key,
            "number": number,
            "partSize": part_size,
            "objectKey": object_key,
        }
        headers = self._headers()
        headers["X-Storage-Param"] = encode_storage_param(param)
        response = self.session.post(
            f"{self.store_host}/api/upload/multipart/upload",
            headers=headers,
            data=data,
            timeout=300,
        )
        if not getattr(response, "ok", False):
            raise StoragePartUploadError("part_upload", "multipart part upload failed", retryable=True)
        data_block = response.json().get("data") or {}
        part_string = str(data_block.get("partString") or "")
        if not part_string:
            raise StoragePartUploadError("part_upload", "multipart part upload response missing partString", retryable=True)
        return part_string

    def complete_multipart(self, *, object_key: str, initial_key: str, part_strings: list[str]) -> None:
        response = self.session.post(
            f"{self.store_host}/api/upload/multipart/complete",
            headers=self._headers(),
            json={"path": object_key, "initialKey": initial_key, "partString": part_strings},
            timeout=300,
        )
        if not getattr(response, "ok", False):
            raise StorageCompleteError("complete", "multipart complete failed")
```

- [ ] **Step 4: Implement multipart uploader**

Create `multipart.py`:

```python
from __future__ import annotations

import math
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .manifest import ManifestStore


def _read_part(file_path: Path, offset: int, size: int) -> bytes:
    with open(file_path, "rb") as fh:
        fh.seek(offset)
        return fh.read(size)


def _part_specs(file_size: int, part_size: int) -> list[dict[str, int]]:
    count = max(math.ceil(file_size / part_size), 1)
    specs: list[dict[str, int]] = []
    for index in range(count):
        offset = index * part_size
        size = min(part_size, max(file_size - offset, 0))
        specs.append({"number": index + 1, "offset": offset, "size": size})
    return specs


def _completed_from_manifest(
    *,
    manifest_store: ManifestStore,
    transfer_id: str,
    object_key: str,
    file_size: int,
    file_mtime_ns: int,
    part_size: int,
    token: str,
) -> tuple[str | None, dict[int, str]]:
    try:
        manifest = manifest_store.read(transfer_id)
    except FileNotFoundError:
        return None, {}
    if manifest.get("object_key") != object_key:
        return None, {}
    if int(manifest.get("file_size") or -1) != file_size:
        return None, {}
    if int(manifest.get("file_mtime_ns") or -1) != file_mtime_ns:
        return None, {}
    if int(manifest.get("part_size") or -1) != part_size:
        return None, {}
    if str(manifest.get("token") or "") != token:
        return None, {}
    initial_key = str(manifest.get("initial_key") or "")
    if not initial_key:
        return None, {}
    completed: dict[int, str] = {}
    for part in manifest.get("parts") or []:
        if not isinstance(part, dict):
            continue
        if part.get("status") != "completed":
            continue
        part_string = str(part.get("part_string") or "")
        if not part_string:
            continue
        completed[int(part["number"])] = part_string
    return initial_key, completed


def _write_manifest(
    *,
    manifest_store: ManifestStore,
    transfer_id: str,
    object_key: str,
    initial_key: str,
    token: str,
    part_size: int,
    file_size: int,
    file_mtime_ns: int,
    parts: list[dict[str, int]],
    completed: dict[int, str],
) -> None:
    manifest_store.write(
        transfer_id,
        {
            "schema_version": "v1",
            "transfer_id": transfer_id,
            "object_key": object_key,
            "initial_key": initial_key,
            "token": token,
            "part_size": part_size,
            "file_size": file_size,
            "file_mtime_ns": file_mtime_ns,
            "parts": [
                {
                    "number": part["number"],
                    "offset": part["offset"],
                    "size": part["size"],
                    "part_string": completed.get(part["number"]),
                    "status": "completed" if part["number"] in completed else "pending",
                }
                for part in parts
            ],
        },
    )


def upload_file_multipart(
    *,
    client,
    file_path: str | Path,
    object_key: str,
    manifest_store: ManifestStore,
    transfer_id: str,
    part_size: int = 64 * 1024 * 1024,
    concurrency: int = 4,
    part_retries: int = 3,
) -> dict[str, Any]:
    path = Path(file_path)
    stat_result = path.stat()
    file_size = stat_result.st_size
    file_mtime_ns = stat_result.st_mtime_ns
    parts = _part_specs(file_size, part_size)
    token = str(getattr(client, "token", ""))
    initial_key, completed = _completed_from_manifest(
        manifest_store=manifest_store,
        transfer_id=transfer_id,
        object_key=object_key,
        file_size=file_size,
        file_mtime_ns=file_mtime_ns,
        part_size=part_size,
        token=token,
    )
    resume_used = bool(initial_key and completed)
    if not initial_key:
        initial_key = client.init_multipart(object_key)
    _write_manifest(
        manifest_store=manifest_store,
        transfer_id=transfer_id,
        object_key=object_key,
        initial_key=initial_key,
        token=token,
        part_size=part_size,
        file_size=file_size,
        file_mtime_ns=file_mtime_ns,
        parts=parts,
        completed=completed,
    )

    def upload_one(spec: dict[str, int]) -> tuple[int, str]:
        last_error: BaseException | None = None
        for attempt in range(1, part_retries + 1):
            try:
                data = _read_part(path, spec["offset"], spec["size"])
                return spec["number"], client.upload_part(
                    object_key=object_key,
                    initial_key=initial_key,
                    number=spec["number"],
                    part_size=spec["size"],
                    data=data,
                )
            except Exception as exc:
                last_error = exc
                if attempt < part_retries:
                    time.sleep(min(2 ** (attempt - 1), 30) + random.uniform(0, 0.25))
        raise RuntimeError(f"part {spec['number']} failed") from last_error

    pending_parts = [part for part in parts if part["number"] not in completed]
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(upload_one, spec) for spec in pending_parts]
        for future in as_completed(futures):
            number, part_string = future.result()
            completed[number] = part_string
            _write_manifest(
                manifest_store=manifest_store,
                transfer_id=transfer_id,
                object_key=object_key,
                initial_key=initial_key,
                token=token,
                part_size=part_size,
                file_size=file_size,
                file_mtime_ns=file_mtime_ns,
                parts=parts,
                completed=completed,
            )
    part_strings = [completed[number] for number in sorted(completed)]
    client.complete_multipart(
        object_key=object_key,
        initial_key=initial_key,
        part_strings=part_strings,
    )
    return {
        "ok": True,
        "object_key": object_key,
        "parts_total": len(parts),
        "bytes_total": file_size,
        "resume_used": resume_used,
    }
```

- [ ] **Step 5: Verify client/multipart tests**

Run:

```bash
uv run pytest tests/matmaster_bohrium_transfer/test_client_multipart.py -q
```

Expected: tests pass.

- [ ] **Step 6: Add optional real StoreHost contract test**

Create `tests/matmaster_bohrium_transfer/test_storehost_contract.py`:

```python
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pytest

from matmaster_bohrium_transfer.client import StoreHostClient
from matmaster_bohrium_transfer.download import download_file
from matmaster_bohrium_transfer.manifest import ManifestStore
from matmaster_bohrium_transfer.multipart import upload_file_multipart


def test_real_storehost_multipart_upload_complete_and_download(tmp_path: Path) -> None:
    store_host = os.environ.get("BOHRIUM_STOREHOST_CONTRACT_HOST", "").rstrip("/")
    token = os.environ.get("BOHRIUM_STOREHOST_CONTRACT_TOKEN", "").strip()
    prefix = os.environ.get("BOHRIUM_STOREHOST_CONTRACT_PREFIX", "").strip().strip("/")
    if not store_host or not token or not prefix:
        pytest.skip(
            "set BOHRIUM_STOREHOST_CONTRACT_HOST, "
            "BOHRIUM_STOREHOST_CONTRACT_TOKEN, and "
            "BOHRIUM_STOREHOST_CONTRACT_PREFIX to run StoreHost contract test"
        )

    source = tmp_path / "contract.bin"
    payload = b"matmaster-storehost-contract\n" * 1024
    source.write_bytes(payload)
    object_key = f"{prefix}/contract-{uuid4().hex}.bin"
    client = StoreHostClient(store_host, token)

    upload_file_multipart(
        client=client,
        file_path=source,
        object_key=object_key,
        manifest_store=ManifestStore(tmp_path / "manifest"),
        transfer_id="contract",
        part_size=4096,
        concurrency=2,
        part_retries=2,
    )

    encoded = quote(object_key, safe="/")
    download_url = (
        f"{store_host}/api/download/{encoded}?token={token}"
        "&Response-Content-Type=application/octet-stream"
    )
    downloaded = tmp_path / "downloaded.bin"
    download_file(download_url, downloaded, part_size=4096, concurrency=2)

    assert downloaded.read_bytes() == payload
```

- [ ] **Step 7: Verify client/multipart and skipped contract tests**

Run:

```bash
uv run pytest \
  tests/matmaster_bohrium_transfer/test_client_multipart.py \
  tests/matmaster_bohrium_transfer/test_storehost_contract.py \
  -q
```

Expected without contract env vars: client/multipart tests pass and contract test is skipped. Expected with contract env vars: contract test uploads and downloads a tiny object successfully.

- [ ] **Step 8: Commit client/multipart**

Run:

```bash
git add packages/bohrium-transfer/src/matmaster_bohrium_transfer/client.py packages/bohrium-transfer/src/matmaster_bohrium_transfer/multipart.py tests/matmaster_bohrium_transfer/test_client_multipart.py tests/matmaster_bohrium_transfer/test_storehost_contract.py
git commit -m "feat: add sdk-free bohrium multipart upload"
```

### Task 9: Integrate SDK-Free Upload With Existing Bohrium Submit

**Files:**
- Modify: `matmaster/bohrium/upload.py`
- Modify: `matmaster/tools/builtin/bohrium_tool/transfers.py`
- Modify: `packages/bohrium-transfer/src/matmaster_bohrium_transfer/remote.py`
- Test: `tests/matmaster/bohrium/test_upload.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py`
- Test: `tests/matmaster_bohrium_transfer/test_remote_cli.py`

- [ ] **Step 1: Add failing upload tests that reject Tiefblue import**

Modify `tests/matmaster/bohrium/test_upload.py` by replacing the missing SDK test with:

```python
def test_upload_input_archive_does_not_import_bohrium_sdk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("bohrium"):
            raise AssertionError("bohrium-sdk must not be imported")
        return original_import(name, globals, locals, fromlist, level)

    zip_path = tmp_path / "input.zip"
    zip_path.write_bytes(b"zip-bytes")
    calls: list[dict] = []

    def fake_upload_file(*, create_data, zip_path, manifest_root=None):
        calls.append({"create_data": create_data, "zip_path": zip_path})
        from matmaster.bohrium.upload import UploadedArchive

        return UploadedArchive(
            oss_key="sandbox/jobs/run-2/input.zip",
            download_url="https://store.example.com/api/download/sandbox/jobs/run-2/input.zip?token=token-456",
        )

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr("matmaster.bohrium.upload._upload_input_archive_sdk_free", fake_upload_file)

    from matmaster.bohrium.upload import upload_input_archive

    uploaded = upload_input_archive(
        create_data={
            "storePath": "sandbox/jobs/run-2/",
            "storeHost": "https://store.example.com",
            "token": "token-456",
        },
        zip_path=zip_path,
    )

    assert uploaded.oss_key == "sandbox/jobs/run-2/input.zip"
    assert calls[0]["zip_path"] == zip_path
```

- [ ] **Step 2: Run failing upload tests**

Run:

```bash
uv run pytest tests/matmaster/bohrium/test_upload.py -q
```

Expected before implementation: `_upload_input_archive_sdk_free` missing or current code imports `bohrium-sdk`.

- [ ] **Step 3: Implement SDK-free upload adapter**

Modify `matmaster/bohrium/upload.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from matmaster_bohrium_transfer.client import StoreHostClient
from matmaster_bohrium_transfer.manifest import ManifestStore
from matmaster_bohrium_transfer.multipart import upload_file_multipart


@dataclass(frozen=True)
class UploadedArchive:
    oss_key: str
    download_url: str


def _build_download_url(store_host: str, oss_key: str, token: str) -> str:
    encoded_key = quote(oss_key, safe="/")
    return (
        f"{store_host}/api/download/{encoded_key}?token={token}"
        "&Response-Content-Type=application/octet-stream"
    )


def _upload_input_archive_sdk_free(
    *,
    create_data: dict,
    zip_path: Path,
    manifest_root: Path | None = None,
) -> UploadedArchive:
    store_path = str(create_data["storePath"]).strip()
    if not store_path.endswith("/"):
        store_path += "/"
    store_host = str(create_data["storeHost"]).rstrip("/")
    token = str(create_data["token"]).strip()
    oss_key = f"{store_path}input.zip"
    root = manifest_root or (Path(zip_path).parent / ".matmaster" / "transfers")
    client = StoreHostClient(store_host, token)
    upload_file_multipart(
        client=client,
        file_path=zip_path,
        object_key=oss_key,
        manifest_store=ManifestStore(root),
        transfer_id=f"submit-input-{abs(hash(oss_key))}",
    )
    return UploadedArchive(
        oss_key=oss_key,
        download_url=_build_download_url(store_host, oss_key, token),
    )


def upload_input_archive(*, create_data: dict, zip_path: Path) -> UploadedArchive:
    return _upload_input_archive_sdk_free(create_data=create_data, zip_path=zip_path)
```

- [ ] **Step 4: Implement remote CLI upload command**

Extend `packages/bohrium-transfer/src/matmaster_bohrium_transfer/remote.py`:

```python
from pathlib import Path

from .archive import create_zip_store
from .client import StoreHostClient
from .manifest import ManifestStore
from .multipart import upload_file_multipart
from .security import redact_secrets
from .version import PROTOCOL_VERSION, SCHEMA_VERSION


def _load_payload(path: str) -> dict[str, object]:
    payload_path = Path(path)
    raw = payload_path.read_text(encoding="utf-8")
    payload_path.unlink(missing_ok=True)
    payload = json.loads(raw)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("payload schema_version mismatch")
    return payload


def _upload_submit(payload: dict[str, object]) -> dict[str, object]:
    input_dir = Path(str(payload["input_dir"]))
    store_host = str(payload["store_host"]).rstrip("/")
    store_path = str(payload["store_path"]).strip().rstrip("/") + "/"
    token = str(payload["token"])
    object_name = str(payload.get("object_name") or "input.zip")
    transfer_root = Path(str(payload.get("transfer_root") or "/share/.matmaster/transfers"))
    archive_path = transfer_root / "archives" / object_name
    archive = create_zip_store(input_dir, archive_path)
    object_key = f"{store_path}{object_name}"
    client = StoreHostClient(store_host, token)
    summary = upload_file_multipart(
        client=client,
        file_path=archive.archive_path,
        object_key=object_key,
        manifest_store=ManifestStore(transfer_root),
        transfer_id=str(payload.get("transfer_id") or f"submit-input-{abs(hash(object_key))}"),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "ok": True,
        "oss_key": object_key,
        "bytes_total": summary["bytes_total"],
        "parts_total": summary["parts_total"],
    }
```

Add subparser:

```python
upload_parser = subparsers.add_parser("upload-submit")
upload_parser.add_argument("--payload-file", required=True)
```

Add handler:

```python
if args.command == "upload-submit":
    try:
        _print_json(_upload_submit(_load_payload(args.payload_file)))
        return 0
    except Exception as exc:
        _print_json(
            {
                "schema_version": SCHEMA_VERSION,
                "protocol_version": PROTOCOL_VERSION,
                "ok": False,
                "stage": "upload_submit",
                "retryable": False,
                "safe_message": redact_secrets(exc),
                "resume_available": False,
            }
        )
        return 1
```

- [ ] **Step 5: Verify upload integration**

Run:

```bash
uv run pytest \
  tests/matmaster/bohrium/test_upload.py \
  tests/matmaster/tools/builtin/test_bohrium_tool.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py \
  tests/matmaster_bohrium_transfer/test_remote_cli.py \
  -q
```

Expected: tests pass; tests no longer monkeypatch `_load_tiefblue_client` for the primary path.

- [ ] **Step 6: Commit SDK-free upload integration**

Run:

```bash
git add matmaster/bohrium/upload.py matmaster/tools/builtin/bohrium_tool/transfers.py packages/bohrium-transfer/src/matmaster_bohrium_transfer/remote.py tests/matmaster/bohrium/test_upload.py tests/matmaster/tools/builtin/test_bohrium_tool.py tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py tests/matmaster_bohrium_transfer/test_remote_cli.py
git commit -m "feat: use sdk-free bohrium submit upload"
```

## Phase D: SDK-Free Download And Dependency Removal

### Task 10: Add Concurrent Range Download And Safe ZIP Helpers

**Files:**
- Create: `packages/bohrium-transfer/src/matmaster_bohrium_transfer/download.py`
- Test: `tests/matmaster_bohrium_transfer/test_download.py`

- [ ] **Step 1: Write failing download tests**

Create `tests/matmaster_bohrium_transfer/test_download.py`:

```python
from __future__ import annotations

import zipfile
from pathlib import Path

from matmaster_bohrium_transfer.download import (
    choose_sandbox_zip_object,
    download_file,
    extract_zip_safe,
    probe_range,
)


class FakeResponse:
    def __init__(self, content: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.content = content
        self.headers = headers or {}
        self.status_code = 200
        self.ok = True

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 65536):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]


class FakeRangeSession:
    def __init__(self) -> None:
        self.content = b"0123456789"
        self.get_headers: list[dict[str, str]] = []

    def head(self, url, *, allow_redirects=True, timeout=30):
        return FakeResponse(
            headers={
                "Content-Length": str(len(self.content)),
                "Accept-Ranges": "bytes",
            }
        )

    def get(self, url, *, headers=None, timeout=300, stream=True):
        request_headers = headers or {}
        self.get_headers.append(request_headers)
        range_header = request_headers.get("Range")
        if not range_header:
            return FakeResponse(self.content)
        start_text, end_text = range_header.removeprefix("bytes=").split("-", 1)
        start = int(start_text)
        end = int(end_text)
        return FakeResponse(
            self.content[start : end + 1],
            headers={"Content-Length": str(end - start + 1)},
        )


def test_choose_sandbox_zip_prefers_job_id_and_skips_task_zip() -> None:
    objects = [
        {"path": "prefix/task.zip", "isDir": False},
        {"path": "prefix/other.zip", "isDir": False},
        {"path": "prefix/job-1.zip", "isDir": False},
    ]

    assert choose_sandbox_zip_object("job-1", objects) == "prefix/job-1.zip"


def test_choose_sandbox_zip_falls_back_to_non_task_zip() -> None:
    objects = [
        {"path": "prefix/task.zip", "isDir": False},
        {"path": "prefix/other.zip", "isDir": False},
    ]

    assert choose_sandbox_zip_object("job-1", objects) == "prefix/other.zip"


def test_extract_zip_safe_rejects_zip_slip(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.txt", "bad")

    try:
        extract_zip_safe(archive, tmp_path / "out")
    except Exception as exc:
        assert "unsafe zip member" in str(exc)
    else:
        raise AssertionError("zip slip was accepted")


def test_probe_range_handles_missing_content_length() -> None:
    class Response:
        status_code = 200
        headers = {}

    capability = probe_range(Response())

    assert capability.resume_supported is False
    assert capability.bytes_total is None


def test_download_file_uses_concurrent_range_requests(tmp_path: Path) -> None:
    session = FakeRangeSession()
    dest = tmp_path / "out.zip"

    summary = download_file(
        "https://store.example/api/download/out.zip?token=t",
        dest,
        session=session,
        part_size=4,
        concurrency=3,
    )

    assert dest.read_bytes() == b"0123456789"
    ranges = sorted(
        headers.get("Range")
        for headers in session.get_headers
        if headers.get("Range")
    )
    assert ranges == ["bytes=0-3", "bytes=4-7", "bytes=8-9"]
    assert summary.bytes_total == 10
    assert summary.resume_supported is True
```

- [ ] **Step 2: Run failing download tests**

Run:

```bash
uv run pytest tests/matmaster_bohrium_transfer/test_download.py -q
```

Expected before implementation: import failure for `download.py`.

- [ ] **Step 3: Implement download helpers**

Create `download.py`:

```python
from __future__ import annotations

import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .errors import ExtractError


@dataclass(frozen=True)
class RangeCapability:
    resume_supported: bool
    bytes_total: int | None
    reason: str


@dataclass(frozen=True)
class DownloadSummary:
    path: Path
    bytes_total: int | None
    bytes_done: int
    resume_supported: bool


def probe_range(response) -> RangeCapability:
    raw_length = response.headers.get("Content-Length") if hasattr(response, "headers") else None
    if not raw_length:
        return RangeCapability(False, None, "missing_content_length")
    try:
        total = int(raw_length)
    except ValueError:
        return RangeCapability(False, None, "invalid_content_length")
    accept_ranges = str(response.headers.get("Accept-Ranges", "")).lower()
    return RangeCapability("bytes" in accept_ranges, total, "ok" if "bytes" in accept_ranges else "range_not_advertised")


def _range_specs(total: int, part_size: int) -> list[tuple[int, int, int]]:
    specs: list[tuple[int, int, int]] = []
    start = 0
    index = 0
    while start < total:
        end = min(start + part_size - 1, total - 1)
        specs.append((index, start, end))
        start = end + 1
        index += 1
    return specs


def _download_stream(session, url: str, dest: Path, *, timeout: int) -> DownloadSummary:
    response = session.get(url, timeout=timeout, stream=True)
    response.raise_for_status()
    total_header = response.headers.get("Content-Length") if hasattr(response, "headers") else None
    bytes_total = int(total_header) if total_header and total_header.isdigit() else None
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    bytes_done = 0
    with open(tmp, "wb") as fh:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            fh.write(chunk)
            bytes_done += len(chunk)
    tmp.replace(dest)
    return DownloadSummary(
        path=dest,
        bytes_total=bytes_total,
        bytes_done=bytes_done,
        resume_supported=False,
    )


def _download_one_range(
    *,
    session,
    url: str,
    part_path: Path,
    start: int,
    end: int,
    timeout: int,
) -> int:
    expected = end - start + 1
    if part_path.exists() and part_path.stat().st_size == expected:
        return expected
    tmp = part_path.with_suffix(part_path.suffix + ".tmp")
    response = session.get(
        url,
        headers={"Range": f"bytes={start}-{end}"},
        timeout=timeout,
        stream=True,
    )
    response.raise_for_status()
    with open(tmp, "wb") as fh:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                fh.write(chunk)
    if tmp.stat().st_size != expected:
        raise IOError(
            f"range download size mismatch for bytes={start}-{end}: "
            f"expected={expected} got={tmp.stat().st_size}"
        )
    tmp.replace(part_path)
    return expected


def _download_ranges(
    session,
    url: str,
    dest: Path,
    *,
    bytes_total: int,
    part_size: int,
    concurrency: int,
    timeout: int,
) -> DownloadSummary:
    specs = _range_specs(bytes_total, part_size)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part_paths = {
        index: dest.with_suffix(dest.suffix + f".part.{index}")
        for index, _start, _end in specs
    }
    bytes_done = 0
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(
                _download_one_range,
                session=session,
                url=url,
                part_path=part_paths[index],
                start=start,
                end=end,
                timeout=timeout,
            )
            for index, start, end in specs
        ]
        for future in as_completed(futures):
            bytes_done += future.result()
    tmp = dest.with_suffix(dest.suffix + ".part")
    with open(tmp, "wb") as out:
        for index, _start, _end in specs:
            with open(part_paths[index], "rb") as part:
                shutil.copyfileobj(part, out)
    if tmp.stat().st_size != bytes_total:
        raise IOError(
            f"assembled download size mismatch: expected={bytes_total} got={tmp.stat().st_size}"
        )
    tmp.replace(dest)
    for part_path in part_paths.values():
        part_path.unlink(missing_ok=True)
    return DownloadSummary(
        path=dest,
        bytes_total=bytes_total,
        bytes_done=bytes_done,
        resume_supported=True,
    )


def download_file(
    url: str,
    dest: str | Path,
    *,
    session=None,
    part_size: int = 64 * 1024 * 1024,
    concurrency: int = 4,
    timeout: int = 300,
) -> DownloadSummary:
    http = session or requests.Session()
    target = Path(dest)
    try:
        head = http.head(url, allow_redirects=True, timeout=30)
        capability = probe_range(head)
    except Exception:
        capability = RangeCapability(False, None, "head_failed")
    if (
        capability.resume_supported
        and capability.bytes_total is not None
        and concurrency > 1
    ):
        return _download_ranges(
            http,
            url,
            target,
            bytes_total=capability.bytes_total,
            part_size=part_size,
            concurrency=concurrency,
            timeout=timeout,
        )
    return _download_stream(http, url, target, timeout=timeout)


def choose_sandbox_zip_object(job_id: int | str, objects: list[dict[str, Any]]) -> str | None:
    preferred_name = f"{job_id}.zip"
    for obj in objects:
        object_path = str(obj.get("path") or obj.get("key") or "").strip()
        if object_path and Path(object_path).name == preferred_name:
            return object_path
    for obj in objects:
        object_path = str(obj.get("path") or obj.get("key") or "").strip()
        if object_path.endswith(".zip") and Path(object_path).name != "task.zip":
            return object_path
    for obj in objects:
        object_path = str(obj.get("path") or obj.get("key") or "").strip()
        if object_path.endswith(".zip"):
            return object_path
    return None


def extract_zip_safe(archive: str | Path, extract_dir: str | Path) -> list[str]:
    archive_path = Path(archive)
    root = Path(extract_dir)
    root.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    with zipfile.ZipFile(archive_path, "r") as zf:
        for member in zf.infolist():
            if member.filename.endswith("/"):
                continue
            target = root / member.filename
            resolved_root = root.resolve()
            resolved_target = target.resolve()
            if resolved_root not in (resolved_target, *resolved_target.parents):
                raise ExtractError("extract", f"unsafe zip member path: {member.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            files.append(member.filename)
    return files
```

- [ ] **Step 4: Verify download helper tests**

Run:

```bash
uv run pytest tests/matmaster_bohrium_transfer/test_download.py -q
```

Expected: tests pass.

- [ ] **Step 5: Commit download helpers**

Run:

```bash
git add packages/bohrium-transfer/src/matmaster_bohrium_transfer/download.py tests/matmaster_bohrium_transfer/test_download.py
git commit -m "feat: add bohrium transfer download helpers"
```

### Task 11: Integrate Download Package And Preserve Sandbox Semantics

**Files:**
- Modify: `matmaster/bohrium/artifacts.py`
- Modify: `packages/bohrium-transfer/src/matmaster_bohrium_transfer/download.py`
- Modify: `packages/bohrium-transfer/src/matmaster_bohrium_transfer/remote.py`
- Test: `tests/matmaster/bohrium/test_artifacts.py`
- Test: `tests/matmaster/tools/builtin/test_bohrium_tool_download.py`

- [ ] **Step 1: Add regression test for sandbox fallback chain**

Ensure `tests/matmaster/bohrium/test_artifacts.py` has these imports:

```python
import io
import zipfile
```

Append to `tests/matmaster/bohrium/test_artifacts.py`:

```python
def test_sandbox_download_preserves_zip_and_object_fallback_order(tmp_path, monkeypatch):
    from matmaster.bohrium.artifacts import download_job_artifacts
    from matmaster.bohrium.types import BohriumContext, BohriumCredentials

    ctx = BohriumContext(
        credentials=BohriumCredentials(
            access_key="ak",
            project_id=42,
            user_id=None,
            user_no="",
            base_url="https://openapi.test.dp.tech",
        ),
        credential_source="env",
        sandbox=True,
    )
    calls: list[str] = []

    class Response:
        def __init__(self, content=b"", json_data=None):
            self.content = content
            self._json = json_data or {}
            self.ok = True
            self.status_code = 200
            self.headers = {"Content-Length": str(len(content))}

        def raise_for_status(self):
            return None

        def json(self):
            return self._json

        def iter_content(self, chunk_size=65536):
            yield self.content

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("log", "done")

    def fake_post(url, **kwargs):
        calls.append(url)
        return Response(
            json_data={
                "code": 0,
                "data": {
                    "objects": [
                        {"path": "prefix/task.zip", "isDir": False},
                        {"path": "prefix/job-55.zip", "isDir": False},
                    ],
                    "hasNext": False,
                },
            }
        )

    def fake_get(url, **kwargs):
        calls.append(url)
        return Response(content=buffer.getvalue())

    monkeypatch.setattr("matmaster.bohrium.artifacts.requests.post", fake_post)
    monkeypatch.setattr("matmaster.bohrium.artifacts.requests.get", fake_get)

    files, log_tail = download_job_artifacts(
        job_id="job-55",
        detail_data={"resultUrl": "https://store.example/api/download/prefix/job-55.zip?token=t"},
        result_dir=tmp_path / "results",
        ctx=ctx,
    )

    assert "log" in files
    assert "done" in log_tail
    assert any("iterate" in call for call in calls)
    assert any("job-55.zip" in call for call in calls)
```

- [ ] **Step 2: Run existing download tests before refactor**

Run:

```bash
uv run pytest tests/matmaster/bohrium/test_artifacts.py tests/matmaster/tools/builtin/test_bohrium_tool_download.py -q
```

Expected before integration: tests pass against old implementation. This establishes the behavior to preserve.

- [ ] **Step 3: Refactor `artifacts.py` to call transfer helpers without changing public API**

In `matmaster/bohrium/artifacts.py`, import helpers:

```python
from matmaster_bohrium_transfer.download import (
    choose_sandbox_zip_object,
    extract_zip_safe,
)
```

Replace `_sandbox_choose_zip_object()` body with:

```python
def _sandbox_choose_zip_object(job_id: int | str, objects: list[dict]) -> str | None:
    return choose_sandbox_zip_object(job_id, objects)
```

Replace `_extract_zip()` body with:

```python
def _extract_zip(zip_path: Path, extract_dir: Path) -> list[str]:
    try:
        return extract_zip_safe(zip_path, extract_dir)
    except zipfile.BadZipFile:
        return [f"(bad zip: {zip_path.name})"]
```

Keep the current `_sandbox_download_results()` fallback ordering intact.

- [ ] **Step 4: Add package download orchestration for standard and sandbox jobs**

Append to `packages/bohrium-transfer/src/matmaster_bohrium_transfer/download.py`:

```python
import os
import time
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import uuid4

from .version import PROTOCOL_VERSION, SCHEMA_VERSION

_SANDBOX_OBJECT_DOWNLOAD_LIMIT = 128


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"missing required payload field: {key}")
    return value


def read_log(result_dir: str | Path, *, max_chars: int = 4000) -> str:
    root = Path(result_dir)
    for name in ("log", "STDOUTERR"):
        file_path = root / name
        if file_path.exists():
            size = file_path.stat().st_size
            with open(file_path, "rb") as fh:
                if size > max_chars * 4:
                    fh.seek(-(max_chars * 4), os.SEEK_END)
                raw = fh.read()
            return raw.decode("utf-8", errors="replace")[-max_chars:]
    return "(no log file found in result directory)"


def publish_result_dir(staging: str | Path, result_dir: str | Path) -> None:
    staging_path = Path(staging)
    result_path = Path(result_dir)
    lockdir = result_path.with_name(result_path.name + ".lock")
    backup = result_path.with_name(result_path.name + f".bak.{uuid4().hex}")
    lock_acquired = False
    try:
        lockdir.mkdir()
        lock_acquired = True
        if result_path.exists():
            result_path.rename(backup)
        staging_path.replace(result_path)
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        if backup.exists() and not result_path.exists():
            backup.rename(result_path)
        raise
    finally:
        if lock_acquired:
            shutil.rmtree(lockdir, ignore_errors=True)


def _parse_sandbox_result_url(result_url: str) -> tuple[str, str, str, str]:
    parsed = urlparse(result_url)
    host = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    token = parse_qs(parsed.query).get("token", [""])[0].strip()
    object_path = unquote(parsed.path.removeprefix("/api/download/")).strip("/")
    if not host or not token or not object_path:
        raise ValueError("invalid sandbox resultUrl")
    prefix = object_path.rsplit("/", 1)[0] + "/" if "/" in object_path else ""
    return host, token, object_path, prefix


def _iterate_objects(host: str, token: str, prefix: str, *, session=None) -> list[dict[str, Any]]:
    http = session or requests.Session()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    objects: list[dict[str, Any]] = []
    next_token = ""
    while True:
        payload: dict[str, Any] = {"prefix": prefix}
        if next_token:
            payload["nextToken"] = next_token
        response = http.post(
            f"{host.rstrip('/')}/api/iterate",
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        body = response.json() or {}
        if body.get("code") not in (None, 0):
            raise ValueError(f"sandbox iterate failed: {body}")
        data = body.get("data") or {}
        objects.extend(data.get("objects") or [])
        if not data.get("hasNext"):
            break
        next_token = str(data.get("nextToken") or "").strip()
        if not next_token:
            break
    return objects


def _download_object_url(host: str, token: str, object_path: str) -> str:
    encoded_path = quote(object_path, safe="/")
    return (
        f"{host.rstrip('/')}/api/download/{encoded_path}?token={token}"
        "&Response-Content-Type=application/octet-stream"
    )


def _download_object(
    host: str,
    token: str,
    object_path: str,
    dest_path: Path,
    *,
    session=None,
) -> DownloadSummary:
    return download_file(
        _download_object_url(host, token, object_path),
        dest_path,
        session=session,
    )


def _merge_log_file(files: list[str], log_downloaded: bool) -> list[str]:
    if not log_downloaded or "log" in files:
        return files
    return ["log", *files]


def _sandbox_relative_object_path(object_path: str, root_prefix: str) -> str:
    path = object_path.strip()
    if root_prefix and path.startswith(root_prefix):
        path = path[len(root_prefix) :]
    return path.lstrip("/")


def _download_sandbox_log(
    *,
    payload: dict[str, Any],
    staging: Path,
    root_host: str,
    root_token: str,
    objects: list[dict[str, Any]],
    session=None,
) -> tuple[bool, int]:
    log_file = payload.get("sandbox_log_file")
    if isinstance(log_file, dict):
        host = str(log_file.get("host") or "").strip()
        path = str(log_file.get("path") or "").strip()
        token = str(log_file.get("token") or "").strip()
        if host and path and token:
            try:
                summary = _download_object(host, token, path, staging / "log", session=session)
                return True, summary.bytes_done
            except Exception:
                pass
    if root_host and root_token:
        for obj in objects:
            object_path = str(obj.get("path") or obj.get("key") or "").strip()
            if object_path and Path(object_path).name == "log":
                summary = _download_object(
                    root_host,
                    root_token,
                    object_path,
                    staging / "log",
                    session=session,
                )
                return True, summary.bytes_done
    return False, 0


def _download_sandbox_results(
    *,
    payload: dict[str, Any],
    staging: Path,
    session=None,
) -> tuple[list[str], str, int]:
    job_id = _required_str(payload, "job_id")
    detail_data = payload.get("detail_data") or {}
    if not isinstance(detail_data, dict):
        raise ValueError("detail_data must be a JSON object")
    result_url = str(detail_data.get("resultUrl") or detail_data.get("result") or "")
    objects: list[dict[str, Any]] = []
    root_host = ""
    root_token = ""
    root_prefix = ""
    bytes_transferred = 0

    if result_url:
        try:
            root_host, root_token, _object_path, root_prefix = _parse_sandbox_result_url(result_url)
            objects = _iterate_objects(root_host, root_token, root_prefix, session=session)
        except Exception:
            objects = []

    log_downloaded, log_bytes = _download_sandbox_log(
        payload=payload,
        staging=staging,
        root_host=root_host,
        root_token=root_token,
        objects=objects,
        session=session,
    )
    bytes_transferred += log_bytes

    zip_key = choose_sandbox_zip_object(job_id, objects)
    if zip_key and root_host and root_token:
        try:
            zip_path = staging / Path(zip_key).name
            summary = _download_object(root_host, root_token, zip_key, zip_path, session=session)
            bytes_transferred += summary.bytes_done
            files = extract_zip_safe(zip_path, staging)
            return _merge_log_file(files, log_downloaded), read_log(staging), bytes_transferred
        except Exception:
            pass

    if result_url:
        try:
            zip_path = staging / "out.zip"
            summary = download_file(result_url, zip_path, session=session)
            bytes_transferred += summary.bytes_done
            files = extract_zip_safe(zip_path, staging)
            return _merge_log_file(files, log_downloaded), read_log(staging), bytes_transferred
        except Exception:
            pass

    if objects and root_host and root_token:
        downloaded: list[str] = []
        count = 0
        for obj in objects:
            if count >= _SANDBOX_OBJECT_DOWNLOAD_LIMIT:
                break
            if not isinstance(obj, dict) or obj.get("isDir"):
                continue
            object_path = str(obj.get("path") or obj.get("key") or "").strip()
            if not object_path:
                continue
            relative_path = _sandbox_relative_object_path(object_path, root_prefix)
            if not relative_path or relative_path.endswith(".zip"):
                continue
            summary = _download_object(
                root_host,
                root_token,
                object_path,
                staging / relative_path,
                session=session,
            )
            bytes_transferred += summary.bytes_done
            downloaded.append(relative_path)
            count += 1
        downloaded = _merge_log_file(downloaded, log_downloaded)
        if downloaded:
            return downloaded, read_log(staging), bytes_transferred

    if log_downloaded:
        return ["log"], read_log(staging), bytes_transferred
    if result_url:
        return [], "(sandbox resultUrl download failed)", bytes_transferred
    return [], "(no resultUrl in job detail)", bytes_transferred


def _download_standard_results(
    *,
    detail_data: dict[str, Any],
    staging: Path,
    session=None,
) -> tuple[list[str], str, int]:
    result_url = str(detail_data.get("resultUrl") or detail_data.get("result") or "")
    if not result_url:
        out_files = (detail_data.get("jobFiles") or {}).get("outFiles") or []
        if out_files and isinstance(out_files[0], dict):
            result_url = str(out_files[0].get("url") or "")
    if not result_url:
        return [], "(no resultUrl in job detail)", 0
    zip_path = staging / "out.zip"
    summary = download_file(result_url, zip_path, session=session)
    files = extract_zip_safe(zip_path, staging)
    return files, read_log(staging), summary.bytes_done


def run_download_results_payload(
    payload: dict[str, Any],
    *,
    session=None,
) -> dict[str, Any]:
    started = time.monotonic()
    result_dir = Path(_required_str(payload, "result_dir"))
    staging = result_dir.with_name(result_dir.name + f".tmp.{uuid4().hex}")
    detail_data = payload.get("detail_data") or {}
    if not isinstance(detail_data, dict):
        raise ValueError("detail_data must be a JSON object")
    try:
        staging.mkdir(parents=True, exist_ok=False)
        if bool(payload.get("sandbox")):
            files, log_tail, bytes_transferred = _download_sandbox_results(
                payload=payload,
                staging=staging,
                session=session,
            )
        else:
            files, log_tail, bytes_transferred = _download_standard_results(
                detail_data=detail_data,
                staging=staging,
                session=session,
            )
        publish_result_dir(staging, result_dir)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    elapsed = max(time.monotonic() - started, 0.001)
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "ok": True,
        "result_dir": str(result_dir),
        "files": files,
        "log_tail": log_tail,
        "bytes_transferred": bytes_transferred,
        "transfer_rate_mbps": round(bytes_transferred * 8 / elapsed / 1_000_000, 3),
    }
```

- [ ] **Step 5: Add remote CLI download-results implementation**

Extend `remote.py` with:

```python
from .download import run_download_results_payload


def _download_results(payload: dict[str, object]) -> dict[str, object]:
    return run_download_results_payload(payload)
```

Add subparser and handler:

```python
download_parser = subparsers.add_parser("download-results")
download_parser.add_argument("--payload-file", required=True)
```

```python
if args.command == "download-results":
    try:
        _print_json(_download_results(_load_payload(args.payload_file)))
        return 0
    except Exception as exc:
        _print_json(
            {
                "schema_version": SCHEMA_VERSION,
                "protocol_version": PROTOCOL_VERSION,
                "ok": False,
                "stage": "download_results",
                "retryable": False,
                "safe_message": redact_secrets(exc),
                "resume_available": False,
            }
        )
        return 1
```

- [ ] **Step 6: Verify download integration**

Run:

```bash
uv run pytest \
  tests/matmaster/bohrium/test_artifacts.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_download.py \
  tests/matmaster_bohrium_transfer/test_download.py \
  tests/matmaster_bohrium_transfer/test_remote_cli.py \
  -q
```

Expected: tests pass; sandbox fallback behavior remains unchanged.

- [ ] **Step 7: Commit download package integration**

Run:

```bash
git add matmaster/bohrium/artifacts.py packages/bohrium-transfer/src/matmaster_bohrium_transfer/download.py packages/bohrium-transfer/src/matmaster_bohrium_transfer/remote.py tests/matmaster/bohrium/test_artifacts.py tests/matmaster/tools/builtin/test_bohrium_tool_download.py tests/matmaster_bohrium_transfer/test_remote_cli.py
git commit -m "feat: preserve bohrium downloads through transfer package"
```

### Task 12: Remove SDK Dependency And Update Project Contract

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `Dockerfile.remote`
- Modify: `AGENTS.md`
- Test: `tests/matmaster/test_import_audit.py`

- [ ] **Step 1: Add import audit test**

Append to `tests/matmaster/test_import_audit.py`:

```python
def test_runtime_code_does_not_import_bohrium_sdk() -> None:
    import ast
    from pathlib import Path

    roots = [
        Path("matmaster/bohrium"),
        Path("matmaster/tools/builtin/bohrium_tool"),
        Path("packages/bohrium-transfer/src/matmaster_bohrium_transfer"),
    ]
    offenders: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "bohrium" or alias.name.startswith("bohrium."):
                            offenders.append(f"{path}:{node.lineno}")
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module == "bohrium" or module.startswith("bohrium."):
                        offenders.append(f"{path}:{node.lineno}")
    assert offenders == []
```

- [ ] **Step 2: Run failing import audit**

Run:

```bash
uv run pytest tests/matmaster/test_import_audit.py::test_runtime_code_does_not_import_bohrium_sdk -q
```

Expected before cleanup: failures pointing at legacy helper or upload imports.

- [ ] **Step 3: Remove `bohrium-sdk` from dependencies**

Modify root `pyproject.toml`:

```toml
# remove this dependency from [project].dependencies
# "bohrium-sdk>=0.15.0",
```

Modify `Dockerfile.remote` pip install list:

```dockerfile
# remove this line
# "bohrium-sdk>=0.15.0" \
```

Run:

```bash
uv lock
```

Expected: `uv.lock` no longer includes `bohrium-sdk` unless pulled transitively by another package.

- [ ] **Step 4: Remove or quarantine legacy helper imports**

If `matmaster/bohrium/remote_transfer_helper.py` still imports `bohrium.resources.tiefblue`, either delete the file or reduce it to a compatibility message that imports no SDK:

```python
from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    del argv
    print(
        '{"schema_version":"v1","protocol_version":"1.0","ok":false,'
        '"stage":"legacy_helper_removed",'
        '"safe_message":"legacy remote_transfer_helper has been removed; '
        'install matmaster_bohrium_transfer on the remote image"}'
    )
    return 1
```

- [ ] **Step 5: Update AGENTS.md project contract**

Add or update the Bohrium transfer section in `AGENTS.md`:

```markdown
- **Bohrium 大文件传输**：builtin `Bohrium(action="submit"|"download")`
  的数据面走独立包 `matmaster_bohrium_transfer`，不再依赖
  `bohrium-sdk`。主项目只保留 path resolution、Bohrium 控制面 API、
  tool result 组装等控制面逻辑。
- **远端 transfer runtime**：Bohrium 远端镜像必须预装
  `matmaster_bohrium_transfer`，Worker 调用
  `python -m matmaster_bohrium_transfer.remote upload-submit --payload-file <payload>` 或
  `python -m matmaster_bohrium_transfer.remote download-results --payload-file <payload>`。
  远端版本不兼容时失败并提示更新镜像，不运行时复制 helper 源码。
- **传输状态**：同会话 resume 状态保存在 `.matmaster/transfers/` 或
  `/share/.matmaster/transfers/`，manifest/payload 必须按 0600 权限写入并
  对 token/access key 做日志脱敏。
```

- [ ] **Step 6: Run final focused verification**

Run:

```bash
uv run pytest \
  tests/matmaster_bohrium_transfer \
  tests/matmaster/bohrium/test_upload.py \
  tests/matmaster/bohrium/test_artifacts.py \
  tests/matmaster/tools/builtin/test_bohrium_tool.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_download.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py \
  tests/matmaster/integration/test_runtime_credential_bridge_e2e.py \
  tests/matmaster/test_import_audit.py \
  -q
```

Expected: all focused tests pass.

- [ ] **Step 7: Commit dependency removal**

Run:

```bash
git add pyproject.toml uv.lock Dockerfile.remote AGENTS.md matmaster/bohrium/remote_transfer_helper.py tests/matmaster/test_import_audit.py
git commit -m "feat: remove bohrium sdk transfer dependency"
```

## Final Verification

- [ ] **Step 1: Run formatting and focused tests**

Run:

```bash
uv run pytest \
  tests/matmaster_bohrium_transfer \
  tests/matmaster/bohrium \
  tests/matmaster/tools/builtin/test_bohrium_tool.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_transfers.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_download.py \
  tests/matmaster/tools/builtin/test_bohrium_tool_remote_runner.py \
  tests/matmaster/integration/test_runtime_credential_bridge_e2e.py \
  tests/matmaster/test_import_audit.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 2: Build transfer wheel**

Run:

```bash
uv run python scripts/build_bohrium_transfer_bundle.py
```

Expected: command prints JSON with `wheel_path` and `sha256_path`; both files exist.

- [ ] **Step 3: Verify no accidental files are staged**

Run:

```bash
git status --short
```

Expected: only intentional implementation files are modified; `.superpowers/` and unrelated user edits remain unstaged.
