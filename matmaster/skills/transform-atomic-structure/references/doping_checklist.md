# Doping and Defect Acceptance Checklist

Every doping/defect result must be checked and reported:

1. **Stoichiometry**: actual replacement/removal count equals the requested
   count or `round(site_count * fraction)`. Output formula must equal input
   formula minus removed species plus replacement species.
2. **Charge balance**: if oxidation states are provided or inferable, total
   charge after substitution must be close to neutral. If not neutral, report
   the explicit compensation strategy (`anion_adjust`, `cation_vacancy`,
   `anion_vacancy`, `mixed`, or user-approved uncompensated charge).
3. **Minimum distance**: no interatomic distance below the accepted threshold
   (default 0.5 A unless the task sets another value).
4. **Symmetry trace**: report space group before and after. Random substitutions
   may lower symmetry; ordered/Wyckoff substitutions should preserve intended
   symmetry or explain why it changed.
5. **Wyckoff fidelity**: in Wyckoff mode, every substituted atom must belong to
   the requested Wyckoff label/group under `SpacegroupAnalyzer`.
6. **Multi-rule disjointness**: multiple doping rules must not select the same
   site twice.
7. **Determinism**: same seed and same input should reproduce the same selected
   sites and output coordinates.
8. **Supercell sanity**: if requested concentration is impossible in the current
   cell, build a supercell first rather than silently rounding to zero.

Defects additionally require mass balance. If a vacancy creates an isolated
unphysical fragment, stop or route to `operate-molecular-crystal` for
molecule-cluster removal.
