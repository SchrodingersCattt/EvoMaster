"""Single source of truth for DevShell eval default LLM route keys.

These strings must exist as ``routes`` in ``config/llm_config.yaml``. Centralizing
them here avoids drift between the inner runner ``run_devshell_eval.py`` (which owns
the CLI defaults), the outer ``run_devshell_agent_loop.py`` (which forwards the same
flags), and the MCP tool schema descriptions in ``mcp_tool_schemas.py``.
"""

from __future__ import annotations

#: Primary route for the inner ``mm-devshell run --model`` (Bedrock Converse).
DEFAULT_DEVSHELL_MODEL_ROUTE = "bedrock-claude-opus"

#: LiteLLM route retried once per task when devshell logs look like a
#: Bedrock/botocore transport error (read timeout, etc.).
DEFAULT_DEVSHELL_FALLBACK_MODEL_ROUTE = "global.anthropic.claude-opus-4-6-v1"
