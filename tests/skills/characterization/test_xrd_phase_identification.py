from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_xrd_client():
    path = (
        Path(__file__).resolve().parents[3]
        / "matmaster/skills/xrd-phase-identification/scripts/xrd_phase_identification.py"
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

    def fake_post(url, *, files, data, timeout):
        captured.update(url=url, filename=files["file"][0], data=data)
        return FakeResponse()

    monkeypatch.setattr(client.httpx, "post", fake_post)

    result = client.parse_pattern(fixture, tmp_path, "Non_removal baseline")

    assert result["status"] == "success"
    assert captured == {
        "url": f"{client.DEFAULT_SERVICE_URL}/v1/xrd/parse",
        "filename": "pxrd_303K.xy",
        "data": {"baseline_mode": "Non_removal baseline"},
    }
    raw_data = Path(result["raw_data_path"])
    assert raw_data.is_file()
    assert (
        raw_data.read_text(encoding="utf-8").splitlines()[0]
        == "2Theta,Intensity,Baseline"
    )
    assert Path(result["features_path"]).is_file()
    assert Path(result["chart_option_path"]).is_file()
