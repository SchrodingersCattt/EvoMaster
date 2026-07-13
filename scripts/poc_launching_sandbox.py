"""Contract smoke for the current Bohrium Launching Sandbox APIs.

The default mode is a side-effect-free, redacted plan. Pass ``--smoke`` to
create resources in a live environment. The live path intentionally uses the
public ``lbgcore.sdbx`` facade from lbg 4.0.0b56 and never retries an ambiguous
sandbox-create request.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import shlex
import sys
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

LBG_VERSION = "4.0.0b56"
DEFAULT_BASE_URLS = {
    "test": "https://openapi.test.dp.tech",
    "uat": "https://openapi.uat.dp.tech",
    "prod": "https://open.bohrium.com",
}
DEFAULT_SKU_NAME = "c1_m2_cpu"
DEFAULT_TIMEOUT_SECONDS = 7200
IMAGE_CACHE_WAIT_SECONDS = 600
IMAGE_CACHE_POLL_SECONDS = 5
AMBIGUOUS_RECONCILE_ATTEMPTS = 3
AMBIGUOUS_RECONCILE_INTERVAL_SECONDS = 2
MOUNT_USER_STORAGE_KEY = "bohr.launching.io/mount-user-storage"
PUBLIC_VISIBILITY = 1
ACTIVE_TEMPLATE_STATUS = 1
_PRICE_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s+RMB/h\s*$")


class ContractError(RuntimeError):
    """A live response violated the contract required by MatMaster."""


class OpenApiClient(Protocol):
    settings: Any

    def request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any: ...

    def create_sandbox_set_template(self, payload: dict[str, Any]) -> Any: ...

    def create_sandbox(
        self,
        template: str,
        *,
        timeout: int | None = None,
        metadata: dict[str, str] | None = None,
        envs: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...

    def delete_sandbox_set_template(self, name: str) -> dict[str, Any]: ...

    def list_sandboxes(self) -> list[dict[str, Any]]: ...


class E2BClient(Protocol):
    def sandbox_exec(
        self,
        sandbox_id: str,
        command: str,
        envs: dict[str, str] | None = None,
        user: str | None = None,
        cwd: str | None = None,
        background: bool = False,
        timeout: float | None = 60,
    ) -> dict[str, Any]: ...

    def sandbox_files_read(
        self, sandbox_id: str, path: str, format: str = "text"
    ) -> Any: ...

    def sandbox_files_write(
        self, sandbox_id: str, path: str, data: Any
    ) -> dict[str, Any]: ...

    def sandbox_files_write_many(
        self, sandbox_id: str, entries: list[tuple[str, bytes]]
    ) -> list[dict[str, Any]]: ...

    def sandbox_kill(self, sandbox_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SmokeConfig:
    environment: str
    base_url: str
    access_key: str
    template_access_key: str
    user_id: str
    org_id: str
    project_id: str
    template_name: str
    sku_name: str
    image: str | None
    timeout_seconds: int
    request_timeout_seconds: float
    smoke: bool
    create_disposable_template: bool
    keep_template: bool
    require_distinct_template_owner: bool
    confirmed_free_sku: str | None
    allow_non_test: bool
    run_id: str


def _clean_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url.startswith(("https://", "http://")):
        raise ContractError("base URL must start with https:// or http://")
    return url


def _positive_identifier(value: str, name: str) -> str:
    text = value.strip()
    if not text.isdigit() or int(text) <= 0:
        raise ContractError(f"{name} must be a positive integer")
    return text


def validate_config(config: SmokeConfig) -> None:
    if not config.smoke:
        return
    if config.environment != "test" and not config.allow_non_test:
        raise ContractError("live smoke is test-only unless --allow-non-test is set")
    if not config.base_url.startswith("https://"):
        raise ContractError("live smoke requires an https:// base URL")
    if not config.access_key:
        raise ContractError("--smoke requires BOHRIUM_ACCESS_KEY")
    _positive_identifier(config.user_id, "user id")
    _positive_identifier(config.org_id, "org id")
    _positive_identifier(config.project_id, "project id")
    if config.confirmed_free_sku != config.sku_name:
        raise ContractError(
            "--smoke requires --confirmed-free-sku matching --sku-name; "
            "this records the platform-owner FreeSkuNames confirmation"
        )
    if config.create_disposable_template and not config.image:
        raise ContractError("--create-disposable-template requires --image")
    if config.keep_template and not config.create_disposable_template:
        raise ContractError("--keep-template requires --create-disposable-template")
    if not 1 <= config.timeout_seconds <= DEFAULT_TIMEOUT_SECONDS:
        raise ContractError(
            f"timeout must be between 1 and {DEFAULT_TIMEOUT_SECONDS} seconds"
        )
    if not 0 < config.request_timeout_seconds <= 600:
        raise ContractError("request timeout must be between 0 and 600 seconds")
    if (
        config.create_disposable_template
        and config.require_distinct_template_owner
        and config.template_access_key == config.access_key
    ):
        raise ContractError(
            "distinct template-owner validation requires "
            "BOHRIUM_TEMPLATE_ACCESS_KEY different from BOHRIUM_ACCESS_KEY"
        )


def build_redacted_plan(config: SmokeConfig) -> dict[str, Any]:
    return {
        "mode": "smoke" if config.smoke else "dry-run",
        "environment": config.environment,
        "base_url": config.base_url,
        "lbg_version": LBG_VERSION,
        "template_name": config.template_name,
        "sku_name": config.sku_name,
        "image": config.image,
        "timeout_seconds": config.timeout_seconds,
        "create_disposable_template": config.create_disposable_template,
        "keep_template": config.keep_template,
        "template_owner_distinct": bool(
            config.access_key
            and config.template_access_key
            and config.access_key != config.template_access_key
        ),
        "identity_present": {
            "user_id": bool(config.user_id),
            "org_id": bool(config.org_id),
            "project_id": bool(config.project_id),
        },
        "checks": [
            "live SKU exists and reports 0.00 RMB/h",
            "platform owner confirmed SKU is in FreeSkuNames",
            "template is active, public, and bound to the expected SKU",
            "/personal and /share are real writable mount points",
            "text and binary files survive sandbox recreation on mounted storage",
            "an ephemeral /tmp marker does not survive sandbox recreation",
            "every created sandbox is killed in finally",
        ],
    }


def _unwrap_data(payload: Any, operation: str) -> Any:
    if not isinstance(payload, dict):
        raise ContractError(f"{operation} returned a non-object response")
    code = payload.get("code")
    if code not in (None, 0, "0", "0000"):
        raise ContractError(f"{operation} failed with code={code}")
    return payload.get("data", payload)


def _find_sku(payload: Any, sku_name: str) -> dict[str, Any]:
    data = _unwrap_data(payload, "list SKUs")
    if not isinstance(data, dict):
        raise ContractError("list SKUs returned invalid data")
    rows: list[Any] = []
    for bucket in ("cpu", "gpu"):
        value = data.get(bucket, [])
        if isinstance(value, list):
            rows.extend(value)
    for row in rows:
        if isinstance(row, dict) and row.get("sku_name") == sku_name:
            return row
    raise ContractError(f"required sandbox SKU is unavailable: {sku_name}")


def _assert_zero_price(row: dict[str, Any], sku_name: str) -> None:
    raw_price = str(row.get("price") or "")
    match = _PRICE_PATTERN.fullmatch(raw_price)
    if match is None:
        raise ContractError(
            f"SKU {sku_name} returned an unrecognized price: {raw_price!r}"
        )
    try:
        price = Decimal(match.group(1))
    except InvalidOperation as exc:
        raise ContractError(f"SKU {sku_name} returned an invalid price") from exc
    if price != 0:
        raise ContractError(f"SKU {sku_name} is no longer free: {raw_price}")


def _sandbox_id(payload: Any) -> str:
    data = _unwrap_data(payload, "create sandbox")
    if not isinstance(data, dict):
        raise ContractError("create sandbox returned invalid data")
    for key in ("sandboxID", "sandbox_id", "sandboxId"):
        value = data.get(key)
        if value not in (None, ""):
            return str(value).strip()
    raise ContractError("create sandbox response did not contain sandboxID")


def _template_row(payload: Any) -> dict[str, Any]:
    data = _unwrap_data(payload, "lookup template")
    if not isinstance(data, dict):
        raise ContractError("lookup template returned invalid data")
    return data


def _assert_template(row: dict[str, Any], config: SmokeConfig) -> None:
    expected = {
        "name": config.template_name,
        "sku_name": config.sku_name,
        "status": ACTIVE_TEMPLATE_STATUS,
        "visibility": PUBLIC_VISIBILITY,
    }
    for key, value in expected.items():
        if row.get(key) != value:
            raise ContractError(
                f"template {key} mismatch: expected {value!r}, got {row.get(key)!r}"
            )
    if config.image and row.get("image") != config.image:
        raise ContractError("template image does not match --image")


def _assert_exec_success(payload: Any, operation: str) -> None:
    if not isinstance(payload, dict):
        raise ContractError(f"{operation} returned invalid exec data")
    raw = payload.get("exit_code", payload.get("exitCode", 0))
    try:
        exit_code = int(raw)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{operation} returned invalid exit code") from exc
    if exit_code != 0:
        raise ContractError(f"{operation} failed with exit_code={exit_code}")


def _as_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    raise ContractError(f"binary file read returned {type(value).__name__}, not bytes")


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8")
    raise ContractError(f"text file read returned {type(value).__name__}")


class SandboxContractSmoke:
    def __init__(
        self,
        config: SmokeConfig,
        runtime_openapi: OpenApiClient,
        template_openapi: OpenApiClient,
        e2b: E2BClient,
    ) -> None:
        self.config = config
        self.runtime_openapi = runtime_openapi
        self.template_openapi = template_openapi
        self.e2b = e2b
        self.active_sandboxes: list[str] = []
        self.template_created = False
        self.cleanup_errors: list[str] = []
        self.persistent_cleanup_paths: tuple[str, ...] = ()

    def run(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "run_id": self.config.run_id,
            "template_name": self.config.template_name,
            "sku_name": self.config.sku_name,
            "checks": {},
            "sandbox_ids": [],
        }
        primary_error: BaseException | None = None
        try:
            sku = self._verify_sku()
            report["sku"] = {
                "sku_id": sku.get("sku_id"),
                "sku_name": sku.get("sku_name"),
                "price": sku.get("price"),
            }
            report["checks"]["sku_free"] = True
            if self.config.create_disposable_template:
                self._create_template()
            row = self._lookup_template()
            _assert_template(row, self.config)
            report["checks"]["template_contract"] = True
            report["template"] = {
                key: row.get(key)
                for key in (
                    "name",
                    "image",
                    "sku_name",
                    "visibility",
                    "status",
                    "image_cache_status",
                )
            }
            self._exercise_two_sandboxes(report)
        except BaseException as exc:
            primary_error = exc
        finally:
            self._cleanup()
            report["cleanup_errors"] = list(self.cleanup_errors)
        if primary_error is not None:
            if self.cleanup_errors:
                raise ContractError(
                    f"{primary_error}; cleanup failed: "
                    + "; ".join(self.cleanup_errors)
                ) from primary_error
            raise primary_error
        if self.cleanup_errors:
            raise ContractError("cleanup failed: " + "; ".join(self.cleanup_errors))
        return report

    def _verify_sku(self) -> dict[str, Any]:
        url = self.runtime_openapi.settings.sandbox_list_api_url.rstrip("/")
        url = url.rsplit("/", 1)[0] + "/skus"
        payload = self.runtime_openapi.request(
            "GET", url, params={"chooseType": "cpu"}, retry_on_network=2
        )
        row = _find_sku(payload, self.config.sku_name)
        _assert_zero_price(row, self.config.sku_name)
        return row

    def _create_template(self) -> None:
        payload = {
            "name": self.config.template_name,
            "display_name": self.config.template_name,
            "description": f"MatMaster contract smoke {self.config.run_id}",
            "image": self.config.image,
            "sku_name": self.config.sku_name,
            "replicas": 0,
            "visibility": PUBLIC_VISIBILITY,
            "extra_ephemeral_storage_gb": 0,
            "pause_enabled": False,
        }
        response = self.template_openapi.create_sandbox_set_template(payload)
        _unwrap_data(response, "create template")
        self.template_created = True

    def _lookup_template(self) -> dict[str, Any]:
        url = self.runtime_openapi.settings.template_list_api_url.rstrip("/")
        response = self.runtime_openapi.request(
            "GET",
            url + "/lookup",
            params={"name": self.config.template_name},
            retry_on_network=2,
        )
        return _template_row(response)

    def _create_sandbox(self) -> str:
        metadata = {
            MOUNT_USER_STORAGE_KEY: "true",
            "matmaster.contract.run-id": self.config.run_id,
        }
        cache_deadline = time.monotonic() + IMAGE_CACHE_WAIT_SECONDS
        while True:
            try:
                response = self._create_sandbox_once(metadata)
                break
            except Exception as exc:
                if self._is_image_cache_not_ready(exc):
                    if time.monotonic() >= cache_deadline:
                        raise ContractError(
                            "template image cache did not become ready within "
                            f"{IMAGE_CACHE_WAIT_SECONDS}s"
                        ) from exc
                    # Launching rejected this request at its HTTP 400 pre-create
                    # image-cache gate, before E2B received it. Some deployments
                    # omit image_cache_status from template lookup, so the explicit
                    # create rejection is the portable readiness probe. 502/504,
                    # transport failures, and every other error are never retried.
                    time.sleep(IMAGE_CACHE_POLL_SECONDS)
                    continue
                self._reconcile_ambiguous_create()
                raise
        sandbox_id = _sandbox_id(response)
        self.active_sandboxes.append(sandbox_id)
        return sandbox_id

    def _create_sandbox_once(self, metadata: dict[str, str]) -> dict[str, Any]:
        return self.runtime_openapi.create_sandbox(
            self.config.template_name,
            timeout=self.config.timeout_seconds,
            metadata=metadata,
            envs={"MATMASTER_CONTRACT_RUN_ID": self.config.run_id},
        )

    @staticmethod
    def _is_image_cache_not_ready(exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            "[400]" in message
            and "image cache" in message
            and (
                "not ready" in message
                or ("still warming up" in message and "status: creating" in message)
            )
        )

    def _reconcile_ambiguous_create(self) -> None:
        for attempt in range(AMBIGUOUS_RECONCILE_ATTEMPTS):
            try:
                rows = self.runtime_openapi.list_sandboxes()
            except Exception as exc:  # noqa: BLE001 - retain primary create error
                self.cleanup_errors.append(
                    f"ambiguous create reconciliation failed: {exc}"
                )
                return
            found = False
            for row in rows:
                if not isinstance(row, dict):
                    continue
                metadata = self._sandbox_metadata(row)
                if metadata.get("matmaster.contract.run-id") != self.config.run_id:
                    continue
                template = (
                    row.get("template")
                    or row.get("templateID")
                    or row.get("template_id")
                )
                if template and str(template) != self.config.template_name:
                    continue
                try:
                    sandbox_id = _sandbox_id(row)
                except ContractError:
                    continue
                if sandbox_id not in self.active_sandboxes:
                    self.active_sandboxes.append(sandbox_id)
                found = True
            if found:
                return
            if attempt + 1 < AMBIGUOUS_RECONCILE_ATTEMPTS:
                time.sleep(AMBIGUOUS_RECONCILE_INTERVAL_SECONDS)

    @staticmethod
    def _sandbox_metadata(row: dict[str, Any]) -> dict[str, Any]:
        value = row.get("metadata")
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                return {}
            if isinstance(parsed, dict):
                return parsed
        return {}

    def _kill(self, sandbox_id: str) -> None:
        response = self.e2b.sandbox_kill(sandbox_id)
        if not isinstance(response, dict) or not response.get("killed"):
            raise ContractError(f"sandbox kill was not acknowledged: {sandbox_id}")
        if sandbox_id in self.active_sandboxes:
            self.active_sandboxes.remove(sandbox_id)

    def _exercise_two_sandboxes(self, report: dict[str, Any]) -> None:
        marker = f"matmaster-contract-{self.config.run_id}"
        personal_dir = f"/personal/.matmaster/contract-smoke/{self.config.run_id}"
        share_dir = f"/share/.matmaster/contract-smoke/{self.config.run_id}"
        personal_file = f"{personal_dir}/marker.txt"
        share_file = f"{share_dir}/payload.bin"
        ephemeral_file = f"/tmp/{marker}.txt"
        binary_payload = bytes(range(256)) * 4
        sandbox_a = self._create_sandbox()
        report["sandbox_ids"].append(sandbox_a)
        self._assert_mounts(sandbox_a)
        self._mkdirs(sandbox_a, personal_dir, share_dir)
        self.persistent_cleanup_paths = (personal_dir, share_dir)
        report["persistent_marker_roots"] = list(self.persistent_cleanup_paths)
        self.e2b.sandbox_files_write(sandbox_a, personal_file, marker)
        self.e2b.sandbox_files_write_many(
            sandbox_a, [(share_file, binary_payload), (ephemeral_file, b"ephemeral")]
        )
        if (
            _as_bytes(
                self.e2b.sandbox_files_read(sandbox_a, share_file, format="bytes")
            )
            != binary_payload
        ):
            raise ContractError("binary file round-trip failed in sandbox A")
        self._kill(sandbox_a)

        sandbox_b = self._create_sandbox()
        report["sandbox_ids"].append(sandbox_b)
        self._assert_mounts(sandbox_b)
        if _as_text(self.e2b.sandbox_files_read(sandbox_b, personal_file)) != marker:
            raise ContractError("/personal marker did not survive sandbox recreation")
        restored = _as_bytes(
            self.e2b.sandbox_files_read(sandbox_b, share_file, format="bytes")
        )
        if restored != binary_payload:
            raise ContractError("/share binary did not survive sandbox recreation")
        command = f"test ! -e {shlex.quote(ephemeral_file)}"
        _assert_exec_success(
            self.e2b.sandbox_exec(sandbox_b, command),
            "ephemeral isolation check",
        )
        self._remove_persistent_markers(sandbox_b)
        report["checks"].update(
            {
                "mounts_writable": True,
                "personal_persisted": True,
                "share_binary_persisted": True,
                "ephemeral_isolated": True,
            }
        )

    def _assert_mounts(self, sandbox_id: str) -> None:
        command = """
