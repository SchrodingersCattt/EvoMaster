#!/usr/bin/env python3
"""
render_abacus_workflow.py — Generate multi-step ABACUS workflow files.

Supports:
  - scf_band:  SCF + NSCF (band structure) — two-step with run.sh
  - scf_dos:   SCF + NSCF (DOS) — two-step with run.sh
  - scf_only:  Single SCF with all companion files (INPUT, STRU, KPT)
  - relax:     Geometry relaxation with all companion files
  - cell_relax: Cell relaxation with all companion files
  - vacancy:   Vacancy/BSSE supercell SCF (uses kspacing)
  - workfunction: Work function / slab potential with dipole correction

Usage:
  python render_abacus_workflow.py --workflow scf_band --output-dir ./abacus_band/
  python render_abacus_workflow.py --workflow relax --output-dir ./abacus_relax/ --param ntype=2
  python render_abacus_workflow.py --workflow vacancy --output-dir ./vac/ --param nspin=2

Output: Creates directory with all required ABACUS input files + run.sh for Bohrium.
"""

import argparse
import json
import sys
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))


def _parse_params(param_list: list[str]) -> dict:
    """Parse KEY=VALUE list to dict."""
    params: dict = {}
    for item in param_list:
        if "=" not in item:
            continue
        key, _, value = item.partition("=")
        params[key.strip()] = value.strip()
    return params


def _render_run_sh(steps: list[dict]) -> str:
    """Generate run.sh for multi-step workflow on Bohrium."""
    lines = ["#!/bin/bash", "set -e", ""]
    for i, step in enumerate(steps, 1):
        lines.append(f"# Step {i}: {step['label']}")
        lines.append(f"cp {step['input_file']} INPUT")
        lines.append(f"cp {step['kpt_file']} KPT")
        lines.append("OMP_NUM_THREADS=1 mpirun -np 16 abacus > log_step{i} 2>&1".format(i=i))
        lines.append("")
    return "\n".join(lines) + "\n"


def generate_scf_band(params: dict, output_dir: Path) -> dict:
    """Generate two-step SCF → NSCF band structure workflow."""
    from engine.renderer import RenderIntent
    from engine.software.abacus import AbacusBackend

    backend = AbacusBackend()

    # Step 1: SCF
    scf_params = dict(params)
    scf_params["out_chg"] = "1"
    scf_params["kpoint_file"] = "KPT_scf"
    scf_intent = RenderIntent(
        software="abacus", task_type="scf",
        structure_file=None, params=scf_params,
    )
    scf_files = backend.render_all(scf_intent)

    # Step 2: NSCF band
    nscf_params = dict(params)
    nscf_params["kpoint_file"] = "KPT_band"
    nscf_params.setdefault("nbands", "40")
    nscf_intent = RenderIntent(
        software="abacus", task_type="band",
        structure_file=None, params=nscf_params,
    )
    nscf_files = backend.render_all(nscf_intent)

    # Write files
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []

    # SCF INPUT
    (output_dir / "INPUT_scf").write_text(scf_files["INPUT"], encoding="utf-8")
    written.append("INPUT_scf")

    # NSCF INPUT
    (output_dir / "INPUT_nscf").write_text(nscf_files["INPUT"], encoding="utf-8")
    written.append("INPUT_nscf")

    # STRU (shared)
    (output_dir / "STRU").write_text(scf_files["STRU"], encoding="utf-8")
    written.append("STRU")

    # KPT_scf (uniform mesh)
    (output_dir / "KPT_scf").write_text(scf_files["KPT"], encoding="utf-8")
    written.append("KPT_scf")

    # KPT_band (line-mode)
    (output_dir / "KPT_band").write_text(nscf_files["KPT"], encoding="utf-8")
    written.append("KPT_band")

    # run.sh
    run_sh = _render_run_sh([
        {"label": "SCF (charge density)", "input_file": "INPUT_scf", "kpt_file": "KPT_scf"},
        {"label": "NSCF (band structure)", "input_file": "INPUT_nscf", "kpt_file": "KPT_band"},
    ])
    (output_dir / "run.sh").write_text(run_sh, encoding="utf-8")
    written.append("run.sh")

    return {"success": True, "files": written, "workflow": "scf_band"}


