"""HTTP service for XRD parsing and phase identification."""

from __future__ import annotations

import asyncio
import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from xrd_service.vendor.xrd_core.adapter import InMemoryXRDResult
from xrd_service.vendor.xrd_core.parse import analyze_data, parse_file
from xrd_service.vendor.xrd_core.search_element import search_elements
from xrd_service.vendor.xrd_core.vis import XRDVis

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
RAW_SUFFIXES = {".xrdml", ".xy", ".asc", ".txt", ".mdi", ".raw"}


class HealthResponse(BaseModel):
    status: str = "ok"
    database_path: str


class Artifact(BaseModel):
    key: str
    name: str
    content: str


class ParseResponse(BaseModel):
    result: dict[str, Any]
    artifacts: list[Artifact]


class IdentifyResponse(BaseModel):
    result: dict[str, Any]
    artifacts: list[Artifact]


def _database_path() -> Path:
    return Path(__file__).resolve().parent / "vendor" / "xrd_core" / "XRD_database.h5"


@asynccontextmanager
async def lifespan(_: FastAPI):
    database = _database_path()
    if not database.is_file():
        raise RuntimeError(f"XRD reference database is missing: {database}")
    yield


app = FastAPI(title="XRD Service", version="1.0.0", lifespan=lifespan)


