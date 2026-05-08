# GPUMD Potential Files Reference

Use this file when the task asks for non-NEP potentials, potential-file
preparation, or compatibility checks.

Official sources:

- Potential overview: https://gpumd.org/potentials/index.html
- `potential` keyword: https://gpumd.org/gpumd/input_parameters/potential.html
- EAM: https://gpumd.org/potentials/eam.html
- FCP: https://gpumd.org/potentials/fcp.html
- Tersoff 1988: https://gpumd.org/potentials/tersoff_1988.html
- Tersoff 1989: https://gpumd.org/potentials/tersoff_1989.html
- ADP: https://gpumd.org/potentials/adp.html
- NEP: https://gpumd.org/potentials/nep.html

## `potential` Keyword

Official syntax:

```text
potential <potential_filename>
```

The filename may be relative or absolute. Put the line before minimization,
static calculations, MD ensembles, computes, dumps, or `run`.

For multi-potential observer/active-learning workflows, use multiple
`potential` lines. The first potential is the driver in observer mode.

## NEP Potentials

NEP potentials are usually stored as text files such as `nep.txt`.

Guards:

- Species in `model.xyz` must match the NEP potential's species.
- For multiple NEP potentials used by `dump_observer`, all supplied potentials
  must list atomic species in the same order.
- Do not use a NEP potential outside its training chemistry unless the user
  explicitly asks for exploratory testing.

## EAM

GPUMD supports two analytical EAM forms:

- Zhou 2004: alloy systems with up to 10 atom types.
- Dai 2006: single-element systems.

Zhou-style file header:

```text
eam_zhou_2004 num_types <list of elements>
r_e f_e rho_e rho_s alpha beta A B kappa lambda F_n0 F_n1 F_n2 F_n3 F_0 F_1 F_2 F_3 eta F_e cutoff
```

There are `num_types` parameter rows. Row order must match the element list in
the first line.

Dai-style file:

```text
eam_dai_2006 1 Element
A d c c_0 c_1 c_2 c_3 c_4 B
```

LAMMPS `eam/alloy` table format is supported if the first line is modified to:

```text
eam/alloy <num_types> <El1> <El2> ...
```

## ADP

ADP extends EAM with angular terms. GPUMD supports the LAMMPS ADP-style table
format with a modified first line:

```text
adp <num_elements> <El1> <El2> ...
```

Notes:

- Element types are matched by element symbols.
- The order and number of atom types in `model.xyz` can differ from those in the
  ADP potential file if symbols are present and compatible.

## Tersoff 1989

Use this when applicable because the official docs say it is faster than
Tersoff 1988, though less general.

Single-element file:

```text
tersoff_1989 1 <element>
A B lambda mu beta n c d h R S
```

Two-element file:

```text
tersoff_1989 2 <El0> <El1>
A_0 B_0 lambda_0 mu_0 beta_0 n_0 c_0 d_0 h_0 R_0 S_0
A_1 B_1 lambda_1 mu_1 beta_1 n_1 c_1 d_1 h_1 R_1 S_1
chi_01
```

Limits:

- Supports one or two atom types.
- Cross terms are generated from mixing rules plus `chi_01`.

## Tersoff 1988

Use when a more general Tersoff form is needed.

Single-element file:

```text
tersoff_1988 1 <element>
A_000 B_000 lambda_000 mu_000 beta_000 n_000 c_000 d_000 h_000 R_000 S_000 m_000 alpha_000 gamma_000
```

Two-element file has eight triplet parameter rows:

```text
tersoff_1988 2 <El0> <El1>
A_000 B_000 lambda_000 mu_000 beta_000 n_000 c_000 d_000 h_000 R_000 S_000 m_000 alpha_000 gamma_000
A_001 B_001 lambda_001 mu_001 beta_001 n_001 c_001 d_001 h_001 R_001 S_001 m_001 alpha_001 gamma_001
A_010 B_010 lambda_010 mu_010 beta_010 n_010 c_010 d_010 h_010 R_010 S_010 m_010 alpha_010 gamma_010
A_011 B_011 lambda_011 mu_011 beta_011 n_011 c_011 d_011 h_011 R_011 S_011 m_011 alpha_011 gamma_011
A_100 B_100 lambda_100 mu_100 beta_100 n_100 c_100 d_100 h_100 R_100 S_100 m_100 alpha_100 gamma_100
A_101 B_101 lambda_101 mu_101 beta_101 n_101 c_101 d_101 h_101 R_101 S_101 m_101 alpha_101 gamma_101
A_110 B_110 lambda_110 mu_110 beta_110 n_110 c_110 d_110 h_110 R_110 S_110 m_110 alpha_110 gamma_110
A_111 B_111 lambda_111 mu_111 beta_111 n_111 c_111 d_111 h_111 R_111 S_111 m_111 alpha_111 gamma_111
```

The extension to more components follows the same triplet-index pattern.

## FCP

Force constant potential uses Taylor-expanded force constants and needs a driver
file plus force-constant data generated externally, often by `hiphive`.

Driver file:

```text
fcp number_of_atom_types <list of atom symbols>
highest_force_order highest_heat_current_order
path_to_force_constant_files
```

Rules:

- `highest_force_order` controls the highest force-constant order used for
  forces. GPUMD supports up to sixth order.
- `highest_heat_current_order` can only be `2` or `3`.
- `path_to_force_constant_files` must not end with a trailing slash.

Expected files in the force-constant folder:

```text
clusters_order2.in
clusters_order3.in
clusters_order4.in
clusters_order5.in
clusters_order6.in
fcs_order2.in
fcs_order3.in
fcs_order4.in
fcs_order5.in
fcs_order6.in
r0.in
```

Only include files up to the force-constant order used. `r0.in` contains the
equilibrium/reference positions and must use the same atom order as `model.xyz`.

## Potential Selection Guards

- Prefer NEP when the task provides a NEP potential or asks for ML potential MD.
- Prefer EAM/ADP for metallic systems only when an appropriate potential file is
  provided or explicitly requested.
- Prefer Tersoff for covalent materials only when a matching Tersoff parameter
  file exists.
- Do not invent potential parameters. If no potential file or trusted source is
  available, state that a compatible potential is required.
