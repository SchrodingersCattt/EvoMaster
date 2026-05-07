# VASP Complete Input Examples

## Example 1: Standard SCF (Metal)

**INCAR**:
```
SYSTEM = Al FCC SCF
PREC   = Accurate
ENCUT  = 400
EDIFF  = 1E-6
ISMEAR = 1
SIGMA  = 0.1
LREAL  = .FALSE.
LWAVE  = .FALSE.
LCHARG = .TRUE.
ALGO   = Fast
NELM   = 100
```

**KPOINTS**:
```
Automatic mesh
0
Gamma
  11 11 11
  0  0  0
```

---

## Example 2: Standard SCF (Semiconductor)

**INCAR**:
```
SYSTEM = Si diamond SCF
PREC   = Accurate
ENCUT  = 400
EDIFF  = 1E-6
ISMEAR = 0
SIGMA  = 0.05
LREAL  = .FALSE.
LWAVE  = .FALSE.
LCHARG = .TRUE.
ALGO   = Fast
NELM   = 100
```

**KPOINTS**:
```
Automatic mesh
0
Gamma
  8 8 8
  0 0 0
```

---

## Example 3: Full Cell + Ionic Relaxation

**INCAR**:
```
SYSTEM = full cell relaxation
PREC   = Accurate
ENCUT  = 520
EDIFF  = 1E-6
ISMEAR = 0
SIGMA  = 0.05
IBRION = 2
NSW    = 200
ISIF   = 3
EDIFFG = -0.01
LREAL  = Auto
LWAVE  = .FALSE.
LCHARG = .FALSE.
ALGO   = Fast
NELM   = 100
```

---

## Example 4: Band Structure (Two-Step)

### Step 1 -- SCF INCAR:
```
SYSTEM = band structure SCF
PREC   = Accurate
ENCUT  = 400
EDIFF  = 1E-6
ISMEAR = 0
SIGMA  = 0.05
LCHARG = .TRUE.
LWAVE  = .FALSE.
ALGO   = Fast
NELM   = 100
```
SCF KPOINTS (uniform mesh):
```
Automatic mesh
0
Gamma
  8 8 8
  0 0 0
```

### Step 2 -- NSCF INCAR:
```
SYSTEM = band structure NSCF
PREC   = Accurate
ENCUT  = 400
EDIFF  = 1E-6
ISMEAR = 0
SIGMA  = 0.05
ICHARG = 11
LORBIT = 11
LCHARG = .FALSE.
LWAVE  = .FALSE.
ALGO   = Fast
NELM   = 100
NBANDS = 20
```
NSCF KPOINTS (line-mode, FCC example):
```
k-points along high-symmetry lines
40
Line-mode
Reciprocal
  0.000  0.000  0.000   ! GAMMA
  0.500  0.000  0.500   ! X

  0.500  0.000  0.500   ! X
  0.500  0.250  0.750   ! W

  0.500  0.250  0.750   ! W
  0.375  0.375  0.750   ! K

  0.375  0.375  0.750   ! K
  0.000  0.000  0.000   ! GAMMA

  0.000  0.000  0.000   ! GAMMA
  0.500  0.500  0.500   ! L
```

---

## Example 5: DOS / PDOS

**INCAR** (single-step dense mesh or NSCF after SCF):
```
SYSTEM = DOS calculation
PREC   = Accurate
ENCUT  = 400
EDIFF  = 1E-6
ISMEAR = -5
SIGMA  = 0.05
LORBIT = 11
NEDOS  = 3001
EMIN   = -10
EMAX   = 10
LCHARG = .FALSE.
LWAVE  = .FALSE.
ALGO   = Fast
NELM   = 100
```
If NSCF step: add `ICHARG = 11`.

---

## Example 6: Spin-Polarized (Fe)

