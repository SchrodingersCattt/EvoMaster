#!/usr/bin/env python3
"""Direct client for the deployed electron-microscope recognition service.

The script preserves the former MCP tool's XML-RPC request and result semantics,
while placing all generated artifacts beneath an explicit output directory.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
import xmlrpc.client
from pathlib import Path
from typing import Any

DEFAULT_SERVICE_URL = "http://101.126.67.113:7877"
MODEL_KEY = "sam_vitb_maskonflow"
RETRY_DELAYS_SECONDS = (1.0, 2.0)
PARTICLE_COLUMNS = [
    "category_id",
    "score",
    "is_edge",
    "is_overlay_occluded",
    "is_scalebar_occluded",
    "is_invalid",
    "area_pixel",
    "area_nm2",
    "perimeter_pixel",
    "perimeter_nm",
    "feret_nm",
    "diameter_nm",
    "bbox_minx_px",
    "bbox_miny_px",
    "bbox_maxx_px",
    "bbox_maxy_px",
    "centroid_x_px",
    "centroid_y_px",
    "aspect_ratio",
    "circularity",
]


def _load_dependencies() -> tuple[Any, Any, Any, Any]:
    try:
        import pandas as pd
        import shapely.geometry as geom
        from PIL import Image, ImageDraw, ImageOps
    except ImportError as exc:
        raise RuntimeError(
            "Missing EM CLI dependency. Install pandas, shapely, and Pillow."
        ) from exc
    return pd, geom, Image, (ImageDraw, ImageOps)


def _stats(frame: Any, column: str) -> dict[str, float | None]:
    values = frame[column].dropna()
    if values.empty:
        return {"mean": None, "p50": None, "std": None, "min": None, "max": None}
    return {
        "mean": float(values.mean()),
        "p50": float(values.median()),
        "std": float(values.std(ddof=0)),
        "min": float(values.min()),
        "max": float(values.max()),
    }


def _process_result(
    result: dict[str, Any], csv_path: Path
) -> tuple[Any, dict[str, Any]]:
    pd, geom, _, _ = _load_dependencies()
    scalebar = result.get("scalebar") or {}
    try:
        nm_per_px = (
            float(scalebar["value"]) / float(scalebar["width"])
            if scalebar.get("width")
            else None
        )
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        nm_per_px = None

    records: list[dict[str, Any]] = []
    for item in result.get("data") or []:
        try:
            polygon = item.get("polygon") or []
            if len(polygon) < 3:
                continue
            poly = geom.Polygon(
                [(float(point[0]), float(point[1])) for point in polygon]
            )
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                continue

            area_px = float(poly.area)
            perimeter_px = float(poly.length)
            min_x, min_y, max_x, max_y = (float(value) for value in poly.bounds)
            width_px, height_px = max_x - min_x, max_y - min_y
            major_px, minor_px = max(width_px, height_px), min(width_px, height_px)

            area_nm2 = area_px * nm_per_px**2 if nm_per_px is not None else None
            perimeter_nm = perimeter_px * nm_per_px if nm_per_px is not None else None
            feret_nm = major_px * nm_per_px if nm_per_px is not None else None
            diameter_nm = (
                2 * math.sqrt(area_nm2 / math.pi) if area_nm2 is not None else None
            )
            circularity = (
                4 * math.pi * area_px / perimeter_px**2 if perimeter_px > 0 else None
            )
            aspect_ratio = major_px / minor_px if minor_px > 0 else None

            records.append(
                {
                    "category_id": item.get("category_id"),
                    "score": item.get("score"),
                    "is_edge": item.get("is_edge"),
                    "is_overlay_occluded": item.get("is_overlay_occluded"),
                    "is_scalebar_occluded": item.get("is_scalebar_occluded"),
                    "is_invalid": item.get("is_invalid"),
                    "area_pixel": area_px,
                    "area_nm2": area_nm2,
                    "perimeter_pixel": perimeter_px,
                    "perimeter_nm": perimeter_nm,
                    "feret_nm": feret_nm,
                    "diameter_nm": diameter_nm,
                    "bbox_minx_px": min_x,
                    "bbox_miny_px": min_y,
                    "bbox_maxx_px": max_x,
                    "bbox_maxy_px": max_y,
                    "centroid_x_px": float(poly.centroid.x),
                    "centroid_y_px": float(poly.centroid.y),
                    "aspect_ratio": aspect_ratio,
                    "circularity": circularity,
                }
            )
        except (IndexError, TypeError, ValueError):
            continue

    frame = pd.DataFrame(records, columns=PARTICLE_COLUMNS)
    frame.to_csv(csv_path, index=False)

    total = len(frame)
    edge = int(frame["is_edge"].fillna(False).sum()) if total else 0
    invalid = int(frame["is_invalid"].fillna(False).sum()) if total else 0
    occluded = (
        int(
            (
                frame["is_overlay_occluded"].fillna(False)
                | frame["is_scalebar_occluded"].fillna(False)
            ).sum()
        )
        if total
        else 0
    )
    counts = {
        "total": total,
        "valid": total - edge - invalid,
        "edge": edge,
        "invalid": invalid,
        "occluded": occluded,
    }
    scale = {
        "nm_per_pixel": nm_per_px,
        "scalebar_value_nm": _as_float(scalebar.get("value")),
        "scalebar_width_px": _as_float(scalebar.get("width")),
    }
    summary = {
        "scale": scale,
        "counts": counts,
        "stats": {
            "area_nm2": _stats(frame, "area_nm2"),
            "diameter_nm": _stats(frame, "diameter_nm"),
            "aspect_ratio": _stats(frame, "aspect_ratio"),
            "circularity": _stats(frame, "circularity"),
        },
    }
    return frame, summary


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    if value is None:
        return False
    try:
        if math.isnan(value):
            return False
    except TypeError:
        pass
    return bool(value)


def _json_value(value: Any) -> Any:
    try:
        if math.isnan(value):
            return None
    except TypeError:
        pass
    return value.item() if hasattr(value, "item") else value


def _draw_overlay(image_path: Path, frame: Any, output_path: Path) -> None:
    _, _, Image, image_modules = _load_dependencies()
    ImageDraw, ImageOps = image_modules
    image = Image.open(image_path)
    try:
        image.seek(0)
    except EOFError:
        pass
    image = ImageOps.exif_transpose(image).convert("RGB")
    draw = ImageDraw.Draw(image)
    for _, row in frame.iterrows():
        try:
            invalid = _as_bool(row["is_invalid"])
            edge = _as_bool(row["is_edge"])
            occluded = _as_bool(row["is_overlay_occluded"]) or _as_bool(
                row["is_scalebar_occluded"]
            )
            color = (
                "red"
                if invalid
                else "orange" if edge else "magenta" if occluded else "lime"
            )
            radius = math.sqrt(float(row["area_pixel"]) / math.pi)
            x, y = float(row["centroid_x_px"]), float(row["centroid_y_px"])
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius), outline=color, width=2
            )
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=color)
        except (TypeError, ValueError):
            continue
    image.save(output_path)


def _call_service(image_path: Path, service_url: str) -> dict[str, Any]:
    image_data = image_path.read_bytes()
    last_error: Exception | None = None
    for attempt in range(len(RETRY_DELAYS_SECONDS) + 1):
        try:
            with xmlrpc.client.ServerProxy(service_url, allow_none=True) as proxy:
                result = proxy.run_single(
                    xmlrpc.client.Binary(image_data),
                    image_path.name,
                    MODEL_KEY,
                    [],
                )
            if not isinstance(result, dict) or "data" not in result:
                raise RuntimeError(
                    "Recognition service returned unexpected result format"
                )
            return result
        except (OSError, xmlrpc.client.ProtocolError) as exc:
            last_error = exc
            if attempt < len(RETRY_DELAYS_SECONDS):
                time.sleep(RETRY_DELAYS_SECONDS[attempt])
                continue
            break
        except xmlrpc.client.Fault as exc:
            raise RuntimeError(f"Remote fault: {exc}") from exc
    raise RuntimeError(f"EM recognition failed after retries: {last_error}")


def analyze(image_path: Path, output_dir: Path, service_url: str) -> dict[str, Any]:
    if not image_path.is_file():
        raise FileNotFoundError(f"Image file not found: {image_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    result = _call_service(image_path, service_url)
    csv_path = output_dir / "particles.csv"
    overlay_path = output_dir / "overlay.png"
    frame, summary = _process_result(result, csv_path)
    _draw_overlay(image_path, frame, overlay_path)

    sample: list[dict[str, Any]] = []
    for index, row in frame.head(5).iterrows():
        sample.append(
            {
                "id": int(index),
                "centroid_px": [
                    _json_value(row.get("centroid_x_px")),
                    _json_value(row.get("centroid_y_px")),
                ],
                "diameter_nm": _json_value(row.get("diameter_nm")),
                "area_nm2": _json_value(row.get("area_nm2")),
                "circularity": _json_value(row.get("circularity")),
                "aspect_ratio": _json_value(row.get("aspect_ratio")),
                "flags": {
                    "edge": _as_bool(row.get("is_edge")),
                    "invalid": _as_bool(row.get("is_invalid")),
                    "occluded": _as_bool(row.get("is_overlay_occluded"))
                    or _as_bool(row.get("is_scalebar_occluded")),
                },
            }
        )

    diameter = summary["stats"]["diameter_nm"]
    if summary["scale"]["nm_per_pixel"] is None:
        llm_text = f"共检测到 {summary['counts']['total']} 个颗粒（无有效比例尺信息）。"
    elif diameter["mean"] is None or diameter["p50"] is None:
        llm_text = (
            f"共检测到 {summary['counts']['total']} 个颗粒，" "但未能计算有效粒径统计。"
        )
    else:
        llm_text = (
            f"共检测到 {summary['counts']['total']} 个颗粒，其中有效 "
            f"{summary['counts']['valid']} 个。平均直径 {diameter['mean']:.2f} nm"
            f"（P50={diameter['p50']:.2f} nm），边界颗粒 "
            f"{summary['counts']['edge']} 个，疑似遮挡 "
            f"{summary['counts']['occluded']} 个。"
        )

    return {
        "status": "success",
        "file_name": image_path.name,
        **summary,
        "particles_sample": sample,
        "artifacts_csv_path": str(csv_path.resolve()),
        "overlay_image_path": str(overlay_path.resolve()),
        "raw": {
            "data_count": summary["counts"]["total"],
            "scalebar_present": summary["scale"]["scalebar_value_nm"] is not None,
        },
        "llm_text": llm_text,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze particles in an EM image.")
    parser.add_argument("--image", required=True, help="Path to a local EM image.")
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for particles.csv and overlay.png.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = analyze(
            Path(args.image),
            Path(args.output_dir),
            os.environ.get("EM_SERVICE_URL", DEFAULT_SERVICE_URL),
        )
    except Exception as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
