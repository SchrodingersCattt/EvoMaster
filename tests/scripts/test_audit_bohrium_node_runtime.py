from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "audit_bohrium_node_runtime.py"
)
SPEC = importlib.util.spec_from_file_location("audit_bohrium_node_runtime", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

AuditDependencies = MODULE.AuditDependencies
audit_candidates = MODULE.audit_candidates
main = MODULE.main
render_report = MODULE.render_report


def _candidate(node_id: int, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": node_id + 100,
        "node_id": node_id,
        "user_id": "u1",
        "org_id": "o1",
        "project_id": 99,
        "sku_id": 388,
        "last_used_at": "2026-07-01 12:00:00",
    }
    row.update(overrides)
    return row


def _deps(
    candidates: list[dict[str, Any]],
    *,
    access_key_loader=lambda _user_id, _org_id: "secret-ak",
    node_detail_loader=lambda _access_key, _node_id: {
        "status": 2,
        "image_name": "matmaster:v1",
    },
    apply_stop=lambda _candidate, _access_key: "STOPPED_TO_PAUSED",
):
    return AuditDependencies(
        candidate_loader=lambda _limit: candidates,
        access_key_loader=access_key_loader,
        node_detail_loader=node_detail_loader,
        apply_stop=apply_stop,
    )


def test_classifies_provider_states_conservatively_and_redacts_secret():
    candidates = [_candidate(node_id) for node_id in range(1, 5)]
    details = {
        1: {"status": 2, "image_name": "matmaster:v1", "password": "pwd"},
        2: None,
        3: {"status": 7, "image_name": "matmaster:v2"},
        4: {"status": None, "image_name": None},
    }

    result = audit_candidates(
        candidates,
        access_key_loader=lambda _user, _org: "secret-ak",
        node_detail_loader=lambda _ak, node_id: details[node_id],
    )

    assert result.incomplete is False
    assert result.apply_failed is False
    assert [row.recommendation for row in result.rows] == [
        "VERIFY_IDLE_THEN_STOP",
        "DB_ROW_STALE_CANDIDATE",
        "MANUAL_REVIEW_STATUS_7",
        "MANUAL_REVIEW_STATUS_UNKNOWN",
    ]
    assert {row.execution for row in result.rows} == {"DRY_RUN"}
    output = render_report(result.rows)
    assert "secret-ak" not in output
    assert "pwd" not in output
    assert "SUMMARY total=4 audit_incomplete=0" in output


def test_incomplete_rows_continue_without_leaking_error_text():
    def load_access_key(user_id: str, _org_id: str) -> str | None:
        return None if user_id == "u1" else "secret-ak"

    def load_detail(_ak: str, _node_id: int) -> dict[str, Any] | None:
        raise RuntimeError("provider failed with secret-ak")

    candidates = [_candidate(1), _candidate(2, user_id="u2")]
    result = audit_candidates(
        candidates,
        access_key_loader=load_access_key,
        node_detail_loader=load_detail,
    )

    assert result.incomplete is True
    assert [row.recommendation for row in result.rows] == [
        "AUDIT_INCOMPLETE",
        "AUDIT_INCOMPLETE",
    ]
    output = render_report(result.rows)
    assert "MissingAccessKey" in output
    assert "RuntimeError" in output
    assert "provider failed" not in output
    assert "secret-ak" not in output


def test_access_keys_are_cached_by_user_and_org():
    calls: list[tuple[str, str]] = []

    def load_access_key(user_id: str, org_id: str) -> str:
        calls.append((user_id, org_id))
        return "secret-ak"

    result = audit_candidates(
        [_candidate(1), _candidate(2)],
        access_key_loader=load_access_key,
        node_detail_loader=lambda _ak, _node_id: {"status": 2},
    )

    assert result.incomplete is False
    assert calls == [("u1", "o1")]


def test_cli_dry_run_renders_report_and_passes_limit(capsys):
    limits: list[int] = []
    deps = _deps([])
    deps = AuditDependencies(
        candidate_loader=lambda limit: limits.append(limit) or [],
        access_key_loader=deps.access_key_loader,
        node_detail_loader=deps.node_detail_loader,
        apply_stop=deps.apply_stop,
    )

    assert main(["--limit", "10"], deps=deps) == 0

    output = capsys.readouterr().out
    assert limits == [10]
    assert "SUMMARY total=0 audit_incomplete=0" in output


def test_cli_exit_codes_for_incomplete_and_database_failure(capsys):
    assert (
        main(
            [],
            deps=_deps(
                [_candidate(1)],
                access_key_loader=lambda _user, _org: None,
            ),
        )
        == 2
    )

    def fail_query(_limit: int):
        raise ConnectionError("database secret")

    broken = _deps([])
    broken = AuditDependencies(
        candidate_loader=fail_query,
        access_key_loader=broken.access_key_loader,
        node_detail_loader=broken.node_detail_loader,
        apply_stop=broken.apply_stop,
    )
    assert main([], deps=broken) == 1
    output = capsys.readouterr().out
    assert "ConnectionError" in output
    assert "database secret" not in output


@pytest.mark.parametrize("limit", ["0", "1001", "not-an-int"])
def test_cli_rejects_invalid_limit(limit):
    with pytest.raises(SystemExit) as exc_info:
        main(["--limit", limit], deps=_deps([]))

    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["--apply"],
        ["--confirm-stop-all-unleased-ready"],
    ],
)
def test_cli_requires_apply_and_confirmation_together(argv):
    with pytest.raises(SystemExit) as exc_info:
        main(argv, deps=_deps([]))

    assert exc_info.value.code == 2


def test_production_dependencies_use_existing_access_key_only(monkeypatch):
    calls: list[str] = []

    class _Table:
        def list_ready_without_live_leases(self, _limit):
            return []

    class _NodeService:
        def get_node_detail(self, _access_key, _node_id):
            return None

    class _Manager:
        def stop_unleased_ready_slot(self, *_args, **_kwargs):
            return "unused"

    monkeypatch.setattr(MODULE, "get_bohrium_nodes_table", lambda: _Table())
    monkeypatch.setattr(MODULE, "get_bohrium_node_service", lambda: _NodeService())
    monkeypatch.setattr(MODULE, "get_bohrium_node_lease_manager", lambda: _Manager())
    monkeypatch.setattr(
        MODULE.UserService,
        "get_existing_bohrium_access_key",
        lambda _user, _org: calls.append("existing") or "ak",
    )
    monkeypatch.setattr(
        MODULE.UserService,
        "get_bohrium_access_key",
        lambda _user, _org: calls.append("creating") or "ak",
    )

    deps = MODULE._build_production_dependencies()

    assert deps.access_key_loader("u1", "o1") == "ak"
    assert calls == ["existing"]
