# Evaluation Coverage Report (Semantic)

Generated: 2026-05-07 17:45 UTC

## Summary

| Metric | Value |
|--------|-------|
| Total rules | 1428 |
| Covered | 121 (8.5%) |
| Uncovered | 1307 |

## Coverage by Rule Type

| Type | Covered | Total | % |
|------|---------|-------|---|
| pitfall | 1 | 4 | 25.0% |
| hard_guard | 20 | 83 | 24.1% |
| acceptance | 9 | 45 | 20.0% |
| decision_tree | 14 | 87 | 16.1% |
| general | 61 | 925 | 6.6% |
| physical_check | 4 | 61 | 6.6% |
| workflow_step | 11 | 180 | 6.1% |
| config_default | 1 | 26 | 3.8% |
| api_recipe | 0 | 17 | 0.0% |

## Coverage by Skill

| Skill | Covered | Total | % |
|-------|---------|-------|---|
| operate-molecular-crystal | 19 | 44 | 43.2% |
| gromacs | 17 | 44 | 38.6% |
| tasker-polar-surface | 14 | 83 | 16.9% |
| pxrd-refinement | 11 | 24 | 45.8% |
| vasp | 11 | 47 | 23.4% |
| abacus | 9 | 58 | 15.5% |
| build-atomic-structure | 9 | 28 | 32.1% |
| checkcif-validator | 6 | 17 | 35.3% |
| cp2k | 4 | 35 | 11.4% |
| system_prompt | 4 | 21 | 19.0% |
| transform-atomic-structure | 3 | 39 | 7.7% |
| gpumd | 3 | 29 | 10.3% |
| lammps | 3 | 28 | 10.7% |
| mcp-mat-struct-db | 3 | 12 | 25.0% |
| agent_tool | 2 | 28 | 7.1% |
| assemble-atomic-structure | 1 | 31 | 3.2% |
| sample-atomic-structures | 1 | 30 | 3.3% |
| xrd-analysis | 1 | 16 | 6.2% |
| abinit | 0 | 23 | 0.0% |
| inspect-atomic-structure | 0 | 27 | 0.0% |
| data-analysis | 0 | 14 | 0.0% |
| mcp-mat-compdart | 0 | 11 | 0.0% |
| mcp-mat-doc | 0 | 4 | 0.0% |
| mcp-mat-electron-microscope | 0 | 4 | 0.0% |
| mcp-mat-nmr | 0 | 8 | 0.0% |
| mcp-mat-xrd | 0 | 4 | 0.0% |
| mlips | 0 | 33 | 0.0% |
| orca | 0 | 33 | 0.0% |
| plan-executor | 0 | 127 | 0.0% |
| compliance-guardian | 0 | 10 | 0.0% |
| composition-optimization | 0 | 54 | 0.0% |
| deep-survey | 0 | 20 | 0.0% |
| input-manual-helper | 0 | 77 | 0.0% |
| lit-data-organizer | 0 | 9 | 0.0% |
| manuscript-scribe | 0 | 31 | 0.0% |
| md-analysis | 0 | 13 | 0.0% |
| poly-forcefield | 0 | 12 | 0.0% |
| poly-generator | 0 | 29 | 0.0% |
| result-analysis | 0 | 15 | 0.0% |
| structure-manager | 0 | 24 | 0.0% |
| vaspkit-postprocess | 0 | 35 | 0.0% |
| proposal-review | 0 | 25 | 0.0% |
| pyatb | 0 | 33 | 0.0% |
| pyscf | 0 | 33 | 0.0% |
| quantum_espresso | 0 | 25 | 0.0% |
| skill-manager | 0 | 20 | 0.0% |
| bohrium_tool | 0 | 20 | 0.0% |
| bash_tool | 0 | 1 | 0.0% |
| edit_tool | 0 | 1 | 0.0% |
| glob_tool | 0 | 5 | 0.0% |
| grep_tool | 0 | 10 | 0.0% |
| paper_search_tool | 0 | 5 | 0.0% |
| read_tool | 0 | 2 | 0.0% |
| skill_tool | 0 | 10 | 0.0% |
| todo_write_tool | 0 | 2 | 0.0% |
| web_fetch_tool | 0 | 2 | 0.0% |
| web_search_tool | 0 | 1 | 0.0% |
| write_tool | 0 | 2 | 0.0% |

## Critical Uncovered Rules

### abacus (18 uncovered)

