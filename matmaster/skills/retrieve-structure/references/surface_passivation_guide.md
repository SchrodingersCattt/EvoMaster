# Surface Passivation Quick Guide

When building **hydrogen-passivated semiconductor slabs** (Si-H, Ge-H, etc.):

## Workflow (3 Steps — Target < 5 minutes)

1. **Build the slab** first using `build_surface_slab` (MCP) or pymatgen SlabGenerator. Save immediately.
2. **Passivate** with `passivate_surface.py` — runs in seconds, handles both top and bottom surfaces.
3. **Verify** with `assess_structure.py` — check atom count, formula, dimensionality.

## Script Usage

```bash
python ${SKILL_DIR}/scripts/passivate_surface.py slab.cif -o slab_passivated.cif \
    --element Si --bond-length 1.48 --cutoff 2.6 --target-coordination 4
```

### Element-Specific Defaults

| Element | Bond length (Å) | Cutoff (Å) | Coordination |
|---------|-----------------|------------|--------------|
| Si      | 1.48            | 2.6        | 4            |
| Ge      | 1.53            | 2.7        | 4            |
| C (diamond) | 1.09        | 1.8        | 4            |
| GaAs (Ga) | 1.56          | 2.8        | 4            |
| GaAs (As) | 1.52          | 2.8        | 4            |

For **compound semiconductors** (GaAs, InP, etc.), run passivation twice — once for each element with appropriate bond lengths.

## Common Pitfalls

- **Don't write custom passivation code** — `passivate_surface.py` handles tetrahedral geometry, PBC, and both surfaces automatically.
- **Don't over-engineer slab generation** — a standard pymatgen or MCP slab is sufficient. Save the bare slab before passivation so you have an intermediate deliverable.
- **Check surface_fraction** — if the script misses atoms, increase `--surface-fraction 0.20`.
