"""Gap 4 (27-02-01 / CALC-01): resolve_mcp_config_path and get_current_env.

Behavioral contract:
- Both functions importable from matmaster.mcp.calculation.config_env.
- get_current_env returns 'prod' by default (when SERVICE_ENV not set).
- get_current_env returns the value of SERVICE_ENV when set.
- resolve_mcp_config_path returns the original path in prod environment.
- resolve_mcp_config_path switches to env-specific file when SERVICE_ENV=test and file exists.
- resolve_mcp_config_path falls back to original when env-specific file does not exist.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch


class TestGetCurrentEnv:
    def test_default_is_prod(self):
        from matmaster.mcp.calculation.config_env import get_current_env

        with patch.dict(os.environ, {}, clear=False):
            env = os.environ.pop("SERVICE_ENV", None)
            try:
                result = get_current_env()
                assert result == "prod"
            finally:
                if env is not None:
                    os.environ["SERVICE_ENV"] = env

    def test_returns_service_env_value(self):
        from matmaster.mcp.calculation.config_env import get_current_env

        with patch.dict(os.environ, {"SERVICE_ENV": "test"}):
            assert get_current_env() == "test"

    def test_returns_uat_when_set(self):
        from matmaster.mcp.calculation.config_env import get_current_env

        with patch.dict(os.environ, {"SERVICE_ENV": "uat"}):
            assert get_current_env() == "uat"


class TestResolveMcpConfigPath:
    def test_prod_env_returns_original_path(self, tmp_path):
        from matmaster.mcp.calculation.config_env import resolve_mcp_config_path

        config_file = tmp_path / "mcp_config.json"
        config_file.write_text("{}")
        with patch.dict(os.environ, {"SERVICE_ENV": "prod"}):
            result = resolve_mcp_config_path(config_file)
        assert result == config_file

    def test_test_env_returns_test_file_when_exists(self, tmp_path):
        from matmaster.mcp.calculation.config_env import resolve_mcp_config_path

        config_file = tmp_path / "mcp_config.json"
        config_file.write_text("{}")
        test_file = tmp_path / "mcp_config.test.json"
        test_file.write_text("{}")
        with patch.dict(os.environ, {"SERVICE_ENV": "test"}):
            result = resolve_mcp_config_path(config_file)
        assert result == test_file

    def test_test_env_falls_back_when_env_file_missing(self, tmp_path):
        from matmaster.mcp.calculation.config_env import resolve_mcp_config_path

        config_file = tmp_path / "mcp_config.json"
        config_file.write_text("{}")
        with patch.dict(os.environ, {"SERVICE_ENV": "test"}):
            result = resolve_mcp_config_path(config_file)
        assert result == config_file

    def test_uat_env_returns_uat_file_when_exists(self, tmp_path):
        from matmaster.mcp.calculation.config_env import resolve_mcp_config_path

        config_file = tmp_path / "mcp_config.json"
        config_file.write_text("{}")
        uat_file = tmp_path / "mcp_config.uat.json"
        uat_file.write_text("{}")
        with patch.dict(os.environ, {"SERVICE_ENV": "uat"}):
            result = resolve_mcp_config_path(config_file)
        assert result == uat_file

    def test_accepts_path_object(self, tmp_path):
        from matmaster.mcp.calculation.config_env import resolve_mcp_config_path

        config_file = tmp_path / "mcp_config.json"
        config_file.write_text("{}")
        with patch.dict(os.environ, {"SERVICE_ENV": "prod"}):
            result = resolve_mcp_config_path(config_file)
        assert isinstance(result, Path)


class TestEnvConfigPackageLevelImport:
    def test_importable_from_package(self):
        from matmaster.mcp.calculation import get_current_env, resolve_mcp_config_path

        assert callable(get_current_env)
        assert callable(resolve_mcp_config_path)
