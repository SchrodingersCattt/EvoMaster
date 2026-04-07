from pathlib import Path


def _iter_python_sources() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[3]
    return [
        path
        for path in repo_root.rglob("*.py")
        if "tests/" not in str(path).replace("\\", "/")
    ]


def test_production_code_no_longer_imports_runtime_bridge() -> None:
    offenders = []
    for path in _iter_python_sources():
        text = path.read_text(encoding="utf-8")
        if "matmaster.integration.runtime_bridge" in text:
            offenders.append(path)
    assert offenders == []


def test_production_code_no_longer_imports_bohrium_env() -> None:
    offenders = []
    for path in _iter_python_sources():
        text = path.read_text(encoding="utf-8")
        if "matmaster.integration.bohrium_env" in text:
            offenders.append(path)
    assert offenders == []


def test_production_code_no_longer_imports_calculation_adaptors() -> None:
    offenders = []
    for path in _iter_python_sources():
        text = path.read_text(encoding="utf-8")
        if "matmaster.adaptors.calculation" in text:
            offenders.append(path)
    assert offenders == []


def test_production_code_no_longer_reads_session_bohrium_credentials() -> None:
    offenders = []
    for path in _iter_python_sources():
        text = path.read_text(encoding="utf-8")
        if "._bohrium_credentials" in text:
            offenders.append(path)
    assert offenders == []
