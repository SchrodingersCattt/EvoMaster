from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _configure_database(monkeypatch, tmp_path: Path) -> None:
    from pxrd_service import service

    database = tmp_path / "db.h5"
    with service.pd.HDFStore(database, mode="w") as store:
        store.put("ready", service.pd.DataFrame({"value": [1]}))
    monkeypatch.setattr(service, "_database_path", lambda: database)


# All analysis routes require X-User-Id and X-Org-Id (workload attribution).
_IDENTITY_HEADERS = {"X-User-Id": "12345", "X-Org-Id": "67890"}


def test_parse_rejects_unsupported_upload(monkeypatch, tmp_path: Path) -> None:
    from pxrd_service import service

    _configure_database(monkeypatch, tmp_path)
    with TestClient(service.app) as client:
        response = client.post(
            "/v1/pxrd/parse",
            files={"file": ("pattern.pdf", BytesIO(b"not xrd"), "application/pdf")},
            headers=_IDENTITY_HEADERS,
        )
    assert response.status_code == 400


def test_identify_requires_processed_csv(monkeypatch, tmp_path: Path) -> None:
    from pxrd_service import service

    _configure_database(monkeypatch, tmp_path)
    with TestClient(service.app) as client:
        response = client.post(
            "/v1/pxrd/identify",
            files={"file": ("pattern.xy", BytesIO(b"10 100"), "text/plain")},
            headers=_IDENTITY_HEADERS,
        )
    assert response.status_code == 400


def test_parse_rejects_processed_csv(monkeypatch, tmp_path: Path) -> None:
    from pxrd_service import service

    _configure_database(monkeypatch, tmp_path)
    with TestClient(service.app) as client:
        response = client.post(
            "/v1/pxrd/parse",
            files={
                "file": ("processed.csv", BytesIO(b"2Theta,Intensity\n"), "text/csv")
            },
            headers=_IDENTITY_HEADERS,
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "no_numeric_data"


def test_health_reports_vendored_database(monkeypatch, tmp_path: Path) -> None:
    from pxrd_service import service

    _configure_database(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "_database_sha256", lambda: "a" * 64)
    with TestClient(service.app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service_version"] == service.SERVICE_VERSION
    assert len(response.json()["database_sha256"]) == 64


def _multi_trace_csv() -> bytes:
    lines = ["2Theta,300K,350K"]
    for index in range(20):
        theta = 10 + index * 0.1
        lines.append(f"{theta:.1f},{index + 1},{20 - index}")
    return ("\n".join(lines) + "\n").encode()


def _simple_cif() -> bytes:
    return b"""data_si
_cell_length_a 5.431
_cell_length_b 5.431
_cell_length_c 5.431
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_symmetry_space_group_name_H-M 'F d -3 m'
loop_
_symmetry_equiv_pos_as_xyz
'x, y, z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Si1 Si 0 0 0
"""


def test_parse_accepts_multi_trace_csv(monkeypatch, tmp_path: Path) -> None:
    from pxrd_service import service

    _configure_database(monkeypatch, tmp_path)
    with TestClient(service.app) as client:
        response = client.post(
            "/v1/pxrd/parse",
            files={"file": ("series.csv", BytesIO(_multi_trace_csv()), "text/csv")},
            headers=_IDENTITY_HEADERS,
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["trace_count"] == 2
    assert {item["trace_id"] for item in payload["result"]["traces"]} == {
        "300k",
        "350k",
    }
    assert any(item["key"] == "manifest_path" for item in payload["artifacts"])


def test_parse_rejects_binary_raw_with_stable_error(
    monkeypatch, tmp_path: Path
) -> None:
    from pxrd_service import service

    _configure_database(monkeypatch, tmp_path)
    with TestClient(service.app) as client:
        response = client.post(
            "/v1/pxrd/parse",
            files={
                "file": (
                    "instrument.raw",
                    BytesIO(b"RAW\x00\x01"),
                    "application/octet-stream",
                )
            },
            headers=_IDENTITY_HEADERS,
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_binary_raw"


def test_simulate_returns_cu_ka1_metadata(monkeypatch, tmp_path: Path) -> None:
    from pxrd_service import service

    _configure_database(monkeypatch, tmp_path)
    with TestClient(service.app) as client:
        response = client.post(
            "/v1/pxrd/simulate",
            files={"cif": ("silicon.cif", BytesIO(_simple_cif()), "text/plain")},
            headers=_IDENTITY_HEADERS,
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["radiation"] == "cu-ka1"
    assert payload["result"]["wavelength_angstrom"] == 1.540598
    assert payload["result"]["peak_count"] > 0
    assert payload["artifacts"][0]["key"] == "simulated_pattern_path"


def test_compare_returns_refinement_handoff(monkeypatch, tmp_path: Path) -> None:
    from pxrd_service import service

    _configure_database(monkeypatch, tmp_path)
    with TestClient(service.app) as client:
        response = client.post(
            "/v1/pxrd/compare",
            files={
                "pattern": ("series.csv", BytesIO(_multi_trace_csv()), "text/csv"),
                "cif": ("silicon.cif", BytesIO(_simple_cif()), "text/plain"),
            },
            headers=_IDENTITY_HEADERS,
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["trace_count"] == 2
    assert any(
        item["key"] == "refinement_handoff_path" for item in payload["artifacts"]
    )
