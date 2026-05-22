---
name: vasp
description: "Use to RUN VASP calculations: SCF, relaxation, band/DOS, MD, hybrid DFT, SOC, magnetism, DFT+U, optical, NEB. Covers INCAR/KPOINTS/POSCAR generation, Bohrium submission, parsing. Do NOT use for VASP literature search or plotting precomputed band/DOS arrays."
skill_type: operator
---

# VASP Skill

Generate VASP input files (INCAR, KPOINTS, POSCAR) and submit via Bohrium.
Running the VASP binary locally is not allowed (commercial license).

## Capability Gate

- **FORBIDDEN**: VASP literature search, plotting precomputed band/DOS arrays, POTCAR generation.
  Action: STOP. Tell user "This task is outside the VASP skill scope." Wait.
- **Default output set**: When a task says "generate input files" or "prepare
  inputs" without specifying which files, produce **INCAR + KPOINTS + POSCAR**.
  If the task explicitly asks for a single file (e.g., "generate an INCAR"),
  produce only that file. POTCAR is license-restricted — note recommended
  pseudopotentials but never generate it.

## Scripts

| Script | Args | Output |
|--------|------|--------|
| `scripts/get_potcar.py` | `--elements "Fe,O" [--for-gw]` | stdout: recommended PAW PPs, max ENMAX |
| `scripts/validate_incar.py` | `-f INCAR -t {scf,relax,band,dos,md,hybrid,gw,phonon,neb,optical} [--is-metal] [--enmax ENMAX]` | stdout: pass/fail + tag conflict warnings |
| `scripts/generate_kpoints.py` | `--structure POSCAR [--mode {auto,line,gamma}]` | file: KPOINTS |

## Hard Guards

### Electronic Structure

| System type | ISMEAR | SIGMA |
|-------------|--------|-------|
| Metal | 1 or 2 (MP) | 0.1–0.2 |
| Semiconductor / insulator | 0 (Gaussian) | 0.05 |
| DOS / accurate total energy | -5 (tetrahedron+Blöchl) | — |
| Molecule / Gamma-only | 0 | 0.01 |

- **ENCUT must be set explicitly.** Run `scripts/get_potcar.py -e "<elements>"`
  to get max ENMAX, then set ENCUT ≥ 1.3× that value. Typical: 400–520 eV.
- **Band structure / DOS requires two-step**: SCF (uniform k-mesh) → NSCF with
  `ICHARG = 11` (band) or `ICHARG = 11` + dense mesh (DOS).
- **Static / NSCF calculations**: `IBRION = -1` (or omit), `NSW = 0`.
- **Projected band / PDOS / magnetic-moment analysis**: set `LORBIT = 11`
  unless the task explicitly requests another projection mode.

### Relaxation

- `IBRION = 2` (CG) or `1` (quasi-Newton); `NSW >= 100`.
- `ISIF = 2` (ionic-only) or `ISIF = 3` (full cell+ionic).
- `EDIFFG` negative for force convergence (e.g., `EDIFFG = -0.01` eV/Å).
- Fixed-cell slab/surface: set `ISIF = 2` explicitly.

### Magnetism & Spin-Orbit

- **Spin-polarized**: `ISPIN = 2`; set `MAGMOM` per atom. Use the element's
  formal oxidation state to estimate initial moments (e.g., Fe³⁺ high-spin → 5).
- **NUPDOWN / MAGMOM for multi-site or mixed-valence systems**: Do not assume
  spin states — the same element can be high-spin or low-spin depending on
  coordination environment (e.g., octahedral strong-field ligands force
  low-spin). Look up the material's established magnetic ground state before
  assigning values.
- **SOC decision table**:

| Condition | Action |
|-----------|--------|
| System contains Z ≥ 57 elements (lanthanides, actinides, 5d/6p: Hf, Ta, W, Re, Os, Ir, Pt, Au, Hg, Tl, Pb, Bi, Lu, etc.) AND task is band/DOS | Enable SOC: `LSORBIT = .TRUE.`, `ISPIN = 2`, `ISYM = 0`. Use `vasp_ncl`. |
| System contains Z ≥ 57 elements AND task is relaxation/SCF | Use AskQuestion: "体系含重元素，是否需要开启自旋-轨道耦合(SOC)？" Enable if user confirms. |
| No heavy elements (Z < 57) | SOC not needed |

- `LMAXMIX = 4` for d-electron systems; `LMAXMIX = 6` for f-electron systems.

### Special Methods

- **Hybrid DFT (HSE06)**: `LHFCALC = .TRUE.`, `HFSCREEN = 0.2`,
  `ALGO = Damped` or `All`, `TIME = 0.4`.
- **DFT+U**: Required for strongly correlated systems (e.g., NiO, FeO, MnO,
  CoO, transition-metal oxides with localized d/f electrons). Set
  `LDAU = .TRUE.`, `LDAUTYPE = 2` (Dudarev), `LDAUL`/`LDAUU`/`LDAUJ` arrays
  matching species order in POSCAR. U values are material-specific — look up
  published values for the target material before assigning.
- **Meta-GGA (SCAN/R2SCAN)**: `LASPH = .TRUE.` required.
- **Elastic tensor**: `IBRION = 6`, `ISIF = 3`, `NFREE = 2`.
- **Dispersion**: layered/MOF/organic/surface systems → `IVDW = 11` or `12`.
  For complex setups → `references/incar_tags.md` §Dispersion.

### POTCAR Resolution (before submission only)

Use AskQuestion to ask where POTCAR is:
- "VASP 镜像中已内置 POTCAR（如 `/opt/vasp/potcar/PBE/`）"
- "Bohrium 节点上的某个目录（请填路径）"
- "我没有 POTCAR"

No POTCAR → STOP, inform user it's license-restricted.
Has POTCAR → write `run.sh` that concatenates POTCAR from that path.

## Workflow

1. **Determine** calculation type and system from task spec.
2. **POSCAR** — construct from provided structure info (pymatgen or ASE).
3. **KPOINTS** — run `scripts/generate_kpoints.py --structure POSCAR`.
   K-mesh rules: bulk → Gamma-centered MP (~30–40 Å density); slab → dense
   in-plane, 1 in vacuum; band → line-mode; DOS → ≥2× SCF density; molecule → Γ.
4. **INCAR** — apply Hard Guards above. For full tag reference →
   `references/incar_tags.md`. For worked examples → `references/input_examples.md`.
5. **Validate** — `scripts/validate_incar.py -f INCAR -t <type> [--enmax <val>]`.
6. **Submit** (if requested) — resolve POTCAR first (see above), then Bohrium.

## Bohrium Submission

| Item | Default |
|------|---------|
| image | `list_images` with keyword `vasp` |
| machine | `c32_m128_cpu` |
| cmd | `OMP_NUM_THREADS=1 mpirun -np 16 vasp_std > log 2>&1` |

- Use `vasp_gam` for Gamma-only, `vasp_ncl` for SOC/noncollinear.
- `-np` = half of CPU cores (32 vCPU → 16 physical on Bohrium).
