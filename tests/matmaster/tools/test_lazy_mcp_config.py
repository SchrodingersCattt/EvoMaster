from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from matmaster.mcp.manager import MCPConcurrencyPolicy
from matmaster.tools.lazy_mcp import (
    _DEFAULT_CALCULATION_SYNC_MCP_TOOL_TIMEOUT,
    configure_mcp_manager,
    resolve_lazy_mcp_tool_timeout,
)


class FakeMCPManager:
    """Minimal MCPToolManager mock for configure_mcp_manager tests."""

    def __init__(self):
        self.calculation_preflight_servers: set = set()
        self.calculation_preflight_factory = None
        self.sync_tools_by_server: dict = {}
        self.tool_include_only: dict = {}
        self.concurrency_defaults_by_transport: dict = {}
        self.concurrency_by_server: dict = {}


class TestConfigureMCPManager:
    def test_sets_calculation_preflight_servers_from_explicit_list(self):
        manager = FakeMCPManager()
        config = {
            "calculation_preflight": "calculation",
            "calculation_servers": ["mat_sg", "mat_dpa"],
        }
        configure_mcp_manager(manager, config)
        assert manager.calculation_preflight_servers == {"mat_sg", "mat_dpa"}

    def test_calculation_preflight_servers_fallback_to_all_servers(self):
        """When calculation_servers is absent, fallback to all_server_names."""
        manager = FakeMCPManager()
        config = {"calculation_preflight": "calculation"}
        configure_mcp_manager(
            manager, config, all_server_names={"mat_sg", "mat_sn"}
        )
        assert manager.calculation_preflight_servers == {
            "mat_sg",
            "mat_sn",
        }

    def test_sync_tools_only_inside_calculation_branch(self):
        """sync_tools_by_server is only set when calculation_preflight == calculation."""
        manager = FakeMCPManager()
        config = {
            "calculation_preflight": "calculation",
            "calculation_executors": {
                "mat_sg": {"sync_tools": ["build_bulk_structure_by_wyckoff"]},
            },
        }
        configure_mcp_manager(manager, config)
        assert (
            "build_bulk_structure_by_wyckoff" in manager.sync_tools_by_server["mat_sg"]
        )

    def test_sync_tools_not_set_without_calculation(self):
        """Without calculation_preflight=calculation, sync_tools_by_server stays empty."""
        manager = FakeMCPManager()
        config = {
            "calculation_executors": {
                "mat_sg": {"sync_tools": ["build_bulk_structure_by_wyckoff"]},
            },
        }
        configure_mcp_manager(manager, config)
        assert manager.sync_tools_by_server == {}

    def test_sets_tool_include_only(self):
        manager = FakeMCPManager()
        config = {
            "tool_include_only": {
                "mat_sn": ["web-search", "search-papers-enhanced"],
                "bad_entry": "not_a_list",
            }
        }
        configure_mcp_manager(manager, config)
        assert manager.tool_include_only["mat_sn"] == [
            "web-search",
            "search-papers-enhanced",
        ]
        assert manager.tool_include_only["bad_entry"] == []

    def test_sets_transport_level_concurrency_defaults(self):
        manager = FakeMCPManager()
        config = {
            "mcp_concurrency": {
                "defaults": {
                    "HTTP": {
                        "mode": "multiplex",
                        "max_inflight": 6,
                        "max_pending_requests": 24,
                    },
                    "sse": {
                        "mode": "serial",
                        "max_inflight": 1,
                        "max_pending_requests": 8,
                    },
                }
            }
        }

        configure_mcp_manager(manager, config)

        assert manager.concurrency_defaults_by_transport == {
            "http": MCPConcurrencyPolicy(
                mode="multiplex",
                max_inflight=6,
                max_pending_requests=24,
            ),
            "sse": MCPConcurrencyPolicy(
                mode="serial",
                max_inflight=1,
                max_pending_requests=8,
            ),
        }

    def test_server_override_wins_over_transport_default(self):
        manager = FakeMCPManager()
        config = {
            "mcp_concurrency": {
                "defaults": {
                    "http": {
                        "mode": "serial",
                        "max_inflight": 1,
                        "max_pending_requests": 4,
                    }
                },
                "servers": {
                    "mat_struct_db": {
                        "mode": "multiplex",
                        "max_inflight": 5,
                        "max_pending_requests": 20,
                    }
                },
            }
        }

        configure_mcp_manager(manager, config)

        assert manager.concurrency_defaults_by_transport["http"] == (
            MCPConcurrencyPolicy(
                mode="serial",
                max_inflight=1,
                max_pending_requests=4,
            )
        )
        assert manager.concurrency_by_server == {
            "mat_struct_db": MCPConcurrencyPolicy(
                mode="multiplex",
                max_inflight=5,
                max_pending_requests=20,
            )
        }

    def test_ignores_invalid_concurrency_entries(self):
        manager = FakeMCPManager()
        config = {
            "mcp_concurrency": {
                "defaults": {
                    "http": {
                        "mode": "parallel",
                        "max_inflight": 2,
                        "max_pending_requests": 8,
                    },
                    "sse": {
                        "mode": "serial",
                        "max_inflight": 0,
                        "max_pending_requests": 8,
                    },
                },
                "servers": {
                    "mat_struct_db": {
                        "mode": "multiplex",
                        "max_inflight": 3,
                    },
                    "mat_nmr": "bad",
                },
            },
            "tool_include_only": {"mat_struct_db": ["search_structures"]},
        }

        configure_mcp_manager(manager, config)

        assert manager.concurrency_defaults_by_transport == {}
        assert manager.concurrency_by_server == {}
        assert manager.tool_include_only == {
            "mat_struct_db": ["search_structures"]
        }

    def test_invalid_concurrency_entries_emit_warnings_with_config_paths(self, caplog):
        manager = FakeMCPManager()
        config = {
            "mcp_concurrency": {
                "defaults": {
                    "http": {
                        "mode": "parallel",
                        "max_inflight": 2,
                        "max_pending_requests": 8,
                    }
                },
                "servers": {
                    "mat_struct_db": {
                        "mode": "multiplex",
                        "max_inflight": 3,
                    }
                },
            }
        }

        with caplog.at_level("WARNING"):
            configure_mcp_manager(manager, config)

        assert manager.concurrency_defaults_by_transport == {}
        assert manager.concurrency_by_server == {}
        assert "mcp_concurrency.defaults.http" in caplog.text
        assert "mcp_concurrency.servers.mat_struct_db" in caplog.text

    @pytest.mark.parametrize("bad_concurrency", ["bad", []])
    def test_non_dict_top_level_concurrency_emits_warning_and_is_ignored(
        self, caplog, bad_concurrency
    ):
        manager = FakeMCPManager()
        config = {"mcp_concurrency": bad_concurrency}

        with caplog.at_level("WARNING"):
            configure_mcp_manager(manager, config)

        assert manager.concurrency_defaults_by_transport == {}
        assert manager.concurrency_by_server == {}
        assert "mcp_concurrency" in caplog.text
        assert "expected dict" in caplog.text

    def test_empty_config_noop(self):
        manager = FakeMCPManager()
        configure_mcp_manager(manager, {})
        assert manager.calculation_preflight_servers == set()
        assert manager.sync_tools_by_server == {}
        assert manager.tool_include_only == {}
        assert manager.concurrency_defaults_by_transport == {}
        assert manager.concurrency_by_server == {}

    def test_calculation_preflight_factory_uses_client_namespace(self):
        """Verify factory uses matmaster.mcp.calculation.preflight."""
        manager = FakeMCPManager()
        config = {
            "calculation_preflight": "calculation",
            "calculation_servers": ["mat_sg"],
        }
        with patch("matmaster.mcp.calculation.preflight.CalculationPreflight") as cls:
            cls.return_value = MagicMock()
            configure_mcp_manager(manager, config)

        assert manager.calculation_preflight_factory is not None
        manager.calculation_preflight_factory()
        cls.assert_called_once_with(config.get("calculation_executors") or {})


