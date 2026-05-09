#!/usr/bin/env python
"""Analyze H-C-H angles in dacmor_hydrogenated.cif to identify problem carbons."""
import sys
sys.path.insert(0, '/root/MolCrysKit')

import numpy as np
from pymatgen.core import Structure
from itertools import combinations
from collections import defaultdict

# Load structure
struct = Structure.from_file('dacmor_hydrogenated.cif')
print(f"Total atoms: {len(struct)}")
print(f"Composition: {struct.composition.reduced_formula}")
print(f"Total H: {struct.composition['H']}")
print(f"Total C: {struct.composition['C']}")
print(f"Total N: {struct.composition['N']}")
print(f"Total O: {struct.composition['O']}")
print()

# Find all C atoms and their bonded H atoms (C-H cutoff 1.5 A)
CH_CUTOFF = 1.5
all_angles = []
problem_carbons = []

for i, site_c in enumerate(struct):
    if str(site_c.specie) != 'C':
        continue
    
    # Find H neighbors within cutoff
    h_neighbors = []
    for j, site_h in enumerate(struct):
        if str(site_h.specie) != 'H':
            continue
        dist = struct.get_distance(i, j)
        if dist < CH_CUTOFF:
            h_neighbors.append((j, dist))
    
    if len(h_neighbors) < 2:
        continue  # Need at least 2 H for H-C-H angle
    
    # Calculate all H-C-H angles for this carbon
    for (h1_idx, h1_dist), (h2_idx, h2_dist) in combinations(h_neighbors, 2):
        # Minimum image vectors for PBC
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
        
        # Check if this is an outlier (more than 5 deg from 109.5)
        if abs(angle - 109.5) > 5:
            problem_carbons.append({
                'c_idx': i,
                'h1_idx': h1_idx,
                'h2_idx': h2_idx,
                'h1_dist': h1_dist,
                'h2_dist': h2_dist,
                'angle': angle,
                'n_h': len(h_neighbors)
            })

all_angles = np.array(all_angles)
print(f"=== H-C-H Angle Statistics (ALL triplets, C-H cutoff {CH_CUTOFF} A) ===")
print(f"Total H-C-H triplets: {len(all_angles)}")
print(f"Mean: {np.mean(all_angles):.2f} deg")
print(f"Std:  {np.std(all_angles):.2f} deg")
print(f"Min:  {np.min(all_angles):.2f} deg")
print(f"Max:  {np.max(all_angles):.2f} deg")
print()

print(f"=== Problem Carbons (|angle - 109.5| > 5 deg) ===")
print(f"Number of outlier triplets: {len(problem_carbons)}")
print()

# Group by carbon index
by_carbon = defaultdict(list)
for p in problem_carbons:
    by_carbon[p['c_idx']].append(p)

print(f"Number of unique problem C atoms: {len(by_carbon)}")
print()

for c_idx in sorted(by_carbon.keys()):
    entries = by_carbon[c_idx]
    site = struct[c_idx]
    print(f"  C atom index {c_idx} at ({site.frac_coords[0]:.4f}, {site.frac_coords[1]:.4f}, {site.frac_coords[2]:.4f})")
    print(f"    Number of bonded H: {entries[0]['n_h']}")
    for e in entries:
        print(f"    H-C-H angle: {e['angle']:.2f} deg (H{e['h1_idx']}, H{e['h2_idx']}, C-H dists: {e['h1_dist']:.3f}, {e['h2_dist']:.3f} A)")
    print()

# Angle distribution
print("\n=== Angle Distribution ===")
bins = [0, 50, 60, 70, 80, 90, 100, 105, 110, 115, 120, 130, 180]
hist, _ = np.histogram(all_angles, bins=bins)
for i in range(len(bins)-1):
    if hist[i] > 0:
        print(f"  {bins[i]:3d} - {bins[i+1]:3d} deg: {hist[i]} triplets")

# Check CH multiplicity
print("\n=== C-H multiplicity ===")
ch_counts = defaultdict(int)
for i, site_c in enumerate(struct):
    if str(site_c.specie) != 'C':
        continue
    n_h = 0
    for j, site_h in enumerate(struct):
        if str(site_h.specie) != 'H':
            continue
        dist = struct.get_distance(i, j)
        if dist < CH_CUTOFF:
            n_h += 1
    if n_h > 0:
        ch_counts[n_h] += 1

for k in sorted(ch_counts.keys()):
    print(f"  C with {k} H atoms: {ch_counts[k]} carbons")
