---
name: result-analysis
description: "Parse DFT/MD calculation logs (LAMMPS, ABACUS, CP2K, QE) and generate publication-quality plots. Extract energies, band gaps, DOS, forces, convergence. Not for VASP or Gaussian."
skill_type: operator
---

# Result Analysis Skill

Extracts data from open-source simulation logs, parses DFT output files, aggregates features from multi-step workflows, and produces publication-ready figures.

## Workflow

1. **Parse**: Run `parse_results.py` for LAMMPS, or `parse_abacus.py` for ABACUS outputs.
2. **Report**: Agent reads the JSON output to answer questions ("What is the band gap?", "Did SCF converge?").
3. **Aggregate**: For multi-step workflows, parse each step's output and combine into a feature summary.
4. **Visualize**: Run `plot_publication.py` on JSON data (convergence, EOS, band, DOS) to produce figures.

## Scripts

### 1. LAMMPS Data Extraction
* **parse_results.py**
    * **Usage**: `python parse_results.py --file <path> --type lammps`
    * **Supported**: **LAMMPS**: potential_energy, temperature, pressure, step.
    * **Not supported**: VASP, Gaussian (commercial software).
    * **Example Output**: `{"potential_energy": -123.45, "temperature": 300.0, "pressure": 0.0, "step": 1000}`

### 2. ABACUS Data Extraction
* **parse_abacus.py**
    * **Usage**: `python parse_abacus.py --dir <OUT.ABACUS_or_result_dir> --type <scf|band|dos|all>`
    * **Modes**:
        * `scf` — Extract total energy, Fermi energy, convergence status, magnetization, forces, stress from `running_scf.log`.
        * `band` — Extract band gap, VBM, CBM from `BANDS_1.dat`. Pass `--fermi <eV>` or parse SCF first for auto-detection.
        * `dos` — Extract DOS energy range and data points from `DOS1_smearing.dat`.
        * `all` — Parse all available output files in one call.
    * **Optional flag**: `--fermi <float>` — provide Fermi energy in eV for band gap determination.
    * **Example Output (scf)**:
      ```json
      {"scf": {"converged": true, "total_energy_eV": -1234.567, "fermi_energy_eV": 5.43, "n_scf_steps": 28}}
      ```
    * **Example Output (band)**:
      ```json
      {"band": {"band_gap_eV": 1.12, "vbm_eV": 5.43, "cbm_eV": 6.55, "is_metal": false}}
      ```
    * **Auto-discovery**: The script searches `OUT.ABACUS/` or any `OUT.*` subdirectory for log files. If given a Bohrium result directory, it looks inside for the output folder.

### 3. Visualization (Publication Quality)
* **plot_publication.py**
    * **Usage**: `python plot_publication.py --data <json_file> --plot_type <type> --output "fig.png"`
    * **Plot Types** (data from JSON only):
        * `convergence`: Energy/Force vs Step (JSON: steps, energies, [forces]).
        * `eos`: Energy vs Volume (JSON: volumes, energies).
    * **Publication defaults**: DPI=300, single column 3.25 inch, Arial/Helvetica, minimal style. Saves PNG + PDF.

## Multi-Step Workflow Feature Extraction

When a task requires **extracting and aggregating features from a completed multi-step workflow** (e.g., SCF→NSCF→band gap, or relaxation→energy→force analysis), follow this procedure:

### Procedure
1. **Identify output files**: List the result directory to find all output files. ABACUS outputs are in `OUT.ABACUS/`; look for `running_*.log`, `BANDS_1.dat`, `DOS1_smearing.dat`, `STRU_ION*_D`, `ElecStaticPot.cube`, etc.
2. **Parse each step**: Use `parse_abacus.py --type all` to extract all available features in one call.
3. **Aggregate features**: Combine parsed outputs into a single feature summary. Common aggregations:
   - **Band gap workflow**: SCF total energy + NSCF band gap + Fermi energy
   - **Surface energy**: E(slab) + E(bulk) → surface energy = (E_slab − n × E_bulk) / (2 × A)
   - **Formation energy**: E(compound) − Σ(E_element × count)
   - **Adsorption energy**: E(slab+ads) − E(slab) − E(gas)
   - **Convergence test**: energies at different ecutwfc/kpoints → convergence curves
