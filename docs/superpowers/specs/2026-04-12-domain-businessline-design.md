# Domain Business-Line Redesign

## Goal

Redefine `domain` as a strict business-line axis for evaluation question banks.

After this redesign:

- `capability` answers "what task is being performed"
- `domain` answers "which business line / application scenario this task belongs to"
- `tags` carry material objects, methods, software, and other secondary facets

This is a hard cutover:

- no compatibility layer
- no mixed old/new `domain` values
- no `general` fallback

## Non-Goals

- Do not redesign `capability` in this round
- Do not preserve old physics-axis `domain` values such as `elec`, `mech`, `thermo`, `kinetic`, `struct`
- Do not promote method/tool concepts such as `scxrd`, `incar`, `mlip` into the new `domain`
- Do not force every existing question into a business line if the fit is weak

## New Domain Semantics

`domain` is no longer a scientific-subfield label.

`domain` is a business-line slice used for:

- bank grouping
- CLI/config filtering
- coverage reporting
- ownership and roadmap planning

Only business-line terms are valid.

Material-object terms and method/tool terms must move to `tags`.

## First-Version Domain Set

The first version is intentionally small:

- `battery`
- `catalysis`
- `polymer`
- `alloy`
- `semiconductor`

These five are the only in-scope target domains for the first migration wave.

## Explicitly Invalid As New Domains

The following categories must not remain in `domain`:

- Physics axes: `elec`, `mech`, `thermo`, `kinetic`
- Generic structural axes: `struct`, `general`
- Method/tool axes: `scxrd`, `incar`, `mlip`
- Object axes: `surface`, `interface`, `molecule`, `crystal`

These concepts should be expressed through `tags` where needed.

## Migration Strategy

Migration happens in three buckets.

### 1. `direct_migrate`

Whole bank can move to one target business-line domain with high confidence.

### 2. `needs_split_or_review`

Bank contains mixed business lines, ambiguous scope, or mostly generic tasks.
These banks require question-level review and may need splitting.

### 3. `out_of_scope`

Bank is not a good fit for the business-line domain system in this wave.
These banks are not force-migrated.

## Bank Classification Rules

Classification must follow this order:

1. Judge the final business objective of the task, not the current old `domain`
2. Use stable business-line evidence from intent, prompt, and tags
3. If most questions in a bank clearly belong to one business line, mark `direct_migrate`
4. If the bank mixes several business lines or is dominated by generic infrastructure tasks, mark `needs_split_or_review`
5. If the bank is fundamentally protocol-, safety-, or method-driven rather than business-line-driven, mark `out_of_scope`

Negative rule:

- Presence of `surface`, `slab`, `wyckoff`, `incar`, `scxrd`, or `mlip` must not determine `domain` by itself

## First-Pass Bank Mapping

### `direct_migrate`

- `evaluation/question_bank/polymer/pl_donor.yaml` -> `polymer`
- `evaluation/question_bank/polymer/pl_membrane.yaml` -> `polymer`
- `evaluation/question_bank/polymer/pl_rheology.yaml` -> `polymer`
- `evaluation/question_bank/polymer/pl_adhesion.yaml` -> `polymer`
- `evaluation/question_bank/polymer/pl_hopping.yaml` -> `polymer`
- `evaluation/question_bank/co2rr_reproduction/wo_co2rr_unit_ops.yaml` -> `catalysis`
- `evaluation/question_bank/co2rr_reproduction/co2rr_wo_mech.yaml` -> `catalysis`
- `evaluation/question_bank/co2rr_reproduction/co2rr_sa_elec.yaml` -> `catalysis`
- `evaluation/question_bank/co2rr_reproduction/co2rr_bp_struct.yaml` -> `catalysis`
- `evaluation/question_bank/co2rr_reproduction/co2rr_sa_general.yaml` -> `catalysis`
- `evaluation/question_bank/structure_construction/sc_elec_adsorption.yaml` -> `catalysis`
- `evaluation/question_bank/workflow_orchestration/wo_elec_adsorption.yaml` -> `catalysis`
- `evaluation/question_bank/batch_processing/bp_elec.yaml` -> `catalysis`
- `evaluation/question_bank/workflow_orchestration/wo_elec_nfpp_refactored.yaml` -> `battery`
- `evaluation/question_bank/scientific_analysis/sa_elec.yaml` -> `battery`
- `evaluation/question_bank/scientific_analysis/sa_mech.yaml` -> `alloy`
- `evaluation/question_bank/workflow_orchestration/wo_mech_struct.yaml` -> `alloy`
- `evaluation/question_bank/workflow_orchestration/wo_mech_thermo.yaml` -> `alloy`
- `evaluation/question_bank/workflow_orchestration/wo_general_mech.yaml` -> `alloy`
- `evaluation/question_bank/data_fitting/df_elec.yaml` -> `semiconductor`

