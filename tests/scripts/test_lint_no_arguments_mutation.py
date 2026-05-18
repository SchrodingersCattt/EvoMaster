"""Tests for the ToolCallData.arguments mutation lint."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_lint_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "lint_no_arguments_mutation.py"
    )
    spec = importlib.util.spec_from_file_location(
        "lint_no_arguments_mutation",
        script_path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_lint_flags_generic_arguments_subscript_mutation(tmp_path):
    lint = _load_lint_module()
    path = tmp_path / "tool.py"
    path.write_text(
        "async def execute(arguments):\n" "    arguments['q'] = 'mutated'\n",
        encoding="utf-8",
    )

    violations = lint.check_file(path)

    assert any("subscript assign arguments[k]" in item[1] for item in violations)


def test_lint_flags_effective_args_method_mutation(tmp_path):
    lint = _load_lint_module()
    path = tmp_path / "runner.py"
    path.write_text(
        "def validate(effective_args):\n"
        "    effective_args.update({'q': 'mutated'})\n",
        encoding="utf-8",
    )

    violations = lint.check_file(path)

    assert any("effective_args.<method>" in item[1] for item in violations)


def test_lint_flags_multiline_model_copy_arguments_update(tmp_path):
    lint = _load_lint_module()
    path = tmp_path / "copy.py"
    path.write_text(
        "def copy(tc):\n"
        "    return tc.model_copy(\n"
        "        update={\n"
        "            'arguments': {'q': 'new'},\n"
        "        }\n"
        "    )\n",
        encoding="utf-8",
    )

    violations = lint.check_file(path)

    assert any(
        "model_copy(update={'arguments': ...})" in item[1] for item in violations
    )