**INCAR**:
```
SYSTEM = Fe spin-polarized
PREC   = Accurate
ENCUT  = 500
EDIFF  = 1E-6
ISMEAR = 1
SIGMA  = 0.1
ISPIN  = 2
MAGMOM = 2*3.0
LORBIT = 11
LREAL  = .FALSE.
LWAVE  = .FALSE.
LCHARG = .TRUE.
ALGO   = Fast
NELM   = 200
AMIX   = 0.2
BMIX   = 0.001
AMIX_MAG = 0.8
BMIX_MAG = 0.001
```

---

## Example 7: HSE06 Hybrid DFT

**INCAR**:
```
SYSTEM = HSE06 band gap
PREC   = Accurate
ENCUT  = 400
EDIFF  = 1E-6
ISMEAR = 0
SIGMA  = 0.05
LHFCALC = .TRUE.
HFSCREEN = 0.2
AEXX   = 0.25
ALGO   = Damped
TIME   = 0.4
PRECFOCK = Fast
LREAL  = .FALSE.
LWAVE  = .FALSE.
LCHARG = .FALSE.
NELM   = 200
```

---

## Example 8: DFT+U (Fe2O3)

**INCAR**:
```
SYSTEM = Fe2O3 DFT+U
PREC   = Accurate
ENCUT  = 520
EDIFF  = 1E-6
ISMEAR = 0
SIGMA  = 0.05
ISPIN  = 2
MAGMOM = 4*5.0 6*0.6
LDAU   = .TRUE.
LDAUTYPE = 2
LDAUL  = 2 -1
LDAUU  = 5.0 0.0
LDAUJ  = 0.0 0.0
LMAXMIX = 4
ALGO   = Fast
NELM   = 200
LREAL  = Auto
LWAVE  = .FALSE.
LCHARG = .TRUE.
```

> Species order matches POSCAR: Fe first, O second.

---

## Example 9: SOC

**INCAR**:
```
SYSTEM = SOC calculation
PREC   = Accurate
ENCUT  = 400
EDIFF  = 1E-6
ISMEAR = 0
SIGMA  = 0.05
ISPIN  = 2
LSORBIT = .TRUE.
MAGMOM = 0 0 3.0  0 0 0.0
GGA_COMPAT = .FALSE.
LREAL  = .FALSE.
LWAVE  = .FALSE.
LCHARG = .TRUE.
ALGO   = Fast
NELM   = 200
NBANDS = 40
```

> MAGMOM for SOC: 3 components per atom (x y z). Use vasp_ncl binary.

---

## Example 10: AIMD NVT (300 K)

**INCAR**:
```
SYSTEM = AIMD NVT 300K
PREC   = Normal
ENCUT  = 400
EDIFF  = 1E-5
ISMEAR = 0
SIGMA  = 0.1
IBRION = 0
NSW    = 5000
POTIM  = 1.0
SMASS  = 0
TEBEG  = 300
TEEND  = 300
ISIF   = 2
ISYM   = 0
LREAL  = Auto
LWAVE  = .FALSE.
LCHARG = .FALSE.
ALGO   = VeryFast
NELM   = 100
```

---

## Example 11: Elastic Constants (Finite Differences)

**INCAR**:
```
SYSTEM = elastic constants
PREC   = Accurate
ENCUT  = 520
EDIFF  = 1E-7
ISMEAR = 0
SIGMA  = 0.05
IBRION = 6
ISIF   = 3
NFREE  = 2
NSW    = 1
ADDGRID = .TRUE.
LREAL  = .FALSE.
LWAVE  = .FALSE.
LCHARG = .FALSE.
ALGO   = Fast
NELM   = 200
```

---

## Common Mistakes Checklist

- Missing ENCUT -> inconsistent across calculations
- ISMEAR = -5 for relaxation of metals -> force jumps
- ISMEAR = 1 for DOS -> wrong DOS shape
- Band structure without ICHARG = 11 -> re-runs SCF
- Relaxation with positive EDIFFG -> energy criterion, not force
- NSW = 0 for relaxation -> static calculation
- SOC without ISPIN = 2 -> error
- Hybrid DFT with ALGO = Fast -> won't converge
- DFT+U wrong LDAUL species order -> +U on wrong element
- MD with ISYM != 0 -> symmetry artifacts
