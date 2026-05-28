# VASP INCAR Tag Reference

Organized by functional category. For each tag: name, type, default, typical
values, and critical notes.

---

## 1. General

| Tag       | Type   | Default     | Typical                    | Notes |
|-----------|--------|-------------|----------------------------|-------|
| SYSTEM    | string | —           | descriptive label          | Comment only, no effect on calculation |
| PREC      | string | Normal      | Normal, Accurate, High     | Accurate -> ENCUT x 1.3, tighter FFT grid |
| ENCUT     | float  | from POTCAR | 400-520 eV                 | **Always set explicitly.** 1.3x max ENMAX. |
| ISTART    | int    | 0 (1 if WAVECAR) | 0=new, 1=continue     | 0 for fresh calc; 1 to restart |
| ICHARG    | int    | 2 (0 if ISTART>0) | 0,1,2,11             | 2=superposition; 11=read CHGCAR, keep fixed (NSCF) |
| LWAVE     | bool   | .TRUE.      | .TRUE./.FALSE.             | Write WAVECAR; .FALSE. saves disk |
| LCHARG    | bool   | .TRUE.      | .TRUE./.FALSE.             | Write CHGCAR; needed for NSCF step |
| NPAR      | int    | 1           | sqrt(ncores)               | Parallelization; tune for performance |
| KPAR      | int    | 1           | <= number of k-points      | k-point parallelization |

## 2. Electronic Minimization

| Tag       | Type   | Default  | Typical                    | Notes |
|-----------|--------|----------|----------------------------|-------|
| ALGO      | string | Normal   | Normal, Fast, VeryFast, All, Damped | Normal=Davidson; Fast=Davidson+RMM; All=CG (needed for hybrid) |
| NELM      | int    | 60       | 100-200                    | Max SCF iterations; increase for difficult convergence |
| NELMIN    | int    | 2        | 4-6                        | Min SCF iterations before checking convergence |
| EDIFF     | float  | 1E-4     | 1E-5 to 1E-7              | SCF energy convergence (eV); 1E-5 standard, 1E-7 for phonon/elastic |
| ISMEAR    | int    | 1        | -5, -1, 0, 1, 2           | **Critical.** -5=tetrahedron; 0=Gaussian; 1,2=MP order 1,2 |
| SIGMA     | float  | 0.2      | 0.01-0.2                  | Smearing width (eV). Reduce for semiconductors/insulators. |
| LREAL     | string | .FALSE.  | Auto, .FALSE.              | .FALSE. for small cells (<20 atoms); Auto for large cells |
| AMIX      | float  | 0.4      | 0.2                        | Linear mixing for difficult systems |
| BMIX      | float  | 1.0      | 0.001-1.0                 | Kerker mixing parameter |
| AMIX_MAG  | float  | 1.6      | 0.8                        | Magnetic mixing |
| BMIX_MAG  | float  | 1.0      | 0.001-1.0                 | Magnetic Kerker mixing |

## 3. Ionic Relaxation

| Tag       | Type   | Default  | Typical                    | Notes |
|-----------|--------|----------|----------------------------|-------|
| IBRION    | int    | -1 (0 if NSW>0) | -1,0,1,2,5,6,7,8   | -1=static; 0=MD; 1=quasi-Newton; 2=CG; 5,6=DFPT; 7,8=finite-diff |
| NSW       | int    | 0        | 100-500                    | Max ionic steps. 0=static (SCF only). |
| ISIF      | int    | 2        | 2,3,4                     | 2=ions only; 3=ions+cell+volume; 4=ions+cell shape (fix V) |
| EDIFFG    | float  | EDIFF x 10 | -0.01 to -0.05          | **Negative = force criterion (eV/A)**; positive = energy criterion |
| POTIM     | float  | 0.5      | 0.1-0.5                   | Step width; reduce for difficult relaxation |
| ADDGRID   | bool   | .FALSE.  | .TRUE.                     | Extra FFT grid for forces; improves force accuracy |

## 4. Spin / Magnetism

| Tag           | Type   | Default  | Typical                  | Notes |
|---------------|--------|----------|--------------------------|-------|
| ISPIN         | int    | 1        | 1, 2                     | 1=non-spin; 2=spin-polarized |
| MAGMOM        | array  | 1.0/atom | per-atom moments         | e.g., `4*5.0 4*-5.0 8*0.6` for AFM Fe8O8 |
| NUPDOWN       | int    | -1 (off) | total N↑ − N↓           | Constrains total spin. Use for mixed-valence or hard-to-converge magnetic states |
| LORBIT        | int    | 0        | 10, 11, 12               | 10=DOSCAR+lm-PROCAR; 11=same+phase; needed for PDOS |

### NUPDOWN for mixed-valence systems

When a system has the same element in multiple oxidation states (e.g., Fe²⁺/Fe³⁺), determine NUPDOWN by:

1. **Assign oxidation states** from charge balance (e.g., A₄Fe₈(CN)₂₄: Na⁺₄ + Fe₈ + CN⁻₂₄ → Fe avg +2.5 → 4×Fe²⁺ + 4×Fe³⁺)
2. **Determine spin state per site** from ligand field:
   - Strong-field ligands (CN⁻, CO) → low-spin
   - Weak-field ligands (N-end of CN⁻, O²⁻, H₂O) → high-spin
3. **Count unpaired electrons** per site using d-electron config:
   - Fe²⁺ (d⁶) low-spin: t₂g⁶ → 0 unpaired
   - Fe²⁺ (d⁶) high-spin: t₂g⁴eg² → 4 unpaired
   - Fe³⁺ (d⁵) low-spin: t₂g⁵ → 1 unpaired
   - Fe³⁺ (d⁵) high-spin: t₂g³eg² → 5 unpaired
4. **Sum all sites** → NUPDOWN = total unpaired electrons

## 5. Spin-Orbit Coupling (SOC)

| Tag            | Type | Default | Notes |
|----------------|------|---------|-------|
| LSORBIT        | bool | .FALSE. | .TRUE. enables SOC; implies LNONCOLLINEAR |
| LNONCOLLINEAR  | bool | .FALSE. | Automatically .TRUE. when LSORBIT=.TRUE. |
| SAXIS          | vec  | 0 0 1   | Spin quantization axis |
| NBANDS         | int  | auto    | Often must increase 2x for SOC |
| GGA_COMPAT     | bool | .TRUE.  | Set .FALSE. for accurate SOC forces |

Use `vasp_ncl` binary for SOC calculations.

## 6. Hybrid DFT (HSE06, PBE0)

| Tag       | Type   | Default  | Typical (HSE06)          | Notes |
|-----------|--------|----------|--------------------------|-------|
| LHFCALC   | bool   | .FALSE.  | .TRUE.                   | Enable Hartree-Fock exchange |
| HFSCREEN  | float  | 0.0      | 0.2 (HSE06)             | Screening parameter; 0.0=PBE0 |
| AEXX      | float  | 0.25     | 0.25                     | Fraction of exact exchange |
| ALGO      | string | Normal   | Damped or All            | **Must use Damped or All for hybrid** |
| TIME      | float  | 0.4      | 0.4                      | Damping parameter for ALGO=Damped |
| PRECFOCK  | string | Normal   | Fast or Normal           | FFT grid for Fock exchange |

## 7. DFT+U (Dudarev)

| Tag       | Type   | Default  | Typical                  | Notes |
|-----------|--------|----------|--------------------------|-------|
| LDAU      | bool   | .FALSE.  | .TRUE.                   | Enable DFT+U |
| LDAUTYPE  | int    | 2        | 2 (Dudarev)             | Type 2: only U_eff = U-J matters |
| LDAUL     | array  | -1       | per-species: 2 for d, 3 for f, -1 for none | Angular momentum for +U correction |
| LDAUU     | array  | 0.0      | per-species U values     | e.g., `5.0 0.0` for Fe-O |
| LDAUJ     | array  | 0.0      | per-species J values     | Often 0.0 for Dudarev (U_eff = U-J) |
| LMAXMIX   | int    | 4        | 4 for d-electrons, 6 for f | Must match LDAUL angular momentum |

## 8. vdW / Dispersion

| Tag         | Type   | Default  | Typical                | Notes |
|-------------|--------|----------|------------------------|-------|
| IVDW        | int    | 0        | 11 (D3-BJ), 12 (D3-zero) | DFT-D3 corrections |
| LUSE_VDW    | bool   | .FALSE.  | .TRUE.                 | For optB86b-vdW, vdW-DF2, etc. |
| GGA         | string | PE       | MK (optB86b), ML (vdW-DF2) | Functional override for nonlocal vdW |
| AGGAC       | float  | 1.0      | 0.0                    | Set 0.0 for vdW-DF functionals |

## 9. Band Structure (NSCF)

Two-step workflow: SCF -> NSCF.

**Step 1 -- SCF INCAR** (write CHGCAR):
- Standard electronic tags + `LCHARG = .TRUE.`

**Step 2 -- NSCF INCAR** (read CHGCAR, line-mode KPOINTS):
- `ICHARG = 11` -- **mandatory** for NSCF; without it VASP re-runs SCF
- `LORBIT = 11` -- write projected band character
- `LCHARG = .FALSE.`, `LWAVE = .FALSE.`
- Same electronic tags as SCF

## 10. DOS / PDOS

