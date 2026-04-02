---
name: result-analysis
description: "Unified skill for PARSING calculation logs (open-source only, e.g. LAMMPS) and VISUALIZING results (Publication Quality Plots). Use to extract energies, temperature, pressure, or to generate plots (convergence, EOS) from JSON with formatting (dpi=300, Arial). Commercial software (VASP, Gaussian) is not supported."
skill_type: operator
---

# Result Analysis Skill

Extracts data from open-source simulation logs and turns parsed or user-provided data into publication-ready figures.

## Workflow

1. **Parse**: Run `parse_results.py` on LAMMPS log (or other supported open-source output).
2. **Report**: Agent reads the JSON output to answer questions ("What is the potential energy?").
3. **Visualize**: Run `plot_publication.py` on JSON data (convergence, EOS) to produce figures.

## Scripts

### 1. Data Extraction
* **parse_results.py**
    * **Usage**: `python parse_results.py --file <path> --type lammps`
    * **Supported**: Open-source codes only. **LAMMPS**: potential_energy, temperature, pressure, step.
    * **Not supported**: VASP, Gaussian (commercial software).
    * **Example Output**: `{"potential_energy": -123.45, "temperature": 300.0, "pressure": 0.0, "step": 1000}`

### 2. Visualization (Publication Quality)
* **plot_publication.py**
    * **Usage**: `python plot_publication.py --data <json_file> --plot_type <type> --output "fig.png"`
    * **Plot Types** (data from JSON only):
        * `convergence`: Energy/Force vs Step (JSON: steps, energies, [forces]).
        * `eos`: Energy vs Volume (JSON: volumes, energies).
    * **Publication defaults**: DPI=300, single column 3.25 inch, Arial/Helvetica, minimal style. Saves PNG + PDF.

## When to use

* "Did the LAMMPS run finish and what is the energy?" -> `parse_results.py`
* "Plot the energy convergence for my paper." -> `plot_publication.py` with convergence JSON.
* "Generate an EOS figure." -> `plot_publication.py` with eos JSON (volumes, energies).

## Tool (via use_skill)

- **run_script** with **script_name**: `parse_results.py` or `plot_publication.py`; **script_args**: e.g. `--file log.lammps --type lammps` or `--data out.json --plot_type convergence --output fig.png`.

## Rules

* **Do not** use this skill to parse VASP or Gaussian outputs; only open-source codes are supported.
* When plotting, use JSON produced by your workflow or by `parse_results.py` (e.g. LAMMPS). Prioritize clarity over complexity.
