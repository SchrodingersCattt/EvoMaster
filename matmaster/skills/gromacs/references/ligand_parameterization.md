# Small-Molecule Ligand Parameterization

## Route Overview

| Route | Force field | Available in image | Notes |
|-------|------------|-------------------|-------|
| `acpype -a gaff` | GAFF | ✅ Ready | AM1-BCC charges |
| `acpype -a gaff2` | GAFF2 | ✅ Ready | AM1-BCC charges (recommended) |
| `acpype -a opls` | OPLS-AA | ✅ Ready | AM1-BCC charges, OPLS atom types |
| `gmx pdb2gmx -ff oplsaa` | OPLS-AA | ✅ Ready | Standard residues only (protein, nucleic acid, solvents) |
| `LigParGen + BOSS` | OPLS-AA | ⚠️ Needs BOSS | CM1A-LBCC charges (most accurate for OPLS) |

## Route Selection Logic

When user requests **GAFF/GAFF2**: use `acpype -a gaff2` directly.

When user requests **OPLS-AA for small molecules**: **ask the user** which approach:
1. **`acpype -a opls`** — ready to use, good enough for most cases
2. **`LigParGen + BOSS`** — highest accuracy (CM1A-LBCC charges), requires user-provided BOSS path

When user requests **OPLS-AA for biomolecules** (protein, peptide, nucleic acid): use `gmx pdb2gmx -ff oplsaa` (no extra tooling needed).

Use `Open Babel` or `RDKit` for `SMILES` to `mol2/pdb` conversion when needed.

## GAFF/GAFF2 Route

Tools in image: `acpype` (2023.10.27), `antechamber`, `parmchk2`, `tleap` (AmberTools 24.8), `obabel` (3.1.1), `rdkit` (2025.9.5).

```bash
# In run.sh:
acpype -i molecule.mol2 -c bcc -a gaff2
# Produces: molecule.acpype/molecule_GMX.{gro,itp,top}
```

## OPLS-AA via ACPYPE

```bash
# In run.sh:
acpype -i molecule.mol2 -c bcc -a opls
# Produces: molecule.acpype/molecule_GMX_OPLS.{itp,top}
```

## OPLS-AA via LigParGen + BOSS

BOSS is **academic-free but closed-source** (Yale, Jorgensen group). LigParGen requires BOSS's `xZCM1A` binary.

**Before submitting**: ask user for BOSS package path. If provided (e.g. `/share/boss0824.tar.gz`), include in input_dir:

```bash
# In run.sh:
tar xzf boss0824.tar.gz
export BOSSdir=$(pwd)/boss
export PATH="$BOSSdir:$PATH"
python -m LigParGen -s 'CCO' -r MOL -c 0 -o 0
```

If user does NOT have BOSS: suggest `acpype -a opls` or the LigParGen web server (https://traken.chem.yale.edu/ligpargen/).

## Acceptance Checks

- **MUST**: parameterization outputs generated and runnable (`grompp` + short `mdrun` succeed).
- **SHOULD**: EM converges (`converged to Fmax`) or final `Epot` < initial.
- GAFF route produces `*_GMX.gro`, `*_GMX.itp`, `*_GMX.top`.
- OPLS route produces `*_GMX_OPLS.itp`/`.top` (ACPYPE) or LigParGen output files.
