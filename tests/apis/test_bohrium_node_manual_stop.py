def test_bohrium_node_manual_stop_route_is_not_registered():
    from app import app

    paths = app.openapi()["paths"]
    assert "/api/v1/chat/sessions/runtime/bohrium-node/stop" not in paths
