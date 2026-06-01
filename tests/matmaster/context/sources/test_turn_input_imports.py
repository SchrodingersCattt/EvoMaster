from __future__ import annotations

import subprocess
import sys


def test_turn_input_module_cold_imports_without_type_contract_cycle() -> None:
    script = (
        "import importlib\n"
        "importlib.import_module('matmaster.context.sources.turn_input')\n"
        "from matmaster.types import AgentKernelSpec, CompactionConfig\n"
        "assert AgentKernelSpec.__name__ == 'AgentKernelSpec'\n"
        "assert CompactionConfig.__name__ == 'CompactionConfig'\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
