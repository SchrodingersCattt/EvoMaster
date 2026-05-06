"""
Unit tests for the multi-mode --data argument resolution in gsas2_pawley.py.

The script's ``main()`` routes between single / directory / wide-csv / multi-file
modes purely from the parsed argparse Namespace. These tests exercise the
resolution logic by parsing CLI fragments and inspecting the resulting
``args._explicit_files`` / ``args.data`` / mode dispatch markers without ever
calling GSAS-II.

Run from project root:
  uv run pytest matmaster/skills/playground-skills/pxrd-refinement/tests/ -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SKILL_DIR = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _SKILL_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import gsas2_pawley  # noqa: E402


def _stub_main_run(monkeypatch, argv: list[str]):
    """Run ``gsas2_pawley.main`` up to mode dispatch, then capture args.

    Replaces ``setup_gsas2`` and the three run_* entry points with capture
    stubs so the test never touches GSAS-II. Returns the captured Namespace
    and which run_* function was invoked.
    """
    captured: dict = {"args": None, "mode": None}

    monkeypatch.setattr(gsas2_pawley, "setup_gsas2", lambda *a, **k: None)

    def _capture(name):
        def _run(args):
            captured["args"] = args
            captured["mode"] = name
            return {"success": True, "_test_mode": name}

        return _run

    monkeypatch.setattr(gsas2_pawley, "run_single", _capture("single"))
    monkeypatch.setattr(gsas2_pawley, "run_directory", _capture("directory"))
    monkeypatch.setattr(gsas2_pawley, "run_wide_csv", _capture("wide_csv"))

    monkeypatch.setattr(sys, "argv", ["gsas2_pawley.py", *argv])
    gsas2_pawley.main()
    return captured


def test_single_file_mode(tmp_path, monkeypatch, capsys):
    f = tmp_path / "pxrd_303K.xy"
    f.write_text("0.0 0.0\n1.0 1.0\n", encoding="utf-8")

    cap = _stub_main_run(
        monkeypatch,
        argv=[
            "--data", str(f),
            "--space-group", "P 21",
            "--cell", "a=10.83,b=9.62,c=10.13,beta=108.75",
        ],
    )
    assert cap["mode"] == "single"
    assert cap["args"].data == str(f)
    assert cap["args"]._explicit_files is None


def test_directory_mode(tmp_path, monkeypatch):
    d = tmp_path / "patterns"
    d.mkdir()
    (d / "pxrd_303K.xy").write_text("0 0\n", encoding="utf-8")
    (d / "pxrd_323K.xy").write_text("0 0\n", encoding="utf-8")

    cap = _stub_main_run(
        monkeypatch,
        argv=[
            "--data", str(d),
            "--space-group", "P 21",
            "--cell", "a=10.83,b=9.62,c=10.13,beta=108.75",
        ],
    )
    assert cap["mode"] == "directory"
    assert cap["args"].data == str(d)
    assert cap["args"]._explicit_files is None


def test_multi_file_mode_routes_to_directory(tmp_path, monkeypatch):
    f1 = tmp_path / "pxrd_303K.xy"
    f2 = tmp_path / "pxrd_323K.xy"
    f3 = tmp_path / "pxrd_343K.xy"
    f4 = tmp_path / "pxrd_363K.xy"
    for f in (f1, f2, f3, f4):
        f.write_text("0 0\n", encoding="utf-8")

    cap = _stub_main_run(
        monkeypatch,
        argv=[
            "--data", str(f1), str(f2), str(f3), str(f4),
            "--space-group", "P 21",
            "--cell", "a=10.83,b=9.62,c=10.13,beta=108.75",
            "--chain-cell",
            "--chain-cell-direction", "both",
        ],
    )
    assert cap["mode"] == "directory"
    explicit = cap["args"]._explicit_files
    assert explicit is not None
    assert len(explicit) == 4
    assert {p.name for p in explicit} == {
        "pxrd_303K.xy", "pxrd_323K.xy", "pxrd_343K.xy", "pxrd_363K.xy",
    }


def test_multi_file_mode_preserves_argv_order_and_deduplicates(tmp_path, monkeypatch):
    f1 = tmp_path / "c.xy"
    f2 = tmp_path / "a.xy"
    f3 = tmp_path / "b.xy"
    for f in (f1, f2, f3):
        f.write_text("0 0\n", encoding="utf-8")

    cap = _stub_main_run(
        monkeypatch,
        argv=[
            "--data", str(f1), str(f2), str(f1), str(f3),
            "--space-group", "P 21",
            "--cell", "a=10.83,b=9.62,c=10.13,beta=108.75",
            "--chain-cell",
        ],
    )

    assert cap["mode"] == "directory"
    assert [p.name for p in cap["args"]._explicit_files] == ["c.xy", "a.xy", "b.xy"]


def test_multi_file_mode_with_directory_rejected(tmp_path, monkeypatch, capsys):
    f1 = tmp_path / "pxrd_303K.xy"
    f1.write_text("0 0\n", encoding="utf-8")
    d = tmp_path / "extra"
    d.mkdir()

    monkeypatch.setattr(gsas2_pawley, "setup_gsas2", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", [
        "gsas2_pawley.py",
        "--data", str(f1), str(d),
        "--space-group", "P 21",
        "--cell", "a=10,b=10,c=10,beta=90",
    ])
    with pytest.raises(SystemExit) as exc:
        gsas2_pawley.main()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["success"] is False
    assert "Multi-file" in payload["error"]


def test_wide_csv_requires_single_path(tmp_path, monkeypatch, capsys):
    f1 = tmp_path / "wide.csv"
    f1.write_text("T,2theta,intensity\n", encoding="utf-8")
    f2 = tmp_path / "wide2.csv"
    f2.write_text("T,2theta,intensity\n", encoding="utf-8")

    monkeypatch.setattr(gsas2_pawley, "setup_gsas2", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", [
        "gsas2_pawley.py",
        "--wide-csv",
        "--data", str(f1), str(f2),
        "--space-group", "P 21",
        "--cell", "a=10,b=10,c=10,beta=90",
    ])
    with pytest.raises(SystemExit) as exc:
        gsas2_pawley.main()
    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["success"] is False
    assert "wide-csv" in payload["error"].lower()


def test_path_not_found_reports_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gsas2_pawley, "setup_gsas2", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", [
        "gsas2_pawley.py",
        "--data", str(tmp_path / "missing.xy"),
        "--space-group", "P 21",
        "--cell", "a=10,b=10,c=10,beta=90",
    ])
    with pytest.raises(SystemExit) as exc:
        gsas2_pawley.main()
    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["success"] is False
    assert "Not found" in payload["error"]
