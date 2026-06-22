"""Tests for deterministic DP-GEN dargs/runtime validators."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from evaluation.validators.dpgen_dargs import check_dpgen_dargs

BASE_PARAM: dict = {
    "default_training_param": {
        "learning_rate": {},
        "loss": {},
        "model": {},
        "training": {},
    },
    "fp_incar": "/path/to/vasp/INCAR",
    "fp_pp_files": ["POTCAR.O", "POTCAR.H"],
    "fp_pp_path": "/path/to/vasp/potcar",
    "fp_style": "vasp",
    "fp_task_max": 50,
    "fp_task_min": 5,
    "init_data_sys": ["/path/to/water/init/deepmd"],
    "mass_map": [15.999, 1.008],
    "model_devi_dt": 0.001,
    "model_devi_f_trust_hi": 0.15,
    "model_devi_f_trust_lo": 0.05,
    "model_devi_jobs": [
        {
            "ensemble": "nvt",
            "nsteps": 2000,
            "sys_idx": [0],
            "temps": [300, 330, 360],
            "trj_freq": 10,
        }
    ],
    "model_devi_skip": 0,
    "numb_models": 4,
    "sys_configs": [["/path/to/water/configs/*.vasp"]],
    "type_map": ["O", "H"],
}

BASE_MACHINE: dict = {
    "api_version": "1.0",
    "deepmd_version": "2.2.8",
    "fp": {
        "command": "vasp_std",
        "machine": {
            "batch_type": "Shell",
            "context_type": "local",
            "local_root": "/path/to/work/vasp",
        },
        "resources": {
            "batch_type": "Shell",
            "cpu_per_node": 16,
            "gpu_per_node": 0,
            "group_size": 4,
            "number_node": 1,
        },
    },
    "model_devi": {
        "command": "lmp -in input.lammps",
        "machine": {
            "batch_type": "Shell",
            "context_type": "local",
            "local_root": "/path/to/work/lammps",
        },
        "resources": {
            "batch_type": "Shell",
            "cpu_per_node": 8,
            "gpu_per_node": 1,
            "group_size": 16,
            "number_node": 1,
        },
    },
    "train": {
        "command": "dp train input.json",
        "machine": {
            "batch_type": "Shell",
            "context_type": "local",
            "local_root": "/path/to/work/train",
        },
        "resources": {
            "batch_type": "Shell",
            "cpu_per_node": 8,
            "gpu_per_node": 1,
            "group_size": 4,
            "number_node": 1,
        },
    },
}


def _write_json(tmp_path: Path, name: str, data: object) -> None:
    (tmp_path / name).write_text(
        json.dumps(data, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _machine_with_list_sections() -> dict:
    data = copy.deepcopy(BASE_MACHINE)
    for section in ("train", "model_devi", "fp"):
        data[section] = [data[section]]
    return data


def test_param_schema_accepts_valid_param(tmp_path: Path) -> None:
    _write_json(tmp_path, "param_output.json", BASE_PARAM)

    ok, reason = check_dpgen_dargs(
        tmp_path,
        filename="param_output.json",
        kind="param",
        check="schema",
        strict=False,
    )

    assert ok, reason


def test_param_schema_rejects_top_level_list(tmp_path: Path) -> None:
    _write_json(tmp_path, "param_output.json", [])

    ok, reason = check_dpgen_dargs(
        tmp_path,
        filename="param_output.json",
        kind="param",
        check="schema",
    )

    assert not ok
    assert "top-level is list" in reason


def test_machine_schema_accepts_canonical_dict_sections(tmp_path: Path) -> None:
    _write_json(tmp_path, "machine_output.json", BASE_MACHINE)

    ok, reason = check_dpgen_dargs(
        tmp_path,
        filename="machine_output.json",
        kind="machine",
        check="schema",
        strict=False,
    )

    assert ok, reason


def test_machine_schema_rejects_deprecated_list_sections(tmp_path: Path) -> None:
    _write_json(tmp_path, "machine_output.json", _machine_with_list_sections())

    ok, reason = check_dpgen_dargs(
        tmp_path,
        filename="machine_output.json",
        kind="machine",
        check="schema",
        strict=False,
    )

    assert not ok
    assert "failed DP-GEN machine dargs validation" in reason


def test_machine_runtime_accepts_canonical_dict_sections(tmp_path: Path) -> None:
    _write_json(tmp_path, "machine_output.json", BASE_MACHINE)

    ok, reason = check_dpgen_dargs(
        tmp_path,
        filename="machine_output.json",
        kind="machine",
        check="runtime",
        strict=False,
    )

    assert ok, reason


def test_machine_runtime_accepts_deprecated_list_sections(tmp_path: Path) -> None:
    _write_json(tmp_path, "machine_output.json", _machine_with_list_sections())

    ok, reason = check_dpgen_dargs(
        tmp_path,
        filename="machine_output.json",
        kind="machine",
        check="runtime",
        strict=False,
    )

    assert ok, reason


def test_machine_runtime_rejects_empty_list_section(tmp_path: Path) -> None:
    data = _machine_with_list_sections()
    data["train"] = []
    _write_json(tmp_path, "machine_output.json", data)

    ok, reason = check_dpgen_dargs(
        tmp_path,
        filename="machine_output.json",
        kind="machine",
        check="runtime",
    )

    assert not ok
    assert "train" in reason
    assert "list is empty" in reason


def test_machine_runtime_rejects_non_object_first_list_item(tmp_path: Path) -> None:
    data = _machine_with_list_sections()
    data["model_devi"] = ["not-an-object"]
    _write_json(tmp_path, "machine_output.json", data)

    ok, reason = check_dpgen_dargs(
        tmp_path,
        filename="machine_output.json",
        kind="machine",
        check="runtime",
    )

    assert not ok
    assert "model_devi" in reason
    assert "first list item" in reason


def test_machine_runtime_rejects_missing_command_machine_or_resources(
    tmp_path: Path,
) -> None:
    cases = [("command", "command"), ("machine", "machine"), ("resources", "resources")]
    for field, expected in cases:
        data = copy.deepcopy(BASE_MACHINE)
        data["fp"].pop(field)
        filename = f"machine_missing_{field}.json"
        _write_json(tmp_path, filename, data)

        ok, reason = check_dpgen_dargs(
            tmp_path,
            filename=filename,
            kind="machine",
            check="runtime",
        )

        assert not ok, field
        assert f"fp.{expected}" in reason


def test_invalid_check_mode_fails(tmp_path: Path) -> None:
    _write_json(tmp_path, "machine_output.json", BASE_MACHINE)

    ok, reason = check_dpgen_dargs(
        tmp_path,
        filename="machine_output.json",
        kind="machine",
        check="unknown",
    )

    assert not ok
    assert "check must be" in reason


def test_runtime_check_is_machine_only(tmp_path: Path) -> None:
    _write_json(tmp_path, "param_output.json", BASE_PARAM)

    ok, reason = check_dpgen_dargs(
        tmp_path,
        filename="param_output.json",
        kind="param",
        check="runtime",
    )

    assert not ok
    assert "only supported for kind 'machine'" in reason
