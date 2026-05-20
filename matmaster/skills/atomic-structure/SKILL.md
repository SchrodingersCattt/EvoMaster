---
name: atomic-structure
description: "Orchestrates atomistic structure work: inspect CIF/POSCAR/PDB/XYZ/SMILES, acquire/convert files, build from spec, transform supercells/strain/doping/defects, assemble slabs/interfaces/adsorbates/boxes, handle polar surfaces, molecular crystals, disorder, dangling bonds, and sampling."
skill_type: orchestrator
---

# Atomic Structure Decision Tree

Use this skill as the fallback entry point for ambiguous, compound, or
multi-step atomistic structure workflows. The leaves below are first-class
skills in the router; when this decision tree reaches a leaf, load that
leaf's SKILL.md and follow its narrower workflow.

## Step 0 - Inspect First

For any task with an existing structure file, inspect before choosing a
mutation or construction path. Use `../inspect-atomic-structure/SKILL.md`.

Record at least:

- `unit_class`: `MOLECULAR_CRYSTAL`, `PERIODIC_LATTICE`,
  `ISOLATED_MOLECULE`, or `NEEDS_BUILD`.
- `periodicity`: nonperiodic, 1D, 2D slab/interface, or 3D periodic.
- `formula`, atom count, lattice sanity, space group / Wyckoff sites when
  available.
- `has_disorder`: partial occupancy, mixed occupants, alternate sites, or
  crystallographic disorder markers.
- `has_solvent_or_guest`: removable whole molecules under PBC.
- `has_dangling`: under-coordinated surface / local geometry needing
  completion.
- `is_polar_surface`: a known or suspected polar slab request, especially for
  ionic / heterovalent materials.

After every build, retrieval, conversion, transform, slab cut, interface build,
adsorbate placement, or sampling run, inspect the written output again before
reporting success.

## Decision Tree

1. **No usable input structure yet**

   If the user asks for a known material, database ID, DOI/SI URL, webpage, or
   file conversion, use `../playground-skills/retrieve-structure/SKILL.md`.
   Prefer retrieval for known structures; validate and convert after download.

   If the user gives a formula/prototype, space group plus Wyckoff positions,
   lattice constants, SMILES, or polymer sequence and wants a new structure from
   specification, use `../build-atomic-structure/SKILL.md`.

   If the user asks for global or property-conditional candidate generation
   rather than a deterministic known structure, use
   `../sample-atomic-structures/SKILL.md`.

2. **Input is an isolated molecule**

   Use `../build-atomic-structure/SKILL.md` for SMILES-to-3D or molecule file
   generation. If the molecule must be packed in a box, placed on a surface, or
   combined with another structure, route next to
   `../assemble-atomic-structure/SKILL.md`.

3. **Input is a molecular crystal or contains molecular fragments under PBC**

   Priors for `MOLECULAR_CRYSTAL`:

   - Organic or metal-organic molecular units packed by van der Waals contacts,
     hydrogen bonds, or pi stacking.
   - Discrete molecules recoverable by connectivity under periodic boundary
     conditions.
   - `Z' > 1`, `_chemical_formula_moiety`, guest/solvent molecules, or
     partial-occupancy molecular fragments in a CIF.

   Use `../operate-molecular-crystal/SKILL.md` when an operation could cut,
   delete, extract, desolvate, perturb, or order whole molecular fragments. This
   includes molecular-crystal slab cuts, molecule-cluster vacancies, guest
   extraction, desolvation, and disorder ordering.

4. **Input has disorder or partial occupancy**

   Priors:

   - CIF sites with occupancy below 1.
   - Mixed species on one crystallographic site.
   - Alternate locations, split positions, or moiety constraints.

   Use `../operate-molecular-crystal/SKILL.md` axis B for ordered replicas and
   moiety-aware crystallographic fixes. Do not emit fractional stoichiometry as
   a finished structure.

5. **Task is an identity-preserving edit of one periodic lattice**

   Priors:

   - Supercell, expansion matrix, or repeat counts.
   - Strain, shear, lattice scaling, or deformation.
   - Doping, substitution, vacancy, interstitial, F-center, or ordered alloying.
   - Site selection by random seed, Wyckoff label, symmetry orbit, or exact
     count.

   Use `../transform-atomic-structure/SKILL.md`. If inspection says the input is
   a molecular crystal and the requested edit would break molecular
   connectivity, reroute to `../operate-molecular-crystal/SKILL.md`.

6. **Task combines multiple structural pieces**

   Priors:

   - Bulk-to-slab construction for ordinary nonpolar slabs.
   - Adsorbate on a surface, interface / heterostructure, molecule-in-box,
     amorphous packing, or crosslink network.
   - Multiple inputs that must become one simulation object.

   Use `../assemble-atomic-structure/SKILL.md`. Inspect every input first and
   the final output after assembly.

7. **Task is a polar or Tasker-sensitive surface**

   Priors for suspected polar surface handling:

   - Ionic or heterovalent materials where a Miller-indexed slab can carry a
     dipole normal to the surface.
   - Known-risk surfaces such as wurtzite `(0001)`, rocksalt / fluorite
     `(111)`, perovskite `(001)` or `(110)`, and layered oxide polar cuts.
   - User asks for Tasker type, nonpolar termination, symmetric termination,
     auto-fixing a slab dipole, or validating a polar/ionic slab.

   Use `../playground-skills/tasker-polar-surface/SKILL.md`. If the material
   and Miller index are not in local lookup data, search literature before
   finalizing the provisional Tasker type. Always validate the actual slab after
   construction.

8. **Task needs local geometric repair or local environment characterisation**

   Priors:

   - Under-coordinated atoms, dangling bonds, sp3/sp2 hydrogen completion, or
     octahedral completion.
   - Need to decide coordination number, ideal polyhedron, CShM shape, or local
     packing shell.

   Use `../operate-molecular-crystal/SKILL.md` axis C or D. These axes are
   selected by the geometric task, not by `unit_class`; they may apply to
   inorganic surfaces and defects as well as molecular crystals.

## Compound Prompts

Decompose a compound request into ordered walks. Each walk starts from the
current inspected structure state and closes with inspection of the output.

Examples:

- "Build a ZnO (0001) slab and fix the dipole": retrieve/build bulk, inspect,
  route to Tasker polar-surface handling, inspect final slab.
- "Take this molecular crystal CIF, cut a (010) surface, and remove solvent":
  inspect, route to molecular-crystal operations for PBC-aware slab cutting and
  whole-molecule desolvation, inspect final CIF.
- "Download a CIF, convert to POSCAR, make a 2x2x1 supercell, then put CO on
  the surface": retrieve/convert, inspect, transform, inspect, assemble
  adsorbate, inspect.

When two leaves disagree, prefer the path that preserves physical invariants:
whole molecules stay whole, polar slabs are not accepted without Tasker-style
validation, and every written structure is sanity-checked before downstream
simulation.

## Scope Boundary

This skill orchestrates atomistic structure acquisition, construction,
inspection, conversion, editing, sampling, and assembly. It does not run DFT,
classical MD, MLIP training/inference, or trajectory/property analysis; after
the structure is prepared and inspected, route those requests to the relevant
simulation or analysis skill.
