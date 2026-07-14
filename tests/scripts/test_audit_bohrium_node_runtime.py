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
    apply_preflight=lambda: True,
    access_key_loader=lambda _user_id, _org_id: "secret-ak",
    node_detail_loader=lambda _access_key, _node_id: {
        "status": 2,
        "image_name": "matmaster:v1",
    },
    apply_stop=lambda _candidate, _access_key: "STOPPED_TO_PAUSED",
    apply_stopped=lambda _candidate, _access_key: "ALREADY_STOPPED_TO_PAUSED",
):
    return AuditDependencies(
        apply_preflight=apply_preflight,
        candidate_loader=lambda _limit: candidates,
        access_key_loader=access_key_loader,
        node_detail_loader=node_detail_loader,
        apply_stop=apply_stop,
        apply_stopped=apply_stopped,
    )


def test_classifies_provider_states_conservatively_and_redacts_secret():
    candidates = [_candidate(node_id) for node_id in range(1, 6)]
    details = {
        1: {"status": 2, "image_name": "matmaster:v1", "password": "pwd"},
        2: None,
        3: {"status": -1, "image_name": "matmaster:v2"},
        4: {"status": 7, "image_name": "matmaster:v3"},
        5: {"status": None, "image_name": None},
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
        "PROVIDER_LIST_MISSING",
        "ALREADY_STOPPED",
        "MANUAL_REVIEW_STATUS_7",
        "MANUAL_REVIEW_STATUS_UNKNOWN",
    ]
    assert {row.execution for row in result.rows} == {"DRY_RUN"}
    output = render_report(result.rows)
    assert "secret-ak" not in output
    assert "pwd" not in output
    assert "SUMMARY total=5 audit_incomplete=0" in output


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
        apply_preflight=deps.apply_preflight,
        candidate_loader=lambda limit: limits.append(limit) or [],
        access_key_loader=deps.access_key_loader,
        node_detail_loader=deps.node_detail_loader,
        apply_stop=deps.apply_stop,
        apply_stopped=deps.apply_stopped,
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
        apply_preflight=broken.apply_preflight,
        candidate_loader=fail_query,
        access_key_loader=broken.access_key_loader,
        node_detail_loader=broken.node_detail_loader,
        apply_stop=broken.apply_stop,
        apply_stopped=broken.apply_stopped,
    )
    assert main([], deps=broken) == 1
    output = capsys.readouterr().out
    assert "ConnectionError" in output
    assert "database secret" not in output


def test_cli_redacts_production_dependency_initialization_failure(monkeypatch, capsys):
    def fail_dependencies():
        raise ConnectionError("database failed with secret-ak")

    monkeypatch.setattr(MODULE, "_build_production_dependencies", fail_dependencies)

    assert main([]) == 1
    output = capsys.readouterr().out
    assert "AUDIT_QUERY_FAILED\tConnectionError" in output
    assert "database failed" not in output
    assert "secret-ak" not in output


def test_cli_dry_run_skips_apply_preflight(capsys):
    preflight_calls: list[str] = []

    assert (
        main(
            [],
            deps=_deps(
                [],
                apply_preflight=lambda: preflight_calls.append("preflight") or False,
            ),
        )
        == 0
    )

    assert preflight_calls == []
    assert "APPLY_PREFLIGHT_FAILED" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "apply_preflight",
    [
        lambda: False,
        lambda: (_ for _ in ()).throw(ConnectionError("redis://secret")),
    ],
)
def test_cli_apply_preflight_failure_exits_before_query_and_redacts_error(
    apply_preflight, capsys
):
    query_calls: list[int] = []
    deps = _deps([], apply_preflight=apply_preflight)
    deps = AuditDependencies(
        apply_preflight=deps.apply_preflight,
        candidate_loader=lambda limit: query_calls.append(limit) or [],
        access_key_loader=deps.access_key_loader,
        node_detail_loader=deps.node_detail_loader,
        apply_stop=deps.apply_stop,
        apply_stopped=deps.apply_stopped,
    )

    assert (
        main(
            ["--apply", "--confirm-stop-all-unleased-ready"],
            deps=deps,
        )
        == 1
    )

    output = capsys.readouterr().out
    assert query_calls == []
    assert output == "APPLY_PREFLIGHT_FAILED\tRedisUnavailable\n"
    assert "redis://secret" not in output