set -eu
command -v findmnt >/dev/null
for path in /personal /share; do
  test -d "$path"
  test -w "$path"
  target="$(findmnt -T "$path" -n -o TARGET)"
  test "$target" = "$path"
done
""".strip()
        _assert_exec_success(
            self.e2b.sandbox_exec(sandbox_id, command), "mount verification"
        )

    def _mkdirs(self, sandbox_id: str, *paths: str) -> None:
        command = "mkdir -p -- " + shlex.join(paths)
        _assert_exec_success(self.e2b.sandbox_exec(sandbox_id, command), "mkdir")

    def _remove_persistent_markers(self, sandbox_id: str) -> None:
        if not self.persistent_cleanup_paths:
            return
        command = "rm -rf -- " + shlex.join(self.persistent_cleanup_paths)
        _assert_exec_success(
            self.e2b.sandbox_exec(sandbox_id, command), "persistent marker cleanup"
        )
        self.persistent_cleanup_paths = ()

    def _cleanup(self) -> None:
        if self.persistent_cleanup_paths and self.active_sandboxes:
            try:
                self._remove_persistent_markers(self.active_sandboxes[-1])
            except Exception as exc:  # noqa: BLE001 - report persistent residue
                self.cleanup_errors.append(f"remove persistent markers: {exc}")
        for sandbox_id in list(reversed(self.active_sandboxes)):
            try:
                self._kill(sandbox_id)
            except Exception as exc:  # noqa: BLE001 - preserve every cleanup attempt
                self.cleanup_errors.append(f"kill {sandbox_id}: {exc}")
        if self.template_created and not self.config.keep_template:
            try:
                self.template_openapi.delete_sandbox_set_template(
                    self.config.template_name
                )
            except Exception as exc:  # noqa: BLE001 - report template leak
                self.cleanup_errors.append(
                    f"delete template {self.config.template_name}: {exc}"
                )
        if self.persistent_cleanup_paths and not self.active_sandboxes:
            roots = ", ".join(self.persistent_cleanup_paths)
            self.cleanup_errors.append(f"manual persistent cleanup required: {roots}")


def _settings_kwargs(config: SmokeConfig, access_key: str) -> dict[str, Any]:
    sandbox_base = config.base_url + "/openapi/launching/v2/bohr_sandbox"
    work_base = config.base_url + "/openapi/launching/v2/bohr_sandbox_work"
    return {
        "api_url": work_base,
        "api_key": access_key,
        "template_api_url": sandbox_base + "/sandbox_sets/template",
        "template_list_api_url": sandbox_base + "/templates",
        "template_delete_api_url_tpl": sandbox_base + "/templates/{name}",
        "template_update_api_url_tpl": sandbox_base + "/sandbox_sets/template/{name}",
        "sandbox_list_api_url": sandbox_base + "/sandboxes",
        "sandbox_create_api_url": work_base + "/sandboxes",
        "request_timeout": config.request_timeout_seconds,
        "project_id": config.project_id,
        "user_id": config.user_id,
        "org_id": config.org_id,
    }


def _load_lbg_clients(
    config: SmokeConfig,
) -> tuple[OpenApiClient, OpenApiClient, E2BClient]:
    try:
        installed = importlib.metadata.version("lbg")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ContractError(
            f"lbg is not installed; run with: uv run --with lbg=={LBG_VERSION} python ..."
        ) from exc
    if installed != LBG_VERSION:
        raise ContractError(f"expected lbg {LBG_VERSION}, found {installed}")
    try:
        from lbgcore.sdbx import SdbxE2BClient, SdbxOpenApiClient, SdbxSettings
    except ImportError as exc:
        raise ContractError("lbgcore.sdbx public facade is unavailable") from exc

    runtime_settings = SdbxSettings(**_settings_kwargs(config, config.access_key))
    template_settings = SdbxSettings(
        **_settings_kwargs(config, config.template_access_key)
    )
    return (
        SdbxOpenApiClient(runtime_settings),
        SdbxOpenApiClient(template_settings),
        SdbxE2BClient(runtime_settings),
    )


def _default_template_name(environment: str) -> str:
    if environment == "prod":
        return "matmaster-c1-m2"
    return f"matmaster-{environment}-c1-m2"


def _load_env_file(path: str) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise ContractError("--env-file requires python-dotenv") from exc
    if not load_dotenv(path, override=False):
        raise ContractError(f"environment file not found or empty: {path}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    env_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    env_parser.add_argument("--env-file", default=None)
    env_args, _ = env_parser.parse_known_args(argv)
    if env_args.env_file:
        _load_env_file(str(env_args.env_file))

    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--env", choices=("test", "uat", "prod"), default="test")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--template-name", default=None)
    parser.add_argument("--sku-name", default=DEFAULT_SKU_NAME)
    parser.add_argument("--image", default=None)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--request-timeout-seconds", type=float, default=600.0)
    parser.add_argument(
        "--user-id",
        default=os.environ.get("LBG_SDBX_USER_ID")
        or os.environ.get("BOHRIUM_USER_ID", ""),
    )
    parser.add_argument(
        "--org-id",
        default=os.environ.get("LBG_SDBX_ORG_ID")
        or os.environ.get("BOHRIUM_ORG_ID", ""),
    )
    parser.add_argument(
        "--project-id", default=os.environ.get("BOHRIUM_PROJECT_ID", "")
    )
    parser.add_argument("--confirmed-free-sku", default=None)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--create-disposable-template", action="store_true")
    parser.add_argument("--keep-template", action="store_true")
    parser.add_argument("--require-distinct-template-owner", action="store_true")
    parser.add_argument("--allow-non-test", action="store_true")
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> SmokeConfig:
    run_id = uuid.uuid4().hex[:12]
    template_name = args.template_name or _default_template_name(args.env)
    if args.create_disposable_template:
        template_name = f"{template_name}-smoke-{run_id}"
    access_key = os.environ.get("BOHRIUM_ACCESS_KEY", "").strip()
    template_access_key = os.environ.get(
        "BOHRIUM_TEMPLATE_ACCESS_KEY", access_key
    ).strip()
    return SmokeConfig(
        environment=args.env,
        base_url=_clean_base_url(args.base_url or DEFAULT_BASE_URLS[args.env]),
        access_key=access_key,
        template_access_key=template_access_key,
        user_id=str(args.user_id),
        org_id=str(args.org_id),
        project_id=str(args.project_id),
        template_name=template_name,
        sku_name=str(args.sku_name).strip(),
        image=str(args.image).strip() if args.image else None,
        timeout_seconds=int(args.timeout_seconds),
        request_timeout_seconds=float(args.request_timeout_seconds),
        smoke=bool(args.smoke),
        create_disposable_template=bool(args.create_disposable_template),
        keep_template=bool(args.keep_template),
        require_distinct_template_owner=bool(args.require_distinct_template_owner),
        confirmed_free_sku=(
            str(args.confirmed_free_sku).strip() if args.confirmed_free_sku else None
        ),
        allow_non_test=bool(args.allow_non_test),
        run_id=run_id,
    )


def _redact_error(exc: BaseException, *secrets: str) -> str:
    text = str(exc)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def main(argv: list[str] | None = None) -> int:
    config: SmokeConfig | None = None
    try:
        config = config_from_args(parse_args(argv))
        validate_config(config)
        if not config.smoke:
            print(json.dumps(build_redacted_plan(config), ensure_ascii=False, indent=2))
            return 0
        runtime_openapi, template_openapi, e2b = _load_lbg_clients(config)
        report = SandboxContractSmoke(
            config, runtime_openapi, template_openapi, e2b
        ).run()
        report["binary_sha256"] = hashlib.sha256(bytes(range(256)) * 4).hexdigest()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI emits a concise redacted failure
        secrets = ()
        failure: dict[str, Any] = {"status": "failed"}
        if config is not None:
            secrets = (config.access_key, config.template_access_key)
            failure.update(
                {
                    "run_id": config.run_id,
                    "template_name": config.template_name,
                }
            )
        failure["error"] = _redact_error(exc, *secrets)
        print(
            json.dumps(failure, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
