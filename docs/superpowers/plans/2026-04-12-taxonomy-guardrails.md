# Taxonomy Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automatic guardrails so evaluation taxonomy metadata cannot silently drift out of sync.

**Architecture:** Enforce top-level question-bank hints in `QuestionBank` validation, and add tests that check manifest metadata against the actual bank files. Keep the implementation narrow so existing active banks continue to load unchanged.

**Tech Stack:** Pydantic, pytest, YAML.

---

### Task 1: Add Failing Tests For Bank Hint Consistency

**Files:**
- Create: `tests/evaluation/test_question_bank_taxonomy.py`

- [ ] **Step 1: Write failing tests for mismatched top-level hints**
- [ ] **Step 2: Run the new test file and observe failures**

Run: `.venv/bin/pytest tests/evaluation/test_question_bank_taxonomy.py`
Expected: FAIL before `QuestionBank` validation is tightened.

### Task 2: Enforce Consistency In Schema Validation

**Files:**
- Modify: `evaluation/core/schemas.py`

- [ ] **Step 1: Add `QuestionBank` validation for top-level `capability`/`domain` hints**
- [ ] **Step 2: Keep mixed banks legal by requiring them to omit misleading top-level hints instead of forcing uniformity**

### Task 3: Verify Manifest And Active Banks

**Files:**
- Create: `tests/evaluation/test_question_bank_taxonomy.py`

- [ ] **Step 1: Add a manifest consistency test for question counts and optional capability hints**
- [ ] **Step 2: Run targeted verification**

Run:
- `.venv/bin/pytest tests/evaluation/test_question_bank_taxonomy.py`
- `.venv/bin/python -c "from pathlib import Path; from evaluation.core.runner import load_question_banks; load_question_banks(Path('evaluation/question_bank')); print('question banks loaded')"`

Expected: both PASS.
