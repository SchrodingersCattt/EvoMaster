"""Audit historical Bohrium Node slots and optionally stop eligible Nodes."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from src.dao.bohrium_nodes_table import get_bohrium_nodes_table
from src.services.bohrium_node_lifecycle import get_bohrium_node_lease_manager
from src.services.bohrium_node_service import get_bohrium_node_service
from src.services.bohrium_run_support import _creator_id_from_user
from src.services.user_service import UserService

MAX_LIMIT = 1000


@dataclass(frozen=True)
class AuditRow:
    node_id: int
    user_id: str
    org_id: str
    project_id: int
    sku_id: int
    last_used_at: Any
    provider_status: Any
    image_name: str | None
    recommendation: str
    execution: str = "DRY_RUN"
    error_type: str | None = None


@dataclass(frozen=True)
class AuditResult:
    rows: list[AuditRow]
    incomplete: bool
    apply_failed: bool


@dataclass(frozen=True)
class AuditDependencies:
    candidate_loader: Callable[[int], list[dict[str, Any]]]
    access_key_loader: Callable[[str, str], str | None]
    node_detail_loader: Callable[[str, int], dict[str, Any] | None]
    apply_stop: Callable[[dict[str, Any], str], Any]


def _bounded_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc
    if not 1 <= limit <= MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


def _classify(detail: dict[str, Any] | None) -> tuple[Any, str | None, str]:
    if detail is None:
        return None, None, "DB_ROW_STALE_CANDIDATE"
    status = detail.get("status")
    image_name = detail.get("image_name")
    if status == 2:
        recommendation = "VERIFY_IDLE_THEN_STOP"
    elif status is None:
        recommendation = "MANUAL_REVIEW_STATUS_UNKNOWN"
    else:
        recommendation = f"MANUAL_REVIEW_STATUS_{status}"
    return status, image_name, recommendation


def _audit_row(
    candidate: dict[str, Any],
    *,
    provider_status: Any = None,
    image_name: str | None = None,
    recommendation: str,
    execution: str = "DRY_RUN",
    error_type: str | None = None,
) -> AuditRow:
    return AuditRow(
        node_id=int(candidate["node_id"]),
        user_id=str(candidate["user_id"]),
        org_id=str(candidate["org_id"]),
        project_id=int(candidate["project_id"]),
        sku_id=int(candidate["sku_id"]),
        last_used_at=candidate.get("last_used_at"),
        provider_status=provider_status,
        image_name=image_name,
        recommendation=recommendation,
        execution=execution,
        error_type=error_type,
    )


def audit_candidates(
    candidates: Sequence[dict[str, Any]],
    *,
    access_key_loader: Callable[[str, str], str | None],
    node_detail_loader: Callable[[str, int], dict[str, Any] | None],
    apply: bool = False,
    apply_stop: Callable[[dict[str, Any], str], Any] | None = None,
) -> AuditResult:
    """Classify candidates and optionally stop all provider-ready Nodes."""
    rows: list[AuditRow] = []
    access_keys: dict[tuple[str, str], str | None] = {}
    incomplete = False
    apply_failed = False

    for candidate in candidates:
        user_id = str(candidate["user_id"])
        org_id = str(candidate["org_id"])
        cache_key = (user_id, org_id)
        try:
            if cache_key not in access_keys:
                access_keys[cache_key] = access_key_loader(user_id, org_id)
            access_key = access_keys[cache_key]
        except Exception as exc:
            incomplete = True
            rows.append(
                _audit_row(
                    candidate,
                    recommendation="AUDIT_INCOMPLETE",
                    execution="NOT_ELIGIBLE" if apply else "DRY_RUN",
                    error_type=type(exc).__name__,
                )
            )
            continue
        if not access_key:
            incomplete = True
            rows.append(
                _audit_row(
                    candidate,
                    recommendation="AUDIT_INCOMPLETE",
                    execution="NOT_ELIGIBLE" if apply else "DRY_RUN",
                    error_type="MissingAccessKey",
                )
            )
            continue
        try:
            detail = node_detail_loader(access_key, int(candidate["node_id"]))
        except Exception as exc:
            incomplete = True
            rows.append(
                _audit_row(
                    candidate,
                    recommendation="AUDIT_INCOMPLETE",
                    execution="NOT_ELIGIBLE" if apply else "DRY_RUN",
                    error_type=type(exc).__name__,
                )
            )
            continue

        provider_status, image_name, recommendation = _classify(detail)
        row = _audit_row(
            candidate,
            provider_status=provider_status,
            image_name=image_name,
            recommendation=recommendation,
            execution="NOT_ELIGIBLE" if apply else "DRY_RUN",
        )
        if apply and provider_status == 2:
            if apply_stop is None:
                raise ValueError("apply_stop is required when apply is enabled")
            try:
                outcome = apply_stop(candidate, access_key)
                execution = getattr(outcome, "value", outcome)
                row = replace(row, execution=str(execution))
            except Exception as exc:
                apply_failed = True
                row = replace(
                    row,
                    execution=f"FAILED_{type(exc).__name__}",
                    error_type=type(exc).__name__,
                )
        rows.append(row)

    return AuditResult(
        rows=rows,
        incomplete=incomplete,
        apply_failed=apply_failed,
    )


def _display_field(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def render_report(rows: Sequence[AuditRow]) -> str:
    columns = (
        "NODE_ID",
        "USER_ID",
        "ORG_ID",
        "PROJECT_ID",
        "SKU_ID",
        "LAST_USED_AT",
        "PROVIDER_STATUS",
        "IMAGE_NAME",
        "RECOMMENDATION",
        "EXECUTION",
        "ERROR",
    )
    lines = ["\t".join(columns)]
    for row in rows:
        lines.append(
            "\t".join(
                _display_field(value)
                for value in (
                    row.node_id,
                    row.user_id,
                    row.org_id,
                    row.project_id,
                    row.sku_id,
                    row.last_used_at,
                    row.provider_status,
                    row.image_name,
                    row.recommendation,
                    row.execution,
                    row.error_type,
                )
            )
        )
    counts = Counter(row.recommendation for row in rows)
    incomplete_count = counts.get("AUDIT_INCOMPLETE", 0)
    lines.append(f"SUMMARY total={len(rows)} audit_incomplete={incomplete_count}")
    for recommendation, count in sorted(counts.items()):
        lines.append(f"SUMMARY {recommendation}={count}")
    return "\n".join(lines)


def _build_production_dependencies() -> AuditDependencies:
    nodes_table = get_bohrium_nodes_table()
    node_service = get_bohrium_node_service()

    def apply_stop(candidate: dict[str, Any], access_key: str) -> Any:
        return get_bohrium_node_lease_manager().stop_unleased_ready_slot(
            candidate,
            access_key=access_key,
            creator_id=_creator_id_from_user(candidate.get("user_id")),
        )

    return AuditDependencies(
        candidate_loader=nodes_table.list_ready_without_live_leases,
        access_key_loader=UserService.get_existing_bohrium_access_key,
        node_detail_loader=node_service.get_node_detail,
        apply_stop=apply_stop,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit historical ready Bohrium Nodes; optionally stop every "
            "provider-ready candidate after explicit confirmation."
        )
    )
    parser.add_argument("--limit", type=_bounded_limit, default=MAX_LIMIT)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--confirm-stop-all-unleased-ready",
        action="store_true",
        help="confirm all status=2 candidates may be stopped",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    deps: AuditDependencies | None = None,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.apply != args.confirm_stop_all_unleased_ready:
        parser.error(
            "--apply and --confirm-stop-all-unleased-ready must be used together"
        )
    dependencies = deps or _build_production_dependencies()
    try:
        candidates = dependencies.candidate_loader(args.limit)
    except Exception as exc:
        print(f"AUDIT_QUERY_FAILED\t{type(exc).__name__}")
        return 1
    result = audit_candidates(
        candidates,
        access_key_loader=dependencies.access_key_loader,
        node_detail_loader=dependencies.node_detail_loader,
        apply=args.apply,
        apply_stop=dependencies.apply_stop,
    )
    print(render_report(result.rows))
    if result.apply_failed:
        return 3
    if result.incomplete:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
