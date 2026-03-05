"""Dangerous command detection for execute_bash.

Used by ToolGuard (mat_master) and BashTool so that dangerous commands
(e.g. env, rm -rf /) are blocked at both guard and execution layers.
Aligned with compliance-guardian DANGEROUS_COMMAND_PATTERNS where applicable.
"""

from __future__ import annotations

import re
from typing import Tuple

# Commands whose first token must not be executed (env leak, etc.)
BLOCKED_FIRST_TOKENS = frozenset({
    "env",       # leaks environment variables including secrets
    "set",       # shell builtin, can leak env
    "printenv",  # same as env
})

# Dangerous patterns (matched against full command, case-insensitive).
# Kept in sync with compliance-guardian check_compliance.py where applicable.
DANGEROUS_COMMAND_PATTERNS = [
    r"rm\s+-rf\s+/",           # rm -rf /
    r"rm\s+-rf\s+/\*",          # rm -rf /*
    r"rm\s+-rf\s+\.",           # rm -rf .
    r"rm\s+-rf\s+\.\.",         # rm -rf ..
    r":\s*\(\s*\)\s*\{\s*[^}]*\|\s*:.*\}",  # fork bomb
    r"mkfs\.?\s",
    r"dd\s+if=.*of=/dev",
    r"\bchmod\s+[0-7]{3,4}\s+/",
    r">\s*/dev/sd",
    r"ssh\s+.*\s+root@",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in DANGEROUS_COMMAND_PATTERNS]

# Dangerous patterns for Python script file_text content
DANGEROUS_PYTHON_CONTENT_PATTERNS = [
    (r"\bos\.environ\b",              "reads environment variables (os.environ)"),
    (r"\bos\.getenv\b",               "reads environment variables (os.getenv)"),
    (r"/proc/self/environ",           "reads /proc/self/environ directly"),
    (r"subprocess[^#\n]*\benv\b",     "runs 'env' via subprocess"),
    (r"glob\s*\(.*?\.env",            "scans for .env files"),
    (r"open\s*\(\s*['\"]\.env",       "reads .env file directly"),
    # credential hunting
    (r"(AK|SK|KEY|TOKEN|SECRET|CREDENTIAL|BEARER|ACCESS).{0,40}environ",
     "searches environment for credential-like keys"),
    (r"environ.{0,40}(AK|SK|KEY|TOKEN|SECRET|CREDENTIAL|BEARER|ACCESS)",
     "filters environment variables for credentials"),
]

_PYTHON_CONTENT_COMPILED = [
    (re.compile(p, re.IGNORECASE), msg) for p, msg in DANGEROUS_PYTHON_CONTENT_PATTERNS
]


def is_dangerous_python_content(content: str) -> Tuple[bool, str]:
    """Scan Python source code for dangerous patterns (env dump, credential hunting).
    Used by ToolGuard before creating or executing agent-written scripts."""
    if not content or not isinstance(content, str):
        return False, ""
    for pat, msg in _PYTHON_CONTENT_COMPILED:
        if pat.search(content):
            return True, msg
    return False, ""


def is_dangerous_bash_command(command: str) -> Tuple[bool, str]:
    """Check if a bash command is dangerous and must not be executed.

    Returns:
        (True, reason) if the command should be blocked,
        (False, "") if the command is allowed.
    """
    if not command or not isinstance(command, str):
        return False, ""

    raw = command.strip()
    if not raw:
        return False, ""

    # 1) Block by first token (e.g. env, printenv)
    first_token = raw.split(None, 1)[0].lower() if raw else ""
    if first_token in BLOCKED_FIRST_TOKENS:
        return True, f"'{first_token}' is not allowed (blocked for security)."

    # 2) Block by dangerous patterns
    for pat in _COMPILED_PATTERNS:
        if pat.search(command):
            return True, "The command contains potentially destructive or unsafe operations."

    return False, ""