def test_cli_apply_preflight_success_continues(capsys):
    preflight_calls: list[str] = []
    query_calls: list[int] = []
    deps = _deps(
        [],
        apply_preflight=lambda: preflight_calls.append("preflight") or True,
    )
    deps = AuditDependencies(
        apply_preflight=deps.apply_preflight,
        candidate_loader=lambda limit: query_calls.append(limit) or [],
        access_key_loader=deps.access_key_loader,
        node_detail_loader=deps.node_detail_loader,
        apply_stop=deps.apply_stop,
        apply_stopped=deps.apply_stopped,
    )

    assert (
        main(
            ["--limit", "10", "--apply", "--confirm-stop-all-unleased-ready"],
            deps=deps,
        )
        == 0
    )

    assert preflight_calls == ["preflight"]
    assert query_calls == [10]
    assert "SUMMARY total=0 audit_incomplete=0" in capsys.readouterr().out


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


def test_dry_run_never_invokes_apply_actions():
    stops: list[int] = []
    reconciliations: list[int] = []

    result = audit_candidates(
        [_candidate(1), _candidate(2)],
        access_key_loader=lambda _user, _org: "secret-ak",
        node_detail_loader=lambda _ak, node_id: {"status": 2 if node_id == 1 else -1},
        apply_stop=lambda candidate, _ak: stops.append(candidate["node_id"]),
        apply_stopped=lambda candidate, _ak: reconciliations.append(
            candidate["node_id"]
        ),
    )

    assert stops == []
    assert reconciliations == []
    assert {row.execution for row in result.rows} == {"DRY_RUN"}


@pytest.mark.parametrize(
    ("status", "apply_stop", "apply_stopped", "missing_name"),
    [
        (2, None, lambda _candidate, _ak: None, "apply_stop"),
        (-1, lambda _candidate, _ak: None, None, "apply_stopped"),
    ],
)
def test_apply_requires_the_matching_action(
    status, apply_stop, apply_stopped, missing_name
):
    with pytest.raises(ValueError, match=missing_name):
        audit_candidates(
            [_candidate(1)],
            access_key_loader=lambda _user, _org: "secret-ak",
            node_detail_loader=lambda _ak, _node_id: {"status": status},
            apply=True,
            apply_stop=apply_stop,
            apply_stopped=apply_stopped,
        )


def test_apply_dispatches_running_and_stopped_candidates_separately():
    candidates = [_candidate(node_id) for node_id in range(1, 5)]
    details = {
        1: {"status": 2},
        2: {"status": -1},
        3: None,
        4: {"status": 7},
    }
    stops: list[int] = []
    reconciliations: list[int] = []

    result = audit_candidates(
        candidates,
        access_key_loader=lambda _user, _org: "secret-ak",
        node_detail_loader=lambda _ak, node_id: details[node_id],
        apply=True,
        apply_stop=lambda candidate, _ak: stops.append(candidate["node_id"])
        or "STOPPED_TO_PAUSED",
        apply_stopped=lambda candidate, _ak: reconciliations.append(
            candidate["node_id"]
        )
        or "ALREADY_STOPPED_TO_PAUSED",
    )

    assert stops == [1]
    assert reconciliations == [2]
    assert [row.execution for row in result.rows] == [
        "STOPPED_TO_PAUSED",
        "ALREADY_STOPPED_TO_PAUSED",
        "NOT_ELIGIBLE",
        "NOT_ELIGIBLE",
    ]


def test_apply_stops_only_status_two_candidates_and_renders_outcomes():
    candidates = [_candidate(node_id) for node_id in range(1, 5)]
    details = {
        1: {"status": 2},
        2: None,
        3: {"status": 7},
        4: {"status": 2},
    }
    stops: list[int] = []

    def apply_stop(candidate, _access_key):
        stops.append(candidate["node_id"])
        if candidate["node_id"] == 1:
            return "STOPPED_TO_PAUSED"
        return "SKIPPED_CONCURRENT_LEASE"

    result = audit_candidates(
        candidates,
        access_key_loader=lambda _user, _org: "secret-ak",
        node_detail_loader=lambda _ak, node_id: details[node_id],
        apply=True,
        apply_stop=apply_stop,
    )

    assert stops == [1, 4]
    assert [row.execution for row in result.rows] == [
        "STOPPED_TO_PAUSED",
        "NOT_ELIGIBLE",
        "NOT_ELIGIBLE",
        "SKIPPED_CONCURRENT_LEASE",
    ]
    assert result.apply_failed is False


