"""
Convert between atomic structure file formats using dpdata.

Handles CIF (via pymatgen/ASE intermediary), POSCAR, LAMMPS data/dump,
XYZ, Gaussian, GROMACS, ABACUS, DeePMD, and more.

Usage examples:
  # CIF -> POSCAR
  python convert_format.py --input struct.cif --output POSCAR --output-fmt vasp/poscar

  # POSCAR -> LAMMPS data (need type_map for element ordering)
  python convert_format.py --input POSCAR --output data.lmp \
      --input-fmt vasp/poscar --output-fmt lammps/lmp --type-map O H

  # LAMMPS dump -> POSCAR (need type_map to map numeric types to elements)
  python convert_format.py --input dump.lammpstrj --output POSCAR \
      --input-fmt lammps/dump --output-fmt vasp/poscar --type-map O H

  # LAMMPS full-style data -> POSCAR
  python convert_format.py --input data.lmp --output POSCAR \
      --input-fmt lammps/lmp --output-fmt vasp/poscar \
      --type-map O H --atom-style full

  # POSCAR -> LAMMPS data (atom_style full, charges zeroed)
  python convert_format.py --input POSCAR --output data.lmp \
      --output-fmt lammps/lmp --type-map O H --atom-style full

Output: JSON to stdout with success, output path, and structure info.
"""

import argparse
import json
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Format auto-detection
# ---------------------------------------------------------------------------

# Map of file extensions to dpdata format strings.
# "cif" is a sentinel: handled specially via pymatgen/ASE intermediary.
_EXT_TO_FMT: dict[str, str] = {
    ".cif": "cif",
    ".vasp": "vasp/poscar",
    ".lmp": "lammps/lmp",
    ".data": "lammps/lmp",       # common LAMMPS data file extension
    ".dump": "lammps/dump",
    ".lammpstrj": "lammps/dump",
    ".xyz": "xyz",
    ".extxyz": "mace/xyz",
    ".gjf": "gaussian/gjf",
    ".gro": "gromacs/gro",
    ".stru": "abacus/stru",
    ".mol": "mol_file",
    ".sdf": "sdf_file",
}

# Bare file names (no extension) -> format
_NAME_TO_FMT: dict[str, str] = {
    "poscar": "vasp/poscar",
    "contcar": "vasp/poscar",
}

# Formats that REQUIRE --type-map (LAMMPS uses integer atom types)
_NEEDS_TYPE_MAP = {"lammps/lmp", "lammps/dump", "lmp", "dump"}


def _guess_fmt(filepath: str) -> str | None:
    """Guess dpdata format string from file path."""
    p = Path(filepath)
    # Check bare name first (POSCAR, CONTCAR)
    if fmt := _NAME_TO_FMT.get(p.name.lower()):
        return fmt
    # Then check extension
    if fmt := _EXT_TO_FMT.get(p.suffix.lower()):
        return fmt
    return None


# ---------------------------------------------------------------------------
# CIF loading: dpdata has no native CIF reader -> use pymatgen / ASE first
# ---------------------------------------------------------------------------

def _load_cif(filepath: str) -> "dpdata.System":
    """Read CIF via pymatgen Structure or ASE Atoms, then wrap into dpdata.System.

    CIF files may contain symmetry operations, partial occupancies, etc.
    pymatgen is preferred because it handles space-group expansion natively.
    """
    import dpdata

    # Strategy 1: pymatgen (recommended)
    try:
        from pymatgen.core import Structure as PmgStructure

        struct = PmgStructure.from_file(filepath)
        return dpdata.System(struct, fmt="pymatgen/structure")
    except Exception:
        pass

    # Strategy 2: ASE
    try:
        from ase.io import read as ase_read

        atoms = ase_read(filepath, format="cif")
        return dpdata.System(atoms, fmt="ase/structure")
    except Exception:
        pass

    raise RuntimeError(
        f"Cannot read CIF file '{filepath}'. "
        "Ensure pymatgen or ASE is installed:\n"
        "  pip install -e '.[calculation]'"
    )


# ---------------------------------------------------------------------------
# LAMMPS atom_style-aware writing
# ---------------------------------------------------------------------------

