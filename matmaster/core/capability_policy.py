"""DefaultCapabilityPolicy -- tool-level safety checks for the constraint model.

Layer C of the three-layer constraint model. Evaluates tool calls for
safety violations (dangerous bash commands, env credential leaks, etc.).

Bash/Python safety patterns migrated from bash_tool.py (Phase 35-01).
"""

from __future__ import annotations

import re
from typing import Any

from matmaster.types.tool_decision import ToolDecision

# ---- Bash Safety Patterns (migrated from bash_tool.py) ----

_BLOCKED_FIRST_TOKENS = frozenset({"env", "set", "printenv"})

_DANGEROUS_COMMAND_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+/\*",
    r"rm\s+-rf\s+\.",
    r"rm\s+-rf\s+\.\.",
    r":\s*\(\s*\)\s*\{\s*[^}]*\|\s*:.*\}",
    r"mkfs\.?\s",
    r"dd\s+if=.*of=/dev",
    r"\bchmod\s+[0-7]{3,4}\s+/",
    r">\s*/dev/sd",
    r"ssh\s+.*\s+root@",
]
_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _DANGEROUS_COMMAND_PATTERNS]

_DANGEROUS_PYTHON_CONTENT_PATTERNS = [
    (r"\bos\.environ\b", "reads environment variables (os.environ)"),
    (r"\bos\.getenv\b", "reads environment variables (os.getenv)"),
    (r"/proc/self/environ", "reads /proc/self/environ directly"),
    (r"subprocess[^#\n]*\benv\b", "runs 'env' via subprocess"),
    (r"glob\s*\(.*?\.env", "scans for .env files"),
    (r"open\s*\(\s*['\"]\.env", "reads .env file directly"),
    (
        r"(AK|SK|KEY|TOKEN|SECRET|CREDENTIAL|BEARER|ACCESS).{0,40}environ",
        "searches environment for credential-like keys",
    ),
    (
        r"environ.{0,40}(AK|SK|KEY|TOKEN|SECRET|CREDENTIAL|BEARER|ACCESS)",
        "filters environment variables for credentials",
    ),
]
_PYTHON_CONTENT_COMPILED = [
    (re.compile(p, re.IGNORECASE), msg) for p, msg in _DANGEROUS_PYTHON_CONTENT_PATTERNS
]


def is_dangerous_python_content(content: str) -> tuple[bool, str]:
    """Scan Python source code for dangerous patterns (env dump, credential hunting)."""
    if not content or not isinstance(content, str):
        return False, ""
    for pat, msg in _PYTHON_CONTENT_COMPILED:
        if pat.search(content):
            return True, msg
    return False, ""


def is_dangerous_bash_command(command: str) -> tuple[bool, str]:
    """Check if a bash command is dangerous and must not be executed."""
    if not command or not isinstance(command, str):
        return False, ""
    raw = command.strip()
    if not raw:
        return False, ""
    first_token = raw.split(None, 1)[0].lower() if raw else ""
    if first_token in _BLOCKED_FIRST_TOKENS:
        return True, f"'{first_token}' is not allowed (blocked for security)."
    for pat in _COMPILED_PATTERNS:
        if pat.search(command):
            return (
                True,
                "The command contains potentially destructive or unsafe operations.",
            )
    return False, ""


# ---- End Bash Safety Patterns ----


class DefaultCapabilityPolicy:
    """Layer C capability policy: tool-specific safety checks.

    Currently handles:
    - execute_bash: dangerous command pattern detection
    - execute_bash + python -c: Python content safety scanning
    - effect_level: external_effect tools blocked without EXTERNAL_SERVICE plane
    """

    def evaluate(
        self,
        runtime_topology: Any,
        tool_instance: Any,
        tool_args: dict[str, Any],
    ) -> ToolDecision:
        """Evaluate tool call against capability policy.

        Dispatches to tool-specific checks based on tool_name.
        """
        spec = tool_instance.tool_spec
        tool_name = spec.tool_name

        # 1. effect_level constraint
        if spec.effect_level == "external_write":
            active_planes = getattr(runtime_topology, "active_planes", set())
            from matmaster.types.topology import ToolPlane

            if ToolPlane.EXTERNAL_SERVICE not in active_planes:
                return ToolDecision(
                    decision="deny",
                    reason="External effect tools are not allowed in current topology",
                    guidance="This tool makes external service calls. Ensure the session topology permits external access.",
                )

        # 2. Tool-specific safety checks
        if tool_name == "execute_bash":
            return self.check_bash_safety(tool_args)

        return ToolDecision(decision="allow")

    def check_bash_safety(self, tool_args: dict[str, Any]) -> ToolDecision:
        """Check bash command safety. Returns ToolDecision.

        Extracted as public method for direct testing. Called internally
        by evaluate() when tool_name == "execute_bash".
        """
        command = tool_args.get("command", "").strip()

        # Check dangerous bash patterns
        dangerous, reason = is_dangerous_bash_command(command)
        if dangerous:
            return ToolDecision(
                decision="deny",
                reason=f"Blocked: {reason}",
                guidance="This command is blocked for safety. Use a safer alternative.",
            )

        # Check embedded Python content if python -c is used
        if "python -c" in command or "python3 -c" in command:
            # Extract the Python code after -c
            for marker in ("python3 -c ", "python -c "):
                idx = command.find(marker)
                if idx >= 0:
                    python_code = command[idx + len(marker) :]
                    dangerous_py, reason_py = is_dangerous_python_content(python_code)
                    if dangerous_py:
                        return ToolDecision(
                            decision="deny",
                            reason=f"Blocked: embedded Python code {reason_py}",
                            guidance="Avoid accessing environment variables or credentials in inline Python.",
                        )
                    break

        return ToolDecision(decision="allow")
