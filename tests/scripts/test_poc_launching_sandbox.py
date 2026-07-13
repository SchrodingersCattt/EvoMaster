from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "poc_launching_sandbox.py"
)
SPEC = importlib.util.spec_from_file_location("poc_launching_sandbox", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

MOUNT_USER_STORAGE_KEY = MODULE.MOUNT_USER_STORAGE_KEY
ContractError = MODULE.ContractError
SandboxContractSmoke = MODULE.SandboxContractSmoke
SmokeConfig = MODULE.SmokeConfig
_assert_zero_price = MODULE._assert_zero_price
_find_sku = MODULE._find_sku
build_redacted_plan = MODULE.build_redacted_plan
main = MODULE.main
validate_config = MODULE.validate_config


def _config(**overrides: Any) -> SmokeConfig:
    values: dict[str, Any] = {
        "environment": "test",
        "base_url": "https://open.example.test",
        "access_key": "runtime-secret",
        "template_access_key": "owner-secret",
        "user_id": "101",
        "org_id": "202",
        "project_id": "303",
        "template_name": "matmaster-test-c1-m2-smoke-run123",
        "sku_name": "c1_m2_cpu",
        "image": "registry.example.test/matmaster:sha-123",
        "timeout_seconds": 7200,
        "request_timeout_seconds": 600.0,
        "smoke": True,
        "create_disposable_template": True,
        "keep_template": False,
        "require_distinct_template_owner": True,
        "confirmed_free_sku": "c1_m2_cpu",
        "allow_non_test": False,
        "run_id": "run123",
    }
    values.update(overrides)
    return SmokeConfig(**values)


def test_default_cli_is_dry_run_and_redacts_environment_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BOHRIUM_ACCESS_KEY", "must-not-leak")

    assert main(["--env", "test"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert '"mode": "dry-run"' in captured.out
    assert '"base_url": "https://openapi.test.dp.tech"' in captured.out
    assert "must-not-leak" not in captured.out


def test_env_file_loads_bohrium_identity_without_leaking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in (
        "BOHRIUM_ACCESS_KEY",
        "BOHRIUM_USER_ID",
        "BOHRIUM_ORG_ID",
        "BOHRIUM_PROJECT_ID",
        "LBG_SDBX_USER_ID",
        "LBG_SDBX_ORG_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env.test"
    env_file.write_text(
        "\n".join(
            (
                "BOHRIUM_ACCESS_KEY=env-file-secret",
                "BOHRIUM_USER_ID=101",
                "BOHRIUM_ORG_ID=202",
                "BOHRIUM_PROJECT_ID=303",
            )
        ),
        encoding="utf-8",
    )

    assert main(["--env", "test", "--env-file", str(env_file)]) == 0

    captured = capsys.readouterr()
    assert "env-file-secret" not in captured.out
    assert '"user_id": true' in captured.out
    assert '"org_id": true' in captured.out
    assert '"project_id": true' in captured.out


def test_redacted_plan_contains_no_access_keys() -> None:
    plan = build_redacted_plan(_config(smoke=False))

    assert "runtime-secret" not in str(plan)
    assert "owner-secret" not in str(plan)
    assert plan["template_owner_distinct"] is True


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"access_key": ""}, "BOHRIUM_ACCESS_KEY"),
        ({"project_id": ""}, "project id"),
        ({"confirmed_free_sku": None}, "confirmed-free-sku"),
        ({"environment": "prod"}, "test-only"),
        ({"base_url": "http://open.example.test"}, "https"),
        ({"image": None}, "requires --image"),
        (
            {"create_disposable_template": False, "keep_template": True},
            "keep-template",
        ),
        ({"timeout_seconds": 7201}, "timeout must"),
        ({"request_timeout_seconds": 0}, "request timeout"),
        (
            {"template_access_key": "runtime-secret"},
            "distinct template-owner",
        ),
    ],
)
def test_validate_config_fails_closed(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ContractError, match=message):
        validate_config(_config(**overrides))


def test_validate_config_allows_non_test_only_with_explicit_override() -> None:
    validate_config(_config(environment="uat", allow_non_test=True))


