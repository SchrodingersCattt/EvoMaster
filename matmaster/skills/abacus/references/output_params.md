# ABACUS Output Control Parameters

Canonical reference for which **INPUT keywords** produce which **output files**, and for **manual grep** on logs. For scripted extraction and plots after a run, see `matmaster/skills/playground-skills/result-analysis` (`parse_abacus.py`, `plot_publication.py`).

Include in INPUT as needed. Results written to `OUT.ABACUS/`.

| Parameter | Values | Default | Purpose |
|-----------|--------|---------|---------|
| `out_chg` | `0`/`1` | `0` | Charge density → `SPIN1_CHG.cube`. **Required for SCF→NSCF.** |
| `out_band` | `0`/`1` | `0` | Band eigenvalues → `BANDS_1.dat`. For NSCF band structure. |
| `out_dos` | `0`/`1` | `0` | DOS → `DOS1_smearing.dat`. For NSCF DOS. |
| `out_pot` | `0`/`1`/`2` | `0` | Electrostatic potential. `2` → `ElecStaticPot.cube`. |
| `out_stru` | `0`/`1` | `0` | Relaxed structures (`STRU_ION*_D`). For relax/cell-relax. |
| `out_wfc_lcao` | `0`/`1` | `0` | LCAO wavefunction coefficients. For PyATB, Wannier. |
| `cal_force` | `0`/`1` | `0` | Atomic forces. Set `1` for relax or force analysis. |
| `cal_stress` | `0`/`1` | `0` | Stress tensor. Set `1` for cell-relax or EOS. |
| `init_chg` | `atomic`/`file` | `atomic` | Charge init. `file` = read prior SCF. **Required for NSCF.** |
| `nbands` | integer | auto | Number of bands. Must be explicit for NSCF (> occupied). |
| `symmetry` | `0`/`1` | `1` | `0` = disable. **Mandatory for NSCF line-mode k-paths.** |

## Density Matrix Output (LCAO only)

| Parameter | Condition | Purpose |
|-----------|-----------|---------|
| `out_dm 1` | `gamma_only 0` (multi-k) | Density matrix D(k) for each k-point |
| `out_dm1 1` | `gamma_only 1` (Γ-only) | Density matrix at Γ |

> **⚠️ Use `out_dm1` when `gamma_only 1`; use `out_dm` when `gamma_only 0`.** Using the wrong variant silently produces no output.

## LCAO Matrix Output (H/S/T/R)

These parameters output Hamiltonian, overlap, and kinetic matrices. **LCAO only** (`basis_type lcao`).

| Parameter | Values | Purpose | k-points needed? |
|-----------|--------|---------|------------------|
| `out_mat_hs` | `0`/`1` | H(k) and S(k) in k-space → `hks1k1_nao.txt`, `sks1k1_nao.txt` (one per k-point) | Yes — needs KPT with multi-k mesh |
| `out_mat_hs2` | `0`/`1` | H(R) and S(R) in real-space CSR → `data-HR-sparse_SPIN0.csr`, `data-SR-sparse_SPIN0.csr` | Yes — needs KPT |
| `out_mat_r` | `0`/`1` | Position matrix r(R) in real-space → `data-rR-sparse.csr` | Yes — needs KPT |
| `out_mat_t` | `0`/`1` | Kinetic matrix T(R) → `data-TR-sparse_SPIN0.csr` | Yes — needs KPT |

> **⚠️ `out_mat_hs` vs `out_mat_hs2`**: These are easy to confuse. `out_mat_hs` = **k-space** (one file per k-point). `out_mat_hs2` = **real-space** (CSR sparse format). The naming is counterintuitive — remember "hs2" = R-space.

### `calculation get_s` — overlap matrix only

Use `calculation get_s` (a dedicated calculation type) to extract the overlap matrix S without running a full SCF. Outputs `SR.csr` in the same CSR format as `out_mat_hs2`. Requires KPT file (use `gamma_only 0` for multi-k).

### Typical INPUT patterns

**run_hsk/** — H(k) and S(k) in k-space:
```
calculation scf
out_mat_hs 1
```

**run_hsr/** — H(R) and S(R) in real-space:
```
calculation scf
out_mat_hs2 1
```

**run_get_s/** — overlap matrix only (no SCF):
```
calculation get_s
```

## ABACUS Output Files

| File | Produced by | Contains |
|------|-------------|----------|
| `running_scf.log` | SCF | Total energy, Fermi energy, convergence, forces, stress |
| `running_nscf.log` | NSCF | Fermi energy, eigenvalue info |
| `SPIN1_CHG.cube` | SCF + `out_chg 1` | Charge density (cube format) |
| `BANDS_1.dat` | NSCF + `out_band 1` | Band eigenvalues along k-path |
| `DOS1_smearing.dat` | NSCF + `out_dos 1` | Density of states |
| `ElecStaticPot.cube` | `out_pot 2` | Electrostatic potential (work function) |
| `STRU_ION*_D` | Relax | Relaxed structure at each ionic step |

## Key grep patterns
- Total energy: `!FINAL_ETOT_IS <energy> eV`
- Fermi energy: `EFERMI = <energy> eV`
- Convergence: `charge density convergence is achieved`
- Forces: lines after `TOTAL-FORCE (eV/Angstrom)`
- Stress: lines after `TOTAL-STRESS (KBAR)`

## Population Analysis

| Parameter | Values | Purpose | Basis |
|-----------|--------|---------|-------|
| `out_mul` | `0`/`1` | Mulliken population analysis → `OUT.${suffix}/mulliken.txt` | LCAO only |

> **⚠️ The parameter is `out_mul`, NOT `out_mulliken`**. ABACUS uses the abbreviated form.

## Common mistakes
- Forgetting `out_chg 1` in SCF → NSCF with `init_chg file` fails or recomputes SCF
- Leaving `symmetry 1` in NSCF band structure → k-path folded, wrong band plot
- `BANDS_1.dat` eigenvalues are absolute energies (not relative to Fermi level). To identify VBM/CBM and compute band gap, first extract `EFERMI` from `running_scf.log`, then subtract it from all eigenvalues. Without this step, occupied and unoccupied bands cannot be distinguished.