class TestResolveLazyMCPToolTimeout:
    def test_prefers_explicit_server_timeout(self):
        timeout = resolve_lazy_mcp_tool_timeout(
            {
                "tool_timeouts": {"mat_sg": 7},
                "calculation_executors": {
                    "mat_sg": {
                        "executor": {"type": "dispatcher"},
                        "sync_tools": ["build_bulk"],
                    }
                },
            },
            server_name="mat_sg",
            remote_tool_name="build_bulk",
        )
        assert timeout == 7.0

    def test_executor_backed_sync_tool_uses_fast_default(self):
        timeout = resolve_lazy_mcp_tool_timeout(
            {
                "calculation_executors": {
                    "mat_sg": {
                        "executor": {"type": "dispatcher"},
                        "sync_tools": ["build_bulk"],
                    }
                }
            },
            server_name="mat_sg",
            remote_tool_name="build_bulk",
        )
        assert timeout == _DEFAULT_CALCULATION_SYNC_MCP_TOOL_TIMEOUT

    def test_null_executor_sync_tool_keeps_global_default(self):
        timeout = resolve_lazy_mcp_tool_timeout(
            {
                "calculation_executors": {
                    "mat_nmr": {
                        "executor": None,
                        "sync_tools": ["NMR_search_tool"],
                    }
                }
            },
            server_name="mat_nmr",
            remote_tool_name="NMR_search_tool",
        )
        assert timeout is None

    def test_non_sync_tool_keeps_global_default(self):
        timeout = resolve_lazy_mcp_tool_timeout(
            {
                "calculation_executors": {
                    "mat_sg": {
                        "executor": {"type": "dispatcher"},
                        "sync_tools": ["build_bulk"],
                    }
                }
            },
            server_name="mat_sg",
            remote_tool_name="submit_build_bulk",
        )
        assert timeout is None
