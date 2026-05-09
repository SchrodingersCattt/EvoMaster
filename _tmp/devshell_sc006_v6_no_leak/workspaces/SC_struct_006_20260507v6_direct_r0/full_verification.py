#!/usr/bin/env python
import sys
sys.path.insert(0, '/root/MolCrysKit')

import numpy as np
from pymatgen.core import Structure
from itertools import combinations
from collections import defaultdict

# Load structure
struct = Structure.from_file('dacmor_hydrogenated.cif')

print('=== FULL VERIFICATION ===')
print(f'Reduced formula: {struct.composition.reduced_formula}')
print(f'Total atoms in unit cell: {len(struct)}')
print(f'Total H: {int(struct.composition["H"])}')
print(f'Total C: {int(struct.composition["C"])}')
print(f'Total N: {int(struct.composition["N"])}')
print(f'Total O: {int(struct.composition["O"])}')
print(f'H per molecule (Z=4): {int(struct.composition["H"])/4}')
print()

# H-C-H angles
CH_CUTOFF = 1.5
all_angles = []

for i, site_c in enumerate(struct):
    if str(site_c.specie) != 'C':
        continue
    
    h_neighbors = []
    for j, site_h in enumerate(struct):
        if str(site_h.specie) != 'H':
            continue
        dist = struct.get_distance(i, j)
        if dist < CH_CUTOFF:
            h_neighbors.append((j, dist))
    
    if len(h_neighbors) < 2:
        continue
    
    for (h1_idx, h1_dist), (h2_idx, h2_dist) in combinations(h_neighbors, 2):
        frac_c = site_c.frac_coords
        frac_h1 = struct[h1_idx].frac_coords
        frac_h2 = struct[h2_idx].frac_coords
        
        dfrac1 = frac_h1 - frac_c
        dfrac1 -= np.round(dfrac1)
        vec1 = struct.lattice.get_cartesian_coords(dfrac1)
        
        dfrac2 = frac_h2 - frac_c
        dfrac2 -= np.round(dfrac2)
        vec2 = struct.lattice.get_cartesian_coords(dfrac2)
        
        cos_angle = np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))
        cos_angle = np.clip(cos_angle, -1.0, 1.0)
        angle = np.degrees(np.arccos(cos_angle))
        all_angles.append(angle)

all_angles = np.array(all_angles)
print(f'=== H-C-H Angle Statistics (C-H cutoff {CH_CUTOFF} A) ===')
print(f'Total H-C-H triplets: {len(all_angles)}')
print(f'Mean: {np.mean(all_angles):.2f} deg')
print(f'Std:  {np.std(all_angles):.2f} deg')
print(f'Min:  {np.min(all_angles):.2f} deg')
print(f'Max:  {np.max(all_angles):.2f} deg')
print(f'Within 109.5 +/- 5 deg: {np.sum(np.abs(all_angles - 109.5) <= 5)} / {len(all_angles)} triplets')
print()

# Check molecular connectivity
print('=== Molecular Connectivity Check ===')
from molcrys_kit import read_mol_crystal
mc = read_mol_crystal('dacmor_hydrogenated.cif')
mols = mc.get_unwrapped_molecules()
print(f'Number of molecules: {len(mols)}')
for i, mol in enumerate(mols):
    syms = mol.get_chemical_symbols()
    c_count = syms.count('C')
    h_count = syms.count('H')
    n_count = syms.count('N')
    o_count = syms.count('O')
    print(f'  Molecule {i}: C{c_count}H{h_count}N{n_count}O{o_count} ({len(syms)} atoms)')

print()
print('=== VERIFICATION RESULT ===')
mean_angle = np.mean(all_angles)
if abs(mean_angle - 109.5) <= 5:
    print(f'PASS: Mean H-C-H angle {mean_angle:.2f} deg is within 109.5 +/- 5 deg')
else:
    print(f'FAIL: Mean H-C-H angle {mean_angle:.2f} deg is outside 109.5 +/- 5 deg')

if int(struct.composition["H"])/4 == 23:
    print(f'PASS: H per molecule = 23 (correct for C21H23NO5)')
else:
    print(f'FAIL: H per molecule = {int(struct.composition["H"])/4} (expected 23)')
