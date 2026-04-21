# Batch Structure Processing Guide

When a task requires processing multiple structures (≥3), follow this guide for efficient breadth-first execution.

## Core Principles

1. **Breadth-first, not depth-first**: Process all structures through one stage before moving to the next stage. Do NOT fully complete structure A before starting structure B.
2. **Save early, save often**: Write each intermediate structure file to disk immediately after creation. If a later step fails or times out, previously saved files are still deliverable.
3. **Fail-forward**: If one structure fails, skip it and continue with the rest. Report failures at the end — do NOT let one failure block all others.
4. **Budget turns per structure**: Allocate ~2–3 MCP/tool calls per structure. If a single structure consumes >3 turns, skip and move on.
5. **Verify breadth-first too**: After building all structures, run `assess_structure.py` on all of them (not one-by-one interleaved with building).

## Standard Batch Workflow

### Phase 1: Acquire all structures
```
For each target:
  1. Query database / search literature / generate structure
  2. Save as CIF/POSCAR immediately: structure_001.cif, structure_002.cif, ...
  3. If query fails after 1-2 attempts: record failure, move to next target
```

### Phase 2: Process / transform all structures
```
For each saved structure:
  1. Apply transformation (supercell, slab, defect, conversion, etc.)
  2. Save transformed structure immediately: transformed_001.cif, ...
  3. If transformation fails: record failure, keep original as partial deliverable
```

### Phase 3: Validate all structures
```
For each transformed structure:
  1. Run assess_structure.py --file <path>
  2. Record: formula, atom count, dimensionality, warnings
  3. If invalid: flag for review but do NOT delete the file
```

### Phase 4: Produce deliverables
```
Compile summary table/report with:
  - Status per structure (success/failure/partial)
  - Key properties (formula, space group, lattice params)
  - Any failures with reasons
```

## Common Batch Patterns

### Pattern: Multiple structures from database
```python
targets = ["Fe2O3", "TiO2", "ZnO", "Al2O3", "SiO2"]
for formula in targets:
    # Query DB (budget 1-2 attempts)
    # If DB returns summary only (no CIF), build locally:
    #   Structure.from_spacegroup(sg, lattice, species, coords)
    # Save immediately
```

### Pattern: Multiple surfaces from one bulk
```python
millers = [(1,0,0), (1,1,0), (1,1,1)]
for hkl in millers:
    # Build slab using build_slab_tasker_fix.py or MCP
    # Save immediately
    # Validate with check_slab_tasker.py
```

### Pattern: Multiple format conversions
```python
for cif_file in cif_files:
    # convert_format.py --input <cif> --output <target_format>
    # Save immediately
    # Assess with assess_structure.py
```

### Pattern: Systematic variations (EOS, defect series)
```python
scale_factors = [0.96, 0.98, 1.00, 1.02, 1.04]
for sf in scale_factors:
    # Scale lattice vectors
    # Save as eos_vol_{sf:.2f}.cif
```

## Batch-Capable Scripts in This Skill

| Script | Batch support |
|--------|--------------|
| `assess_structure.py` | Run sequentially on each file |
| `convert_format.py` | Run sequentially on each file |
| `fetch_web_structure.py` | One URL at a time |
| `build_molecular_crystal_slab.py` | One CIF at a time |
| `passivate_surface.py` | One slab at a time |

For slab batch processing (build + check + adsorbate), use the **tasker-polar-surface** skill which has native `--batch` JSON support. See `matmaster/skills/playground-skills/tasker-polar-surface/reference/batch_modes.md`.

## Batch from mcp-mat-struct-db

When fetching multiple structures from the database:
- The download tarball may contain only `summary.json` without actual CIF files.
- When this happens: extract lattice params, space group, and Wyckoff positions from summary, then build locally with `pymatgen Structure.from_spacegroup(...)`.
- **Do NOT** issue additional DB queries trying to get CIF files — build locally instead.
- Save each structure immediately after construction.

## Error Handling Template

```
results = []
for i, target in enumerate(targets):
    try:
        structure = process(target)
        save(structure, f"output_{i:03d}.cif")
        results.append({"target": target, "status": "success", "file": f"output_{i:03d}.cif"})
    except Exception as e:
        results.append({"target": target, "status": "failed", "error": str(e)})
        continue  # NEVER stop the loop

# Report ALL results including failures
write_summary(results)
```

## Timeout Strategy

- If approaching the task timeout with structures still pending:
  1. Stop processing new structures immediately.
  2. Write whatever summary/report is possible with completed structures.
  3. Save the list of unprocessed targets for reference.
  4. A partial result (e.g., 7/10 structures) is much better than a timeout with nothing saved.