def generate_scf_dos(params: dict, output_dir: Path) -> dict:
    """Generate two-step SCF → NSCF DOS workflow."""
    from engine.renderer import RenderIntent
    from engine.software.abacus import AbacusBackend

    backend = AbacusBackend()

    # Step 1: SCF
    scf_params = dict(params)
    scf_params["out_chg"] = "1"
    scf_params["kpoint_file"] = "KPT_scf"
    scf_intent = RenderIntent(
        software="abacus", task_type="scf",
        structure_file=None, params=scf_params,
    )
    scf_files = backend.render_all(scf_intent)

    # Step 2: NSCF DOS
    nscf_params = dict(params)
    nscf_params["kpoint_file"] = "KPT_dos"
    nscf_params.setdefault("nbands", "40")
    nscf_intent = RenderIntent(
        software="abacus", task_type="dos",
        structure_file=None, params=nscf_params,
    )
    nscf_files = backend.render_all(nscf_intent)

    # For DOS, use a dense uniform mesh (not line-mode)
    dos_kpt = "K_POINTS\n0\nGamma\n12 12 12 0 0 0\n"

    # Write files
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []

    (output_dir / "INPUT_scf").write_text(scf_files["INPUT"], encoding="utf-8")
    written.append("INPUT_scf")
    (output_dir / "INPUT_nscf").write_text(nscf_files["INPUT"], encoding="utf-8")
    written.append("INPUT_nscf")
    (output_dir / "STRU").write_text(scf_files["STRU"], encoding="utf-8")
    written.append("STRU")
    (output_dir / "KPT_scf").write_text(scf_files["KPT"], encoding="utf-8")
    written.append("KPT_scf")
    (output_dir / "KPT_dos").write_text(dos_kpt, encoding="utf-8")
    written.append("KPT_dos")

    run_sh = _render_run_sh([
        {"label": "SCF (charge density)", "input_file": "INPUT_scf", "kpt_file": "KPT_scf"},
        {"label": "NSCF (DOS)", "input_file": "INPUT_nscf", "kpt_file": "KPT_dos"},
    ])
    (output_dir / "run.sh").write_text(run_sh, encoding="utf-8")
    written.append("run.sh")

    return {"success": True, "files": written, "workflow": "scf_dos"}


def generate_single_task(task: str, params: dict, output_dir: Path) -> dict:
    """Generate single-step task with all companion files."""
    from engine.renderer import RenderIntent
    from engine.software.abacus import AbacusBackend

    backend = AbacusBackend()
    intent = RenderIntent(
        software="abacus", task_type=task,
        structure_file=None, params=params,
    )
    files = backend.render_all(intent)

    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for fname, content in sorted(files.items()):
        (output_dir / fname).write_text(content, encoding="utf-8")
        written.append(fname)

    # Single-step run.sh
    run_sh = (
        "#!/bin/bash\nset -e\n\n"
        "OMP_NUM_THREADS=1 mpirun -np 16 abacus > log 2>&1\n"
    )
    (output_dir / "run.sh").write_text(run_sh, encoding="utf-8")
    written.append("run.sh")

    return {"success": True, "files": written, "workflow": task}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate multi-step ABACUS workflow files."
    )
    ap.add_argument(
        "--workflow",
        required=True,
        choices=[
            "scf_band", "scf_dos", "scf_only", "scf",
            "relax", "cell_relax", "cell-relax",
            "vacancy", "defect", "bsse",
            "workfunction", "work_function",
            "dftu", "dft+u",
            "md",
            "spin_scf", "magnetic",
        ],
        help="Workflow type to generate.",
    )
    ap.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write all workflow files.",
    )
    ap.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Override parameter (repeatable).",
    )
    ap.add_argument(
        "--overrides",
        default=None,
        help="JSON object with parameter overrides.",
    )
    args = ap.parse_args()

    params = _parse_params(args.param)
    if args.overrides:
        try:
            params.update(json.loads(args.overrides))
        except json.JSONDecodeError as e:
            print(f"Error: invalid --overrides JSON: {e}", file=sys.stderr)
            sys.exit(1)

    output_dir = Path(args.output_dir)
    workflow = args.workflow.lower().replace("-", "_")

    if workflow == "scf_band":
        result = generate_scf_band(params, output_dir)
    elif workflow == "scf_dos":
        result = generate_scf_dos(params, output_dir)
    elif workflow in ("scf_only", "scf"):
        result = generate_single_task("scf", params, output_dir)
    elif workflow == "relax":
        result = generate_single_task("relax", params, output_dir)
    elif workflow in ("cell_relax", "cell-relax"):
        result = generate_single_task("cell-relax", params, output_dir)
    elif workflow in ("vacancy", "defect", "bsse"):
        result = generate_single_task("vacancy", params, output_dir)
    elif workflow in ("workfunction", "work_function"):
        result = generate_single_task("workfunction", params, output_dir)
    elif workflow in ("dftu", "dft+u"):
        result = generate_single_task("dftu", params, output_dir)
    elif workflow == "md":
        result = generate_single_task("md", params, output_dir)
    elif workflow in ("spin_scf", "magnetic"):
        result = generate_single_task("spin_scf", params, output_dir)
    else:
        result = {"success": False, "error": f"Unknown workflow: {workflow}"}

    print(json.dumps(result, indent=2))
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
