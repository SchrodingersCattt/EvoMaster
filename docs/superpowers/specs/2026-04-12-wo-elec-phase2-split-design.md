# WO Elec Phase 2 Split Design

## Goal

Split the archived legacy bank
`evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_elec.yaml`
into business-line-clean units without forcing ambiguous questions back into the
active corpus.

This round follows a strict conservative rule:

- migrate only questions with a hard business-line fit
- keep ambiguous questions in archive
- do not keep any mixed-domain active bank

## Scope

In scope:

- question-level review of the five archived `wo_elec` questions
- creation of two new active banks under
  `evaluation/question_bank/workflow_orchestration/`
- trimming the archived `wo_elec.yaml` to only unresolved questions
- manifest and test updates required for the new active banks

Out of scope:

- reclassifying `WO_elec_002_20260411v2`
- reclassifying `WO_elec_008_20260411v1`
- redesigning `capability`
- changing the current five-domain business-line set

## Current Bank Assessment

The archived bank currently mixes several different end-use objectives under the
old `domain: elec` umbrella:

- `WO_elec_001_20260411v2`: Si band-structure workflow
- `WO_elec_002_20260411v2`: periodic liquid-water gap workflow
- `WO_elec_006_20260411v2`: Fe BCC(110) surface-energy workflow
- `WO_elec_007_20260411v1`: Pd(111) work-function workflow
- `WO_elec_008_20260411v1`: Al2O3 Bader-charge workflow

Under the new taxonomy, this bank cannot return to the active tree as a whole.

## Classification Decisions

### 1. Migrate to `workflow_orchestration/wo_semiconductor.yaml`

Question:

- `WO_elec_001_20260411v2`

Decision:

- active
- `capability: workflow_orchestration`
- `domain: semiconductor`

Reasoning:

- the workflow centers on Si band structure and band-gap classification
- the final business objective is semiconductor electronic-structure analysis
- the fit is stronger than any alternative business line

## 2. Migrate to `workflow_orchestration/wo_catalysis.yaml`

Questions:

- `WO_elec_006_20260411v2`
- `WO_elec_007_20260411v1`

Decision:

- active
- `capability: workflow_orchestration`
- `domain: catalysis`

Reasoning:

- both questions are surface-facing workflows with slab construction, vacuum
  handling, and surface-property reporting
- under the current five-domain set, surface-energy / work-function workflow fits
  best under the catalysis / surface-reaction business line
- grouping these two together creates a clean active bank without semantic stretch

## 3. Keep in Archive for Later Review

Questions:

- `WO_elec_002_20260411v2`
- `WO_elec_008_20260411v1`

Decision:

- remain in
  `evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_elec.yaml`

Reasoning:

- `WO_elec_002_20260411v2` is a periodic liquid-water gap workflow; its end-use
  business line is not hard enough under the current five-domain set
- `WO_elec_008_20260411v1` is an Al2O3 Bader-analysis workflow; the final business
  line is still ambiguous between generic oxide analysis and application-specific
  lines
- conservative policy says unresolved questions must stay archived rather than be
  force-migrated

## File-Level Design

### New active files

- `evaluation/question_bank/workflow_orchestration/wo_semiconductor.yaml`
  - single-question bank
  - contains `WO_elec_001_20260411v2`
  - top-level `capability: workflow_orchestration`
  - top-level `domain: semiconductor`

- `evaluation/question_bank/workflow_orchestration/wo_catalysis.yaml`
  - two-question bank
  - contains `WO_elec_006_20260411v2` and `WO_elec_007_20260411v1`
  - top-level `capability: workflow_orchestration`
  - top-level `domain: catalysis`

### Updated archive file

- `evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_elec.yaml`
  - remove the three migrated questions
  - keep only `WO_elec_002_20260411v2` and `WO_elec_008_20260411v1`
  - keep old archive semantics; this file stays outside active loading

### Registry and tests

- `evaluation/question_bank/manifest.yaml`
  - add entries for `wo_semiconductor.yaml` and `wo_catalysis.yaml`
  - update total active bank count and question count

- `tests/evaluation/test_question_bank_taxonomy.py`
  - extend direct-migrate / active-bank expectations to include the two new banks
  - keep asserting that active banks use only business-line domains

- `evaluation/AGENTS_evaluation.md`
  - add one short note that Phase 2 split banks may be created from archived
    legacy banks when a strict question-level review justifies it

## Data and Metadata Rules

- question ids stay unchanged because task semantics do not change
- `capability` stays `workflow_orchestration`
- migrated questions must update both top-level and per-question `domain`
- tags remain as-is unless they now duplicate the new domain; duplicated tags must
  be removed
- no compatibility alias such as `elec` may remain in the new active files

## Validation Requirements

Minimum validation for this split:

- `tests/evaluation/test_question_bank_taxonomy.py`
- `tests/evaluation/test_runtime_and_structure_checks.py`
- `tests/evaluation/test_slice_parser.py`
- loader smoke:
  `load_question_banks(Path('evaluation/question_bank'))`
- grep for legacy `domain:` values under `evaluation/question_bank/`

## Risks

- `WO_elec_007_20260411v1` could be read as generic surface science rather than
  catalysis; this is accepted because the current active domain set has no
  dedicated surface-science line
- future Phase 2 work may still decide that `WO_elec_002_20260411v2` or
  `WO_elec_008_20260411v1` need a new business line; this design intentionally
  avoids pre-committing to that outcome

## Recommendation

Implement this split as a narrow Phase 2 slice:

- create the two active banks
- trim the archive bank
- update manifest and taxonomy tests
- do not touch the two unresolved archived questions