def _write_lammps_lmp(
    system: "dpdata.System",
    output: str,
    frame_idx: int = 0,
    atom_style: str = "atomic",
    type_map: list[str] | None = None,
) -> None:
    """Write LAMMPS data file with the requested atom_style.

    For ``atomic`` style dpdata writes natively.  For other styles
    (``full``, ``charge``, etc.) we go through ASE whose ``lammps-data``
    writer has solid atom_style support.  ``specorder`` is set explicitly
    to guarantee the LAMMPS type numbering matches the user's type_map.

    dpdata's own ``to("lammps/lmp")`` only reliably writes *atomic* style;
    its writer does NOT accept an ``atom_style`` kwarg in most versions.
    """
    if atom_style == "atomic":
        system.to("lammps/lmp", output, frame_idx=frame_idx)
        return

    # --- Non-atomic styles: go through ASE ---
    _write_lammps_via_ase(system, output, frame_idx, atom_style, type_map)


def _write_lammps_via_ase(
    system: "dpdata.System",
    output: str,
    frame_idx: int,
    atom_style: str,
    type_map: list[str] | None = None,
) -> None:
    """Convert dpdata.System -> ASE Atoms -> LAMMPS data file.

    Key details
    -----------
    * ``specorder=type_map`` is passed to ASE so that the LAMMPS integer
      type numbering is *exactly* the user's type_map ordering (type 1 =
      type_map[0], type 2 = type_map[1], ...).  Without this ASE would
      assign types by atomic-number order, which may silently differ.
    * For ``full`` / ``charge`` styles, charges are attached.  If the
      source data has no charges they default to 0.0.
    """
    import numpy as np
    from ase import Atoms
    from ase.io import write as ase_write

    data = system.data
    cell = data["cells"][frame_idx]
    coords = data["coords"][frame_idx]
    atom_names = data["atom_names"]
    atom_types = data["atom_types"]
    symbols = [atom_names[t] for t in atom_types]

    atoms = Atoms(symbols=symbols, positions=coords, cell=cell, pbc=True)

    # Attach charges if available (needed for full / charge styles)
    if atom_style in ("full", "charge"):
        charges = data.get("charges")
        if charges is not None:
            q = charges[frame_idx] if charges.ndim > 1 else charges
        else:
            q = np.zeros(len(atoms))
        atoms.set_initial_charges(q)

    # specorder guarantees LAMMPS type numbers match the user's type_map.
    # Without it ASE sorts by atomic number -> type ordering may differ.
    write_kwargs: dict = {"format": "lammps-data", "atom_style": atom_style}
    if type_map:
        write_kwargs["specorder"] = type_map

    ase_write(output, atoms, **write_kwargs)


# ---------------------------------------------------------------------------
# Main conversion logic
# ---------------------------------------------------------------------------

def convert(
    input_path: str,
    output_path: str,
    input_fmt: str | None = None,
    output_fmt: str | None = None,
    type_map: list[str] | None = None,
    atom_style: str | None = None,
    frame_idx: int = 0,
) -> dict:
    """Perform format conversion.  Returns a result dict (JSON-serialisable)."""
    try:
        import dpdata
    except ImportError:
        return {
            "success": False,
            "error": (
                "dpdata is not installed. "
                "Install with: pip install -e '.[calculation]'"
            ),
        }

    # --- Resolve formats ---
    if input_fmt is None:
        input_fmt = _guess_fmt(input_path)
        if input_fmt is None:
            return {
                "success": False,
                "error": (
                    f"Cannot auto-detect input format for '{input_path}'. "
                    "Please specify --input-fmt."
                ),
            }
    if output_fmt is None:
        output_fmt = _guess_fmt(output_path)
        if output_fmt is None:
            return {
                "success": False,
                "error": (
                    f"Cannot auto-detect output format for '{output_path}'. "
                    "Please specify --output-fmt."
                ),
            }

    # --- Validate type_map for LAMMPS formats ---
    for fmt, label in [(input_fmt, "input"), (output_fmt, "output")]:
        if fmt in _NEEDS_TYPE_MAP and not type_map:
            return {
                "success": False,
                "error": (
                    f"LAMMPS {label} format '{fmt}' requires --type-map to "
                    "map integer atom types to element symbols "
                    "(e.g., --type-map O H means type 1=O, type 2=H)."
                ),
            }

    # --- Load structure ---
    try:
        if input_fmt == "cif":
            system = _load_cif(input_path)
        else:
            kwargs: dict = {}
            if type_map and input_fmt in _NEEDS_TYPE_MAP:
                kwargs["type_map"] = type_map
            if atom_style and input_fmt in {"lammps/lmp", "lmp"}:
                kwargs["atom_style"] = atom_style
            system = dpdata.System(input_path, fmt=input_fmt, **kwargs)
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to read '{input_path}' as '{input_fmt}': {e}",
        }

    nframes = system.get_nframes()
    if nframes == 0:
        return {"success": False, "error": f"No frames found in '{input_path}'."}

    # Resolve negative frame index
    if frame_idx < 0:
        frame_idx = nframes + frame_idx

    # --- Apply type_map ordering (reorder atom types for consistency) ---
    if type_map:
        try:
            system.apply_type_map(type_map)
        except Exception:
            # Non-fatal: system may contain types not in type_map
            try:
                system.sort_atom_names(type_map=type_map)
            except Exception:
                pass

    # --- Create parent directory for output ---
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # --- Write output ---
    try:
        if output_fmt in {"lammps/lmp", "lmp"}:
            out_style = atom_style or "atomic"
            _write_lammps_lmp(system, output_path,
                              frame_idx=frame_idx, atom_style=out_style,
                              type_map=type_map)
        else:
            try:
                system.to(output_fmt, output_path, frame_idx=frame_idx)
            except TypeError:
                # Some formats don't accept frame_idx; extract frame first
                if nframes > 1:
                    sub = system.sub_system([frame_idx])
                    sub.to(output_fmt, output_path)
                else:
                    system.to(output_fmt, output_path)
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to write '{output_path}' as '{output_fmt}': {e}",
        }

    # --- Build summary ---
    info = {
        "atom_names": list(system["atom_names"]),
        "atom_numbs": [int(n) for n in system["atom_numbs"]],
        "natoms": int(system.get_natoms()),
        "nframes": int(nframes),
        "frame_used": frame_idx,
    }
    return {"success": True, "output": str(output_path), "info": info}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

