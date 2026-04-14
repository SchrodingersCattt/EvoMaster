# ABACUS STRU File Format (Detailed)

The STRU file has five sections, in order:

## ATOMIC_SPECIES
```
ATOMIC_SPECIES
<Label> <Mass> <PseudopotentialFile>
```
One line per species. For ghost/empty atoms, create a separate species entry (e.g. `Fe_empty`) with the same PP and orbital files but treat it as a distinct `ntype`.

## NUMERICAL_ORBITAL (LCAO only)
```
NUMERICAL_ORBITAL
<OrbitalFile_for_species1>
<OrbitalFile_for_species2>
```
One `.orb` file per species, same order as ATOMIC_SPECIES. Required for `basis_type lcao`. Omit for `basis_type pw`.

## LATTICE_CONSTANT and LATTICE_VECTORS
```
LATTICE_CONSTANT
1.8897259886  // 1 Angstrom in Bohr

LATTICE_VECTORS
a1x  a1y  a1z
a2x  a2y  a2z
a3x  a3y  a3z
```
Using `1.8897259886` means vectors are in Angstrom.

## ATOMIC_POSITIONS
```
ATOMIC_POSITIONS
<CoordinateType>
<Label>
<InitialMagneticMoment>
<NumberOfAtoms>
x1 y1 z1  m mx my mz
```
- `CoordinateType`: `Direct` (fractional), `Cartesian_angstrom`, `Cartesian_au`, `Cartesian`
- `m mx my mz`: mobility; `1 1 1` = free, `0 0 0` = frozen
- `InitialMagneticMoment`: Bohr magnetons. `0.0` for non-magnetic.
- For multiple species: repeat label/moment/count/coords block per species, same order as ATOMIC_SPECIES.

**Multi-species example** (Fe + ghost atoms):
```
ATOMIC_POSITIONS
Cartesian_angstrom
Fe
2.0
4
0.000  0.000  0.000  1 1 1
1.435  1.435  1.435  1 1 1
0.000  2.870  0.000  1 1 1
2.870  0.000  0.000  1 1 1
Fe_empty
0.0
2
0.000  0.000  4.300  0 0 0
1.435  1.435  5.735  0 0 0
```

---

## Ghost/Empty Atoms for BSSE Correction (LCAO)

LCAO numerical orbitals are atom-centered. Removing an atom (vacancy) or surface boundary removes basis functions → **BSSE**.

**Fix**: ghost atoms contribute basis functions but zero valence charge and zero magnetic moment:
1. `ATOMIC_SPECIES`: add ghost species (e.g. `Fe_empty`) with **same** `.upf` and `.orb`
2. `NUMERICAL_ORBITAL`: add same `.orb` for ghost species
3. `INPUT`: set `ntype` = total species count (real + ghost)
4. `ATOMIC_POSITIONS`: ghost atoms with moment `0.0`, mobility `0 0 0`

**Vacancy example** (bcc Fe):
```
ATOMIC_SPECIES
Fe      55.845  Fe.upf
Fe_empty 55.845  Fe.upf

NUMERICAL_ORBITAL
Fe_gga_9au_100Ry_4s2p2d1f.orb
Fe_gga_9au_100Ry_4s2p2d1f.orb
```
INPUT: `ntype 2`, typically `nspin 2` for magnetic Fe.

**Surface slab**: place empty atoms ~2.0 A from outermost real atoms, in vacuum on both sides.
