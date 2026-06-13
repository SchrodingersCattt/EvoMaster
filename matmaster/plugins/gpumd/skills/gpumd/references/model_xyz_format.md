# GPUMD model.xyz Format Reference

GPUMD uses an extended XYZ format for `model.xyz`. This is **not** standard XYZ — it has a specific header format and optional group/velocity columns.

## Basic Format

```
N
lattice="ax ay az bx by bz cx cy cz" pbc="T T T" Properties=species:S:1:pos:R:3
El  x1  y1  z1
El  x2  y2  z2
...
```

- **Line 1**: Number of atoms `N`
- **Line 2**: Extended XYZ comment line with key=value pairs:
  - `lattice="ax ay az bx by bz cx cy cz"` — 9 floats: row-major lattice vectors (a, b, c) in Angstrom
  - `pbc="T T T"` — periodic boundary conditions (T/F for each direction)
  - `Properties=species:S:1:pos:R:3` — column definitions
- **Lines 3+**: One line per atom: `element x y z`

## With Group Labels (required for NEMD source/sink, group-based compute_*)

To define atom groups, add a `group` column to Properties:

```
N
lattice="ax ay az bx by bz cx cy cz" pbc="T T T" Properties=species:S:1:pos:R:3:group:I:1
El  x1  y1  z1  0
El  x2  y2  z2  1
El  x3  y3  z3  2
...
```

- `group:I:1` adds an integer group column
- Group indices start at 0
- For NEMD: typically group 0 = bulk, group 1 = heat source, group 2 = heat sink
- Reference groups in `run.in` as `source <g1> sink <g2>`

## With Multiple Group Methods

GPUMD supports multiple independent grouping schemes:

```
N
lattice="..." pbc="T T T" Properties=species:S:1:pos:R:3:group:I:2
El  x1  y1  z1  0  0
El  x2  y2  z2  1  0
El  x3  y3  z3  2  1
...
```

- `group:I:2` means 2 group columns (group_method 0 and group_method 1)
- In `run.in`, reference as `group_method <idx>` in compute/dump keywords

## With Velocities

```
N
lattice="..." pbc="T T T" Properties=species:S:1:pos:R:3:vel:R:3
El  x1  y1  z1  vx1  vy1  vz1
...
```

If velocities are provided in model.xyz, the `velocity` keyword in run.in is not needed.

## Key Rules

1. **Lattice is mandatory** for periodic systems. GPUMD does not infer cell from positions.
2. **Species must match the NEP potential** — element symbols in model.xyz must exactly match those the potential was trained on.
3. **Coordinates are Cartesian** (Angstrom), not fractional.
4. **Group labels must be pre-assigned** in model.xyz before the simulation — GPUMD does not auto-partition atoms into groups.
