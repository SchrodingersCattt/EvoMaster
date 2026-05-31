# Question Taxonomy — Field Mapping Rules

How capability, scope, domain, tags, file location, and question ID prefix relate to each other.

## File Location

```
evaluation/question_bank/
├── workflow_orchestration/   # cap=workflow_orchestration, agent executes a multi-step workflow
│   ├── wo_agnostic.yaml     # domain=agnostic
│   ├── wo_alloy.yaml        # domain=alloy
│   ├── wo_battery.yaml      # domain=battery (in platform/)
│   └── wo_catalysis.yaml    # domain=catalysis
├── execution_contract/       # cap=execution_contract, agent behavior/boundary tests
│   └── ec_agnostic.yaml     # domain=agnostic
├── platform/                 # platform-scope questions (environment-specific behavior)
│   ├── ec_agnostic.yaml     # cap=execution_contract, domain=agnostic
│   ├── ec_alloy.yaml        # cap=execution_contract, domain=alloy
│   ├── wo_agnostic.yaml     # cap=workflow_orchestration, domain=agnostic
│   ├── wo_battery.yaml      # cap=workflow_orchestration, domain=battery
│   └── wo_catalysis.yaml    # cap=workflow_orchestration, domain=catalysis
├── input_generation/         # cap=input_generation
├── structure_construction/   # cap=structure_construction
├── scientific_analysis/      # cap=scientific_analysis
└── structure_retrieval/      # cap=structure_retrieval
```

**Rule: question's capability + domain must match the file it lives in.**

## ID Prefix Convention

| Prefix | Capability | Meaning |
|--------|-----------|---------|
| `WO_` | workflow_orchestration | Multi-step computational workflow |
| `PWO_` | workflow_orchestration (platform) | Platform-specific workflow |
| `EC_` | execution_contract | Behavioral boundary / refusal test |
| `IG_` | input_generation | Generate computation input files |
| `SC_` | structure_construction | Build atomic structures |
| `SA_` | scientific_analysis | Analyze results / literature |
| `RT_` | structure_retrieval | Retrieve known structures |

ID suffix: `_{sequence}_{YYYYMMDD}` (date of creation/last criteria change). Append `v2`, `v3` for same-day revisions.

## Tags — Skill Mapping

Tags indicate which skill(s) the question exercises. A question can have multiple tags.

| Tag | Corresponding Skill | What it tests |
|-----|-------------------|---------------|
| `code_mlip` | mlips | DPA/MACE/SevenNet MLIP calculations via ASE+Bohrium |
| `eng_lammps` | lammps | LAMMPS molecular dynamics |
| `eng_abacus` | abacus | ABACUS DFT calculations |
| `eng_vasp` | (no skill, knowledge-based) | VASP input generation |
| `eng_cp2k` | cp2k | CP2K calculations |
| `eng_gpumd` | gpumd | GPUMD simulations |
| `eng_gromacs` | gromacs | GROMACS MD |
| `struct_build` | build-crystal-from-params | Crystal structure construction |
| `struct_molcrys` | operate-molecular-crystal | Molecular crystal operations |
| `struct_surface` | assemble-atomic-structure | Surface/interface construction |
| `struct_transform` | transform-atomic-structure | Structure manipulation |
| `meta_grounding` | (behavioral) | Boundary recognition, refusal, tool availability |
| `meta_database` | aissq-explorer / mcp tools | Database/registry queries |
| `char_diffraction` | mcp-mat-xrd | XRD/diffraction analysis |
| `char_microscopy` | (no skill, knowledge-based) | Microscopy/image-derived characterization, e.g. TEM/SEM size statistics |

## Capability vs Tag Decision

| Question type | capability | tags |
|--------------|-----------|------|
| Agent runs a computation end-to-end | `workflow_orchestration` | skill tag(s) for the engines used |
| Agent must refuse / stop / report limitation | `execution_contract` | `meta_grounding` + relevant skill tag |
| Agent generates input files (no submission) | `input_generation` | engine tag |
| Agent builds a structure | `structure_construction` | `struct_*` tag |

**Key distinction:** If the question's *correctness criteria* are about what the agent *produces* (files, results), it's `workflow_orchestration` or `input_generation`. If the criteria are about what the agent *says or doesn't do* (disclose, stop, don't execute), it's `execution_contract`.

## Domain Assignment

| Domain | When to use |
|--------|------------|
| `agnostic` | Material/system type doesn't matter; tests a general skill capability |
| `alloy` | Alloys, intermetallics, HEAs |
| `battery` | Electrolytes, electrodes, battery materials |
| `catalysis` | Surfaces, adsorbates, reaction barriers |
| `semiconductor` | Band gaps, doping, electronic properties |
| `polymer` | Polymers, soft matter |

## Multi-Tag Examples

| Scenario | Tags |
|----------|------|
| DPA NEB on interface | `[code_mlip]` — only mlips skill involved |
| DPA sublimation of molecular crystal | `[code_mlip, struct_molcrys]` — mlips + operate-molecular-crystal |
| EOS requiring structure construction | `[code_mlip, struct_build]` — mlips + build-crystal-from-params |
| LAMMPS with DeePMD model | `[eng_lammps]` — lammps skill (not mlips) |
| "APEX not available" refusal | `[meta_grounding]` — pure behavioral, no skill execution |
| "Bio head not available" boundary | `[meta_grounding, code_mlip]` — behavioral but mlips-related knowledge |