### `needs_split_or_review`

- `evaluation/question_bank/workflow_orchestration/wo_elec.yaml`
- `evaluation/question_bank/workflow_orchestration/wo_mech.yaml`
- `evaluation/question_bank/scientific_analysis/sa_general.yaml`
- `evaluation/question_bank/structure_construction/sc_struct.yaml`
- `evaluation/question_bank/structure_retrieval/sr_struct_db.yaml`
- `evaluation/question_bank/data_diagnosis/dd_general.yaml`
- `evaluation/question_bank/input_generation/ig_abacus.yaml`
- `evaluation/question_bank/input_generation/ig_abacus_mech.yaml`
- `evaluation/question_bank/input_generation/ig_abacus_thermo.yaml`
- `evaluation/question_bank/workflow_orchestration/wo_mlip_dpa.yaml`
- `evaluation/question_bank/workflow_orchestration/wo_mlip_dpa_mech.yaml`
- `evaluation/question_bank/workflow_orchestration/wo_mlip_dpa_thermo.yaml`
- `evaluation/question_bank/workflow_orchestration/wo_mlip_dpa_kinetic.yaml`
- `evaluation/question_bank/workflow_orchestration/wo_elec_thermo.yaml`
- `evaluation/question_bank/workflow_orchestration/wo_kinetic.yaml`
- `evaluation/question_bank/batch_processing/bp_struct.yaml`

### `out_of_scope`

- `evaluation/question_bank/execution_contract/direct_contract.yaml`
- `evaluation/question_bank/safety_refusal/sr_general.yaml`
- `evaluation/question_bank/data_fitting/df_scxrd.yaml`
- `evaluation/question_bank/input_generation/ig_incar.yaml`
- `evaluation/question_bank/workflow_orchestration/wo_mlip_dpa_mlip.yaml`
- `evaluation/question_bank/data_fitting/df_thermo.yaml`

## Implementation Order

### Phase 1

Migrate only `direct_migrate` banks.

Changes included:

- update `schemas.py` domain literals
- update question-bank top-level `domain`
- update per-question `domain`
- update `manifest.yaml`
- update evaluation documentation

### Phase 2

Process `needs_split_or_review` banks one by one.

For each bank, choose exactly one:

- migrate whole bank
- split bank by business line
- remove from this migration wave

### Phase 3

Finalize and clean up:

- remove all old domain literals from schema/docs/tests
- grep for old-domain residue in `evaluation/question_bank`
- rerun verification

## Constraints

- No bank may contain mixed old/new domains
- No compatibility aliases for old domain names
- No reintroduction of `general`
- No method/object terms in `domain`

## Verification

Minimum verification after each migration batch:

- `tests/evaluation/test_question_bank_taxonomy.py`
- full `load_question_banks(...)`
- targeted checks for manifest consistency
- grep to ensure old domain values are gone from migrated files

## Risks

- Some currently generic banks may need substantial splitting
- Some questions may reveal missing business lines in the five-domain set
- Forcing migration too early would damage question semantics

This is why `needs_split_or_review` and `out_of_scope` are first-class buckets.

## Recommendation

Start implementation with the `direct_migrate` set only.

Do not touch `needs_split_or_review` in the first code pass.