SUPPORTED_FORMATS_HELP = textwrap.dedent("""\
    Commonly used format strings:
      vasp/poscar   - VASP POSCAR / CONTCAR
      lammps/lmp    - LAMMPS data file  (requires --type-map; see --atom-style)
      lammps/dump   - LAMMPS dump       (requires --type-map)
      cif           - CIF               (read via pymatgen/ASE intermediary)
      xyz           - Simple XYZ
      mace/xyz      - Extended XYZ (aliases: extxyz, nequip/xyz, quip/gap/xyz)
      gaussian/gjf  - Gaussian input
      gromacs/gro   - GROMACS GRO
      abacus/stru   - ABACUS STRU
      deepmd/raw    - DeePMD raw format  (directory)
      deepmd/npy    - DeePMD compressed  (directory)

    LAMMPS notes:
      * --type-map is REQUIRED for LAMMPS formats.  It maps integer atom
        types to element symbols: --type-map O H  means type 1=O, type 2=H.
      * --atom-style selects the column layout for lammps/lmp files.
        Default is "atomic" (ID type x y z).
        Use "full" for molecular systems (ID mol-ID type charge x y z).
        If the source .lmp was written with atom_style full but you omit
        --atom-style full, the columns will be MISPARSED silently.
""")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert between atomic structure file formats (dpdata).",
        epilog=SUPPORTED_FORMATS_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--input", required=True, help="Input structure file path")
    ap.add_argument("--output", required=True, help="Output file path")
    ap.add_argument(
        "--input-fmt", default=None,
        help="dpdata format string for input (auto-detected from filename if omitted)",
    )
    ap.add_argument(
        "--output-fmt", default=None,
        help="dpdata format string for output (auto-detected from filename if omitted)",
    )
    ap.add_argument(
        "--type-map", nargs="+", default=None,
        help=(
            "Element ordering for LAMMPS types, e.g. --type-map O H "
            "(type 1=O, type 2=H).  REQUIRED for lammps/lmp and lammps/dump."
        ),
    )
    ap.add_argument(
        "--atom-style",
        default=None,
        choices=["atomic", "charge", "full", "bond", "angle", "molecular"],
        help=(
            "LAMMPS atom_style for reading/writing lammps/lmp files.  "
            "Default: atomic.  Use 'full' for molecular systems with charges."
        ),
    )
    ap.add_argument(
        "--frame",
        type=int,
        default=0,
        help="Frame index to convert (default: 0 = first).  Use -1 for last frame.",
    )
    args = ap.parse_args()

    result = convert(
        input_path=args.input,
        output_path=args.output,
        input_fmt=args.input_fmt,
        output_fmt=args.output_fmt,
        type_map=args.type_map,
        atom_style=args.atom_style,
        frame_idx=args.frame,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
