"""Compatibility acquisition path for Bohrium Nodes without invocation leases."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.services.bohrium_node_service import get_default_node_name
from src.services.bohrium_run_support import _creator_id_from_user, _emit_node_status
from src.utils.constant import BOHRIUM_DEFAULT_IMAGE_ID, BOHRIUM_DEFAULT_IMAGE_NAME

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BohriumNodeAcquisition:
    node_id: int | None
    ip: str | None
    password: str | None
    reuse_tracked: bool


def acquire_compatibility_node(
    *,
    node_service: Any,
    nodes_table: Any | None,
    access_key: str,
    project_id: int,
    sku_id: int,
    user_id: str | None,
    org_id: str,
    event_callback: Callable[..., None],
    cancel_checker: Callable[[], bool] | None = None,
) -> BohriumNodeAcquisition:
    """Reuse or create a Node for old jobs that have no invocation id."""
    _raise_if_cancelled(cancel_checker)
    node_id: int | None = None
    node_ip: str | None = None
    node_password: str | None = None
    reuse_tracked = False
    creator_id = _creator_id_from_user(user_id)

    if nodes_table is not None and user_id and org_id:
        _cleanup_untracked_nodes(
            node_service,
            nodes_table,
            access_key=access_key,
            user_id=user_id,
            org_id=org_id,
            creator_id=creator_id,
        )
        row = nodes_table.find_one_for_reuse(user_id, org_id, project_id, sku_id)
        expected_image_name = _expected_image_name(node_service, access_key)
        if row:
            node_id = int(row["node_id"])
            reuse_tracked = True
            node_info = node_service.get_node_info(access_key, node_id)
            logger.info(
                "run_agent: node image check (ready) node_id=%s "
                "node_image_name=%s expected_image_name=%s",
                node_id,
                node_info.get("image_name") if node_info else None,
                expected_image_name,
            )
            if node_info and node_info.get("ip"):
                if _node_image_outdated(
                    node_info.get("image_name"), expected_image_name
                ):
                    logger.info(
                        "run_agent: reuse skipped, node image outdated "
                        "node_id=%s node_image_name=%s expected_image_name=%s, "
                        "destroy and create new",
                        node_id,
                        node_info.get("image_name"),
                        expected_image_name,
                    )
                    _destroy_outdated_node(
                        node_service,
                        nodes_table,
                        access_key=access_key,
                        user_id=user_id,
                        org_id=org_id,
                        project_id=project_id,
                        sku_id=sku_id,
                        node_id=node_id,
                        creator_id=creator_id,
                    )
                    node_id = None
                    reuse_tracked = False
                else:
                    node_ip = node_info.get("ip")
                    node_password = node_info.get("password")
                    logger.info(
                        "run_agent: reusing Bohrium node node_id=%s ip=%s",
                        node_id,
                        node_ip,
                    )
            else:
                node_detail = node_service.get_node_detail(access_key, node_id)
                logger.info(
                    "run_agent: node image check (not ready) node_id=%s "
                    "node_image_name=%s expected_image_name=%s",
                    node_id,
                    node_detail.get("image_name") if node_detail else None,
                    expected_image_name,
                )
                if node_detail is not None and _node_image_outdated(
                    node_detail.get("image_name"), expected_image_name
                ):
                    _destroy_outdated_node(
                        node_service,
                        nodes_table,
                        access_key=access_key,
                        user_id=user_id,
                        org_id=org_id,
                        project_id=project_id,
                        sku_id=sku_id,
                        node_id=node_id,
                        creator_id=creator_id,
                    )
                    node_id = None
                    reuse_tracked = False
                else:
                    try:
                        _emit_node_status(
                            event_callback,
                            node_id,
                            "restarting",
                            "正在重启 Bohrium 计算节点...",
                        )
                        node_service.restart_node(
                            access_key,
                            node_id,
                            project_id,
                            creator_id=creator_id,
                            sku_id=sku_id,
                        )
                        _emit_node_status(
                            event_callback,
                            node_id,
                            "starting",
                            "节点已重启，正在等待就绪...",
                        )
                        wait_kwargs = (
                            {'cancel_checker': cancel_checker}
                            if cancel_checker is not None
                            else {}
                        )
                        node_info = node_service.wait_until_ready(
                            access_key, node_id, **wait_kwargs
                        )
                        node_ip = node_info.get("ip")
                        node_password = node_info.get("password")
                    except Exception as restart_err:
                        _raise_if_cancelled(cancel_checker)
                        logger.warning(
                            "run_agent: restart node_id=%s failed, will create new: %s",
                            node_id,
                            restart_err,
                        )
                        nodes_table.delete_by_node(
                            user_id,
                            org_id,
                            project_id,
                            sku_id,
                            node_id,
                        )
                        node_id = None
                        reuse_tracked = False

    if node_id is None or node_ip is None:
        _raise_if_cancelled(cancel_checker)
        _emit_node_status(
            event_callback,
            None,
            "creating",
            "正在创建 Bohrium 计算节点...",
        )
        node_info = node_service.create_node(access_key, project_id, sku_id=sku_id)
        node_id = node_info.get("node_id")
        if node_id is not None:
            _emit_node_status(
                event_callback,
                node_id,
                "starting",
                "节点已创建，正在等待就绪...",
            )
            wait_kwargs = (
                {'cancel_checker': cancel_checker} if cancel_checker is not None else {}
            )
            node_info = node_service.wait_until_ready(
                access_key, node_id, **wait_kwargs
            )
            node_ip = node_info.get("ip")
            node_password = node_info.get("password")
            if nodes_table is not None and user_id and org_id:
                try:
                    reuse_tracked = bool(
                        nodes_table.insert_node(
                            user_id,
                            org_id,
                            project_id,
                            sku_id,
                            node_id,
                        )
                    )
                except Exception as insert_err:
                    logger.warning(
                        "run_agent: insert_node failed (table missing?): %s",
                        insert_err,
                        exc_info=True,
                    )

    return BohriumNodeAcquisition(
        node_id=node_id,
        ip=node_ip,
        password=node_password,
        reuse_tracked=reuse_tracked,
    )


def _raise_if_cancelled(cancel_checker: Callable[[], bool] | None) -> None:
    if cancel_checker is not None and cancel_checker():
        raise RuntimeError("Bohrium Node acquisition cancelled")


def _expected_image_name(node_service: Any, access_key: str) -> str | None:
    expected = (
        os.environ.get("BOHRIUM_EXPECTED_IMAGE_NAME")
        or os.environ.get("BOHRIUM_IMAGE_NAME")
        or BOHRIUM_DEFAULT_IMAGE_NAME
    )
    if isinstance(expected, str):
        expected = expected.strip() or None
    else:
        expected = None
    if expected is None:
        expected = node_service.get_image_name_by_id(
            access_key, BOHRIUM_DEFAULT_IMAGE_ID
        )
    return expected


def _node_image_outdated(actual: str | None, expected: str | None) -> bool:
    return bool(expected and actual and actual != expected)


def _cleanup_untracked_nodes(
    node_service: Any,
    nodes_table: Any,
    *,
    access_key: str,
    user_id: str,
    org_id: str,
    creator_id: int,
) -> None:
    try:
        tracked_node_ids = nodes_table.list_node_ids_for_user_org(user_id, org_id)
        node_name = get_default_node_name()
        destroyed = node_service.destroy_untracked_nodes_by_name(
            access_key,
            tracked_node_ids,
            node_name=node_name,
            creator_id=creator_id,
        )
        if destroyed:
            logger.info(
                "run_agent: destroyed untracked nodes user_id=%s org_id=%s "
                "name=%s node_ids=%s",
                user_id,
                org_id,
                node_name,
                destroyed,
            )
    except Exception as cleanup_err:
        logger.warning(
            "run_agent: cleanup untracked nodes failed user_id=%s org_id=%s: %s",
            user_id,
            org_id,
            cleanup_err,
        )


def _destroy_outdated_node(
    node_service: Any,
    nodes_table: Any,
    *,
    access_key: str,
    user_id: str,
    org_id: str,
    project_id: int,
    sku_id: int,
    node_id: int,
    creator_id: int,
) -> None:
    nodes_table.delete_by_node(user_id, org_id, project_id, sku_id, node_id)
    try:
        node_service.destroy_node(
            access_key,
            node_id,
            project_id,
            creator_id=creator_id,
        )
    except Exception as destroy_err:
        logger.warning(
            "run_agent: destroy outdated node_id=%s failed: %s",
            node_id,
            destroy_err,
        )
