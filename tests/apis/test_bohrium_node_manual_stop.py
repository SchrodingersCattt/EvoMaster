from unittest.mock import MagicMock, patch

import pytest

from src.utils.exceptions import ConflictErrorResponse


def test_manual_stop_uses_authenticated_user_org_project_and_sku():
    from src.apis import bohrium_node_api
    from src.models import chat

    stop_bohrium_node = getattr(bohrium_node_api, "stop_bohrium_node", None)
    request_model = getattr(chat, "BohriumNodeStopRequest", None)
    assert stop_bohrium_node is not None
    assert request_model is not None

    manager = MagicMock()
    manager.manual_stop.return_value = True
    with patch(
        "src.apis.bohrium_node_api.UserService.get_bohrium_access_key",
        return_value="ak",
    ):
        response = stop_bohrium_node(
            body=request_model(project_id=99, sku_id=456),
            user_id="u1",
            org_id="o1",
            manager=manager,
        )

    identity = manager.manual_stop.call_args.args[0]
    assert (
        identity.user_id,
        identity.org_id,
        identity.project_id,
        identity.sku_id,
    ) == (
        "u1",
        "o1",
        99,
        456,
    )
    assert manager.manual_stop.call_args.kwargs["access_key"] == "ak"
    assert response.data == {"stopped": True}


def test_manual_stop_maps_live_lease_to_conflict():
    from src.apis import bohrium_node_api
    from src.models import chat

    stop_bohrium_node = getattr(bohrium_node_api, "stop_bohrium_node", None)
    request_model = getattr(chat, "BohriumNodeStopRequest", None)
    assert stop_bohrium_node is not None
    assert request_model is not None

    manager = MagicMock()
    manager.manual_stop.side_effect = RuntimeError("Bohrium Node has a live lease")
    with (
        patch(
            "src.apis.bohrium_node_api.UserService.get_bohrium_access_key",
            return_value="ak",
        ),
        pytest.raises(ConflictErrorResponse, match="正在使用"),
    ):
        stop_bohrium_node(
            body=request_model(project_id=99, sku_id=456),
            user_id="u1",
            org_id="o1",
            manager=manager,
        )
