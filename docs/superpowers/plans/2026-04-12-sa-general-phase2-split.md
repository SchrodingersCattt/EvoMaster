# SA General Phase 2 Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the archived legacy `sa_general.yaml` bank by moving the hard-fit questions back into active business-line banks while leaving the unresolved method-explanation question in archive.

**Architecture:** Drive the change from taxonomy tests first. Use the smallest active-footprint implementation: create one new semiconductor analysis bank for the perovskite task, append the HEA task to the existing alloy analysis bank, then trim the archive bank to only the unresolved ferroelectric method-explanation question.

**Tech Stack:** YAML question banks, pytest, Pydantic validation, git.

---

### File Structure

**Files:**
- Create: `evaluation/question_bank/scientific_analysis/sa_semiconductor.yaml`
- Modify: `evaluation/question_bank/scientific_analysis/sa_mech.yaml`
- Modify: `evaluation/question_bank/manifest.yaml`
- Modify: `evaluation/question_bank_archive/businessline_phase2/scientific_analysis/sa_general.yaml`
- Modify: `tests/evaluation/test_question_bank_taxonomy.py`

**Responsibilities:**
- `evaluation/question_bank/scientific_analysis/sa_semiconductor.yaml`
  - one active semiconductor analysis bank containing only `WO_general_perov_007_20260411v1`
- `evaluation/question_bank/scientific_analysis/sa_mech.yaml`
  - existing alloy analysis bank extended to include `WO_general_hea_005_20260411v1`
- `evaluation/question_bank_archive/businessline_phase2/scientific_analysis/sa_general.yaml`
  - reduced archive bank containing only `WO_general_ferro_009_20260411v1`
- `evaluation/question_bank/manifest.yaml`
  - active registry entries and refreshed counts for the touched analysis banks
- `tests/evaluation/test_question_bank_taxonomy.py`
  - asserts the new active semiconductor bank exists and the migrated question ids land in the expected active banks

### Task 1: Make Taxonomy Tests Expect The SA General Split

**Files:**
- Modify: `tests/evaluation/test_question_bank_taxonomy.py`

- [ ] **Step 1: Write the failing taxonomy expectations**

```python
# tests/evaluation/test_question_bank_taxonomy.py

DIRECT_MIGRATE_DOMAIN_EXPECTATIONS = {
    'batch_processing/bp_elec.yaml': 'catalysis',
    'co2rr_reproduction/co2rr_bp_struct.yaml': 'catalysis',
    'co2rr_reproduction/co2rr_sa_elec.yaml': 'catalysis',
    'co2rr_reproduction/co2rr_sa_general.yaml': 'catalysis',
    'co2rr_reproduction/co2rr_wo_mech.yaml': 'catalysis',
    'co2rr_reproduction/wo_co2rr_unit_ops.yaml': 'catalysis',
    'data_fitting/df_elec.yaml': 'semiconductor',
    'polymer/pl_adhesion.yaml': 'polymer',
    'polymer/pl_donor.yaml': 'polymer',
    'polymer/pl_hopping.yaml': 'polymer',
    'polymer/pl_membrane.yaml': 'polymer',
    'polymer/pl_rheology.yaml': 'polymer',
    'scientific_analysis/sa_elec.yaml': 'battery',
    'scientific_analysis/sa_mech.yaml': 'alloy',
    'scientific_analysis/sa_semiconductor.yaml': 'semiconductor',
    'structure_construction/sc_elec_adsorption.yaml': 'catalysis',
    'workflow_orchestration/wo_elec_adsorption.yaml': 'catalysis',
    'workflow_orchestration/wo_elec_nfpp_refactored.yaml': 'battery',
    'workflow_orchestration/wo_general_mech.yaml': 'alloy',
    'workflow_orchestration/wo_mech_struct.yaml': 'alloy',
    'workflow_orchestration/wo_mech_thermo.yaml': 'alloy',
}


def test_sa_general_phase2_split_banks_have_expected_question_ids() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / 'evaluation' / 'question_bank'

    semiconductor_bank = yaml.safe_load(
        (bank_root / 'scientific_analysis/sa_semiconductor.yaml').read_text(
            encoding='utf-8'
        )
    )
    alloy_bank = yaml.safe_load(
        (bank_root / 'scientific_analysis/sa_mech.yaml').read_text(
            encoding='utf-8'
        )
    )

    assert [q['id'] for q in semiconductor_bank['questions']] == [
        'WO_general_perov_007_20260411v1'
    ]
    assert [q['id'] for q in alloy_bank['questions']] == [
        'WO_general_steel_008_20260411v1',
        'WO_general_hea_005_20260411v1',
    ]


def test_manifest_active_totals_after_sa_general_phase2_split() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / 'evaluation' / 'question_bank'
    manifest = yaml.safe_load((bank_root / 'manifest.yaml').read_text(encoding='utf-8'))

    assert len(manifest['banks']) == 21
    assert sum(int(entry['questions']) for entry in manifest['banks']) == 25
```

