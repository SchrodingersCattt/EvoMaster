#!/usr/bin/env python
import sys
sys.path.insert(0, '/root/MolCrysKit')

import numpy as np
from ase import Atoms
from molcrys_kit import read_mol_crystal
from molcrys_kit.operations.hydrogen_completion import add_hydrogens
from molcrys_kit.structures.crystal import MolecularCrystal
from molcrys_kit.io import write_cif
from rdkit import Chem
from rdkit.Chem import AllChem, rdDetermineBonds
from rdkit.Geometry import Point3D

# Step 1: Load and hydrogenate with molcrys_kit
print('Loading DACMOR.cif...')
crystal = read_mol_crystal('DACMOR.cif')
print(f'Original crystal: {crystal.summary()}')

print('Hydrogenating with molcrys_kit...')
h_crystal = add_hydrogens(crystal, target_elements=['C', 'N', 'O'], optimize_torsion=False)
print(f'Hydrogenated crystal: {h_crystal.summary()}')

# Step 2: For each molecule, optimize H positions with MMFF
print('Optimizing H positions with RDKit MMFF94...')
molecules = h_crystal.get_unwrapped_molecules()
print(f'Number of molecules: {len(molecules)}')

optimized_molecules = []
for mol_idx, mol in enumerate(molecules):
    symbols = mol.get_chemical_symbols()
    positions = mol.get_positions()
    n_atoms = len(symbols)
    
    print(f'  Molecule {mol_idx}: {len(symbols)} atoms')
    
    # Build RDKit molecule from 3D coordinates
    rwmol = Chem.RWMol()
    for sym in symbols:
        atom = Chem.Atom(sym)
        rwmol.AddAtom(atom)
    
    # Set 3D conformer
    conf = Chem.Conformer(n_atoms)
    for i in range(n_atoms):
        conf.SetAtomPosition(i, Point3D(float(positions[i][0]), float(positions[i][1]), float(positions[i][2])))
    rwmol.AddConformer(conf, assignId=True)
    
    # Determine bonds from 3D coordinates
    mol_rd = rwmol.GetMol()
    try:
        rdDetermineBonds.DetermineConnectivity(mol_rd)
        rdDetermineBonds.DetermineBondOrders(mol_rd)
        print(f'    Bond determination: {mol_rd.GetNumBonds()} bonds')
    except Exception as e:
        print(f'    Bond determination failed: {e}')
        optimized_molecules.append(mol)
        continue
    
    # Run MMFF optimization with heavy atoms constrained
    try:
        Chem.SanitizeMol(mol_rd)
        mp = AllChem.MMFFGetMoleculeProperties(mol_rd)
        if mp is None:
            print(f'    MMFF properties failed, trying UFF...')
            ff = AllChem.UFFGetMoleculeForceField(mol_rd)
            if ff is None:
                print(f'    UFF also failed, keeping original')
                optimized_molecules.append(mol)
                continue
            for i in range(n_atoms):
                if symbols[i] != 'H':
                    ff.AddFixedPoint(i)
            ff.Minimize(maxIts=500)
        else:
            ff = AllChem.MMFFGetMoleculeForceField(mol_rd, mp)
            if ff is None:
                print(f'    MMFF FF failed, keeping original')
                optimized_molecules.append(mol)
                continue
            for i in range(n_atoms):
                if symbols[i] != 'H':
                    ff.AddFixedPoint(i)
            result = ff.Minimize(maxIts=500)
            print(f'    MMFF optimization: {"converged" if result==0 else "not converged"}')
        
        # Extract optimized positions
        conf_opt = mol_rd.GetConformer()
        new_positions = np.array([[conf_opt.GetAtomPosition(i).x,
                                   conf_opt.GetAtomPosition(i).y,
                                   conf_opt.GetAtomPosition(i).z] for i in range(n_atoms)])
        
        new_mol = Atoms(symbols=symbols, positions=new_positions)
        optimized_molecules.append(new_mol)
        
        h_mask = np.array([s == 'H' for s in symbols])
        if h_mask.any():
            displ = np.linalg.norm(new_positions[h_mask] - positions[h_mask], axis=1)
            print(f'    Max H displacement: {np.max(displ):.3f} A, Mean: {np.mean(displ):.3f} A')
    except Exception as e:
        print(f'    Optimization failed: {e}')
        import traceback
        traceback.print_exc()
        optimized_molecules.append(mol)

# Step 3: Create new crystal and save
print('Creating new crystal with optimized molecules...')
new_crystal = MolecularCrystal(
    lattice=h_crystal.lattice,
    molecules=optimized_molecules,
    pbc=h_crystal.pbc
)

write_cif(new_crystal, 'dacmor_hydrogenated.cif')
print('Saved dacmor_hydrogenated.cif')
