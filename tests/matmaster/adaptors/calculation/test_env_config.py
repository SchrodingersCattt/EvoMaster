"""Gap 4 (27-02-01 / CALC-01): resolve_mcp_config_path and get_current_env.

Behavioral contract:
- Both functions importable from matmaster.adaptors.calculation.env_config.
- get_current_env returns 'prod' by default (when SERVICE_ENV not set).
- get_current_env returns the value of SERVICE_ENV when set.
- resolve_mcp_config_path returns the original path in prod environment.
- resolve_mcp_config_path switches to env-specific file when SERVICE_ENV=test and file exists.
- resolve_mcp_config_path falls back to original when env-specific file does not exist.
- Module has no evomaster imports.
"""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path
from unittest.mock import patch


class TestGetCurrentEnv:
    def test_default_is_prod(self):
        from matmaster.adaptors.calculation.env_config import get_current_env
        with patch.dict(os.environ, {}, clear=False):
            env = os.environ.pop("SERVICE_ENV", None)
            try:
                result = get_current_env()
                assert result == "prod"
            finally:
                if env is not None:
                    os.environ["SERVICE_ENV"] = env

    def test_returns_service_env_value(self):
        from matmaster.adaptors.calculation.env_config import get_current_env
        with patch.dict(os.environ, {"SERVICE_ENV": "test"}):
            assert get_current_env() == "test"

    def test_returns_uat_when_set(self):
        from matmaster.adaptors.calculation.env_config import get_current_env
        with patch.dict(os.environ, {"SERVICE_ENV": "uat"}):
            assert get_current_env() == "uat"


class TestResolveMcpConfigPath:
    def test_prod_env_returns_original_path(self, tmp_path):
        from matmaster.adaptors.calculation.env_config import resolve_mcp_config_path
        config_file = tmp_path / "mcp_config.json"
        config_file.write_text("{}")
        with patch.dict(os.environ, {"SERVICE_ENV": "prod"}):
            result = resolve_mcp_config_path(config_file)
        assert result == config_file

    def test_test_env_returns_test_file_when_exists(self, tmp_path):
        from matmaster.adaptors.calculation.env_config import resolve_mcp_config_path
        config_file = tmp_path / "mcp_config.json"
        config_file.write_text("{}")
        test_file = tmp_path / "mcp_config.test.json"
        test_file.write_text("{}")
        with patch.dict(os.environ, {"SERVICE_ENV": "test"}):
            result = resolve_mcp_config_path(config_file)
        assert result == test_file

    def test_test_env_falls_back_when_env_file_missing(self, tmp_path):
        from matmaster.adaptors.calculation.env_config import resolve_mcp_config_path
        config_file = tmp_path / "mcp_config.json"
        config_file.write_text("{}")
        # No mcp_config.test.json
        with patch.dict(os.environ, {"SERVICE_ENV": "test"}):
            result = resolve_mcp_config_path(config_file)
        assert result == config_file

    def test_uat_env_returns_uat_file_when_exists(self, tmp_path):
        from matmaster.adaptors.calculation.env_config import resolve_mcp_config_path
        config_file = tmp_path / "mcp_config.json"
        config_file.write_text("{}")
        uat_file = tmp_path / "mcp_config.uat.json"
        uat_file.write_text("{}")
        with patch.dict(os.environ, {"SERVICE_ENV": "uat"}):
            result = resolve_mcp_config_path(config_file)
        assert result == uat_file

    def test_accepts_path_object(self, tmp_path):
        from matmaster.adaptors.calculation.env_config import resolve_mcp_config_path
        config_file = tmp_path / "mcp_config.json"
        config_file.write_text("{}")
        with patch.dict(os.environ, {"SERVICE_ENV": "prod"}):
            result = resolve_mcp_config_path(config_file)
        assert isinstance(result, Path)


class TestEnvConfigPackageLevelImport:
    def test_importable_from_package(self):
        from matmaster.adaptors.calculation import get_current_env, resolve_mcp_config_path
        assert callable(get_current_env)
        assert callable(resolve_mcp_config_path)

    def test_no_evomaster_in_source(self):
        import matmaster.adaptors.calculation.env_config as mod
        source = inspect.getsource(mod)
        assert "evomaster" not in source, "Found 'evomaster' in env_config.py"

    def test_no_top_level_evomaster_imports(self):
        module_file = Path(
            __import__(
                "matmaster.adaptors.calculation.env_config",
                fromlist=["env_config"],
            ).__file__
        )
        source = module_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level_evo = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and "evomaster" in node.module
            and node.col_offset == 0
        ]
        assert top_level_evo == []
