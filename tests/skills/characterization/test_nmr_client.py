from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_nmr_client():
    path = (
        Path(__file__).resolve().parents[3]
        / "matmaster/skills/nmr-analysis/scripts/nmr_client.py"
    )
    spec = importlib.util.spec_from_file_location("test_nmr_client", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_search_uses_source_verified_solver_payload(tmp_path, monkeypatch) -> None:
    client = _load_nmr_client()
    captured: dict = {}
    monkeypatch.setattr(
        client,
        "_call_service",
        lambda payload, service_url: captured.update(
            payload=payload, service_url=service_url
        )
        or {"code": 0, "data": []},
    )

    result = client.search(
        SimpleNamespace(
            h_shifts="[2.1, 7.64]",
            c_shifts="[30.0, 205.0]",
            allowed_elements="C,H,O,N",
            topk=5,
            output_dir=str(tmp_path),
        )
    )

    assert result["success"] is True
    assert captured["service_url"] == client.DEFAULT_SERVICE_URL
    input_data = captured["payload"]["input_data"]
    assert input_data["search"] == {
        "H_shifts": [2.1, 7.64],
        "C_shifts": [30.0, 205.0],
        "allowed_elements": ["C", "H", "O", "N"],
        "num_search": 1000,
        "topk": 5,
    }
    assert input_data["config"]["num_search"] == 1000
    assert input_data["config"]["topk"] == 1000


def test_predict_omits_scores_without_reference_shifts(tmp_path, monkeypatch) -> None:
    client = _load_nmr_client()
    monkeypatch.setattr(
        client,
        "_call_service",
        lambda payload, service_url: {
            "code": 0,
            "data": [
                {
                    "smiles": "CCO",
                    "smiles_with_atom_order": "CCO",
                    "atoms_shift": [],
                    "H_score": 0.8,
                    "C_score": 0.7,
                    "score": 0.75,
                }
            ],
        },
    )
    monkeypatch.setattr(client, "_draw_svg", lambda item: None)
    monkeypatch.setattr(client, "_write_xyz", lambda smiles, path: False)

    result = client.predict(
        SimpleNamespace(
            smiles=["CCO"],
            molecule_file=[],
            h_shifts=None,
            c_shifts=None,
            output_dir=str(tmp_path),
        )
    )

    assert result["success"] is True
    assert result["data"][0] == {
        "smiles": "CCO",
        "markdown": "## 分子 1\n\n**SMILES**: `CCO`\n\n**说明**: 已生成 NMR 化学位移预测\n",
    }


def test_reverse_prediction_uses_topk_in_solver_config(tmp_path, monkeypatch) -> None:
    client = _load_nmr_client()
    captured: dict = {}
    monkeypatch.setattr(
        client,
        "_call_service",
        lambda payload, service_url: captured.update(payload=payload)
        or {"code": 0, "data": []},
    )

    client.reverse_predict(
        SimpleNamespace(
            h_shifts="[1.2]",
            c_shifts=None,
            allowed_elements="C,H,O",
            formula="C2H6O",
            topk=3,
            output_dir=str(tmp_path),
        )
    )

    input_data = captured["payload"]["input_data"]
    assert input_data["reverse_predict"] == {
        "H_shifts": [1.2],
        "constraints": {"formula": "C2H6O", "allowed_elements": ["C", "H", "O"]},
    }
    assert input_data["config"]["topk"] == 3


def test_predict_accepts_xyz_with_smiles_comment(tmp_path, monkeypatch) -> None:
    client = _load_nmr_client()
    xyz = tmp_path / "ethanol.xyz"
    xyz.write_text(
        "3\nSMILES: CCO\nC 0 0 0\nC 1 0 0\nO 2 0 0\n",
        encoding="utf-8",
    )
    captured: dict = {}
    monkeypatch.setattr(
        client,
        "_call_service",
        lambda payload, service_url: captured.update(payload=payload)
        or {"code": 0, "data": []},
    )

    result = client.predict(
        SimpleNamespace(
            smiles=[],
            molecule_file=[str(xyz)],
            h_shifts=None,
            c_shifts=None,
            output_dir=str(tmp_path / "out"),
        )
    )

    assert result["success"] is True
    assert captured["payload"]["input_data"]["predict"]["smiles_list"] == ["CCO"]