- **[workflow_step]** Pseudopotential lookup order: `references/apns_pseudopotentials_v1.list` -> `references/stru_multispecies.md`.
- **[workflow_step]** For uncertain params/workflows, check local `references/*` first.
- **[workflow_step]** Set `pseudo_dir` and `orbital_dir` explicitly in `INPUT`.
- **[workflow_step]** Read provided `STRU` first and reuse filenames exactly (PP/orbital/structure).
- **[workflow_step]** Ensure filenames in `STRU` exactly match files in those directories.
- **[workflow_step]** For Bohrium jobs:
- **[workflow_step]** Orbital lookup: `references/apns_orbitals_efficiency_v1.list`.
- **[hard_guard]** `ntype` in `INPUT` must equal species count in `STRU` `ATOMIC_SPECIES`.
- **[workflow_step]** If references are insufficient or ambiguous, use official ABACUS docs on web as fallback.
- **[workflow_step]** For complex tasks, do not rely only on pretrained priors; gather relevant knowledge from multiple sources to enrich context before finalizing inputs.
- **[hard_guard]** For Bohrium jobs, `INPUT` must include explicit `pseudo_dir` and `orbital_dir`, and PP/orbital filenames in `STRU` must exist in those directories.
- **[hard_guard]** For `cell-relax`, also set `cal_stress 1` explicitly.
- **[hard_guard]** For `relax`/`cell-relax`/`md`, set `cal_force 1` explicitly.
- **[hard_guard]** For SCF -> NSCF workflows:
- **[hard_guard]** SCF: `out_chg 1`
- **[hard_guard]** Every referenced file must exist either in workspace or in the configured runtime directories.
- **[hard_guard]** If file names are not defaults, set `stru_file` and `kpoint_file` to real names.
- **[hard_guard]** NSCF: `init_chg file`, `symmetry 0`, `nbands <N>`, plus `out_band 1` or `out_dos 1`

### abinit (6 uncovered)