def test_find_sku_and_zero_price_require_exact_current_contract() -> None:
    response = {
        "code": 0,
        "data": {
            "cpu": [
                {
                    "sku_id": 1,
                    "sku_name": "c1_m2_cpu",
                    "price": "0.00 RMB/h",
                }
            ],
            "gpu": [],
        },
    }

    row = _find_sku(response, "c1_m2_cpu")
    _assert_zero_price(row, "c1_m2_cpu")

    with pytest.raises(ContractError, match="no longer free"):
        _assert_zero_price({**row, "price": "0.36 RMB/h"}, "c1_m2_cpu")
    with pytest.raises(ContractError, match="unavailable"):
        _find_sku(response, "c2_m4_cpu")


@dataclass
class _Settings:
    sandbox_list_api_url: str = "https://open.test/bohr_sandbox/sandboxes"
    template_list_api_url: str = "https://open.test/bohr_sandbox/templates"


class _FakeOpenApi:
    def __init__(
        self, *, runtime: bool, create_error_once: Exception | None = None
    ) -> None:
        self.settings = _Settings()
        self.runtime = runtime
        self.create_error_once = create_error_once
        self.create_attempts = 0
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []
        self.created_templates: list[dict[str, Any]] = []
        self.deleted_templates: list[str] = []
        self.created_sandboxes: list[dict[str, Any]] = []
        self.listed_sandboxes: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        del payload, kwargs
        self.requests.append((method, url, params))
        if url.endswith("/skus"):
            return {
                "code": 0,
                "data": {
                    "cpu": [
                        {
                            "sku_id": 855,
                            "sku_name": "c1_m2_cpu",
                            "price": "0.00 RMB/h",
                        }
                    ],
                    "gpu": [],
                },
            }
        if url.endswith("/templates/lookup"):
            return {
                "code": 0,
                "data": {
                    "name": "matmaster-test-c1-m2-smoke-run123",
                    "image": "registry.example.test/matmaster:sha-123",
                    "sku_name": "c1_m2_cpu",
                    "status": 1,
                    "visibility": 1,
                    "image_cache_status": 2,
                },
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    def create_sandbox_set_template(self, payload: dict[str, Any]) -> Any:
        self.created_templates.append(payload)
        return {"code": 0}

    def create_sandbox(
        self,
        template: str,
        *,
        timeout: int | None = None,
        metadata: dict[str, str] | None = None,
        envs: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.create_attempts += 1
        if self.create_error_once is not None:
            error = self.create_error_once
            self.create_error_once = None
            raise error
        sandbox_id = f"sandbox-{len(self.created_sandboxes) + 1}"
        self.created_sandboxes.append(
            {
                "sandbox_id": sandbox_id,
                "template": template,
                "timeout": timeout,
                "metadata": metadata,
                "envs": envs,
            }
        )
        return {"sandboxID": sandbox_id, "state": "running"}

    def delete_sandbox_set_template(self, name: str) -> dict[str, Any]:
        self.deleted_templates.append(name)
        return {"status": "deleted"}

    def list_sandboxes(self) -> list[dict[str, Any]]:
        return list(self.listed_sandboxes)


class _FakeE2B:
    def __init__(self, *, corrupt_persistent_read: bool = False) -> None:
        self.corrupt_persistent_read = corrupt_persistent_read
        self.persistent: dict[str, Any] = {}
        self.ephemeral: dict[str, dict[str, Any]] = {}
        self.exec_calls: list[tuple[str, str]] = []
        self.killed: list[str] = []

    def sandbox_exec(
        self,
        sandbox_id: str,
        command: str,
        envs: dict[str, str] | None = None,
        user: str | None = None,
        cwd: str | None = None,
        background: bool = False,
        timeout: float | None = 60,
    ) -> dict[str, Any]:
        del envs, user, cwd, background, timeout
        self.exec_calls.append((sandbox_id, command))
        return {"exit_code": 0}

    def sandbox_files_write(
        self, sandbox_id: str, path: str, data: Any
    ) -> dict[str, Any]:
        self._store(sandbox_id, path)[path] = data
        return {"sandbox_id": sandbox_id, "path": path}

    def sandbox_files_write_many(
        self, sandbox_id: str, entries: list[tuple[str, bytes]]
    ) -> list[dict[str, Any]]:
        for path, data in entries:
            self._store(sandbox_id, path)[path] = data
        return [{"path": path} for path, _ in entries]

    def sandbox_files_read(
        self, sandbox_id: str, path: str, format: str = "text"
    ) -> Any:
        del format
        if self.corrupt_persistent_read and sandbox_id == "sandbox-2":
            return "corrupt"
        return self._store(sandbox_id, path)[path]

    def sandbox_kill(self, sandbox_id: str) -> dict[str, Any]:
        self.killed.append(sandbox_id)
        return {"sandbox_id": sandbox_id, "killed": True}

    def _store(self, sandbox_id: str, path: str) -> dict[str, Any]:
        if path.startswith(("/personal/", "/share/")):
            return self.persistent
        return self.ephemeral.setdefault(sandbox_id, {})


def test_smoke_runs_two_sandboxes_and_cleans_every_resource() -> None:
    runtime = _FakeOpenApi(runtime=True)
    owner = _FakeOpenApi(runtime=False)
    e2b = _FakeE2B()

    report = SandboxContractSmoke(_config(), runtime, owner, e2b).run()

    assert report["checks"] == {
        "sku_free": True,
        "template_contract": True,
        "mounts_writable": True,
        "personal_persisted": True,
        "share_binary_persisted": True,
        "ephemeral_isolated": True,
    }
    assert len(runtime.created_sandboxes) == 2
    assert all(
        row["metadata"][MOUNT_USER_STORAGE_KEY] == "true"
        for row in runtime.created_sandboxes
    )
    assert all(row["timeout"] == 7200 for row in runtime.created_sandboxes)
    assert e2b.killed == ["sandbox-1", "sandbox-2"]
    assert owner.created_templates[0]["sku_name"] == "c1_m2_cpu"
    assert owner.created_templates[0]["visibility"] == 1
    assert owner.created_templates[0]["pause_enabled"] is False
    assert owner.deleted_templates == [_config().template_name]
    assert report["cleanup_errors"] == []


def test_smoke_failure_still_kills_live_sandbox_and_deletes_template() -> None:
    runtime = _FakeOpenApi(runtime=True)
    owner = _FakeOpenApi(runtime=False)
    e2b = _FakeE2B(corrupt_persistent_read=True)

    with pytest.raises(ContractError, match="personal marker"):
        SandboxContractSmoke(_config(), runtime, owner, e2b).run()

    assert e2b.killed == ["sandbox-1", "sandbox-2"]
    assert owner.deleted_templates == [_config().template_name]


def test_smoke_retries_only_explicit_pre_create_image_cache_gate() -> None:
    runtime = _FakeOpenApi(
        runtime=True,
        create_error_once=ContractError(
            "request failed [400] image cache is not ready"
        ),
    )
    owner = _FakeOpenApi(runtime=False)
    e2b = _FakeE2B()

    SandboxContractSmoke(_config(), runtime, owner, e2b).run()

    assert runtime.create_attempts == 3
    assert len(runtime.created_sandboxes) == 2
    assert e2b.killed == ["sandbox-1", "sandbox-2"]


def test_image_cache_retry_classifier_rejects_ambiguous_gateway_failure() -> None:
    assert SandboxContractSmoke._is_image_cache_not_ready(
        ContractError("request failed [400]: image cache is not ready")
    )
    assert not SandboxContractSmoke._is_image_cache_not_ready(
        ContractError("request failed [504]: image cache is not ready")
    )


def test_ambiguous_create_is_reconciled_and_killed_without_retry() -> None:
    runtime = _FakeOpenApi(
        runtime=True,
        create_error_once=ContractError("request failed [504]: gateway timeout"),
    )
    runtime.listed_sandboxes = [
        {
            "sandboxID": "orphan-from-timeout",
            "templateID": _config().template_name,
            "metadata": {"matmaster.contract.run-id": _config().run_id},
        }
    ]
    owner = _FakeOpenApi(runtime=False)
    e2b = _FakeE2B()

    with pytest.raises(ContractError, match="504"):
        SandboxContractSmoke(_config(), runtime, owner, e2b).run()

    assert runtime.create_attempts == 1
    assert e2b.killed == ["orphan-from-timeout"]
    assert owner.deleted_templates == [_config().template_name]
