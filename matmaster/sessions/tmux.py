"""Tmux PS1 parsing helpers -- migrated from evomaster/env/docker.py.

PS1_PATTERN / PS1_BEGIN / PS1_END are used by tmux-based sessions
(Docker, SSH) to extract structured metadata (exit_code, working_dir, pid)
from bash output.

BashMetadata wraps the parsed values and provides factory methods for
generating the PS1 prompt string and parsing its JSON output.
"""

from __future__ import annotations

import json
import re

# PS1 prompt delimiters for structured bash output parsing.
PS1_BEGIN = "\n===PS1JSONBEGIN===\n"
PS1_END = "\n===PS1JSONEND===\n"

# Regex to extract the JSON payload between PS1 delimiters.
PS1_PATTERN = re.compile(
    f"{PS1_BEGIN.strip()}(.*?){PS1_END.strip()}",
    re.DOTALL | re.MULTILINE,
)


class BashMetadata:
    """Structured metadata extracted from tmux PS1 prompt output.

    Attributes:
        exit_code: Exit code of the last command (-1 if unknown).
        working_dir: Current working directory after command execution.
        pid: PID of the last background process (-1 if none).
    """

    def __init__(
        self,
        exit_code: int = -1,
        working_dir: str = "",
        pid: int = -1,
    ) -> None:
        self.exit_code = exit_code
        self.working_dir = working_dir
        self.pid = pid

    @classmethod
    def to_ps1_prompt(cls) -> str:
        """Generate the PS1 prompt configuration string.

        Returns a bash-evaluable prompt that emits JSON metadata
        between PS1_BEGIN and PS1_END delimiters after each command.
        """
        prompt = "===PS1JSONBEGIN==="
        json_str = json.dumps(
            {
                "pid": "$!",
                "exit_code": "$?",
                "working_dir": r"$(pwd)",
            },
            indent=2,
        )
        prompt += json_str.replace('"', r"\"")
        prompt += "===PS1JSONEND===\n"
        return prompt

    @classmethod
    def from_json(cls, json_str: str) -> BashMetadata:
        """Parse BashMetadata from a JSON string.

        Handles malformed input gracefully by returning default values.
        """
        try:
            data = json.loads(json_str)
            return cls(
                exit_code=int(data.get("exit_code", -1)),
                working_dir=data.get("working_dir", ""),
                pid=int(data.get("pid", -1)) if data.get("pid") else -1,
            )
        except (json.JSONDecodeError, ValueError):
            return cls()