def test_apply_failure_continues_and_exit_three_precedes_incomplete(capsys):
    candidates = [
        _candidate(1),
        _candidate(2),
        _candidate(3, user_id="u2"),
    ]
    stops: list[int] = []

    def access_key_loader(user_id, _org_id):
        return None if user_id == "u2" else "secret-ak"

    def apply_stop(candidate, _access_key):
        stops.append(candidate["node_id"])
        if candidate["node_id"] == 1:
            raise TimeoutError("stop failed with secret-ak")
        return "STOPPED_TO_PAUSED"

    deps = _deps(
        candidates,
        access_key_loader=access_key_loader,
        apply_stop=apply_stop,
    )

    assert (
        main(
            ["--apply", "--confirm-stop-all-unleased-ready"],
            deps=deps,
        )
        == 3
    )

    output = capsys.readouterr().out
    assert stops == [1, 2]
    assert "FAILED_TimeoutError" in output
    assert "STOPPED_TO_PAUSED" in output
    assert "AUDIT_INCOMPLETE" in output
    assert "stop failed" not in output
    assert "secret-ak" not in output


def test_production_dependencies_use_existing_access_key_only(monkeypatch):
    calls: list[str] = []
    manager_calls: list[str] = []
    redis_calls: list[str] = []
    stop_calls: list[tuple[dict[str, Any], str, int]] = []
    reconcile_calls: list[dict[str, Any]] = []

    class _Table:
        def list_ready_without_live_leases(self, _limit):
            return []

    class _NodeService:
        def get_node_detail(self, _access_key, _node_id):
            return None

    class _RedisClient:
        def ping(self):
            redis_calls.append("ping")
            return True

    class _RedisDao:
        def get_command_client(self):
            redis_calls.append("client")
            return _RedisClient()

    class _Manager:
        def stop_unleased_ready_slot(self, candidate, *, access_key, creator_id):
            stop_calls.append((candidate, access_key, creator_id))
            return "STOPPED_TO_PAUSED"

        def reconcile_stopped_unleased_ready_slot(self, candidate):
            reconcile_calls.append(candidate)
            return "ALREADY_STOPPED_TO_PAUSED"

    monkeypatch.setattr(MODULE, "get_bohrium_nodes_table", lambda: _Table())
    monkeypatch.setattr(MODULE, "get_bohrium_node_service", lambda: _NodeService())
    monkeypatch.setattr(MODULE, "get_redis_dao", lambda: _RedisDao())
    monkeypatch.setattr(
        MODULE,
        "get_bohrium_node_reconciliation_service",
        lambda: manager_calls.append("manager") or _Manager(),
    )
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

    assert manager_calls == []
    assert deps.apply_preflight() is True
    assert redis_calls == ["client", "ping"]
    assert deps.access_key_loader("u1", "o1") == "ak"
    assert calls == ["existing"]
    candidate = _candidate(1, user_id="110680")
    assert deps.apply_stop(candidate, "secret-ak") == "STOPPED_TO_PAUSED"
    assert deps.apply_stopped(candidate, "secret-ak") == "ALREADY_STOPPED_TO_PAUSED"
    assert manager_calls == ["manager", "manager"]
    assert stop_calls == [(candidate, "secret-ak", 110680)]
    assert reconcile_calls == [candidate]


def test_production_apply_preflight_rejects_missing_redis_client(monkeypatch):
    class _Table:
        def list_ready_without_live_leases(self, _limit):
            return []

    class _NodeService:
        def get_node_detail(self, _access_key, _node_id):
            return None

    class _RedisDao:
        def get_command_client(self):
            return None

    monkeypatch.setattr(MODULE, "get_bohrium_nodes_table", lambda: _Table())
    monkeypatch.setattr(MODULE, "get_bohrium_node_service", lambda: _NodeService())
    monkeypatch.setattr(MODULE, "get_redis_dao", lambda: _RedisDao())

    deps = MODULE._build_production_dependencies()

    assert deps.apply_preflight() is False
