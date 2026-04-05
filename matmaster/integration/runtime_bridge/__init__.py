"""Runtime credential bridge -- unified credential resolution and env projection.

Public API:
- ``resolve_service_credentials`` -- resolve credentials for a service
- ``build_service_env`` -- build env dict from resolved credentials
- ``resolve_output_path`` -- classify output paths (local / remote)
- ``inject_mcp_args`` -- inject credentials into MCP executor templates
- ``ResolvedCredential`` -- resolved credential data model
- ``OutputPathDecision`` -- output path classification data model
"""

from matmaster.integration.runtime_bridge.bridge import (
    build_service_env,
    inject_mcp_args,
    resolve_output_path,
    resolve_service_credentials,
)
from matmaster.integration.runtime_bridge.models import (
    OutputPathDecision,
    ResolvedCredential,
)

__all__ = [
    "OutputPathDecision",
    "ResolvedCredential",
    "build_service_env",
    "inject_mcp_args",
    "resolve_output_path",
    "resolve_service_credentials",
]
