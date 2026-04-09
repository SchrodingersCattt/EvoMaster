def test_bohrium_env_module_removed() -> None:
    import importlib.util

    assert importlib.util.find_spec("matmaster.integration.bohrium_env") is None