- [ ] **Step 2: Run the taxonomy test file and verify it fails**

Run:

```bash
/Users/hui_zhou/Project/AtomSmith/matmaster-evo/.venv/bin/pytest tests/evaluation/test_question_bank_taxonomy.py -q
```

Expected: FAIL because `scientific_analysis/sa_semiconductor.yaml` does not exist yet and manifest totals are still `20` banks / `23` questions.

- [ ] **Step 3: Commit the red test state**

```bash
git add tests/evaluation/test_question_bank_taxonomy.py
git commit -m "test: require sa_general phase2 split"
```

### Task 2: Add The Two Hard-Fit Questions Back To Active Banks

**Files:**
- Create: `evaluation/question_bank/scientific_analysis/sa_semiconductor.yaml`
- Modify: `evaluation/question_bank/scientific_analysis/sa_mech.yaml`

- [ ] **Step 1: Create the new semiconductor analysis bank**

```yaml
version: v5
capability: scientific_analysis
domain: semiconductor
questions:
- id: WO_general_perov_007_20260411v1
  capability: scientific_analysis
  domain: semiconductor
  intent: Query high-efficiency perovskite additives and produce recommendation plus
    a proposal draft based on predicted performance.
  human_prompt_seed: |-
    请围绕“高效率（>26%）钙钛矿添加剂设计”完成一份推荐与提案。
    请输出： 1) `perovskite_additive_candidates.json`：至少包含 `smiles`, `predicted_effect`, `selection_reason`, `similar_formulation_hint`； 2) `perovskite_additive_proposal.md`：基于最佳候选，给出研究目标、验证方案、预期结果与风险。
    最终回答中简要说明：为什么该候选最值得优先验证。
  tags:
  - userlog_derived
  - perovskite
  - additive
  - proposal
  mode_scope:
  - direct
  - planner
  data_files: []
  reference_answers:
  - key: additive_json
    value: perovskite_additive_candidates.json
  - key: proposal_md
    value: perovskite_additive_proposal.md
  - key: additive_tokens
    value:
      filename: perovskite_additive_candidates.json
      tokens:
      - smiles
      - predicted_effect
      - selection_reason
      - similar_formulation_hint
      flags: i
  - key: proposal_tokens
    value:
      filename: perovskite_additive_proposal.md
      tokens:
      - 目标
      - 验证
      - 预期结果
      - 风险
      - 不确定性
      flags: i
  - key: turn_budget
    value:
      max: 10
  - key: duration_budget
    value:
      max: 7200000
  - key: token_budget_total
    value:
      max: 9000
  scoring_checklist:
  - id: additive_json
    criterion: Agent writes `perovskite_additive_candidates.json` to workspace.
    axis: correctness
    verify: artifact_exists
  - id: proposal_md
    criterion: Agent writes `perovskite_additive_proposal.md` to workspace.
    axis: correctness
    verify: artifact_exists
  - id: additive_tokens
    criterion: Candidate JSON includes required additive-recommendation fields.
    axis: correctness
    verify: text_file_contains_all
  - id: proposal_tokens
    criterion: Proposal document covers objectives, validation plan, and uncertainty/risk.
    axis: correctness
    verify: text_file_contains_all
  - id: grounding_source
    criterion: Deliverables are grounded in generated candidate analysis rather than
      unsupported additive claims.
    axis: grounding
    verify: llm_binary_judge
  - id: no_retries
    criterion: No repeated identical tool calls with the same parameter set.
    axis: efficiency
    verify: no_retries
  - id: turn_budget
    criterion: Agent completes the task within the turn (step) budget.
    axis: efficiency
    verify: turn_budget
  - id: duration_budget
    criterion: Wall-clock run duration (duration_ms) is recorded and does not exceed
      a generous benchmark ceiling (tunable per deployment).
    axis: efficiency
    verify: duration_budget
  - id: token_budget_total
    criterion: Total token usage stays within benchmark ceiling (last-turn total_tokens).
    axis: efficiency
    verify: token_budget
```

