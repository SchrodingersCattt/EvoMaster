"""Tests for ``ci/baseline_eval_preset.py`` (questions_mode resolution)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PRESET_PY = REPO_ROOT / "ci" / "baseline_eval_preset.py"


def _load_preset_module():
    spec = importlib.util.spec_from_file_location("_baseline_eval_preset", PRESET_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def preset_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(REPO_ROOT)
    return _load_preset_module()


def test_resolve_questions_mode_reads_yaml_default_preset(preset_module) -> None:
    assert preset_module.resolve_questions_mode() == "preset"


def test_resolve_questions_mode_from_yaml_score_summary(
    preset_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_load() -> dict:
        return {"questions_mode": "score_summary_missing_cc"}

    monkeypatch.setattr(preset_module, "load_preset_file", fake_load)
    assert preset_module.resolve_questions_mode() == "score_summary_missing_cc"


def test_resolve_questions_mode_invalid_yaml_falls_back_preset(
    preset_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_load() -> dict:
        return {"questions_mode": "unknown_mode"}

    monkeypatch.setattr(preset_module, "load_preset_file", fake_load)
    assert preset_module.resolve_questions_mode() == "preset"
