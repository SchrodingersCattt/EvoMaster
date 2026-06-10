# GROMACS Physical Checks & MDP Defaults

## Standard MDP Parameters

| Parameter | Typical Value | Notes |
|-----------|--------------|-------|
| `dt` | 0.002 (2 fs) | Requires LINCS on H-bonds; use 0.001 without constraints |
| `tcoupl` | V-rescale | `tau_t = 0.1` for equilibration and production |
| `pcoupl` (equil) | Berendsen | Fast pressure convergence |
| `pcoupl` (prod) | Parrinello-Rahman | `tau_p = 2.0`, correct ensemble |
| `rcoulomb` / `rvdw` | 1.0 nm | PME for long-range (`coulombtype = PME`) |
| `nstlist` | 10 | Verlet scheme, automatic buffer |
| `nstxout-compressed` | 5000 | Every 10 ps at dt=2fs |
| `nstenergy` | 500 | Energy output frequency |
| `pbc` | xyz | Standard 3D periodic |

## Constraint Settings

| constraints | When |
|-------------|------|
| `h-bonds` | 2 fs timestep (default) |
| `all-bonds` | Water models, or when needed |
| (none) | 1 fs timestep |

Always: `constraint_algorithm = lincs`, `lincs_iter = 1`.

## Box Size Rule

Minimum image convention: box dimension > 2 × rcoulomb.

For solvated systems: `gmx editconf -d 1.0 -bt cubic` ensures ≥1 nm padding from solute to box edge (so box > solute + 2.0 nm > 2 × 1.0 nm cutoff).

## Task Type MDP Essentials

| Task | `integrator` | Key settings |
|------|-------------|--------------|
| em | steep | `emtol`, `nsteps` |
| nvt | md | `tcoupl = V-rescale`, `ref_t`, `gen_vel = yes` |
| npt | md | `tcoupl = V-rescale`, `pcoupl = Parrinello-Rahman`, `ref_p` |
| md | md | `nsteps`, `dt`, output frequencies |
| fep | md / sd | `free_energy = yes`, `init_lambda_state`, `fep_lambdas` |
