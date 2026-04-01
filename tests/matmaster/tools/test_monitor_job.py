"""Tests for MonitorJobTool -- Protocol compliance and json_schema validity.

Coverage:
- Gap 26-02-01: MonitorJobTool satisfies BuiltinTool ABC (ClassVars, _execute signature)
- Gap 26-02-02: json_schema has correct structure (type, properties, required list)
- Import cleanliness: importing the package must not trigger evomaster.agent.tools.builtin loads
"""

from __future__ import annotations

import sys
from typing import Any, ClassVar

import pytest

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_registry import Tool


class TestMonitorJobToolProtocolCompliance:
    """Gap 26-02-01: MonitorJobTool satisfies BuiltinTool ABC and Tool Protocol."""

    def test_import_succeeds(self) -> None:
        """Package can be imported without error."""
        from matmaster.tools.builtin.monitor_job import MonitorJobTool  # noqa: F401

    def test_is_subclass_of_builtin_tool(self) -> None:
        from matmaster.tools.builtin.monitor_job import MonitorJobTool

        assert issubclass(MonitorJobTool, BuiltinTool)

    def test_instance_satisfies_tool_protocol(self) -> None:
        """MonitorJobTool(session=None) satisfies the runtime-checkable Tool Protocol."""
        from matmaster.tools.builtin.monitor_job import MonitorJobTool

        tool = MonitorJobTool(session=None)
        assert isinstance(tool, Tool)

    def test_name_classvar_is_monitor_job(self) -> None:
        from matmaster.tools.builtin.monitor_job import MonitorJobTool

        assert MonitorJobTool.name == "monitor_job"

    def test_description_classvar_is_non_empty_string(self) -> None:
        from matmaster.tools.builtin.monitor_job import MonitorJobTool

        assert isinstance(MonitorJobTool.description, str)
        assert len(MonitorJobTool.description) > 0

    def test_json_schema_classvar_exists(self) -> None:
        from matmaster.tools.builtin.monitor_job import MonitorJobTool

        assert isinstance(MonitorJobTool.json_schema, dict)

    def test_execute_method_exists(self) -> None:
        """_execute is a concrete method (not abstract) on MonitorJobTool."""
        from matmaster.tools.builtin.monitor_job import MonitorJobTool

        assert callable(getattr(MonitorJobTool, "_execute", None))

    def test_construction_with_session_none(self) -> None:
        """MonitorJobTool can be constructed with session=None (default)."""
        from matmaster.tools.builtin.monitor_job import MonitorJobTool

        tool = MonitorJobTool(session=None)
        assert tool._session is None

    def test_instance_name_attribute_accessible(self) -> None:
        """Instance attribute lookup for name works (ClassVar accessible on instance)."""
        from matmaster.tools.builtin.monitor_job import MonitorJobTool

        tool = MonitorJobTool(session=None)
        assert tool.name == "monitor_job"


class TestMonitorJobToolSchemaValidity:
    """Gap 26-02-02: json_schema has correct structure per LLM tool-calling spec."""

    @pytest.fixture()
    def schema(self) -> dict[str, Any]:
        from matmaster.tools.builtin.monitor_job import MonitorJobTool

        return MonitorJobTool.json_schema

    def test_schema_type_is_object(self, schema: dict[str, Any]) -> None:
        assert schema.get("type") == "object"

    def test_schema_has_properties_dict(self, schema: dict[str, Any]) -> None:
        assert "properties" in schema
        assert isinstance(schema["properties"], dict)
        assert len(schema["properties"]) > 0

    def test_schema_has_required_list(self, schema: dict[str, Any]) -> None:
        assert "required" in schema
        assert isinstance(schema["required"], list)

    def test_schema_required_contains_job_id(self, schema: dict[str, Any]) -> None:
        assert "job_id" in schema["required"]

    def test_schema_required_contains_software(self, schema: dict[str, Any]) -> None:
        assert "software" in schema["required"]

    def test_schema_job_id_property_is_string_type(self, schema: dict[str, Any]) -> None:
        job_id_prop = schema["properties"].get("job_id", {})
        assert job_id_prop.get("type") == "string"

    def test_schema_software_property_is_string_type(self, schema: dict[str, Any]) -> None:
        software_prop = schema["properties"].get("software", {})
        assert software_prop.get("type") == "string"

    def test_schema_properties_include_expected_optional_keys(
        self, schema: dict[str, Any]
    ) -> None:
        """Schema defines the full parameter surface (spot-check optional keys)."""
        props = schema["properties"]
        for key in ("workspace", "poll_interval", "llm_decision_mode", "timeout_minutes"):
            assert key in props, f"Expected optional property '{key}' missing from schema"


class TestMonitorJobToolImportCleanliness:
    """Importing the monitor_job package must not trigger evomaster.agent.tools.builtin loads."""

    def test_no_evomaster_agent_tools_builtin_loaded_after_import(self) -> None:
        """
        Verifies the lazy-import contract from 26-02 plan truths.
        evomaster.agent.tools.builtin.* should NOT appear in sys.modules after
        importing matmaster.tools.builtin.monitor_job.
        """
        # Re-import to ensure the module is present (may already be loaded)
        import matmaster.tools.builtin.monitor_job  # noqa: F401

        leaked = [
            k for k in sys.modules if "evomaster.agent.tools.builtin" in k
        ]
        assert not leaked, (
            f"evomaster.agent.tools.builtin modules unexpectedly loaded: {leaked}"
        )

    def test_no_evomaster_agent_session_ssh_loaded_after_import(self) -> None:
        """
        evomaster.agent.session.ssh (SSHSession) must not be imported at module load time.
        """
        import matmaster.tools.builtin.monitor_job  # noqa: F401

        leaked = [
            k for k in sys.modules if "evomaster.agent.session.ssh" in k
        ]
        assert not leaked, (
            f"evomaster.agent.session.ssh unexpectedly loaded: {leaked}"
        )
