# NEP Training Data Format Reference

Official source: https://gpumd.org/nep/input_files/train_test_xyz.html

`train.xyz` and `test.xyz` are both required by the `nep` executable. They use
extended XYZ format. Each structure contains `N + 2` lines.

## Per-Structure Layout

```text
N
lattice="ax ay az bx by bz cx cy cz" energy=<E> properties=species:S:1:pos:R:3:force:R:3
El1 x1 y1 z1 fx1 fy1 fz1
El2 x2 y2 z2 fx2 fy2 fz2
...
```

Line 1:

- Single integer `N`, the number of atoms.

Line 2:

- `lattice="ax ay az bx by bz cx cy cz"` is mandatory.
- `energy=<value>` is mandatory for potential training.
- `virial="vxx vxy vxz vyx vyy vyz vzx vzy vzz"` is optional.
- `stress="sxx sxy sxz syx syy syz szx szy szz"` is optional.
- If both `virial` and `stress` are present, GPUMD uses `virial`.
- `weight=<relative_weight>` is optional.
- `properties=...` is mandatory.

Atom lines:

- Must match the columns declared in `properties`.
- `species:S:1`, `pos:R:3`, and `force:R:3` or `forces:R:3` are the main fields
  for potential training.

## Units

- Lattice vectors and positions: Angstrom.
- Energy: eV, total per structure/cell, not per atom.
- Forces: eV/Angstrom.
- Virial: eV, total per structure/cell.
- Stress: eV/Angstrom^3.
- BEC: elementary charge `e`.
- Dipole and polarizability can use user-chosen units, but the same convention
  must be used consistently.

## Potential Training

For a standard potential model, include:

```text
lattice="..." energy=... virial="..." properties=species:S:1:pos:R:3:force:R:3
```

If virial/stress data is absent from all structures, set this in `nep.in`:

```text
lambda_v 0.0
```

The energy and virial targets are total structure quantities. Do not convert
them to per-atom values.

## Dipole / Polarizability Models

If training a dipole model, GPUMD ignores energy, virial, stress, and force. Add
the structure-level dipole:

```text
dipole="dx dy dz"
```

If training a polarizability model, GPUMD ignores energy, virial, stress, force,
and dipole. Add:

```text
pol="pxx pxy pxz pyx pyy pyz pzx pzy pzz"
```

Only use these modes when the user asks for dipole/polarizability NEP training.

## BEC Data

`bec:R:9` can be included as an optional atomic property:

```text
properties=species:S:1:pos:R:3:force:R:3:bec:R:9
```

BEC data can be present for only some structures.

## Periodicity and Cell Size

The NEP training format assumes periodic boundary conditions in all directions.
If the box thickness in one direction is smaller than twice the radial cutoff,
the code internally replicates the box in that direction.

## Quality Guards

- `train.xyz` and `test.xyz` should use the same field conventions.
- Element symbols are case-sensitive and must match `type` in `nep.in`.
- Every atom line must contain the exact number of fields declared by
  `properties`.
- The minimum number of atoms in one configuration is 1.
- Avoid very large negative reference energies. The official docs warn that
  because NEP training uses single precision, accuracy can be lost when any
  reference energy is smaller than about `-100 eV/atom`.
