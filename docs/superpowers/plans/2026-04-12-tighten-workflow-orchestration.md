# Tighten Workflow Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Narrow `workflow_orchestration` so it keeps only genuine multi-step orchestration tasks and migrates obvious analysis/report tasks to `scientific_analysis`.

**Architecture:** Keep the current capability set unchanged. Reclassify only clearly misfit question banks and questions, then update docs/help text and add regression tests so the narrowed boundary is enforced by schema acceptance and by question-bank loading.

**Tech Stack:** YAML question banks, Pydantic schemas, pytest, Markdown docs.

---

### Task 1: Add Regression Coverage For The Narrowed Boundary

**Files:**
- Modify: `tests/evaluation/test_runtime_and_structure_checks.py`

- [ ] **Step 1: Write the failing test**

```python
def test_question_item_accepts_scientific_analysis() -> None:
    ...


def test_question_item_rejects_removed_legacy_capabilities() -> None:
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/evaluation/test_runtime_and_structure_checks.py -k "new_capabilities or legacy_capabilities"`.
Expected: FAIL before schema/question-bank updates land.

- [ ] **Step 3: Keep the tests as the acceptance gate**

Use the new tests to prove `scientific_analysis` is accepted and old capability names are rejected after the migration.

- [ ] **Step 4: Re-run the same test after implementation**

Run: `.venv/bin/pytest tests/evaluation/test_runtime_and_structure_checks.py -k "removed_capability or removed_domain or new_capabilities or legacy_capabilities"`.
Expected: PASS.

### Task 2: Reclassify Obvious Non-Workflow Questions

**Files:**
- Modify: `evaluation/question_bank/polymer/pl_membrane.yaml`
- Modify: `evaluation/question_bank/polymer/pl_donor.yaml`
- Modify: `evaluation/question_bank/polymer/pl_adhesion.yaml`
- Modify: `evaluation/question_bank/polymer/pl_hopping.yaml`
- Modify: `evaluation/question_bank/polymer/pl_rheology.yaml`
- Modify: `evaluation/question_bank/workflow_orchestration/wo_general.yaml`
- Modify: `evaluation/question_bank/manifest.yaml`

- [ ] **Step 1: Move polymer comparison/report tasks to `scientific_analysis`**

Set the top-level and per-question `capability` in the five polymer banks to `scientific_analysis`.

- [ ] **Step 2: Move obviously analysis-style `wo_general` questions**

Change these question-level capabilities in `wo_general.yaml` to `scientific_analysis`:
- `WO_general_sse_006_20260411v1`
- `WO_general_perov_007_20260411v1`
- `WO_general_steel_008_20260411v1`
- `WO_general_ferro_009_20260411v1`
- `WO_general_hea_005_20260411v1`

Leave `WO_general_intake_001_20260410v3`, `WO_general_postproc_002_20260410v3`, `WO_general_planning_003_20260410v3`, and `WO_general_mechmix_004_20260411v1` as `workflow_orchestration`.

- [ ] **Step 3: Keep mixed-bank metadata honest**

Remove the top-level `capability` hint from `wo_general.yaml` and remove the manifest-level `capability` hint for that mixed bank.
Update manifest entries for the polymer banks to `scientific_analysis`.

### Task 3: Tighten Documentation And Verify Bank Loading

**Files:**
- Modify: `evaluation/AGENTS_evaluation.md`
- Modify: `evaluation/README_CN.md`

- [ ] **Step 1: Narrow the written definition**

Update docs so `workflow_orchestration` means tasks with explicit staged flow, tool/script chaining, or interdependent deliverables.
State that comparison/report/review tasks grounded in provided bundles or literature belong in `scientific_analysis` unless orchestration itself is being tested.

- [ ] **Step 2: Run end-to-end verification**

Run:
- `.venv/bin/pytest tests/evaluation/test_runtime_and_structure_checks.py -k "removed_capability or removed_domain or new_capabilities or legacy_capabilities"`
- `.venv/bin/python -c "from pathlib import Path; from evaluation.core.runner import load_question_banks; load_question_banks(Path('evaluation/question_bank')); print('question banks loaded')"`

Expected:
- pytest passes
- question banks load successfully
