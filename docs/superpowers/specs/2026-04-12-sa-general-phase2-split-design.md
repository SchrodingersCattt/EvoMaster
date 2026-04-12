# SA General Phase 2 Split Design

## Goal

Split the archived legacy bank
`evaluation/question_bank_archive/businessline_phase2/scientific_analysis/sa_general.yaml`
with the same conservative rule already used for `wo_elec`:

- migrate only questions with a hard single business-line fit
- keep ambiguous questions in archive
- do not create any mixed-domain active bank

## Scope

In scope:

- question-level review of the three archived `sa_general` questions
- creation of new active business-line-clean banks only where the fit is hard
- trimming the archived `sa_general.yaml` to only unresolved questions
- manifest, taxonomy-test, and guidance updates required for the split

Out of scope:

- adding a new `perovskite` domain
- redesigning `capability`
- changing the current five-domain business-line set
- forcing method-explanation questions back into the active tree without a hard
  business-line fit

## Current Bank Assessment

The archived bank currently mixes three different subject types under the old
`domain: general` umbrella:

- `WO_general_perov_007_20260411v1`: perovskite additive recommendation and
  proposal generation
- `WO_general_ferro_009_20260411v1`: Sawyer-Tower double-wave method explanation
- `WO_general_hea_005_20260411v1`: HEA solid-solution tendency assessment

Under the current taxonomy, this bank cannot return to the active tree as a
whole.

## Classification Decisions

### 1. Migrate to `scientific_analysis/sa_semiconductor.yaml`

Question:

- `WO_general_perov_007_20260411v1`

Decision:

- active
- `capability: scientific_analysis`
- `domain: semiconductor`

Reasoning:

- the topic is perovskite additive design for high-efficiency devices
- `perovskite` here behaves more like a material-system/topic tag than a stable
  business-line domain
- under the current five-domain set, semiconductor is the closest business-line
  fit without reopening domain semantics

### 2. Migrate to `scientific_analysis/sa_alloy.yaml`

Question:

- `WO_general_hea_005_20260411v1`

Decision:

- active
- `capability: scientific_analysis`
- `domain: alloy`

Reasoning:

- the task directly evaluates an HEA solid-solution tendency
- the final business objective is alloy design / alloy phase prediction
- this is a hard fit under the current business-line set

### 3. Keep in Archive for Later Review

Question:

- `WO_general_ferro_009_20260411v1`

Decision:

- remain in
  `evaluation/question_bank_archive/businessline_phase2/scientific_analysis/sa_general.yaml`

Reasoning:

- this task is primarily a method explanation about Sawyer-Tower
  double-wave measurement
- the business-line target is not hard enough under the current five-domain set
- conservative policy says unresolved questions must remain archived rather than
  be force-migrated

## File-Level Design

### New active files

- `evaluation/question_bank/scientific_analysis/sa_semiconductor.yaml`
  - append or create the semiconductor analysis bank
  - add `WO_general_perov_007_20260411v1`
  - keep top-level `capability: scientific_analysis`
  - keep top-level `domain: semiconductor`

- `evaluation/question_bank/scientific_analysis/sa_alloy.yaml`
  - append or create the alloy analysis bank
  - add `WO_general_hea_005_20260411v1`
  - keep top-level `capability: scientific_analysis`
  - keep top-level `domain: alloy`

### Updated archive file

- `evaluation/question_bank_archive/businessline_phase2/scientific_analysis/sa_general.yaml`
  - remove the two migrated questions
  - keep only `WO_general_ferro_009_20260411v1`
  - keep old archive semantics; this file stays outside active loading

### Registry and tests

- `evaluation/question_bank/manifest.yaml`
  - update question counts for any touched active banks
  - refresh total active bank and question counts

- `tests/evaluation/test_question_bank_taxonomy.py`
  - add expectations for the migrated question ids under their target banks
  - update total active-bank / active-question assertions

- `evaluation/AGENTS_evaluation.md`
  - no new semantic rule is needed beyond the archive-to-active strict-split note
  - only touch this file if implementation reveals a rule gap

## Data and Metadata Rules

- question ids stay unchanged because task semantics do not change
- `capability` stays `scientific_analysis`
- migrated questions must update both top-level and per-question `domain`
- keep `perovskite` as a tag, not a domain
- do not create a new domain for a single material family in this round
- no legacy `general` domain may appear in touched active files

## Validation Requirements

Minimum validation for this split:

- `tests/evaluation/test_question_bank_taxonomy.py`
- `tests/evaluation/test_runtime_and_structure_checks.py`
- `tests/evaluation/test_slice_parser.py`
- loader smoke:
  `load_question_banks(Path('evaluation/question_bank'))`
- grep for legacy `domain:` values under `evaluation/question_bank/`

## Risks

- the perovskite recommendation task could be argued as a future standalone
  vertical, but opening a new domain now would break the business-line discipline
- depending on current active-bank layout, the migrated questions may be appended
  to existing business-line banks rather than receive brand-new files; the
  implementation should choose the smaller semantic footprint

## Recommendation

Implement this split as a narrow Phase 2 slice:

- migrate `WO_general_perov_007_20260411v1` to `semiconductor`
- migrate `WO_general_hea_005_20260411v1` to `alloy`
- keep `WO_general_ferro_009_20260411v1` in archive
- update counts and taxonomy checks only as far as needed
