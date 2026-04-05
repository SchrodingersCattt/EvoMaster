"""Tests for matmaster.integration.bohrium_env -- Bohrium constants, credentials, and env helpers.

Covers:
- BOHRIUM_OPENAPI_HOST constant (default + env override + trailing slash strip)
- get_bohrium_credentials (env fallback + param override)
- get_bohrium_storage_config (structure)
- inject_bohrium_executor (dispatcher / local / deep copy safety)
- build_bohrium_skill_remote_env (session credential extraction)
- BohriumSetupResult (NamedTuple construction)
"""

from __future__ import annotations

import copy
import importlib


class TestBohriumOpenapiHost:
    """BOHRIUM_OPENAPI_HOST constant behavior."""

    def test_bohrium_openapi_host_default(self, monkeypatch):
        """Without BOHRIUM_BASE_URL env var, returns default host."""
        monkeypatch.delenv("BOHRIUM_BASE_URL", raising=False)
        monkeypatch.delenv("SERVICE_ENV", raising=False)
        # Re-import to pick up env change (module-level constant)
        import matmaster.integration.bohrium_env as mod

        mod = importlib.reload(mod)
        assert mod.BOHRIUM_OPENAPI_HOST == "https://openapi.test.dp.tech"

    def test_bohrium_openapi_host_from_service_env(self, monkeypatch):
        monkeypatch.delenv("BOHRIUM_BASE_URL", raising=False)
        monkeypatch.setenv("SERVICE_ENV", "prod")
        import matmaster.integration.bohrium_env as mod

        mod = importlib.reload(mod)
        assert mod.BOHRIUM_OPENAPI_HOST == "https://open.bohrium.com"

    def test_bohrium_openapi_host_from_env(self, monkeypatch):
        """BOHRIUM_BASE_URL overrides default; trailing slash is stripped."""
        monkeypatch.setenv("BOHRIUM_BASE_URL", "https://test.dp.tech/")
        import matmaster.integration.bohrium_env as mod

        mod = importlib.reload(mod)
        assert mod.BOHRIUM_OPENAPI_HOST == "https://test.dp.tech"


class TestGetBohriumCredentials:
    """get_bohrium_credentials: env fallback + param override."""

    def test_get_bohrium_credentials_from_env(self, monkeypatch):
        monkeypatch.setenv("BOHRIUM_ACCESS_KEY", "ak123")
        monkeypatch.setenv("BOHRIUM_PROJECT_ID", "42")
        monkeypatch.setenv("BOHRIUM_USER_ID", "7")
        from matmaster.integration.bohrium_env import get_bohrium_credentials

        result = get_bohrium_credentials()
        assert result["access_key"] == "ak123"
        assert result["project_id"] == 42
        assert result["user_id"] == 7

    def test_get_bohrium_credentials_with_params(self, monkeypatch):
        """Explicit params override environment variables."""
        monkeypatch.setenv("BOHRIUM_ACCESS_KEY", "env_key")
        monkeypatch.setenv("BOHRIUM_PROJECT_ID", "99")
        monkeypatch.setenv("BOHRIUM_USER_ID", "88")
        from matmaster.integration.bohrium_env import get_bohrium_credentials

        result = get_bohrium_credentials(
            access_key="param_key", project_id=10, user_id=20
        )
        assert result["access_key"] == "param_key"
        assert result["project_id"] == 10
        assert result["user_id"] == 20


class TestGetBohriumStorageConfig:
    """get_bohrium_storage_config: structure validation."""

    def test_get_bohrium_storage_config_structure(self, monkeypatch):
        monkeypatch.setenv("BOHRIUM_ACCESS_KEY", "ak")
        monkeypatch.setenv("BOHRIUM_PROJECT_ID", "1")
        from matmaster.integration.bohrium_env import get_bohrium_storage_config

        result = get_bohrium_storage_config()
        assert result["type"] == "https"
        assert result["plugin"]["type"] == "bohrium"
        assert result["plugin"]["app_key"] == "agent"
        assert result["plugin"]["access_key"] == "ak"
        assert result["plugin"]["project_id"] == 1


