---
name: result-analysis
description: "Parse DFT/MD calculation logs (LAMMPS, ABACUS, CP2K, QE) and generate publication-quality plots. Extract energies, band gaps, DOS, forces, convergence. Not for VASP or Gaussian."
skill_type: operator
---

# Result Analysis Skill

Extracts data from open-source simulation logs and produces publication-ready figures.

## Scripts

| Script | Usage | Supported |
|--------|-------|-----------|
| `parse_results.py` | `--file <path> --type lammps` | LAMMPS: potential_energy, temperature, pressure, step |
| `parse_abacus.py` | `--dir <OUT.ABACUS> --type <scf\|band\|dos\|all>` | ABACUS: energy, Fermi, band gap, DOS, forces, stress |
| `plot_publication.py` | `--data <json> --plot_type <type> --output fig.png` | convergence (Energy/Force vs Step), eos (Energy vs Volume) |

### parse_abacus.py Details
- `--type scf`: total energy, Fermi energy, convergence, forces, stress
- `--type band`: band gap, VBM, CBM (pass `--fermi <eV>` or parse SCF first)
- `--type dos`: DOS energy range and data
- `--type all`: parse all available outputs in one call
- Auto-discovers `OUT.ABACUS/` or `OUT.*` subdirectories

## Multi-Step Feature Extraction

1. List result directory for output files
2. Parse each step: `parse_abacus.py --type all`
3. Aggregate: band gap workflow (SCF energy + NSCF band gap), surface energy, formation energy, adsorption energy
4. Report as structured table or JSON

## Ad-hoc ABACUS Output Parsing

| Data | File | Pattern |
|------|------|---------|
| Total energy | `running_scf.log` | `!FINAL_ETOT_IS <energy> eV` |
| Fermi energy | `running_scf.log` | `EFERMI = <energy> eV` |
| SCF convergence | `running_scf.log` | `charge density convergence is achieved` |
| Forces | `running_scf.log` | After `TOTAL-FORCE (eV/Angstrom)` |
| Band eigenvalues | `BANDS_1.dat` | Columnar: k-index, eigenvalues |
| DOS | `DOS1_smearing.dat` | Columnar: energy, DOS |
| Relaxed structure | `STRU_ION_D` | ABACUS STRU format |
| Potential | `ElecStaticPot.cube` | Gaussian cube |

**CP2K**: `ENERGY| Total FORCE_EVAL ... :` (Hartree). **QE**: `!    total energy              =` (Ry).

## Rules

* Do NOT parse VASP or Gaussian outputs.
* Always parse all available output files before reporting.
* Show formula and numerical values for derived quantities (surface energy, band gap, etc.).
