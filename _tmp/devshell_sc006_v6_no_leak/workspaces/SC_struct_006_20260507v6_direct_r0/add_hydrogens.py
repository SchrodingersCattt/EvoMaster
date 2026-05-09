#!/usr/bin/env python
"""Add hydrogens to DACMOR.cif molecular crystal using molcrys_kit."""

import sys
import numpy as np
from pathlib import Path

# Use molcrys_kit for PBC-aware hydrogen completion
from molcrys_kit.io.cif import read_mol_crystal
from molcrys_kit.operations.hydrogen_completion import add_hydrogens
from molcrys_kit.io.output import write_cif

def main():
    input_cif = "DACMOR.cif"
    output_cif = "dacmor_hydrogenated.cif"
    
    print(f"Reading {input_cif}...")
    mol_crystal = read_mol_crystal(input_cif)
    
    print(f"Number of molecules in unit cell: {len(mol_crystal.molecules)}")
    
    # Count atoms before hydrogenation
    total_atoms_before = 0
    for i, mol in enumerate(mol_crystal.molecules):
        natoms = len(mol.atoms)
        total_atoms_before += natoms
        print(f"  Molecule {i}: {natoms} atoms")
    
    print(f"Total atoms before hydrogenation: {total_atoms_before}")
    
    # Add hydrogens with torsion optimization for better geometry
    print("\nAdding hydrogens...")
    crystal_h = add_hydrogens(
        mol_crystal,
        target_elements=None,  # Add H to all deficient heavy atoms
        optimize_torsion=True,  # Optimize CH3/NH2 dihedrals
    )
    
    # Count atoms after hydrogenation
    total_atoms_after = 0
    h_atoms_total = 0
    for i, mol in enumerate(crystal_h.molecules):
        natoms = len(mol.atoms)
        h_count = sum(1 for atom in mol.atoms if atom.element == 'H')
        total_atoms_after += natoms
        h_atoms_total += h_count
        print(f"  Molecule {i}: {natoms} atoms ({h_count} H)")
    
    print(f"Total atoms after hydrogenation: {total_atoms_after}")
    print(f"Total H atoms added: {h_atoms_total}")
    
    # Write output
    print(f"\nWriting {output_cif}...")
    write_cif(crystal_h, filename=output_cif)
    print("Done!")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
