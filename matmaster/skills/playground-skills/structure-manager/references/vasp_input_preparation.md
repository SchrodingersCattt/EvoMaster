# VASP Input File Preparation

When generating VASP input files locally (not running VASP — writing input files only):

## Complete Input Set

A runnable VASP calculation requires at minimum **INCAR + KPOINTS + POSCAR**. When a task asks you to "generate input files" or "prepare inputs" for VASP, always produce all three even if only INCAR is explicitly mentioned. POTCAR is license-restricted; note the path but do not generate it.

- **POSCAR**: Generate from task-provided structure info (formula, space group, lattice constant). Use pymatgen or ASE to build and write POSCAR.
- **KPOINTS**: Use `generate_kpoints.py` from this skill, or write manually for simple meshes.
- **INCAR**: Write all required parameters. Read any spec/JSON config first.

## Batch / Convergence Tests (Parameter Sweeps)

When generating multiple INCAR files that vary a single parameter (e.g., ENCUT sweep):

1. **Byte-identical non-sweep content**: Every line except the swept parameter must be identical across all files — including `SYSTEM`. Use a fixed `SYSTEM` tag like `SYSTEM = Al FCC convergence test` without encoding the sweep value in it.
2. **Single KPOINTS + single POSCAR**: Write one shared KPOINTS and one shared POSCAR file. Do not duplicate per-ENCUT.
3. **Naming**: Use the pattern from the task (e.g., `INCAR_ENCUT280`).

## Structure Construction for POSCAR

For common structures from spec parameters:

```python
from pymatgen.core import Structure, Lattice
# FCC example: Al, a=4.05 Å, Fm-3m
lattice = Lattice.cubic(4.05)
structure = Structure(lattice, ["Al"]*4,
    [[0,0,0],[0.5,0.5,0],[0.5,0,0.5],[0,0.5,0.5]])
structure.to(filename="POSCAR", fmt="poscar")
```

Always verify atom count and formula after construction.
