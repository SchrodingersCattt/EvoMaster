from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_core_compaction_shim_is_removed() -> None:
    shim_path = ROOT / "matmaster/core" / ("context" + "_compactor.py")

    assert not shim_path.exists()


def test_types_context_shim_is_removed() -> None:
    shim_path = ROOT / "matmaster/types" / "context.py"

    assert not shim_path.exists()


def test_types_current_input_shim_is_removed() -> None:
    shim_path = ROOT / "matmaster/types" / ("current" + "_input.py")

    assert not shim_path.exists()


def test_manifests_package_is_removed() -> None:
    package_path = ROOT / "matmaster" / "manifests"
    tests_path = ROOT / "tests/matmaster" / "manifests"

    assert not package_path.exists()
    assert not tests_path.exists()
