from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import pytest


def test_parse_rejects_unsupported_upload(monkeypatch, tmp_path: Path) -> None:
    from xrd_service import service

    monkeypatch.setattr(service, "_database_path", lambda: tmp_path / "db.h5")
    (tmp_path / "db.h5").write_bytes(b"placeholder")
    with TestClient(service.app) as client:
        response = client.post(
            "/v1/xrd/parse",
            files={"file": ("pattern.pdf", BytesIO(b"not xrd"), "application/pdf")},
        )
    assert response.status_code == 400


def test_identify_requires_processed_csv(monkeypatch, tmp_path: Path) -> None:
    from xrd_service import service

    monkeypatch.setattr(service, "_database_path", lambda: tmp_path / "db.h5")
    (tmp_path / "db.h5").write_bytes(b"placeholder")
    with TestClient(service.app) as client:
        response = client.post(
            "/v1/xrd/identify",
            files={"file": ("pattern.xy", BytesIO(b"10 100"), "text/plain")},
        )
    assert response.status_code == 400


def test_health_reports_vendored_database(monkeypatch, tmp_path: Path) -> None:
    from xrd_service import service

    database = tmp_path / "XRD_database.h5"
    database.write_bytes(b"placeholder")
    monkeypatch.setattr(service, "_database_path", lambda: database)
    with TestClient(service.app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["database_path"] == str(database)