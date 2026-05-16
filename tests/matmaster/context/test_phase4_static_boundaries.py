from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_core_compaction_shim_is_removed() -> None:
    shim_path = ROOT / "matmaster/core" / ("context" + "_compactor.py")

    assert not shim_path.exists()


def test_types_context_shim_is_removed() -> None:
    shim_path = ROOT / "matmaster/types" / "context.py"

    assert not shim_path.exists()
