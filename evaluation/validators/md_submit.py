"""Verify structure quality by resolving the file referenced in md_submit.json."""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

from .structure_distance import check_min_interatomic_distance


def check_md_submit_structure_min_distance(
    workspace_dir: str | Path,
    *,
    submit_file: str = "md_submit.json",
    min_distance_A: float = 1.0,
) -> tuple[bool, str]:
    """Resolve structure from md_submit.json and verify min interatomic distance.

    Parses ``input_dir`` and ``--structure`` from the submit JSON's cmd field,
    locates the structure file, and checks that all atom pairs are at least
    *min_distance_A* apart.
    """
    ws_path = Path(workspace_dir)

    submit_path = ws_path / submit_file
    if not submit_path.is_file():
        hits = list(ws_path.rglob(submit_file))
        if not hits:
            return False, f"{submit_file} not found in workspace"
        submit_path = hits[0]

    try:
        submit_data = json.loads(submit_path.read_text())
    except Exception as exc:
        return False, f"could not parse {submit_file}: {exc}"

    input_dir = submit_data.get("input_dir", ".")
    cmd = submit_data.get("cmd", "")

    m = re.search(r"--structure\s+(\S+)", cmd)
    if m:
        struct_filename = m.group(1)
    else:
        struct_filename = "*.xyz"

    search_dir = ws_path / input_dir
    if not search_dir.is_dir():
        search_dir = ws_path

    if "*" in struct_filename or "?" in struct_filename:
        hits = [
            p
            for p in search_dir.rglob("*")
            if p.is_file() and fnmatch.fnmatch(p.name, struct_filename)
        ]
    else:
        exact = search_dir / struct_filename
        hits = [exact] if exact.is_file() else list(search_dir.rglob(struct_filename))

    if not hits:
        return False, f"structure file {struct_filename!r} not found in {search_dir}"

    struct_path = hits[0]
    return check_min_interatomic_distance(
        str(struct_path.parent),
        filename=struct_path.name,
        min_distance_A=min_distance_A,
    )