4. **Report**: Present aggregated features as a structured table or JSON.

### ABACUS Output Parsing Guide (for ad-hoc extraction)

When `parse_abacus.py` doesn't cover a specific need, parse ABACUS outputs directly:

| Data needed | File to read | Grep/parse pattern |
|-------------|-------------|-------------------|
| Total energy | `OUT.ABACUS/running_scf.log` | `!FINAL_ETOT_IS <energy> eV` |
| Fermi energy | `OUT.ABACUS/running_scf.log` | `EFERMI = <energy> eV` |
| SCF convergence | `OUT.ABACUS/running_scf.log` | `charge density convergence is achieved` |
| Forces | `OUT.ABACUS/running_scf.log` | Lines after `TOTAL-FORCE (eV/Angstrom)` |
| Stress tensor | `OUT.ABACUS/running_scf.log` | Lines after `TOTAL-STRESS (KBAR)` |
| Band eigenvalues | `OUT.ABACUS/BANDS_1.dat` | Columnar data: k-index, eigenvalues |
| DOS data | `OUT.ABACUS/DOS1_smearing.dat` | Columnar: energy, DOS |
| Relaxed structure | `OUT.ABACUS/STRU_ION_D` | ABACUS STRU format |
| Electrostatic potential | `OUT.ABACUS/ElecStaticPot.cube` | Gaussian cube format |

### CP2K / Quantum ESPRESSO Output Parsing (ad-hoc)

For other open-source DFT codes, parse outputs directly with Bash/Python:
- **CP2K**: Total energy in `.out` file: `ENERGY| Total FORCE_EVAL ... : <energy>` (Hartree). Forces: after `ATOMIC FORCES` block.
- **QE (pw.x)**: Total energy in `.out`: `!    total energy              = <energy> Ry`. Fermi energy: `the Fermi energy is <energy> ev`. Forces: after `Forces acting on atoms` block.

## When to use

* "Did the LAMMPS run finish and what is the energy?" → `parse_results.py`
* "What is the band gap / total energy from ABACUS?" → `parse_abacus.py --type all`
* "Extract features from this completed workflow" → `parse_abacus.py --type all` + aggregation
* "What are the forces / stress from ABACUS?" → `parse_abacus.py --type scf`
* "Compute the surface/formation/adsorption energy" → parse relevant outputs, then calculate
* "Plot the energy convergence for my paper." → `plot_publication.py`
* "Generate an EOS figure." → `plot_publication.py` with eos JSON (volumes, energies).

## Tool (via Skill)

- **run_script** with **script_name**: `parse_results.py`, `parse_abacus.py`, or `plot_publication.py`; **script_args**: e.g. `--file log.lammps --type lammps` or `--dir OUT.ABACUS --type all` or `--data out.json --plot_type convergence --output fig.png`.

## Rules

* **Do not** use this skill to parse VASP or Gaussian outputs; only open-source codes are supported.
* When plotting, use JSON produced by your workflow or by `parse_results.py` / `parse_abacus.py`. Prioritize clarity over complexity.
* For multi-step workflows, **always parse all available output files** before reporting — do not stop after reading only the log. Check for BANDS, DOS, and potential files too.
* When computing derived quantities (surface energy, band gap, etc.), **show the formula used and the numerical values** so the user can verify.
* **Cross-result comparison / HT screening**: When aggregating results across multiple systems (e.g., adsorption energies on different surfaces, formation energies of a series), present a final **comparison table** with consistent units and column headers. Include: system identifier, key computed value, and whether the result converged. Save the table as both inline Markdown and a JSON/CSV file for downstream use.
