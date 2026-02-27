# Tasker polar surface — reference

## Source

Tasker, P. W. (1979). "The stability of ionic crystal surfaces." *J. Phys. C: Solid State Phys.* **12**, 4977. Classification of ionic crystal surfaces by charge stacking along the surface normal.

## Type 1 (non-polar)

- **Definition**: Stacking along the surface normal has no net dipole per repeat unit; layers are charge-neutral in the perpendicular direction.
- **Examples**:
  - **Rocksalt (100)** (e.g. MgO(100), NiO(100)): Each (100) layer contains both cations and anions in the plane → neutral → Type 1. Safe to cut.
  - **Rutile (110)** (e.g. TiO2(110)): Stoichiometric layers; repeat unit has no net dipole → often treated as Type 1-like for symmetric stoichiometric slabs.

## Type 2 (polar if asymmetric)

- **Definition**: The repeat unit along the normal has no net dipole, but the stacking sequence can create a dipole in a finite slab if the two surfaces are not equivalent.
- **Action**: Choose slab thickness and termination so the slab is symmetric (same chemistry on top and bottom), or use an integer number of repeat units that cancels the dipole.

## Type 3 (polar)

- **Definition**: Each repeat unit along the normal has a net charge (alternating cation-only and anion-only layers) → macroscopic dipole, diverging surface energy in a simple ionic model.
- **Examples**:
  - **ZnO (0001) / (000-1)**: O-terminated and Zn-terminated basal planes; each layer is charged.
  - **Wurtzite / hexagonal (0001)**: Similar alternating charge layers.
- **Stabilization in practice**: Surface reconstruction, adsorbates, or charge transfer (e.g. 2D metallic surface states) can quench the dipole; many polar surfaces are observed and used in calculations with symmetric slabs or explicit stabilization.

## Quick lookup (common oxides)

| Material | Surface | Tasker type | Note |
|----------|---------|-------------|------|
| MgO | (100) | 1 | Non-polar; standard for surface energy. |
| TiO2 rutile | (110) | 1-like | Stoichiometric; bridging O and 5c Ti termination. |
| ZnO | (10-10) | 1-like | Non-polar prism. |
| ZnO | (0001)/(000-1) | 3 | Polar; use symmetric slab or document stabilization. |

For machine-readable (formula, surface) → type + ref, see **reference/tasker_lookup.yaml**; the checker script can use it with `--formula` and `--miller` to cross-validate.
