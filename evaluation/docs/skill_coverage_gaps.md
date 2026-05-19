# Structure Generation Skill Coverage Gaps

This note tracks second-wave coverage gaps found while tightening existing SC
questions. It intentionally does not add new questions in the current pass.

## Weakly Covered Skills

- `sample-atomic-structures`: add a focused CALYPSO/CrystalFormer task that submits a generation job, polls it, and writes exactly N structures with checked composition/space group.
- `assemble-atomic-structure`: add a PACKMOL/amorphous-cell task with density, atom count, minimum-distance, and periodic-boundary checks.
- `inspect-atomic-structure`: add a short routing task where the agent must assess a damaged or ambiguous structure file before choosing build vs transform.
- `mat_sg_make_defect_structure`: add a dedicated point-defect/substitution task that verifies supercell size and defect identity instead of accepting hand-written edits as equivalent coverage.
- `mat_sg_make_amorphous_structure`: add an amorphous builder contract task with deterministic stoichiometry and parseability checks.
- `mat_sg_build_surface_interface`: add an interface builder task with explicit lattice matching/twist/strain outputs separate from generic slab questions.
- `poly-generator`: add a polymer-chain generation task that checks repeat-unit count, terminal atoms, and output parseability.

## Suggested Follow-Up

Create a separate question-bank change for these gaps after the first-wave SC
rubric fixes are reviewed. New questions should reuse deterministic verifiers
where possible and only add new geometry verifiers once the scientific definition
is unambiguous.