def _parse_elements(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _read_artifact(key: str, path: Path) -> Artifact:
    return Artifact(
        key=key,
        name=path.name,
        content=path.read_text(encoding="utf-8"),
    )


async def _save_upload(upload: UploadFile, output_dir: Path) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in RAW_SUFFIXES and suffix != ".csv":
        raise HTTPException(
            status_code=400,
            detail="Unsupported input file type.",
        )
    target = output_dir / (upload.filename or f"input{suffix}")
    size = 0
    with target.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Input file is too large.")
            handle.write(chunk)
    return target


def _parse_pattern(
    input_path: Path, output_dir: Path, baseline_mode: str
) -> dict[str, Any]:
    parsed = parse_file(input_path.name, input_path.read_bytes())
    if parsed is None:
        raise RuntimeError("Parser returned no data")
    analyzed = analyze_data(input_path.name, parsed)
    data = analyzed.get("data") or []
    features = analyzed.get("features") or []
    if len(data) < 3 or not data[0] or not features:
        raise RuntimeError("Analysis returned empty data or features")

    import pandas as pd

    base_name = input_path.stem
    raw_path = output_dir / f"{base_name}_raw_data.csv"
    features_path = output_dir / f"{base_name}_features.csv"
    chart_path = output_dir / f"{base_name}_chart_option.echarts"
    pd.DataFrame({"2Theta": data[0], "Intensity": data[1], "Baseline": data[2]}).to_csv(
        raw_path, index=False
    )
    pd.DataFrame(
        features,
        columns=["2Theta[°]", "Intensity(a.u.)", "FWHM", "Grain size"],
    ).to_csv(features_path, index=False)
    chart_path.write_text(
        json.dumps(
            XRDVis({"data": data}).get_echart_option(baseline_mode),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "status": "success",
        "file_name": input_path.name,
        "peaks_count": len(features),
        "scan_range": f"{min(data[0]):.2f} - {max(data[0]):.2f}",
        "raw_data_path": str(raw_path),
        "features_path": str(features_path),
        "chart_option_path": str(chart_path),
    }


async def _identify_phases(
    input_path: Path,
    output_dir: Path,
    include_any: list[str],
    include_all: list[str],
    exclude: list[str],
    top_n: int,
    show_top_n: int,
) -> dict[str, Any]:
    import pandas as pd

    frame = pd.read_csv(input_path)
    if not {"2Theta", "Intensity"}.issubset(frame.columns):
        raise ValueError(
            "Invalid CSV format. Expected columns '2Theta' and 'Intensity'."
        )
    x = frame["2Theta"].tolist()
    y = frame["Intensity"].tolist()
    if not x or not y:
        raise ValueError("Processed CSV contains no diffraction data")

    file_name = input_path.stem.replace("_raw_data", "")
    chemistry = [False, include_any, include_all, exclude]
    result = InMemoryXRDResult({file_name: {"data": [x, y]}})
    key = f"service_{file_name}"
    search_result = await search_elements(chemistry, [x, y], file_name, result, key, [])
    all_phases = search_result[0]
    if not all_phases:
        return {
            "status": "success",
            "message": "No matching phases found.",
            "top_phases": [],
            "count": 0,
        }

    top_phases = all_phases[:top_n]
    plot_result = await search_elements(
        chemistry,
        [x, y],
        file_name,
        result,
        f"{key}_plot",
        list(range(min(len(all_phases), show_top_n))),
    )
    top_path = output_dir / f"{file_name}_top{top_n}_phases.csv"
    all_path = output_dir / f"{file_name}_all_phases.csv"
    chart_path = output_dir / f"{file_name}_phase_id_chart.echarts"
    pd.DataFrame(top_phases).to_csv(top_path, index=False)
    pd.DataFrame(all_phases).to_csv(all_path, index=False)
    chart_path.write_text(
        json.dumps(plot_result[2], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "status": "success",
        "message": f"Identified {len(all_phases)} phases. Top {len(top_phases)} matches extracted.",
        "top_phases": top_phases,
        "count": len(top_phases),
        "top_phases_csv_path": str(top_path),
        "all_phases_path": str(all_path),
        "chart_option_path": str(chart_path),
    }


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    database = _database_path()
    if not database.is_file():
        raise HTTPException(
            status_code=503, detail="XRD reference database unavailable."
        )
    return HealthResponse(database_path=str(database))


@app.post("/v1/xrd/parse", response_model=ParseResponse)
async def parse(
    file: Annotated[UploadFile, File(...)],
    baseline_mode: Annotated[str, Form()] = "Non_removal baseline",
) -> ParseResponse:
    if baseline_mode not in {"Non_removal baseline", "Removal baseline"}:
        raise HTTPException(status_code=400, detail="Unsupported baseline mode.")
    with tempfile.TemporaryDirectory(prefix="xrd-service-") as directory:
        output_dir = Path(directory)
        input_path = await _save_upload(file, output_dir)
        try:
            result = await asyncio.to_thread(
                _parse_pattern,
                input_path,
                output_dir,
                baseline_mode,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        artifacts = [
            _read_artifact("raw_data_path", Path(result["raw_data_path"])),
            _read_artifact("features_path", Path(result["features_path"])),
            _read_artifact("chart_option_path", Path(result["chart_option_path"])),
        ]
        return ParseResponse(result=_without_paths(result), artifacts=artifacts)


@app.post("/v1/xrd/identify", response_model=IdentifyResponse)
async def identify(
    file: Annotated[UploadFile, File(...)],
    chem_include_any: Annotated[str, Form()] = "",
    chem_include_all: Annotated[str, Form()] = "",
    chem_exclude: Annotated[str, Form()] = "",
    top_n: Annotated[int, Form(ge=1, le=20)] = 5,
    show_top_n: Annotated[int, Form(ge=1, le=20)] = 1,
) -> IdentifyResponse:
    if Path(file.filename or "").suffix.lower() != ".csv":
        raise HTTPException(status_code=400, detail="Processed CSV input is required.")
    with tempfile.TemporaryDirectory(prefix="xrd-service-") as directory:
        output_dir = Path(directory)
        input_path = await _save_upload(file, output_dir)
        try:
            result = await _identify_phases(
                input_path=input_path,
                output_dir=output_dir,
                include_any=_parse_elements(chem_include_any),
                include_all=_parse_elements(chem_include_all),
                exclude=_parse_elements(chem_exclude),
                top_n=top_n,
                show_top_n=show_top_n,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        artifacts = []
        if result.get("status") == "success" and result.get("count", 1):
            for key in ("top_phases_csv_path", "all_phases_path", "chart_option_path"):
                if path := result.get(key):
                    artifacts.append(_read_artifact(key, Path(path)))
        return IdentifyResponse(result=_without_paths(result), artifacts=artifacts)


def _without_paths(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if not key.endswith("_path") and key not in {"raw_data_path", "features_path"}
    }
