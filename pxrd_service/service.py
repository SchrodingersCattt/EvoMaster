"""HTTP service for PXRD parsing, phase screening, and CIF comparison."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import numpy as np
import pandas as pd
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .artifacts import (
    artifact_name,
    csv_text,
    parse_chart,
    peak_rows,
    processed_rows,
)
from .patterns import (
    PARSE_SUFFIXES,
    PatternDataset,
    PatternInputError,
    parse_pattern_bytes,
)
from .processing import CU_K_ALPHA_1_WAVELENGTH, ProcessedTrace, process_trace
from .simulation import (
    DEFAULT_RADIATION,
    compare_trace_to_simulation,
    simulate_cif,
    simulated_rows,
)

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
SERVICE_VERSION = "3.0.0"
PHASE_RESULT_LIMIT = 20


class ErrorDetail(BaseModel):
    code: str
    message: str


class HealthResponse(BaseModel):
    status: str = "ok"
    service_version: str
    database_sha256: str


class Artifact(BaseModel):
    key: str
    name: str
    content: str


class ServiceResponse(BaseModel):
    result: dict[str, Any]
    artifacts: list[Artifact]


class TraceManifest(BaseModel):
    trace_id: str
    label: str
    source_columns: list[str]
    points: int
    scan_range: list[float]
    warnings: list[str] = Field(default_factory=list)


def _database_path() -> Path:
    return Path(__file__).resolve().parent / "vendor" / "xrd_core" / "XRD_database.h5"


def _database_sha256() -> str:
    digest = hashlib.sha256()
    with _database_path().open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@asynccontextmanager
async def lifespan(_: FastAPI):
    database = _database_path()
    if not database.is_file():
        raise RuntimeError("XRD reference database is missing.")
    try:
        with pd.HDFStore(database, mode="r"):
            pass
    except Exception as exc:
        raise RuntimeError("XRD reference database is unreadable.") from exc
    yield


app = FastAPI(title="PXRD Service", version=SERVICE_VERSION, lifespan=lifespan)


def _require_workload_identity(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    x_org_id: Annotated[str | None, Header(alias="X-Org-Id")] = None,
) -> tuple[str, str]:
    """Require trusted workload-attribution headers on analysis routes.

    These are injected by the Open Platform gateway or the MatMaster Worker
    runtime. They are NOT standalone authorization—network policy must restrict
    callers to approved workloads.
    """
    if not (x_user_id or "").strip() or not (x_org_id or "").strip():
        raise HTTPException(
            status_code=401,
            detail="X-User-Id and X-Org-Id headers are required.",
        )
    return x_user_id.strip(), x_org_id.strip()  # type: ignore[union-attr]


@app.exception_handler(PatternInputError)
async def handle_pattern_input_error(_, exc: PatternInputError):
    return _error_response(400, exc.code, str(exc))


def _error_response(status_code: int, code: str, message: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=status_code,
        content={"detail": ErrorDetail(code=code, message=message).model_dump()},
    )


def _parse_elements(raw: str) -> list[str]:
    elements = [item.strip().capitalize() for item in raw.split(",") if item.strip()]
    invalid = [item for item in elements if not item.isalpha() or len(item) > 2]
    if invalid:
        raise PatternInputError(
            "invalid_element_constraint",
            "Chemical constraints must be comma-separated element symbols.",
        )
    return elements


def _parse_trace_ids(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


async def _save_upload(
    upload: UploadFile,
    output_dir: Path,
    *,
    allowed_suffixes: set[str],
    default_stem: str,
) -> Path:
    filename = Path(upload.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed_suffixes:
        raise PatternInputError(
            "unsupported_input_type",
            "Unsupported input type. Expected one of: "
            + ", ".join(sorted(allowed_suffixes)),
        )
    target = output_dir / (filename or f"{default_stem}{suffix}")
    size = 0
    with target.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Input file is too large.")
            handle.write(chunk)
    return target


def _artifact(key: str, name: str, content: str) -> Artifact:
    return Artifact(key=key, name=Path(name).name, content=content)


def _json_artifact(key: str, name: str, payload: dict[str, Any]) -> Artifact:
    return _artifact(
        key,
        name,
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _trace_manifest(processed: ProcessedTrace) -> TraceManifest:
    trace = processed.trace
    return TraceManifest(
        trace_id=trace.trace_id,
        label=trace.label,
        source_columns=trace.source_columns,
        points=len(trace.two_theta),
        scan_range=[min(trace.two_theta), max(trace.two_theta)],
        warnings=processed.warnings,
    )


def _parse_dataset(
    input_path: Path,
    *,
    profile: str,
    baseline_mode: str,
    trace_ids: list[str],
    wavelength: float,
) -> tuple[PatternDataset, list[ProcessedTrace], list[Artifact]]:
    dataset = parse_pattern_bytes(input_path.name, input_path.read_bytes())
    selected = _select_dataset_traces(dataset, trace_ids)
    processed = [
        process_trace(trace, profile=profile, wavelength=wavelength)
        for trace in selected
    ]
    artifacts: list[Artifact] = []
    for item in processed:
        raw_name = artifact_name(input_path.name, item.trace.trace_id, "raw_data.csv")
        peak_name = artifact_name(input_path.name, item.trace.trace_id, "features.csv")
        chart_name = artifact_name(
            input_path.name, item.trace.trace_id, "chart_option.echarts"
        )
        artifacts.extend(
            [
                _artifact(
                    f"{item.trace.trace_id}_raw_data_path",
                    raw_name,
                    csv_text(
                        processed_rows(item),
                        [
                            "2Theta",
                            "Intensity",
                            "NormalizedIntensity",
                            "Baseline",
                            "SubtractedIntensity",
                        ],
                    ),
                ),
                _artifact(
                    f"{item.trace.trace_id}_features_path",
                    peak_name,
                    csv_text(
                        peak_rows(item),
                        [
                            "Trace",
                            "Index",
                            "2Theta",
                            "Intensity",
                            "NormalizedIntensity",
                            "SubtractedIntensity",
                            "Prominence",
                            "FWHM",
                            "DSpacing",
                            "ScherrerSizeNm",
                        ],
                    ),
                ),
                _json_artifact(
                    f"{item.trace.trace_id}_chart_option_path",
                    chart_name,
                    parse_chart(item, baseline_mode),
                ),
            ]
        )
    return dataset, processed, artifacts


def _select_dataset_traces(dataset: PatternDataset, trace_ids: list[str]) -> list[Any]:
    if not trace_ids:
        return dataset.traces
    lookup = {trace.trace_id: trace for trace in dataset.traces}
    unknown = [trace_id for trace_id in trace_ids if trace_id not in lookup]
    if unknown:
        raise PatternInputError(
            "unknown_trace",
            "Unknown trace ID(s): "
            + ", ".join(unknown)
            + ". Available trace IDs: "
            + ", ".join(lookup),
        )
    return [lookup[trace_id] for trace_id in trace_ids]


def _load_processed_dataset(input_path: Path) -> PatternDataset:
    return parse_pattern_bytes(input_path.name, input_path.read_bytes())


async def _identify_trace(
    processed: ProcessedTrace,
    include_any: list[str],
    include_all: list[str],
    exclude: list[str],
    top_n: int,
    show_top_n: int,
) -> tuple[dict[str, Any], list[Artifact]]:
    from .vendor.xrd_core.adapter import InMemoryXRDResult
    from .vendor.xrd_core.parse import PTF, XRD_DATA
    from .vendor.xrd_core.search_element import search_elements

    candidate_pool = PTF([False, include_any, include_all, exclude], XRD_DATA)
    if not candidate_pool:
        return (
            {
                "trace_id": processed.trace.trace_id,
                "message": "No reference candidates remain after chemistry filtering.",
                "constraints": {
                    "include_any": include_any,
                    "include_all": include_all,
                    "exclude": exclude,
                },
                "database_candidate_count": 0,
                "returned_candidate_count": 0,
                "top_phases": [],
            },
            [],
        )

    file_name = processed.trace.trace_id
    x = processed.trace.two_theta
    y = processed.subtracted_intensity
    result = InMemoryXRDResult({file_name: {"data": [x, y]}})
    chemistry = [False, include_any, include_all, exclude]
    search_result = await search_elements(
        chemistry,
        [x, y],
        file_name,
        result,
        f"service_{file_name}",
        [],
    )
    candidates = search_result[0] if search_result else []
    candidates = candidates[:PHASE_RESULT_LIMIT]
    top_candidates = candidates[:top_n]
    artifacts: list[Artifact] = []
    if candidates:
        selected_count = min(len(candidates), show_top_n)
        plot_result = await search_elements(
            chemistry,
            [x, y],
            file_name,
            result,
            f"service_{file_name}_plot",
            list(range(selected_count)),
        )
        artifacts.extend(
            [
                _artifact(
                    f"{file_name}_top_phases_path",
                    f"{file_name}_top{top_n}_phases.csv",
                    csv_text(
                        top_candidates,
                        [
                            "Reference code",
                            "Importance",
                            "Chemical formula",
                            "Crystal system",
                            "Space group",
                        ],
                    ),
                ),
                _artifact(
                    f"{file_name}_candidate_phases_path",
                    f"{file_name}_candidate_phases.csv",
                    csv_text(
                        candidates,
                        [
                            "Reference code",
                            "Importance",
                            "Chemical formula",
                            "Crystal system",
                            "Space group",
                        ],
                    ),
                ),
                _json_artifact(
                    f"{file_name}_phase_id_chart_path",
                    f"{file_name}_phase_id_chart.echarts",
                    plot_result[2],
                ),
            ]
        )
    return (
        {
            "trace_id": file_name,
            "message": "Reference-database candidate screening completed.",
            "constraints": {
                "include_any": include_any,
                "include_all": include_all,
                "exclude": exclude,
            },
            "database_candidate_count": len(candidate_pool),
            "returned_candidate_count": len(candidates),
            "top_phases": [_phase_record(candidate) for candidate in top_candidates],
            "score_note": "heuristic_rank_score is a reference-pattern screening rank, not a confidence or phase proof.",
        },
        artifacts,
    )


def _phase_record(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "reference_code": candidate.get("Reference code"),
        "formula": candidate.get("Chemical formula"),
        "crystal_system": candidate.get("Crystal system"),
        "space_group": candidate.get("Space group"),
        "heuristic_rank_score": candidate.get("Importance"),
    }


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    database = _database_path()
    if not database.is_file():
        raise HTTPException(
            status_code=503, detail="XRD reference database unavailable."
        )
    try:
        checksum = await asyncio.to_thread(_database_sha256)
    except OSError as exc:
        raise HTTPException(
            status_code=503, detail="XRD reference database unavailable."
        ) from exc
    return HealthResponse(service_version=SERVICE_VERSION, database_sha256=checksum)


@app.post("/v1/pxrd/parse", response_model=ServiceResponse)
async def parse(
    file: Annotated[UploadFile, File(...)],
    baseline_mode: Annotated[str, Form()] = "Non_removal baseline",
    profile: Annotated[str, Form()] = "standard",
    trace_ids: Annotated[str, Form()] = "",
    wavelength: Annotated[float, Form(gt=0)] = CU_K_ALPHA_1_WAVELENGTH,
    _identity: tuple[str, str] = Depends(_require_workload_identity),
) -> ServiceResponse:
    if baseline_mode not in {"Non_removal baseline", "Removal baseline"}:
        raise PatternInputError(
            "unsupported_baseline_mode", "Unsupported baseline mode."
        )
    with tempfile.TemporaryDirectory(prefix="pxrd-service-") as directory:
        input_path = await _save_upload(
            file,
            Path(directory),
            allowed_suffixes=PARSE_SUFFIXES,
            default_stem="pattern",
        )
        dataset, processed, artifacts = await asyncio.to_thread(
            _parse_dataset,
            input_path,
            profile=profile,
            baseline_mode=baseline_mode,
            trace_ids=_parse_trace_ids(trace_ids),
            wavelength=wavelength,
        )
    manifests = [_trace_manifest(item).model_dump() for item in processed]
    manifest_name = f"{Path(file.filename or 'pattern').stem}_parse_manifest.json"
    artifacts.append(
        _json_artifact(
            "manifest_path",
            manifest_name,
            {
                "source_name": dataset.source_name,
                "source_format": dataset.source_format,
                "encoding": dataset.encoding,
                "delimiter": dataset.delimiter,
                "traces": manifests,
            },
        )
    )
    return ServiceResponse(
        result={
            "status": "success",
            "service_version": SERVICE_VERSION,
            "processing_profile": profile,
            "wavelength_angstrom": wavelength,
            "trace_count": len(processed),
            "traces": [
                {
                    **manifest,
                    "peaks_count": len(item.peaks),
                    "metadata": item.metadata,
                }
                for item, manifest in zip(processed, manifests)
            ],
        },
        artifacts=artifacts,
    )


@app.post("/v1/pxrd/identify", response_model=ServiceResponse)
async def identify(
    file: Annotated[UploadFile, File(...)],
    chem_include_any: Annotated[str, Form()] = "",
    chem_include_all: Annotated[str, Form()] = "",
    chem_exclude: Annotated[str, Form()] = "",
    top_n: Annotated[int, Form(ge=1, le=20)] = 5,
    show_top_n: Annotated[int, Form(ge=1, le=20)] = 1,
    trace_ids: Annotated[str, Form()] = "",
    _identity: tuple[str, str] = Depends(_require_workload_identity),
) -> ServiceResponse:
    with tempfile.TemporaryDirectory(prefix="pxrd-service-") as directory:
        input_path = await _save_upload(
            file,
            Path(directory),
            allowed_suffixes={".csv"},
            default_stem="processed",
        )
        dataset = await asyncio.to_thread(_load_processed_dataset, input_path)
        selected = _select_dataset_traces(dataset, _parse_trace_ids(trace_ids))
        processed = [
            await asyncio.to_thread(
                process_trace,
                trace,
                profile="standard",
                wavelength=CU_K_ALPHA_1_WAVELENGTH,
            )
            for trace in selected
        ]
        phase_results = await asyncio.gather(
            *[
                _identify_trace(
                    item,
                    _parse_elements(chem_include_any),
                    _parse_elements(chem_include_all),
                    _parse_elements(chem_exclude),
                    top_n,
                    show_top_n,
                )
                for item in processed
            ]
        )
    results, artifact_groups = zip(*phase_results) if phase_results else ([], [])
    artifacts = [artifact for group in artifact_groups for artifact in group]
    return ServiceResponse(
        result={
            "status": "success",
            "service_version": SERVICE_VERSION,
            "trace_count": len(results),
            "traces": list(results),
        },
        artifacts=artifacts,
    )


@app.post("/v1/pxrd/simulate", response_model=ServiceResponse)
async def simulate(
    cif: Annotated[UploadFile, File(...)],
    radiation: Annotated[str, Form()] = DEFAULT_RADIATION,
    wavelength: Annotated[float | None, Form()] = None,
    two_theta_min: Annotated[float, Form(ge=0, le=180)] = 5.0,
    two_theta_max: Annotated[float, Form(gt=0, le=180)] = 90.0,
    _identity: tuple[str, str] = Depends(_require_workload_identity),
) -> ServiceResponse:
    with tempfile.TemporaryDirectory(prefix="pxrd-service-") as directory:
        cif_path = await _save_upload(
            cif,
            Path(directory),
            allowed_suffixes={".cif"},
            default_stem="structure",
        )
        simulated = await asyncio.to_thread(
            simulate_cif,
            cif_path,
            radiation=radiation,
            wavelength=wavelength,
            two_theta_min=two_theta_min,
            two_theta_max=two_theta_max,
        )
    artifact = _artifact(
        "simulated_pattern_path",
        f"{cif_path.stem}_simulated_pxrd.csv",
        csv_text(
            simulated_rows(simulated),
            ["2Theta", "NormalizedIntensity", "DSpacing", "HKL"],
        ),
    )
    return ServiceResponse(
        result={
            "status": "success",
            "service_version": SERVICE_VERSION,
            "radiation": simulated.radiation,
            "wavelength_angstrom": simulated.wavelength,
            "two_theta_range": [simulated.two_theta_min, simulated.two_theta_max],
            "peak_count": len(simulated.two_theta),
            "note": "This is an ideal CIF-derived stick pattern, not a refinement or fit.",
        },
        artifacts=[artifact],
    )


@app.post("/v1/pxrd/compare", response_model=ServiceResponse)
async def compare(
    pattern: Annotated[UploadFile, File(...)],
    cif: Annotated[UploadFile, File(...)],
    radiation: Annotated[str, Form()] = DEFAULT_RADIATION,
    wavelength: Annotated[float | None, Form()] = None,
    two_theta_min: Annotated[float, Form(ge=0, le=180)] = 5.0,
    two_theta_max: Annotated[float, Form(gt=0, le=180)] = 90.0,
    trace_ids: Annotated[str, Form()] = "",
    tolerance: Annotated[float, Form(gt=0, le=5)] = 0.2,
    _identity: tuple[str, str] = Depends(_require_workload_identity),
) -> ServiceResponse:
    with tempfile.TemporaryDirectory(prefix="pxrd-service-") as directory:
        output_dir = Path(directory)
        pattern_path = await _save_upload(
            pattern,
            output_dir,
            allowed_suffixes=PARSE_SUFFIXES,
            default_stem="pattern",
        )
        cif_path = await _save_upload(
            cif,
            output_dir,
            allowed_suffixes={".cif"},
            default_stem="structure",
        )
        dataset = await asyncio.to_thread(
            parse_pattern_bytes, pattern_path.name, pattern_path.read_bytes()
        )
        traces = _select_dataset_traces(dataset, _parse_trace_ids(trace_ids))
        simulated = await asyncio.to_thread(
            simulate_cif,
            cif_path,
            radiation=radiation,
            wavelength=wavelength,
            two_theta_min=two_theta_min,
            two_theta_max=two_theta_max,
        )
        comparisons = await asyncio.gather(
            *[
                asyncio.to_thread(
                    compare_trace_to_simulation, trace, simulated, tolerance=tolerance
                )
                for trace in traces
            ]
        )
    artifacts: list[Artifact] = []
    for comparison in comparisons:
        trace_id = comparison["trace_id"]
        artifacts.extend(
            [
                _artifact(
                    f"{trace_id}_peak_matches_path",
                    f"{Path(pattern.filename or 'pattern').stem}_{trace_id}_cif_matches.csv",
                    csv_text(
                        comparison["matches"],
                        [
                            "experimental_two_theta",
                            "experimental_intensity",
                            "reference_two_theta",
                            "reference_intensity",
                            "difference",
                            "matched",
                        ],
                    ),
                ),
                _json_artifact(
                    f"{trace_id}_comparison_chart_path",
                    f"{Path(pattern.filename or 'pattern').stem}_{trace_id}_cif_compare.echarts",
                    comparison.pop("chart_option"),
                ),
            ]
        )
    handoff = {
        "workflow": "pxrd-refinement-handoff",
        "pattern_source": Path(pattern.filename or "pattern").name,
        "cif_source": Path(cif.filename or "structure.cif").name,
        "radiation": simulated.radiation,
        "wavelength_angstrom": simulated.wavelength,
        "selected_trace_ids": [item["trace_id"] for item in comparisons],
        "warnings": [
            "Use pxrd-refinement for Pawley/Rietveld refinement; comparison does not fit a structure."
        ],
    }
    artifacts.append(
        _json_artifact(
            "refinement_handoff_path", "xrd_refinement_handoff.json", handoff
        )
    )
    return ServiceResponse(
        result={
            "status": "success",
            "service_version": SERVICE_VERSION,
            "radiation": simulated.radiation,
            "wavelength_angstrom": simulated.wavelength,
            "trace_count": len(comparisons),
            "traces": comparisons,
            "note": "Comparison reports peak diagnostics only; it is not a refinement or quantitative phase analysis.",
        },
        artifacts=artifacts,
    )
