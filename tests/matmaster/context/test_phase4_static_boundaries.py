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


def test_model_history_restore_service_shim_is_removed() -> None:
    shim_path = ROOT / "src/services" / ("history" + "_restore_service.py")

    assert not shim_path.exists()


def test_agent_run_instructions_helper_is_removed() -> None:
    helper_path = ROOT / "src/services" / ("agent_run" + "_instructions.py")

    assert not helper_path.exists()
