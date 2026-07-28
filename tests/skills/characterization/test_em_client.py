from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_em_client():
    path = (
        Path(__file__).resolve().parents[3]
        / "matmaster/skills/electron-microscopy-analysis/scripts/analyze_em.py"
    )
    spec = importlib.util.spec_from_file_location("test_em_client", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_call_service_uses_verified_xmlrpc_arguments(tmp_path, monkeypatch) -> None:
    client = _load_em_client()
    image = tmp_path / "image.tif"
    image.write_bytes(b"image-bytes")
    received: dict = {}

    class FakeProxy:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def run_single(self, image_data, filename, model_key, execute_mode):
            received.update(
                image_data=image_data.data,
                filename=filename,
                model_key=model_key,
                execute_mode=execute_mode,
            )
            return {"data": [], "scalebar": {}}

    monkeypatch.setattr(
        client.xmlrpc.client, "ServerProxy", lambda *args, **kwargs: FakeProxy()
    )

    result = client._call_service(image, "http://em.example")

    assert result == {"data": [], "scalebar": {}}
    assert received == {
        "image_data": b"image-bytes",
        "filename": "image.tif",
        "model_key": "sam_vitb_maskonflow",
        "execute_mode": [],
    }


def test_analyze_missing_image_returns_cli_error(tmp_path) -> None:
    client = _load_em_client()

    result = client.main(
        [
            "--image",
            str(tmp_path / "missing.tif"),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert result == 1
