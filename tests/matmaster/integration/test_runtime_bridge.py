def test_runtime_bridge_package_removed() -> None:
    import importlib.util

    assert importlib.util.find_spec("matmaster.integration.runtime_bridge") is None
