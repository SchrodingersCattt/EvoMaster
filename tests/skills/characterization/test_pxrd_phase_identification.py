from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_xrd_client():
    path = (
        Path(__file__).resolve().parents[3]
        / "matmaster/skills/pxrd-phase-identification/scripts/pxrd_phase_identification.py"
    )
    spec = importlib.util.spec_from_file_location("test_xrd_client", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_identify_rejects_raw_pattern(tmp_path) -> None:
    client = _load_xrd_client()
    raw_file = tmp_path / "pattern.xy"
    raw_file.write_text("10 100\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Run the parse subcommand"):
        client.identify_phases(
            input_path=raw_file,
            output_dir=tmp_path / "output",
            include_any=[],
            include_all=[],
            exclude=[],
            top_n=5,
            show_top_n=1,
        )


def test_parse_pattern_uploads_and_writes_artifacts(tmp_path, monkeypatch) -> None:
    client = _load_xrd_client()
    fixture = (
        Path(__file__).resolve().parents[3]
        / "evaluation/question_bank/data/PXRD_pawley_303K_001_20260502_v1/pxrd_303K.xy"
    )
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "result": {
                    "status": "success",
                    "file_name": fixture.name,
                    "peaks_count": 1,
                },
                "artifacts": [
                    {
                        "key": "raw_data_path",
                        "name": "pxrd_303K_raw_data.csv",
                        "content": "2Theta,Intensity,Baseline\n10,100,1\n",
                    },
                    {
                        "key": "features_path",
                        "name": "pxrd_303K_features.csv",
                        "content": "2Theta[°],Intensity(a.u.),FWHM,Grain size\n",
                    },
                    {
                        "key": "chart_option_path",
                        "name": "pxrd_303K_chart_option.echarts",
                        "content": "{}\n",
                    },
                ],
            }

    def fake_post(url, *, files, data, timeout, headers=None):
        captured.update(url=url, filename=files["file"][0], data=data)
        return FakeResponse()

    monkeypatch.setattr(client.httpx, "post", fake_post)
    monkeypatch.setenv("XRD_SERVICE_URL", "http://xrd-service.internal")
    monkeypatch.setenv("BOHRIUM_USER_ID", "12345")
    monkeypatch.setenv("BOHRIUM_ORG_ID", "67890")

    result = client.parse_pattern(fixture, tmp_path, "Non_removal baseline")

    assert result["status"] == "success"
    assert captured == {
        "url": "http://xrd-service.internal/v1/xrd/parse",
        "filename": "pxrd_303K.xy",
        "data": {
            "baseline_mode": "Non_removal baseline",
            "profile": "standard",
            "trace_ids": "",
            "wavelength": 1.540598,
        },
    }
    raw_data = Path(result["raw_data_path"])
    assert raw_data.is_file()
    assert (
        raw_data.read_text(encoding="utf-8").splitlines()[0]
        == "2Theta,Intensity,Baseline"
    )
    assert Path(result["features_path"]).is_file()
    assert Path(result["chart_option_path"]).is_file()


def test_simulate_pattern_uploads_cif_and_writes_artifact(
    tmp_path, monkeypatch
) -> None:
    client = _load_xrd_client()
    cif = tmp_path / "model.cif"
    cif.write_text("data_test\n", encoding="utf-8")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "result": {"status": "success", "radiation": "cu-ka1"},
                "artifacts": [
                    {
                        "key": "simulated_pattern_path",
                        "name": "model_simulated_pxrd.csv",
                        "content": "2Theta,NormalizedIntensity\n28.4,100\n",
                    }
                ],
            }

    captured: dict = {}

    def fake_post(url, *, files, data, timeout, headers=None):
        captured.update(url=url, filename=files["cif"][0], data=data)
        return FakeResponse()

    monkeypatch.setattr(client.httpx, "post", fake_post)
    monkeypatch.setenv("XRD_SERVICE_URL", "http://xrd-service.internal")
    monkeypatch.setenv("BOHRIUM_USER_ID", "12345")
    monkeypatch.setenv("BOHRIUM_ORG_ID", "67890")

    result = client.simulate_pattern(cif, tmp_path, "cu-ka1", None, 5.0, 90.0)

    assert result["status"] == "success"
    assert captured == {
        "url": "http://xrd-service.internal/v1/xrd/simulate",
        "filename": "model.cif",
        "data": {"radiation": "cu-ka1", "two_theta_min": 5.0, "two_theta_max": 90.0},
    }
    assert Path(result["artifacts"]["simulated_pattern_path"]).is_file()


def test_compare_pattern_uploads_pattern_and_cif(tmp_path, monkeypatch) -> None:
    client = _load_xrd_client()
    pattern = tmp_path / "pattern.xy"
    pattern.write_text("10 1\n", encoding="utf-8")
    cif = tmp_path / "model.cif"
    cif.write_text("data_test\n", encoding="utf-8")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "result": {"status": "success", "trace_count": 1},
                "artifacts": [
                    {
                        "key": "refinement_handoff_path",
                        "name": "xrd_refinement_handoff.json",
                        "content": "{}\n",
                    }
                ],
            }

    captured: dict = {}

    def fake_post(url, *, files, data, timeout, headers=None):
        captured.update(
            url=url, filenames=sorted(item[0] for item in files.values()), data=data
        )
        return FakeResponse()

    monkeypatch.setattr(client.httpx, "post", fake_post)
    monkeypatch.setenv("XRD_SERVICE_URL", "http://xrd-service.internal")
    monkeypatch.setenv("BOHRIUM_USER_ID", "12345")
    monkeypatch.setenv("BOHRIUM_ORG_ID", "67890")

    result = client.compare_pattern(
        pattern, cif, tmp_path, "cu-ka1", None, 5.0, 90.0, ["trace_1"], 0.2
    )

    assert result["status"] == "success"
    assert captured == {
        "url": "http://xrd-service.internal/v1/xrd/compare",
        "filenames": ["model.cif", "pattern.xy"],
        "data": {
            "radiation": "cu-ka1",
            "two_theta_min": 5.0,
            "two_theta_max": 90.0,
            "trace_ids": "trace_1",
            "tolerance": 0.2,
        },
    }
    assert Path(result["artifacts"]["refinement_handoff_path"]).is_file()
