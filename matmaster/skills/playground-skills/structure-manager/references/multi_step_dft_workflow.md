# Multi-Step DFT Workflow Patterns — Reference

Common multi-step computational workflows for materials science tasks. These patterns apply across software stacks (ABACUS, CP2K, QE, etc.).

## General Workflow Strategy

**Always follow this order:**
1. **Acquire structure** — from database, literature, or construction
2. **Validate structure** — run `assess_structure.py`
3. **Set up calculation** — generate input files following the software skill
4. **Submit to Bohrium** — with correct image, machine, cmd
5. **Poll and download results** — check for convergence
6. **Analyze and report** — extract physical properties, compare with literature

**Save-early rule**: Write every intermediate file to workspace immediately. If a later step fails, earlier deliverables (structures, input files, partial results) still have value.

## Pattern 1: Surface Energy Calculation

**Required calculations**: bulk cell-relax + slab relax (multiple thicknesses)

1. **Get bulk structure** → cell-relax to find equilibrium bulk energy/cell.
2. **Build slab** → use `build_surface_slab` (MCP) or `build_slab_tasker_fix.py`. Multiple thicknesses (e.g., 5-layer, 7-layer) recommended.
3. **Relax slab** → fix bottom layers, relax top layers. KPT: dense in-plane (≥12×12), 1 in vacuum direction.
4. **Compute** → E_surf = (E_slab − n × E_bulk) / (2 × A), where n = number of bulk units in slab, A = surface area.

**Key checks**:
- Consistent computational parameters across bulk and slab (ecutwfc, smearing, etc.)
- Each INPUT must reference its own STRU/KPT file with `stru_file` / `kpoint_file`
- Vacuum ≥ 15 Å for slab

## Pattern 2: Vacancy / Defect Formation Energy

**Required calculations**: pristine bulk/slab + defected system

1. **Build pristine supercell** → large enough that defect-image distance > 10 Å.
2. **Create defect** → remove atom (vacancy), substitute (antisite), add interstitial.
3. **Relax both** → same computational parameters.
4. **Compute** → E_f = E_defected − E_pristine + μ_removed (for vacancy)

**Key checks**:
- Use `kspacing` in INPUT for supercells (not fixed KPT mesh)
- For magnetic systems: `nspin 2` with appropriate initial moments
- BSSE correction for LCAO: add ghost atoms at vacant sites (see ABACUS STRU format reference)

## Pattern 3: Band Structure / Electronic Properties

**Required calculations**: SCF → NSCF (two-step)

1. **Primitive cell** — use `SpacegroupAnalyzer.get_primitive_standard_structure()` for band structure.
2. **SCF** → converge charge density with `out_chg 1`.
3. **NSCF** → read charge with `init_chg file`, use line-mode k-path for bands or dense mesh for DOS.
4. **Analyze** → extract band gap (VBM, CBM), plot band structure.

**Key checks**:
- Two separate KPT files (uniform mesh for SCF, line-mode for NSCF)
- `symmetry 0` in NSCF INPUT
- `nbands` explicitly set (≥ 1.5× occupied for metals)
- Chain both steps in `run.sh` for Bohrium

## Pattern 4: Equation of State (EOS)

**Required calculations**: SCF at multiple volumes

1. **Generate structures** → scale lattice constant by ±5% in 7–11 steps.
2. **SCF at each volume** → same parameters, only cell dimensions differ.
3. **Fit EOS** → Birch-Murnaghan or Murnaghan equation to E(V) data.
4. **Extract** → equilibrium volume V₀, bulk modulus B₀, pressure derivative B₀'.

**Key checks**:
- All INPUT files must use identical `ecutwfc`, `smearing_method`, `smearing_sigma`, `scf_thr`
- Each INPUT must reference its own STRU file via `stru_file`
- Keep k-point density consistent (use `kspacing` across all volumes)

## Pattern 5: Adsorption Energy

**Required calculations**: clean surface + adsorbate-on-surface + isolated adsorbate

1. **Prepare clean slab** → relax, save energy.
2. **Place adsorbate** → at high-symmetry sites (top, bridge, hollow).
3. **Relax adsorbate+slab** → fix bottom slab layers.
4. **Isolated adsorbate** → molecule in large box.
5. **Compute** → E_ads = E(slab+ads) − E(slab) − E(ads)

**Key checks**:
- Consistent parameters across all three calculations
- Sufficient vacuum (≥ 15 Å) above adsorbate
- Dipole correction for asymmetric slabs

## Pattern 6: Ferroelectric / Perovskite Calculations

1. **Structure**: ABO₃ perovskite or related structure from database.
2. **Cell relaxation**: Full cell-relax to find ground state structure.
3. **Polarization**: Compare paraelectric (high-symmetry) vs ferroelectric (distorted) phases.
4. **Band structure**: If electronic properties requested, follow Pattern 3.

**Material-specific notes**:
- Perovskites often need `nspin 1` (non-magnetic) or `nspin 2` (if magnetic B-site).
- PBE often underestimates band gaps — note this in results.
- Tolerance factor t = (r_A + r_O) / [√2 × (r_B + r_O)] for stability assessment.

## Pattern 7: High-Entropy Alloy (HEA) / Multi-Component Systems

1. **Structure**: Build SQS (Special Quasi-random Structure) or ordered supercell.
2. **Relaxation**: Cell-relax with appropriate k-mesh density.
3. **Properties**: Formation energy, mixing energy, elastic constants.

**Material-specific notes**:
- HEA supercells are large → use `kspacing` for automatic k-mesh.
- Magnetic elements (Fe, Co, Ni, Mn) → `nspin 2`.
- Formation energy: E_f = E_HEA − Σ(x_i × E_i) where x_i = mole fraction.

## Pattern 8: Steel / Alloy Defect Studies

1. **Bulk reference**: Relax bulk Fe (bcc) or austenite (fcc) supercell.
2. **Defect creation**: Vacancy, interstitial C/N, substitutional alloy element.
3. **Relaxation**: Relax defected supercell.
4. **Analysis**: Formation energy, migration barrier (NEB if available).

## Bohrium Submission Strategy for Multi-Step Workflows

### Option A: Single submission with run.sh (preferred)
Write a `run.sh` that chains all steps. One job, one download.

### Option B: Sequential submissions
Submit step 1, poll, download, prepare step 2 from results, submit step 2.
Use this when later steps depend on specific outputs from earlier steps.

### Option C: Parallel independent calculations
Submit all independent calculations simultaneously (e.g., bulk + slab + adsorbate).
Poll all, then download and compute derived quantities.

## Result Analysis Checklist

After downloading results, always:
1. **Check convergence**: Look for `charge density convergence is achieved` (ABACUS) or equivalent.
2. **Extract energies**: Use `parse_abacus.py` or grep patterns from result-analysis skill.
3. **Compare with literature**: State whether results are within expected range.
4. **Report uncertainties**: Mention DFT-level accuracy limitations.
5. **Show formulas**: When computing derived quantities (surface energy, formation energy), write the formula explicitly and show all terms.
