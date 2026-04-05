# Composition to Structure Heuristics

Use this heuristic only when the user provides composition/formula without explicit structures.

## 1) Classify composition domain

- **Mostly metallic systems** (alloy-like): prioritize fcc/bcc/hcp prototype families.
- **Mixed ionic/covalent systems**: prioritize common AB, AB2, ABO3-like prototype families if chemically plausible.
- **Unknown family**: retrieve nearest known compounds first, then expand to generic prototypes.

## 2) Retrieve before generating

1. Query structure databases (`mat_struct_db_*`) for same or near composition.
2. If multiple hits exist, keep a small diverse set (different prototypes and volume ranges).
3. If no adequate hit, generate prototype structures via `mat_sg_*`.

## 3) Build composition-consistent approximants

- Convert target atomic fractions to finite cell stoichiometry with small denominator approximants.
- Prefer smaller cells first (screening mode), then larger ordered approximants if needed.
- For off-stoichiometric targets, keep composition error explicitly recorded.

## 4) Initialize lattice and coordinates

- Start from retrieved prototype lattice when available.
- For generated prototypes, initialize lattice from prototype defaults and composition trends.
- Always ensure output has explicit:
  - lattice (3x3 or equivalent cell parameters)
  - coordinates (fractional or Cartesian with clear convention)
  - atom types / species mapping

## 5) Sanity checks (mandatory)

Run structural checks before downstream DPA steps:

- no overlapping atoms / unphysical short distances
- chemically plausible composition and formula
- expected dimensionality for the task (bulk vs slab vs molecule)

Use `structure-manager` `assess_structure.py` for validation and record warnings.

## 6) Escalation policy

Escalate to user confirmation when:

- multiple prototype families are equally plausible
- composition error after approximant conversion is non-negligible
- validation repeatedly fails for generated candidates

In these cases, provide 2-3 candidate routes with trade-offs instead of guessing a single "best" structure.