- [ ] **Step 2: Append the HEA question to the existing alloy analysis bank**

```yaml
# evaluation/question_bank/scientific_analysis/sa_mech.yaml

- id: WO_general_hea_005_20260411v1
  capability: scientific_analysis
  domain: alloy
  intent: Evaluate phase-formation tendency of AlCr0.8CoFeNi HEA using descriptor
    calculations, binary-phase cues, and solid-solution prediction.
  human_prompt_seed: |-
    请评估 AlCr0.8CoFeNi 高熵合金是否易形成固溶体，并给出可能结构趋势。
    要求覆盖： - VEC、混合焓、混合熵等关键描述符； - 二元形成能/相图信息的归纳； - 固溶体形成判断与可能晶体结构。
    请输出： 1) `hea_alcr0.8cofeni_kb_summary.json`（至少包含 `vec`, `mixing_enthalpy`, `mixing_entropy`, `binary_energy_summary`, `solid_solution_prediction`, `possible_structure`）； 2) `hea_alcr0.8cofeni_kb_report.md`（说明判据来源、结论与不确定性）。
  tags:
  - userlog_derived
  - hea
  - phase_prediction
  - knowledge_base
  - alloy_design
  mode_scope:
  - direct
  - planner
  data_files: []
  reference_answers:
  - key: hea_json
    value: hea_alcr0.8cofeni_kb_summary.json
  - key: hea_report
    value: hea_alcr0.8cofeni_kb_report.md
  - key: hea_required_tokens
    value:
      filename: hea_alcr0.8cofeni_kb_summary.json
      tokens:
      - vec
      - mixing_enthalpy
      - mixing_entropy
      - binary_energy_summary
      - solid_solution_prediction
      - possible_structure
      flags: i
  - key: hea_report_tokens
    value:
      filename: hea_alcr0.8cofeni_kb_report.md
      tokens:
      - AlCr0.8CoFeNi
      - VEC
      - 混合焓
      - 混合熵
      - 固溶体
      - 不确定性
      flags: i
  - key: turn_budget
    value:
      max: 10
  - key: duration_budget
    value:
      max: 7200000
  - key: token_budget_total
    value:
      max: 8000
  scoring_checklist:
  - id: hea_json
    criterion: Agent writes `hea_alcr0.8cofeni_kb_summary.json` to workspace.
    axis: correctness
    verify: artifact_exists
  - id: hea_report
    criterion: Agent writes `hea_alcr0.8cofeni_kb_report.md` to workspace.
    axis: correctness
    verify: artifact_exists
  - id: hea_required_tokens
    criterion: Summary JSON includes required HEA descriptor and prediction fields.
    axis: correctness
    verify: text_file_contains_all
  - id: hea_report_tokens
    criterion: Report explains rationale and uncertainty caveats for the solid-solution
      prediction.
    axis: correctness
    verify: text_file_contains_all
  - id: grounding_source
    criterion: Deliverables are grounded in generated HEA-analysis artifacts and avoid
      unsupported phase claims.
    axis: grounding
    verify: llm_binary_judge
  - id: no_retries
    criterion: No repeated identical tool calls with the same parameter set.
    axis: efficiency
    verify: no_retries
  - id: turn_budget
    criterion: Agent completes the task within the turn (step) budget.
    axis: efficiency
    verify: turn_budget
  - id: duration_budget
    criterion: Wall-clock run duration (duration_ms) is recorded and does not exceed
      a generous benchmark ceiling (tunable per deployment).
    axis: efficiency
    verify: duration_budget
  - id: token_budget_total
    criterion: Total token usage stays within benchmark ceiling (last-turn total_tokens).
    axis: efficiency
    verify: token_budget
```