- **[workflow_step]** Prepare structure (CIF/POSCAR)
- **[workflow_step]** Generate: `render_input.py --software abinit --task gs_scf --structure struct.cif --output run.abi`
- **[workflow_step]** Diagnose: `diagnose_input.py --software abinit --input run.abi`
- **[workflow_step]** Submit: `Bohrium(action="submit", input_dir="<dir>", image="registry.dp.tech/dptech/dp/native/prod-19853/abinit:v9.10.3_pp", cmd="OMPI_ALLOW_RUN_AS_RO
- **[workflow_step]** Collect into one directory (run.abi + PP files)
- **[workflow_step]** Poll: `Bohrium(action="poll", job_id=<id>)`

### assemble-atomic-structure (18 uncovered)

- **[decision_tree]** Stack two slabs into an interface.
- **[decision_tree]** Build a geometric crosslink network from an existing packed cell.
- **[decision_tree]** Place molecular adsorbates on surfaces.
- **[decision_tree]** Cut a surface slab from a non-molecular bulk crystal.
- **[decision_tree]** Pack molecules or polymer chains into an amorphous periodic cell.
- **[decision_tree]** Inspect all input structures first.
- **[decision_tree]** For ordinary inorganic/metal/covalent bulk slabs, use ASE or pymatgen slab
- **[decision_tree]** For adsorbates, build or inspect the molecule first, then choose site type
- **[decision_tree]** For polar ionic surfaces, route through `tasker-polar-surface` before
- **[decision_tree]** For amorphous cells, use PACKMOL with exactly two of box size, density, and
- **[decision_tree]** For interfaces, match in-plane lattice vectors and reject excessive strain.
- **[hard_guard]** Output filename and extension MUST exactly match the caller's specification (spelling, casing, abbreviation, suffix). Never substitute conventional al
- **[hard_guard]** Interfaces must report in-plane strain. If any in-plane strain exceeds 20%, stop or ask the user to accept the mismatch.
- **[hard_guard]** Slab vacuum must be at least 15 A unless the user explicitly accepts a smaller test structure.
- **[hard_guard]** Polar Type-3 surfaces (for example zinc blende (001), wurtzite (0001)) need even layers or symmetric terminations when possible. Try terminations befo
- **[hard_guard]** Binary compounds: an N-bilayer request corresponds to 2N atomic planes. Do not treat bilayers as individual atomic layers.
- **[hard_guard]** Amorphous packing must specify exactly two of `box_size`, `density`, and `molecule_numbers`.
- **[hard_guard]** Crosslink generation is geometric only; coordinates are not relaxed and the result should be minimized before production MD.

### build-atomic-structure (9 uncovered)

- **[decision_tree]** Build an isolated polymer/copolymer chain from monomer names, SMILES, sequence, and architecture.
- **[decision_tree]** Add a simulation cell around an isolated molecule.
- **[decision_tree]** Monomer sequence or polymer architecture: use `poly-generator` for
- **[decision_tree]** SMILES: use RDKit embedding and write SDF/XYZ/PDB.
- **[decision_tree]** Molecule needing a periodic box: add a cell and vacuum after molecule build.
- **[hard_guard]** **Never hand-write fractional coordinates into a manually constructed ASE `cell` array.** Fractional coordinates depend on the cell angle convention (
- **[hard_guard]** SMILES parsing failure is fatal. Do not silently generate random coordinates.
- **[hard_guard]** Do not guess missing lattice constants or Wyckoff coordinates. Ask the user or fetch a known structure first.
- **[hard_guard]** A polymer-chain SDF is one isolated molecule with no periodic box and is not a ready-to-run MD or DFT input. Send it to `assemble-atomic-structure` fo

### checkcif-validator (1 uncovered)

- **[decision_tree]** After completing crystal structure refinement, before reporting R-factors.

### composition-optimization (31 uncovered)

- **[decision_tree]** "Optimize alloy/composition for target property."
- **[decision_tree]** "Use genetic algorithm or run_dart_ga for composition search."
- **[decision_tree]** "I only have composition/formula, please generate usable structures."
- **[decision_tree]** "Build initial candidates from literature, then optimize."
- **[workflow_step]** initial candidate data
- **[workflow_step]** Extract objective(s), constraints, and search space from user input.
- **[workflow_step]** **Normalize the task**
- **[workflow_step]** Record whether the user provided:
- **[workflow_step]** surrogate model
- **[workflow_step]** explicit structures
- **[workflow_step]** If user provided candidate compositions, clean and standardize them.
- **[workflow_step]** **Prepare initial candidates**
- **[workflow_step]** If not provided (or if literature search is planned regardless):
- **[workflow_step]** **Route by surrogate-model availability**
- **[workflow_step]** Call `deep-survey` to collect evidence. Depth choice: `--depth brief` for seed-only sub-step (3-5 calls, no report); `--depth standard` for concise su
- **[workflow_step]** `deep-survey` always produces `collected_<topic>.json`. Pass it to `lit-data-organizer` (build_lit_table.py) to build the canonical evidence table bef
- **[workflow_step]** If no surrogate model is provided, do not force DART GA; return a staged fallback:
- **[workflow_step]** build initial candidate set
- **[workflow_step]** estimate properties with available screening tools
- **[workflow_step]** If a surrogate model is provided and DART GA tool is available, run GA optimization.
- **[workflow_step]** Use the heuristic in [composition_to_structure_heuristics.md](reference/composition_to_structure_heuristics.md).
- **[workflow_step]** optionally request/propose surrogate model training before GA
- **[workflow_step]** Generate candidate structures via `mat_struct_db_*` / `mat_sg_*` tools.
- **[workflow_step]** **Composition -> structure heuristic (mandatory when structure is missing)**
- **[workflow_step]** Validate each new structure using `structure-manager` (`assess_structure.py`).
- **[workflow_step]** Ensure each structure has explicit lattice, coordinates, and atom-type mapping for downstream DPA tools.
- **[workflow_step]** **Report results**
- **[workflow_step]** Provide ranked candidate compositions and associated structures.
- **[workflow_step]** Explicitly disclose assumptions and approximations.
- **[workflow_step]** Include source/provenance of each candidate (user input, DB, generated heuristic, literature evidence).
- **[workflow_step]** If using `manuscript-scribe` to produce the survey report, use `--profile literature_review` (matches deep-survey's 5-section output structure exactly

### cp2k (6 uncovered)

- **[workflow_step]** Prepare structure file (CIF/POSCAR)
- **[workflow_step]** Collect all files into one directory (input.inp + structure + any auxiliary files)
- **[workflow_step]** Diagnose: `diagnose_input.py --software cp2k --input input.inp`
- **[workflow_step]** Generate input: `render_input.py --software cp2k --task scf --structure struct.cif --output input.inp`
- **[workflow_step]** Submit: `Bohrium(action="submit", input_dir="<dir>", image="registry.dp.tech/dptech/cp2k:2024.1", cmd="OMP_NUM_THREADS=1 mpirun -np 32 cp2k.popt -i in
- **[workflow_step]** Poll: `Bohrium(action="poll", job_id=<id>)`

### data-analysis (8 uncovered)

- **[decision_tree]** "Check data quality / QC this CSV" → QC workflow below
- **[decision_tree]** "Summarize experiment metrics" → statistical profiling
- **[decision_tree]** "Find outliers / anomalies" → IQR or Z-score detection
- **[decision_tree]** "Plot / visualize data from a table" → matplotlib rendering
- **[workflow_step]** **Profile**: Read the data file. Report row count, column names & dtypes, missing values per column (count + rate).
- **[workflow_step]** **Unit / consistency audit**: Cross-check column-name suffixes (e.g. `_C`, `_kW`, `_pct`) against value ranges; flag conflicts.
- **[workflow_step]** **Write deliverables**: QC report (Markdown), metrics JSON, and any supplementary files the task requests.
- **[workflow_step]** **Outlier detection — IQR method**: The IQR (Interquartile Range) method uses Q1 (25th percentile) and Q3 (75th percentile) to compute IQR = Q3 − Q1. 

### deep-survey (5 uncovered)

- **[workflow_step]** Run `run_survey.py --topic "..." --depth <tier> [--output survey.md]` to create outline/skeleton.
- **[workflow_step]** Execute retrieval calls (mat_sn_*, web-search). If a tool fails, switch to a different available tool.
- **[workflow_step]** For deep mode, may delegate report writing to manuscript-scribe with `literature_review` profile (not `review`).
- **[workflow_step]** Run `collect_evidence.py --collected_json _tmp/surveys/collected_<topic>.json [--facet "..."]` to auto-populate evidence_cards. **Mandatory** — do NOT
- **[workflow_step]** Write report content (standard/deep only). Do not leave (TBD) in delivered file.

### gpumd (12 uncovered)

- **[workflow_step]** Read the task; identify which simulation type is needed
- **[workflow_step]** Load this skill; consult `references/run_in_keywords.md` or `references/nep_in_keywords.md`
- **[workflow_step]** Write `run.in` (or `nep.in`) following the two-stage pattern below
- **[workflow_step]** Submit to Bohrium
- **[hard_guard]** **`potential` must come first.** Every `run.in` must start with `potential` line(s) before any `ensemble`, `run`, or `compute_*`.
- **[workflow_step]** Stage all input files (`run.in`, `model.xyz`, potential files) in `input_dir/`
- **[hard_guard]** **`dump_*` before its `run`.** Same rule for `dump_thermo`, `dump_position`, `dump_force`, `dump_dipole`, `dump_polarizability`, `dump_observer`.
- **[hard_guard]** **`compute_*` before its `run`.** Any `compute_hac`, `compute_hnemd`, `compute_shc`, `compute_msd`, `compute_sdc`, `compute_viscosity`, `compute_dos` 
- **[hard_guard]** **NVE for equilibrium transport properties.** EMD (`compute_hac`), MSD (`compute_msd`), DOS (`compute_dos`), SHC (`compute_shc`), viscosity (`compute_
- **[hard_guard]** **`model.xyz` must use extended XYZ format.** Header line 2 must contain `lattice="..."` (9 floats, row-major) and `pbc="T T T"`. See `references/mode
- **[hard_guard]** **NEP: `type` line must list actual species.** `type N El1 El2 ...` where N = number of species, matching `train.xyz` data.
- **[hard_guard]** **`compute_*` and `dump_*` reset after each `run`.** If a second `run` block needs the same compute/dump, re-specify them before that `run`.

### gromacs (4 uncovered)

- **[workflow_step]** Ensure all files (`.gro`, `.top`, `.mdp`, `run.sh`) are in one directory
- **[workflow_step]** Submit:
- **[workflow_step]** Poll: `Bohrium(action="poll", job_id=<id>)`
- **[workflow_step]** Download: `Bohrium(action="download", job_id=<id>, result_dir="<output_dir>")`

### input-manual-helper (11 uncovered)

- **[workflow_step]** **User-provided ready file check (exit early)** — Before doing anything else, check whether the user has already provided a complete, ready-to-run inp
- **[workflow_step]** **Choose software and task type** — Determine which local script to use (all route through `render_input.py` + `diagnose_input.py`).
- **[workflow_step]** **Prepare structure_file** — Ensure the structure path exists and is pymatgen-instanceable; do not assume formats the engine cannot read.
- **[workflow_step]** **Call render_input.py** — Run the script to generate the input file with appropriate parameters and software name.
- **[workflow_step]** **Call diagnose_input.py** — Run to validate parameter ranges and consistency. Validation is **physical-sense review**: check that key parameters are 
- **[workflow_step]** **Gather auxiliary files** — Collect all required auxiliary files (pseudopotentials, basis sets, orbital files, topology files, etc.) into one directo
- **[workflow_step]** **Prepare structure** — Place structure file (XYZ or other pymatgen-readable format) in the same directory.
- **[workflow_step]** **Submit** — **`Bohrium`** with `action="submit"`, `input_dir`, `image=<pyscf_image>`, `cmd="python run_pyscf.py > log 2>&1"`.
- **[workflow_step]** **Submit via `Bohrium`** — `action="submit"`, `input_dir="<dir>"`, `image="<image>"`, `cmd="<command>"` (stdout/stderr should land in `log`; the tool 
- **[workflow_step]** **Write Python script** — Create `run_pyscf.py` (or similar) that imports PySCF, loads the structure, sets parameters (charge, spin, method, basis, et
- **[workflow_step]** **No validation step** — PySCF scripts are Python code, not static input files; validation is implicit in the script logic.

### inspect-atomic-structure (13 uncovered)

- **[decision_tree]** Inspect CIF, POSCAR/CONTCAR, XYZ, SDF, MOL, PDB, or LAMMPS-like coordinate files.
- **[decision_tree]** Decide whether a structure is a bulk crystal, molecule, slab, interface, amorphous cell, polymer chain, or molecular crystal.
- **[decision_tree]** Extract Wyckoff sites before targeted doping or ordered substitutions.
- **[decision_tree]** If the file is periodic (`CIF`, `POSCAR`, `CONTCAR`, periodic XYZ), parse it
- **[decision_tree]** Verify a newly generated structure before handing it to simulation skills.
- **[decision_tree]** If the file is molecular (`SDF`, `MOL`, nonperiodic XYZ/PDB), parse it as an
- **[decision_tree]** If the periodic structure contains discrete molecules under PBC connectivity,
- **[decision_tree]** If it contains a large vacuum dimension, classify it as slab/interface and
- **[hard_guard]** Report formula, atom count, lattice parameters, dimensionality, and `is_molecular_crystal`.
- **[hard_guard]** Inspect before and after every mutation that writes a new structure file.
- **[hard_guard]** Do not call a structure valid only because the parser succeeded. Check atom counts, lattice sanity, and obvious overlaps/vacuum.
- **[hard_guard]** If a slab is detected, estimate vacuum thickness. A vacuum dimension below 15 A is suspect for surface simulations.
- **[hard_guard]** If a CIF reports disorder or partial occupancies, route ordering work to `transform-atomic-structure` or `operate-molecular-crystal`.

### lammps (5 uncovered)

- **[workflow_step]** Prepare data file (structure + topology)
- **[workflow_step]** Diagnose: `diagnose_input.py --software lammps --input lammps.in`
- **[workflow_step]** Submit: `Bohrium(action="submit", input_dir="<dir>", image="registry.dp.tech/dptech/lammps-agent:03810da8", cmd="lmp -in lammps.in > log 2>&1", machin
- **[workflow_step]** Poll: `Bohrium(action="poll", job_id=<id>)`
- **[workflow_step]** Collect all files (input script + data file + potential files)

### lit-data-organizer (4 uncovered)

- **[workflow_step]** **Source preparation**: For PDFs, use `mat_doc_*` tools first. For web sources, use search/extraction tools.
- **[workflow_step]** **Normalize and merge**: `build_lit_table.py` harmonizes fields, deduplicates, preserves conflicts with metadata.
- **[workflow_step]** **Enrich** (agent-side): Read `_tmp/lit_data/normalized_rows.json`, apply pattern-based or semantic extraction (see `references/enrich_strategy.md`), 
- **[workflow_step]** **Export**: CSV or JSONL. For business deliverables (e.g. `candidates.csv`), see `references/business_export_candidates.md`.

### manuscript-scribe (5 uncovered)

- **[workflow_step]** **Retrieval first** — Run literature search (mat_sn_*, web-search) before any writing. Exception: `computational_report` with user-provided parameters
- **[workflow_step]** **Validate** — `validate_content.py --draft draft.md --profile <profile>`. Fix sections below minimum word count.
- **[workflow_step]** **Assemble** — `assemble_manuscript.py --sections_dir sections/ --output final.md --validate --profile <profile>`. Runs consistency checks (terms, abb
- **[workflow_step]** **Chunked writing** — Draft each section in chunks via `write_section.py` (create + `--append`), or build in temp files with `append_chunk.py`, then p
- **[workflow_step]** **Polish** — `polish_text.py --file <assembled> --target_section <Name> --use_llm` for point-by-point revision.

### md-analysis (8 uncovered)

- **[decision_tree]** Analyze a finished trajectory or energy file already present in the workspace.
- **[workflow_step]** Confirm the required trajectory, structure, or energy files already exist in the workspace.
- **[decision_tree]** Produce a quick numeric summary from the generated `.xvg` file.
- **[decision_tree]** Generate `.xvg` outputs for RMSD, RMSF, radius of gyration, MSD, RDF, hydrogen bonds, or energy terms.
- **[workflow_step]** Use the structured analysis subcommands before falling back to generic GROMACS execution.
- **[workflow_step]** Keep output `.xvg` files inside the active workspace.
- **[workflow_step]** Report both the output file path and the summary statistics.
- **[workflow_step]** For commands that require group selection, pass the answer through `--stdin-lines`.

### mlips (4 uncovered)

- **[workflow_step]** Prepare structure (CIF/POSCAR/XYZ)
- **[workflow_step]** Copy script(s) + `_calculator.py` to working directory
- **[workflow_step]** Submit (DPA — default): `Bohrium(action="submit", input_dir="<dir>", image="registry.dp.tech/dptech/dpa-calculator:e13a296f", cmd="python optimize_str
- **[workflow_step]** Poll and read `result.json`

### operate-molecular-crystal (10 uncovered)

- **[decision_tree]** Remove solvents by molecule identity or formula.
- **[decision_tree]** Extract independent molecules from a crystal.
- **[decision_tree]** Create vacancy defects by removing complete molecule clusters.
- **[decision_tree]** If the task is defect creation, remove complete molecular units or clusters.
- **[decision_tree]** Run `inspect-atomic-structure`; proceed here only if
- **[decision_tree]** If the task is ordering, call
- **[hard_guard]** Never silently fall back to ASE slab cutting for molecular crystals.
- **[hard_guard]** Molecule extraction should default to unwrapping molecules across PBC.
- **[hard_guard]** Solvent removal must remove whole molecules, not atoms matching an element.
- **[hard_guard]** Defects must remove complete molecule units or complete spatial clusters.

### orca (6 uncovered)

- **[workflow_step]** Prepare molecular structure (XYZ file or embed coordinates in input)
- **[workflow_step]** Generate: `render_input.py --software orca --task sp --structure molecule.xyz --output input.inp`
- **[workflow_step]** Submit: `Bohrium(action="submit", input_dir="<dir>", image="registry.dp.tech/dptech/dp/native/prod-19853/orca:v6.1.1", cmd="OMPI_ALLOW_RUN_AS_ROOT=1 O
- **[workflow_step]** Diagnose: `diagnose_input.py --software orca --input input.inp`
- **[workflow_step]** Place input.inp (and .xyz if using xyzfile) in one directory
- **[workflow_step]** Poll: `Bohrium(action="poll", job_id=<id>)`

### poly-forcefield (4 uncovered)

- **[workflow_step]** Check whether the input is inside the current supported scope.
- **[workflow_step]** If it is out of scope, state the limitation clearly instead of pretending full force-field coverage exists.
- **[workflow_step]** Report the generated `.top` path and any obvious limitations in the result.
- **[workflow_step]** For supported inputs, run the topology generator script and write outputs into the workspace.

### poly-generator (6 uncovered)

- **[workflow_step]** Collect monomer aliases and SMILES such as `A`, `B`, `C`.
- **[workflow_step]** If chemistry cannot be inferred safely, stop and ask for reaction SMARTS or corrected monomer SMILES.
- **[workflow_step]** Prefer marker-based stitching when the SMILES already contains `[*]`.
- **[workflow_step]** Run `build_polymer.py` first to generate the polymer SMILES and expanded sequence.
- **[workflow_step]** If the user wants a structure file, run `generate_3d.py`.
- **[workflow_step]** If the build succeeds, run `generate_2d.py` for a preview image and Markdown report.

### proposal-review (7 uncovered)

- **[decision_tree]** "Review / evaluate this proposal" → full review workflow below
- **[decision_tree]** "Score this project plan" → scorecard generation
- **[workflow_step]** **Score** each dimension per the evaluation policy. Each sub-score rationale must cite ≥ 2 specific evidence points from the proposal text.
- **[decision_tree]** "Assess risks of this project" → risk analysis + mitigation
- **[workflow_step]** **Read** the proposal document thoroughly before scoring.
- **[workflow_step]** **Write deliverables**: scorecard JSON, rationale Markdown, risk mitigation JSON (or as task specifies).
- **[workflow_step]** **Verify**: All files exist with correct filenames; JSON files are parseable; all task-required keys present.

### pxrd-refinement (3 uncovered)

- **[workflow_step]** **Stage `input_dir/`**: copy `gsas2_pawley*.py` + `curation.py` + all data files, flat. Working directory inside the container is the unzipped `input_
- **[workflow_step]** **Parse `results.json`**: check `success`, `warnings`, `curation.verdict` first; then `wR`/`Rwp` against the contract-3 thresholds; then cell vs. init
- **[workflow_step]** wR_fwd − wR_rev

### pyatb (9 uncovered)

- **[workflow_step]** Set `basis_type lcao` in INPUT
- **[workflow_step]** Set `out_mat_hs2 1` to output HR.dat and SR.dat
- **[workflow_step]** Submit via `Bohrium(action="submit", ...)` and wait for completion
- **[workflow_step]** Write PyATB script (`run_pyatb.py`)
- **[workflow_step]** Place script + HR.dat + SR.dat (+ rR.dat if needed) in one directory
- **[workflow_step]** Download results via `Bohrium(action="poll", job_id=<id>)`
- **[workflow_step]** Submit: `Bohrium(action="submit", input_dir="<dir>", image="<pyatb_image>", cmd="python run_pyatb.py > log 2>&1")`
- **[workflow_step]** Poll: `Bohrium(action="poll", job_id=<id>)`
- **[workflow_step]** Query image: `Bohrium(action="list_images", keyword="pyatb")`

### pyscf (5 uncovered)

- **[workflow_step]** Write Python script (e.g. `run_pyscf.py`)
- **[workflow_step]** Place script + structure in one directory
- **[workflow_step]** Prepare structure file (XYZ preferred) if not embedding coordinates
- **[workflow_step]** Submit: `Bohrium(action="submit", input_dir="<dir>", image="registry.dp.tech/dptech/dp/native/prod-19853/pyscf-geometric:dev-260305", cmd="python run_
- **[workflow_step]** Poll: `Bohrium(action="poll", job_id=<id>)`

### quantum_espresso (6 uncovered)

- **[workflow_step]** Prepare structure (CIF/POSCAR)
- **[workflow_step]** Generate: `render_input.py --software qe --task scf --structure struct.cif --output pw.in`
- **[workflow_step]** Collect files into one directory
- **[workflow_step]** Submit: `Bohrium(action="submit", input_dir="<dir>", image="registry.dp.tech/dptech/quantum-espresso:7.1", cmd="OMP_NUM_THREADS=1 mpirun -np 32 pw.x -
- **[workflow_step]** Diagnose: `diagnose_input.py --software qe --input pw.in`
- **[workflow_step]** Poll: `Bohrium(action="poll", job_id=<id>)`

### sample-atomic-structures (13 uncovered)

- **[decision_tree]** Generate candidate crystals using CALYPSO-style evolutionary sampling.
- **[decision_tree]** Explore a composition/property target when known database structures are not sufficient.
- **[decision_tree]** If the user asks for known materials or IDs, do not use this skill.
- **[decision_tree]** Generate property-conditioned candidates with CrystalFormer.
- **[decision_tree]** For composition-only global search, use CALYPSO.
- **[decision_tree]** If using CrystalFormer, ask the user for `space_group` explicitly before
- **[decision_tree]** For target-property constraints, use CrystalFormer.
- **[decision_tree]** After generation, inspect every returned candidate before downstream use.
- **[hard_guard]** Supported CrystalFormer condition names include `bandgap`, `shear_modulus`, `bulk_modulus`, `ambient_pressure`, `high_pressure`, and `sound`.
- **[hard_guard]** `cond_model_type_list`, `target_value_list`, and `target_type_list` must have identical lengths.
- **[hard_guard]** `space_group` for CrystalFormer must be supplied by the user.
- **[hard_guard]** Treat outputs as candidates. They need inspection and often relaxation before simulation or publication.
- **[hard_guard]** Put a reasonable bound on `sample_num` and `mc_steps`; ask before launching large searches.

### skill-manager (3 uncovered)

- **[workflow_step]** Before uploading, verify the directory has a valid `SKILL.md` with frontmatter.
- **[workflow_step]** After upload + register, call list to confirm the skill shows `status: ready`.
- **[workflow_step]** The skill will be available to the user in their next agent session.

### tasker-polar-surface (11 uncovered)

- **[workflow_step]** Prefer **reference/tasker_lookup.yaml** and **reference.md**.
- **[workflow_step]** If (formula, miller) is not in lookup, do literature search first (see §2.1) and set a **provisional** type.
- **[workflow_step]** This pre-classification is for choosing build path. Final judgment still depends on post-build checks.
- **[workflow_step]** If type is uncertain, still build first with this script, then decide with checker + literature consistency.
- **[workflow_step]** **Build slab (default for Type 1/2/3)**
- **[workflow_step]** `mat_sg_build_surface_slab` is optional fallback only when explicitly needed.
- **[workflow_step]** Use `build_slab_tasker_fix.py` as the default builder for all types.
- **[workflow_step]** **Post-build validate and iterate (mandatory)**
- **[workflow_step]** Run `check_slab_tasker.py` on the built slab with the provisional type.
- **[workflow_step]** If non-compliant or inconsistent with literature, adjust parameters (layers/termination/thickness) and rebuild.
- **[workflow_step]** If repeated attempts still fail, report limitation and ask user to choose manual adjustment vs temporary ignore.

### transform-atomic-structure (20 uncovered)

- **[decision_tree]** Substitute dopants randomly, by ordered sites, or by Wyckoff labels.
- **[decision_tree]** Order partially occupied/disordered sites.
- **[decision_tree]** Run `inspect-atomic-structure` on the input.
- **[decision_tree]** Add/remove atoms when the operation is a mutation of the same structure.
- **[decision_tree]** If `is_molecular_crystal=true`, route to `operate-molecular-crystal`.
- **[decision_tree]** If the user requests only cell multiplication, use a supercell matrix.
- **[decision_tree]** If the user requests strain/shear, apply a deformation gradient and decide
- **[decision_tree]** `random`: reproducible random indices with a fixed seed.
- **[decision_tree]** If the user requests dopants, select sites by mode:
- **[decision_tree]** `wyckoff`: choose sites from explicit Wyckoff labels.
- **[decision_tree]** `ordered`: choose symmetry-related sites or ordered sublattices.
- **[decision_tree]** If valence changes, require explicit oxidation states or a charge
- **[hard_guard]** Output filename and extension MUST exactly match the caller's specification (spelling, casing, abbreviation, suffix). Never substitute conventional al
- **[hard_guard]** Supercell mode requires an integer 3x3 matrix or three integer repeats.
- **[hard_guard]** `fraction` and `count` are mutually exclusive for any doping rule.
- **[hard_guard]** Deformation mode must state whether atomic coordinates are scaled with the cell. Default to scaling atoms for physical strain.
- **[hard_guard]** Always preserve the input file and write a new output file.
- **[hard_guard]** Do not silently replace zero atoms. If `fraction * site_count < 1`, require a larger supercell or an exact count.
- **[hard_guard]** For molecular crystals, do not remove individual atoms or cut bonds here.
- **[hard_guard]** Use a fixed seed for stochastic replacements and report it.

### vasp (18 uncovered)

- **[workflow_step]** Read the task spec / JSON config to determine calculation type and system.
- **[workflow_step]** Generate KPOINTS appropriate for the calculation type (see K-point Rules).
- **[workflow_step]** Construct POSCAR from provided structure info (formula, space group, lattice
- **[hard_guard]** **ISMEAR must match the system**:
- **[workflow_step]** Validate consistency: INCAR tags match the calculation type, KPOINTS match
- **[hard_guard]** Metals: `ISMEAR = 1` or `2` (Methfessel-Paxton) with `SIGMA = 0.1-0.2`.
- **[hard_guard]** Semiconductors/insulators: `ISMEAR = 0` (Gaussian) with `SIGMA = 0.05`.
- **[hard_guard]** DOS / accurate total energy: `ISMEAR = -5` (tetrahedron with Blochl corrections).
- **[hard_guard]** Single atom / molecule (Gamma-only): `ISMEAR = 0`, `SIGMA = 0.01`.
- **[hard_guard]** **Relaxation tasks MUST set**:
- **[hard_guard]** `EDIFFG` negative for force convergence (e.g., `EDIFFG = -0.01` eV/A).
- **[hard_guard]** **Static fixed-cell slab/surface setups**: if the prompt says fixed cell or fixed cell shape, set `ISIF = 2` explicitly.
- **[hard_guard]** **Finite-difference elastic tensor**: set `IBRION = 6`, `ISIF = 3`, `POTIM` to the requested displacement amplitude, and `NFREE = 2` unless a higher-o
- **[hard_guard]** **SOC/heavy-element calculations**: set `LSORBIT = .TRUE.`, `ISPIN = 2`, `LNONCOLLINEAR = .TRUE.` (implicit), and `ISYM = 0`; set `LMAXMIX = 4` unless
- **[hard_guard]** "VASP 镜像中已内置 POTCAR（如 `/opt/vasp/potcar/PBE/`）"
- **[hard_guard]** **POTCAR must be resolved before submission.** VASP cannot run without POTCAR. Before submitting, use AskQuestion to ask the user where POTCAR is loca
- **[hard_guard]** "Bohrium 节点上的某个目录（请填路径）"
- **[hard_guard]** "我没有 POTCAR" If the user has no POTCAR, do NOT submit — inform them POTCAR is license-restricted and cannot be auto-generated, then stop. If POTCAR is

### vaspkit-postprocess (5 uncovered)

- **[decision_tree]** "Extract band structure from this VASP run" -> ensure POSCAR, INCAR, EIGENVAL, DOSCAR, and Line-Mode KPOINTS all exist, then `run_vaspkit.py --task 21
- **[decision_tree]** "Hybrid band structure" -> ensure POSCAR, INCAR, EIGENVAL, DOSCAR, KPOINTS, KPATH.in exist, then `run_vaspkit.py --task 252`.
- **[decision_tree]** "Get DOS/PDOS" -> ensure POSCAR, INCAR, DOSCAR exist, then `run_vaspkit.py --task 116` (or 117 for total DOS, which also needs KPOINTS and EIGENVAL).
- **[decision_tree]** "Generate K-path for my POSCAR" -> `run_vaspkit.py --task 303 --symprec 1E-5` (bulk) or 302 (2D).
- **[decision_tree]** "Fermi surface" -> ensure POSCAR, INCAR, EIGENVAL, DOSCAR, KPOINTS exist, then `run_vaspkit.py --task 262`.

### xrd-analysis (11 uncovered)

- **[decision_tree]** HKL → CIF conversion | `scripts/solve_refine_scxrd.py` | `.hkl` (+ `.ins` / `.p4p` auto-discovered)
- **[decision_tree]** PXRD lattice refinement (no GSAS-II) | `scripts/refine_lattice_pxrd.py` | XY pattern + space group + initial cell
- **[decision_tree]** Multi-temperature lattice evolution | `scripts/refine_lattice_pxrd.py --multi-temp` | directory of XY patterns
- **[workflow_step]** **Use the provided script. Never write your own SCXRD solver from scratch.**
- **[workflow_step]** python3 scripts/solve_refine_scxrd.py \
       --hkl data.hkl --sg "P2_1/c" --elements C H N O \
       --grid 72 --trials 2 --cycles 400 \
       --o
- **[workflow_step]** Pipeline: parse HKL (SHELX HKLF4) → try SHELX if installed → charge-flipping fallback → least-squares refinement → CIF output.
- **[workflow_step]** The script auto-discovers companion `.ins` / `.p4p` files from the HKL stem.
- **[workflow_step]** **Parse `result.json`** for R1, wR2, GooF. Report if R1 > 0.15 — the structure may be unreliable.
- **[workflow_step]** **CIF completeness check** — verify the output CIF contains all of:
- **[workflow_step]** python3 scripts/refine_lattice_pxrd.py \
    --data pattern.xy --sg "Pm-3m" \
    --cell "a=3.905" --wavelength 1.5406 \
    -o result.json
- **[workflow_step]** python3 scripts/refine_lattice_pxrd.py \
    --data ./ --sg "Pm-3m" \
    --cell "a=3.905" --wavelength 1.5406 --multi-temp \
    -o result.json