class TestInjectBohriumExecutor:
    """inject_bohrium_executor: dispatcher / local type handling + deep copy safety."""

    def test_inject_bohrium_executor_dispatcher(self, monkeypatch):
        """type=dispatcher injects remote_profile and resources.envs."""
        monkeypatch.setenv("BOHRIUM_ACCESS_KEY", "ak")
        monkeypatch.setenv("BOHRIUM_PROJECT_ID", "1")
        monkeypatch.setenv("BOHRIUM_USER_ID", "5")
        from matmaster.integration.bohrium_env import inject_bohrium_executor

        template = {"type": "dispatcher"}
        result = inject_bohrium_executor(template, user_no="U001")
        rp = result["machine"]["remote_profile"]
        assert rp["access_key"] == "ak"
        assert rp["project_id"] == 1
        envs = result["resources"]["envs"]
        assert envs["BOHRIUM_PROJECT_ID"] == 1
        assert envs["BOHRIUM_USER_ID"] == "5"
        assert envs["BOHRIUM_USER_NO"] == "U001"

    def test_inject_bohrium_executor_local(self, monkeypatch):
        """type=local injects env field."""
        monkeypatch.setenv("BOHRIUM_ACCESS_KEY", "local_ak")
        monkeypatch.setenv("BOHRIUM_PROJECT_ID", "2")
        from matmaster.integration.bohrium_env import inject_bohrium_executor

        template = {"type": "local"}
        result = inject_bohrium_executor(template)
        assert result["env"]["BOHRIUM_PROJECT_ID"] == "2"
        assert result["env"]["BOHRIUM_ACCESS_KEY"] == "local_ak"

    def test_inject_bohrium_executor_deep_copies(self, monkeypatch):
        """Original executor_template must not be modified."""
        monkeypatch.setenv("BOHRIUM_ACCESS_KEY", "ak")
        monkeypatch.setenv("BOHRIUM_PROJECT_ID", "1")
        from matmaster.integration.bohrium_env import inject_bohrium_executor

        template = {"type": "dispatcher", "machine": {"existing": True}}
        original = copy.deepcopy(template)
        inject_bohrium_executor(template)
        assert template == original


class TestBuildBohriumSkillRemoteEnv:
    """build_bohrium_skill_remote_env: session credential extraction."""

    def test_returns_env_dict_with_valid_credentials(self, monkeypatch):
        # Ensure module uses the right BOHRIUM_OPENAPI_HOST
        monkeypatch.delenv("BOHRIUM_BASE_URL", raising=False)
        monkeypatch.setenv("SERVICE_ENV", "uat")
        import matmaster.integration.bohrium_env as mod

        mod = importlib.reload(mod)

        class FakeSession:
            _bohrium_credentials = {
                "access_key": "ak",
                "project_id": 42,
                "user_id": 5,
                "user_no": "U001",
            }

        result = mod.build_bohrium_skill_remote_env(FakeSession())
        assert result["BOHRIUM_ACCESS_KEY"] == "ak"
        assert result["BOHRIUM_PROJECT_ID"] == "42"
        assert result["BOHRIUM_BASE_URL"] == "https://openapi.uat.dp.tech"
        assert result["BOHRIUM_USER_ID"] == "5"
        assert result["BOHRIUM_USER_NO"] == "U001"

    def test_returns_empty_dict_without_credentials(self):
        from matmaster.integration.bohrium_env import build_bohrium_skill_remote_env

        class FakeSession:
            pass

        assert build_bohrium_skill_remote_env(FakeSession()) == {}

    def test_returns_empty_dict_with_empty_access_key(self):
        from matmaster.integration.bohrium_env import build_bohrium_skill_remote_env

        class FakeSession:
            _bohrium_credentials = {"access_key": "", "project_id": 1}

        assert build_bohrium_skill_remote_env(FakeSession()) == {}


class TestBohriumSetupResult:
    """BohriumSetupResult: NamedTuple construction."""

    def test_bohrium_setup_result_is_namedtuple(self):
        from matmaster.integration.bohrium_env import BohriumSetupResult

        # Positional construction
        r1 = BohriumSetupResult(True, None, None, "/tmp/work", "ssh")
        assert r1.ssh_attached is True
        assert r1.execution_workdir == "/tmp/work"

        # Keyword construction
        r2 = BohriumSetupResult(
            ssh_attached=False,
            abort_result=({"error": "x"}, 500),
            execution_session=None,
            execution_workdir=None,
            session_type=None,
        )
        assert r2.ssh_attached is False
        assert r2.abort_result == ({"error": "x"}, 500)