| Tag       | Type   | Default  | Typical                  | Notes |
|-----------|--------|----------|--------------------------|-------|
| ISMEAR    | int    | 1        | **-5** (tetrahedron)     | **Mandatory for accurate DOS** |
| NEDOS     | int    | 301      | 2001-5001                | Number of DOS grid points |
| EMIN      | float  | auto     | -10                      | DOS energy range minimum (eV) |
| EMAX      | float  | auto     | 10                       | DOS energy range maximum (eV) |
| LORBIT    | int    | 0        | 10 or 11                 | **Required for PDOS/lm-decomposed DOS** |

## 11. Molecular Dynamics (AIMD)

| Tag       | Type   | Default  | Typical                  | Notes |
|-----------|--------|----------|--------------------------|-------|
| IBRION    | int    | -1       | 0                        | 0=MD |
| NSW       | int    | 0        | 1000-10000               | Number of MD steps |
| POTIM     | float  | 0.5      | 1.0-2.0                  | Timestep in fs |
| SMASS     | float  | -3       | 0 (NVT Nose), -1 (NVE)  | Thermostat; -1=NVE, 0=NVT |
| TEBEG     | float  | 0        | 300                      | Starting temperature (K) |
| TEEND     | float  | TEBEG    | 300                      | Final temperature (K) |
| ISIF      | int    | 2        | 2 (NVT/NVE), 3 (NPT)   | |
| MDALGO    | int    | 0        | 0 (standard), 3 (Langevin) | MD algorithm |
| ISYM      | int    | 2        | 0                        | **Turn off symmetry for MD** |

## 12. Optical Properties

| Tag       | Type   | Default  | Typical                  | Notes |
|-----------|--------|----------|--------------------------|-------|
| LOPTICS   | bool   | .FALSE.  | .TRUE.                   | Compute dielectric function |
| NEDOS     | int    | 301      | 2001                     | Grid points for optical spectrum |
| NBANDS    | int    | auto     | 2x default               | Need many empty bands |
| CSHIFT    | float  | 0.1      | 0.1                      | Broadening |

## 13. Elastic Constants / Phonon (DFPT)

| Tag       | Type   | Default  | Typical                  | Notes |
|-----------|--------|----------|--------------------------|-------|
| IBRION    | int    | -1       | 5 (DFPT), 6 (finite-diff) | |
| ISIF      | int    | 2        | 3                        | Must include stress |
| NFREE     | int    | —        | 2 or 4                   | +/- displacements |
| LEPSILON  | bool   | .FALSE.  | .TRUE.                   | Born charges + dielectric |
| EDIFF     | float  | 1E-4     | 1E-7 to 1E-8            | Tight convergence required |
| ADDGRID   | bool   | .FALSE.  | .TRUE.                   | Improve force accuracy |

## 14. NEB / CI-NEB

| Tag       | Type   | Default  | Typical                  | Notes |
|-----------|--------|----------|--------------------------|-------|
| IBRION    | int    | -1       | 3                        | Damped dynamics for NEB |
| POTIM     | float  | 0.5      | 0.0                      | 0 -> VTST handles steps |
| IMAGES    | int    | 0        | 3-7                      | Intermediate images |
| SPRING    | float  | -5.0     | -5.0                     | Spring constant |
| LCLIMB    | bool   | .FALSE.  | .TRUE.                   | CI-NEB |
| EDIFFG    | float  | —        | -0.05                    | Force convergence |

---

## Quick-Select: Mandatory INCAR Tags by Calculation Type

| Calculation      | Mandatory INCAR Tags |
|------------------|---------------------|
| SCF (static)     | ENCUT, EDIFF, ISMEAR, SIGMA, PREC |
| Ionic relax      | + IBRION=2, NSW>=100, ISIF=2, EDIFFG<0 |
| Cell+ionic relax | + IBRION=2, NSW>=100, ISIF=3, EDIFFG<0 |
| Band structure   | Step 1: SCF+LCHARG=.TRUE. Step 2: ICHARG=11, LORBIT=11 |
| DOS              | ISMEAR=-5, NEDOS>=2001, LORBIT=10/11 |
| Spin-polarized   | + ISPIN=2, MAGMOM |
| SOC              | + LSORBIT=.TRUE., ISPIN=2, vasp_ncl |
| HSE06            | + LHFCALC=.TRUE., HFSCREEN=0.2, ALGO=Damped, TIME=0.4 |
| DFT+U            | + LDAU=.TRUE., LDAUTYPE=2, LDAUL, LDAUU, LDAUJ, LMAXMIX |
| AIMD NVT         | IBRION=0, NSW, POTIM, SMASS=0, TEBEG, TEEND, ISYM=0 |
| Optical          | + LOPTICS=.TRUE., NBANDS=2x, NEDOS=2001 |
| Elastic (DFPT)   | IBRION=6, ISIF=3, NFREE=2, EDIFF=1E-7 |
| NEB              | IBRION=3, POTIM=0, IMAGES, SPRING, LCLIMB |
