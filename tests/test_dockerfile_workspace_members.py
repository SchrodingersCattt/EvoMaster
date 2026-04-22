from __future__ import annotations

from pathlib import Path


def test_dockerfile_copies_bohrium_transfer_workspace_before_uv_install() -> None:
    lines = Path("Dockerfile").read_text(encoding="utf-8").splitlines()

    install_line_index = next(
        index
        for index, line in enumerate(lines)
        if "uv pip install -e ." in line or "uv sync" in line
    )
    copied_before_install = any(
        line.strip().startswith("COPY packages/bohrium-transfer")
        for line in lines[:install_line_index]
    )

    assert copied_before_install