- [ ] **Step 3: Run the taxonomy suite and confirm only the archive/manifest work remains**

Run:

```bash
/Users/hui_zhou/Project/AtomSmith/matmaster-evo/.venv/bin/pytest tests/evaluation/test_question_bank_taxonomy.py -q
```

Expected: remaining failures should only come from manifest totals or archive-trim expectations if those tests were already added.

- [ ] **Step 4: Commit the active-bank changes**

```bash
git add evaluation/question_bank/scientific_analysis/sa_semiconductor.yaml evaluation/question_bank/scientific_analysis/sa_mech.yaml
git commit -m "refactor: split sa_general active banks"
```

### Task 3: Trim The Archive Bank To The One Unresolved Question

**Files:**
- Modify: `evaluation/question_bank_archive/businessline_phase2/scientific_analysis/sa_general.yaml`

- [ ] **Step 1: Remove the perovskite and HEA questions from the archive bank**

Expected remaining archive bank shape:

```yaml
version: v5
capability: scientific_analysis
domain: general
questions:
- id: WO_general_ferro_009_20260411v1
  capability: scientific_analysis
  domain: general
  intent: Explain principle, applications, and limitations of the double-wave method
    in Sawyer-Tower ferroelectric measurements.
  human_prompt_seed: |-
    请围绕 Sawyer-Tower 铁电测试中的 double-wave method，给出结构化说明。
    请输出： 1) `double_wave_sawyer_tower_notes.md`：包含原理、典型应用、局限性与误差来源； 2) `double_wave_keypoints.json`：至少包含 `principle`, `applications`, `limitations`, `best_practices`。
    最终回答请用简短段落总结：该方法最适合与最不适合的使用场景。
  tags:
  - userlog_derived
  - ferroelectric
  - sawyer_tower
  - method_explanation
  mode_scope:
  - direct
  - planner
  data_files: []
  reference_answers:
  - key: notes_md
    value: double_wave_sawyer_tower_notes.md
  - key: keypoints_json
    value: double_wave_keypoints.json
  - key: notes_tokens
    value:
      filename: double_wave_sawyer_tower_notes.md
      tokens:
      - 原理
      - 应用
      - 局限性
      - 误差
      - Sawyer-Tower
      flags: i
  - key: json_tokens
    value:
      filename: double_wave_keypoints.json
      tokens:
      - principle
      - applications
      - limitations
      - best_practices
      flags: i
  - key: turn_budget
    value:
      max: 8
  - key: duration_budget
    value:
      max: 7200000
  - key: token_budget_total
    value:
      max: 7000
  scoring_checklist:
  - id: notes_md
    criterion: Agent writes `double_wave_sawyer_tower_notes.md` to workspace.
    axis: correctness
    verify: artifact_exists
  - id: keypoints_json
    criterion: Agent writes `double_wave_keypoints.json` to workspace.
    axis: correctness
    verify: artifact_exists
  - id: notes_tokens
    criterion: Notes cover principle, applications, limitations, and error sources.
    axis: correctness
    verify: text_file_contains_all
  - id: json_tokens
    criterion: Keypoint JSON includes all required structured fields.
    axis: correctness
    verify: text_file_contains_all
  - id: grounding_source
    criterion: Deliverables are grounded in methodological references and avoid unsupported
      claims.
    axis: grounding
    verify: llm_binary_judge
  - id: no_retries
    criterion: No repeated identical tool calls with the same parameter set.
    axis: efficiency
    verify: no_retries
  - id: turn_budget
    criterion: Agent completes the task within the turn (step) budget.
    axis: efficiency
    verify: turn_budget
  - id: duration_budget
    criterion: Wall-clock run duration (duration_ms) is recorded and does not exceed
      a generous benchmark ceiling (tunable per deployment).
    axis: efficiency
    verify: duration_budget
  - id: token_budget_total
    criterion: Total token usage stays within benchmark ceiling (last-turn total_tokens).
    axis: efficiency
    verify: token_budget
```

