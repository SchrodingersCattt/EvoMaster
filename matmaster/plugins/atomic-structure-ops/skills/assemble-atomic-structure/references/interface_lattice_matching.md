# Interface Lattice Matching with ZSLGenerator

Use `ZSLGenerator` from `pymatgen.analysis.interfaces.zsl` to find the
smallest-area superlattice match within a strain budget.

```python
import numpy as np
from pymatgen.analysis.interfaces.zsl import ZSLGenerator
from pymatgen.core.surface import SlabGenerator

# Slab generation — use filter_out_sym_slabs=False to avoid
# StructureMatcher numpy compatibility issues
slab_gen = SlabGenerator(bulk, miller, min_slab_size=8, min_vacuum_size=15)
slabs = slab_gen.get_slabs(symmetrize=False, filter_out_sym_slabs=False)

# Lattice matching — enumerate all matches, sort by area
zsl = ZSLGenerator(max_area_ratio_tol=0.09, max_angle_tol=0.01,
                   max_length_tol=0.03)
matches = list(zsl(slab_a.lattice.matrix[:2], slab_b.lattice.matrix[:2],
                   lowest=True))
# Sort by interface area and pick the smallest within strain budget
matches.sort(key=lambda m: m.match_area)

# Strain calculation — use the pre-computed sl_vectors from ZSLMatch,
# do NOT recompute via transformation @ original_lattice (breaks for
# non-orthogonal cells like hexagonal).
def calc_strain(m):
    fa, fb = np.linalg.norm(m.film_sl_vectors[0]), np.linalg.norm(m.film_sl_vectors[1])
    sa, sb = np.linalg.norm(m.substrate_sl_vectors[0]), np.linalg.norm(m.substrate_sl_vectors[1])
    return abs(fa - sa) / sa, abs(fb - sb) / sb

best = min((m for m in matches if max(calc_strain(m)) < 0.05),
           key=lambda m: m.match_area)
```

Higher-level `SubstrateAnalyzer`/`CoherentInterfaceBuilder` can also work but
may trigger internal bugs; `ZSLGenerator` is more robust. Fall back to manual
stacking if no candidate fits.