- [ ] **Step 2: Verify the archive file now contains only the unresolved id**

Run:

```bash
/Users/hui_zhou/.vscode/extensions/openai.chatgpt-26.409.20454-darwin-arm64/bin/macos-aarch64/rg -n '^- id:' evaluation/question_bank_archive/businessline_phase2/scientific_analysis/sa_general.yaml
```

Expected: one match, `WO_general_ferro_009_20260411v1`.

- [ ] **Step 3: Commit the archive trim**

```bash
git add evaluation/question_bank_archive/businessline_phase2/scientific_analysis/sa_general.yaml
git commit -m "refactor: trim archived sa_general bank"
```

### Task 4: Register The Split And Refresh Counts

**Files:**
- Modify: `evaluation/question_bank/manifest.yaml`
- Modify: `tests/evaluation/test_question_bank_taxonomy.py`

- [ ] **Step 1: Add the new semiconductor analysis bank to the manifest**

```yaml
# evaluation/question_bank/manifest.yaml

  - path: "scientific_analysis/sa_semiconductor.yaml"
    capability: "scientific_analysis"
    domain: "semiconductor"
    questions: 1
```

- [ ] **Step 2: Update the existing alloy analysis bank count**

```yaml
# evaluation/question_bank/manifest.yaml

  - path: "scientific_analysis/sa_mech.yaml"
    capability: "scientific_analysis"
    domain: "alloy"
    questions: 2
```

- [ ] **Step 3: Refresh the manifest totals comment**

```yaml
# Total: 25 questions across 21 active bank files
```

- [ ] **Step 4: Run the taxonomy suite and confirm it is fully green**

Run:

```bash
/Users/hui_zhou/Project/AtomSmith/matmaster-evo/.venv/bin/pytest tests/evaluation/test_question_bank_taxonomy.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the registry and test updates**

```bash
git add evaluation/question_bank/manifest.yaml tests/evaluation/test_question_bank_taxonomy.py
git commit -m "test: register sa_general phase2 split"
```

### Task 5: Final Verification

**Files:**
- Modify: `evaluation/question_bank/manifest.yaml`
- Modify: `evaluation/question_bank/scientific_analysis/sa_mech.yaml`
- Modify: `evaluation/question_bank/scientific_analysis/sa_semiconductor.yaml`
- Modify: `evaluation/question_bank_archive/businessline_phase2/scientific_analysis/sa_general.yaml`

- [ ] **Step 1: Run the focused regression suite**

Run:

```bash
/Users/hui_zhou/Project/AtomSmith/matmaster-evo/.venv/bin/pytest tests/evaluation/test_question_bank_taxonomy.py tests/evaluation/test_runtime_and_structure_checks.py tests/evaluation/test_slice_parser.py -q
```

Expected: PASS.

- [ ] **Step 2: Run the loader smoke test on the active tree**

Run:

```bash
/Users/hui_zhou/Project/AtomSmith/matmaster-evo/.venv/bin/python -c "from pathlib import Path; from evaluation.core.runner import load_question_banks; banks = load_question_banks(Path('evaluation/question_bank')); print(len(banks), sum(len(bank.questions) for bank in banks))"
```

Expected: `21 25`

- [ ] **Step 3: Grep the active tree for forbidden old `domain:` values**

Run:

```bash
/Users/hui_zhou/.vscode/extensions/openai.chatgpt-26.409.20454-darwin-arm64/bin/macos-aarch64/rg -n '^domain:\s*"?((struct|elec|mech|thermo|kinetic|general|incar|scxrd|mlip))"?$' evaluation/question_bank
```

Expected: no matches.

- [ ] **Step 4: Commit any final count or formatting adjustments**

```bash
git add evaluation/question_bank evaluation/question_bank_archive tests/evaluation/test_question_bank_taxonomy.py
git commit -m "test: verify sa_general phase2 split"
```
