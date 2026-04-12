# WO Elec Phase 2 Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the archived legacy `wo_elec.yaml` bank into two active business-line-clean banks (`wo_semiconductor.yaml`, `wo_catalysis.yaml`) while leaving the unresolved questions in archive.

**Architecture:** Drive the change from tests first: update taxonomy expectations to require the two new active banks, then create the new active YAML files and trim the archived source bank. Keep the implementation narrow by preserving question ids, preserving `capability=workflow_orchestration`, and touching only manifest/docs needed to register the split.

**Tech Stack:** YAML question banks, pytest, Pydantic validation, git.

---

### File Structure

**Files:**
- Create: `evaluation/question_bank/workflow_orchestration/wo_semiconductor.yaml`
- Create: `evaluation/question_bank/workflow_orchestration/wo_catalysis.yaml`
- Modify: `evaluation/question_bank/manifest.yaml`
- Modify: `evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_elec.yaml`
- Modify: `tests/evaluation/test_question_bank_taxonomy.py`
- Modify: `evaluation/AGENTS_evaluation.md`

**Responsibilities:**
- `evaluation/question_bank/workflow_orchestration/wo_semiconductor.yaml`
  - one active semiconductor workflow bank containing only `WO_elec_001_20260411v2`
- `evaluation/question_bank/workflow_orchestration/wo_catalysis.yaml`
  - one active catalysis workflow bank containing only `WO_elec_006_20260411v2` and `WO_elec_007_20260411v1`
- `evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_elec.yaml`
  - reduced archive bank containing only unresolved `WO_elec_002_20260411v2` and `WO_elec_008_20260411v1`
- `evaluation/question_bank/manifest.yaml`
  - active registry entries and total counts for the two new banks
- `tests/evaluation/test_question_bank_taxonomy.py`
  - asserts the two new banks exist in the active corpus with the correct business-line domains
- `evaluation/AGENTS_evaluation.md`
  - records that Phase 2 question-level splits from archive into active banks are allowed when the review is strict

### Task 1: Make Taxonomy Tests Expect The Split

**Files:**
- Modify: `tests/evaluation/test_question_bank_taxonomy.py`

- [ ] **Step 1: Write the failing test updates for the two new active banks**

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
    'structure_construction/sc_elec_adsorption.yaml': 'catalysis',
    'workflow_orchestration/wo_catalysis.yaml': 'catalysis',
    'workflow_orchestration/wo_elec_adsorption.yaml': 'catalysis',
    'workflow_orchestration/wo_elec_nfpp_refactored.yaml': 'battery',
    'workflow_orchestration/wo_general_mech.yaml': 'alloy',
    'workflow_orchestration/wo_mech_struct.yaml': 'alloy',
    'workflow_orchestration/wo_mech_thermo.yaml': 'alloy',
    'workflow_orchestration/wo_semiconductor.yaml': 'semiconductor',
}


def test_direct_migrate_banks_match_phase1_domain_mapping() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / 'evaluation' / 'question_bank'

    for rel_path, expected_domain in DIRECT_MIGRATE_DOMAIN_EXPECTATIONS.items():
        raw_bank = yaml.safe_load((bank_root / rel_path).read_text(encoding='utf-8'))
        assert raw_bank['domain'] == expected_domain, rel_path
        assert {q['domain'] for q in raw_bank['questions']} == {expected_domain}, (
            rel_path
        )
```

```python
# tests/evaluation/test_question_bank_taxonomy.py

def test_phase2_split_banks_have_expected_question_ids() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / 'evaluation' / 'question_bank'

    semiconductor_bank = yaml.safe_load(
        (bank_root / 'workflow_orchestration/wo_semiconductor.yaml').read_text(
            encoding='utf-8'
        )
    )
    catalysis_bank = yaml.safe_load(
        (bank_root / 'workflow_orchestration/wo_catalysis.yaml').read_text(
            encoding='utf-8'
        )
    )

    assert [q['id'] for q in semiconductor_bank['questions']] == [
        'WO_elec_001_20260411v2'
    ]
    assert [q['id'] for q in catalysis_bank['questions']] == [
        'WO_elec_006_20260411v2',
        'WO_elec_007_20260411v1',
    ]
```

- [ ] **Step 2: Run the taxonomy test file and verify it fails because the new banks do not exist yet**

Run:

```bash
/Users/hui_zhou/Project/AtomSmith/matmaster-evo/.venv/bin/pytest tests/evaluation/test_question_bank_taxonomy.py -q
```

Expected: FAIL with missing-file failures for `workflow_orchestration/wo_semiconductor.yaml` and `workflow_orchestration/wo_catalysis.yaml`, plus manifest expectation failures until the new entries exist.

- [ ] **Step 3: Commit the red test state**

```bash
git add tests/evaluation/test_question_bank_taxonomy.py
git commit -m "test: require wo_elec phase2 split banks"
```

### Task 2: Create The Two New Active Banks

**Files:**
- Create: `evaluation/question_bank/workflow_orchestration/wo_semiconductor.yaml`
- Create: `evaluation/question_bank/workflow_orchestration/wo_catalysis.yaml`

- [ ] **Step 1: Create the semiconductor active bank with only the Si band-structure workflow**

```yaml
version: v5
capability: workflow_orchestration
domain: semiconductor
questions:
- id: WO_elec_001_20260411v2
  capability: workflow_orchestration
  domain: semiconductor
  intent: 'Run band-structure workflow for Si: locate structure, compute bands along
    high-symmetry path, report gap.'
  human_prompt_seed: '计算 Si 的能带结构：从数据库获取金刚石结构 Si，先做 SCF 计算， 然后沿高对称 k 路径 (G-X-L-G)
    计算能带，判断带隙类型（直接/间接）并输出带隙值 (eV)。 注意 DFT 带隙与实验值的偏差，给出不确定性说明。

'
  tags:
  - band_structure
  - si
  - kpath
  priority: P0
  mode_scope:
  - direct
  - planner
  data_files: []
  reference_answers:
  - key: gap_type
    value: indirect
  - key: gap_value_eV
    value: 0.65
    tolerance: 0.25
  - key: required_fields
    value:
    - kpath
    - gap_type
    - gap_value
    - uncertainty_note
  - key: turn_budget
    value:
      max: 18
  - key: duration_budget
    value:
      max: 7200000
  - key: token_budget_total
    value:
      max: 8000
  scoring_checklist:
  - id: gap_type
    criterion: Band-gap type is correctly identified as indirect (not direct).
    axis: correctness
    verify: llm_binary_judge
  - id: gap_value_eV
    criterion: Gap value is in expected DFT range.
    axis: correctness
    verify: numerical_range
  - id: required_fields
    criterion: Report includes high-symmetry k-path, gap type classification, gap
      value, and DFT uncertainty note.
    axis: correctness
    verify: llm_binary_judge
  - id: grounding_source
    criterion: Stated band gap values are tied to band-structure or calculation outcomes
      described in the final answer, not only generic training recall. Judge from
      the answer and named outputs; do not fail because the tool-call log omits MCP,
      web_search, or any specific tool.
    axis: grounding
    verify: llm_binary_judge
  - id: no_retries
    criterion: Band structure calculation is not repeated unnecessarily for the same
      k-path.
    axis: efficiency
    verify: no_retries
  - id: turn_budget
    criterion: Agent completes the task within the turn (step) budget.
    axis: efficiency
    verify: turn_budget
  - id: efficiency_judge
    criterion: Si band structure workflow (fetch → SCF → bands → report) completes
      without redundant intermediate steps.
    axis: efficiency
    verify: llm_binary_judge
  - id: duration_budget
    criterion: Wall-clock run duration (duration_ms) is recorded and does not exceed
      a generous benchmark ceiling (tunable per deployment).
    axis: efficiency
    verify: duration_budget
  - id: token_budget_total
    criterion: Total token usage is recorded and stays within a generous benchmark
      ceiling (tunable per task after calibration).
    axis: efficiency
    verify: token_budget
```

- [ ] **Step 2: Create the catalysis active bank with the two surface-property workflows**

```yaml
version: v5
capability: workflow_orchestration
domain: catalysis
questions:
- id: WO_elec_006_20260411v2
  capability: workflow_orchestration
  domain: catalysis
  intent: Mock surface energy for Fe BCC(110) with BSSE-aware workflow; compare sigma
    to VASP reference using provided energies and area.
  human_prompt_seed: '计算 Fe BCC(110) 表面能（ABACUS LCAO 场景，含 BSSE 校正思路）：从结构数据库或模板获取 Fe
    BCC 体相（a≈2.87 Å），切出 (110) slab（建议至少 6 层原子、约 15 Å 真空）。说明在 LCAO 基组下需要在两侧真空添加 empty
    Fe 层以降低 BSSE。使用以下 mock 单点能量（单位 eV，已对应 BSSE 校正后的 slab SCF）：E_bulk = -230929.72（体相参考），E_slab
    = -230919.46，单侧截面积 A = 34.09 Å²。按 σ = (E_slab − E_bulk) / (2A) 计算表面能，换算为 J/m²（1
    eV/Å² = 16.021766 J/m²），并与 VASP 参考值约 2.412 J/m² 对比。全程不要提交真实 DFT 计算。

'
  tags:
  - abacus
  - bsse
  - surface_energy
  - mock_energy
  mode_scope:
  - direct
  - planner
  data_files: []
  reference_answers:
  - key: sigma_J_m2
    value: 2.411
    tolerance: 0.12
  - key: required_fields
    value:
    - sigma_J_m2
    - formula
    - bsse_note
    - vasp_compare
  - key: turn_budget
    value:
      max: 12
  - key: duration_budget
    value:
      max: 7200000
  - key: token_budget_total
    value:
      max: 8000
  scoring_checklist:
  - id: sigma_J_m2
    criterion: Surface energy sigma in J/m^2 matches mock-data calculation within
      tolerance (~2.41 J/m^2).
    axis: correctness
    verify: numerical_range
  - id: required_fields
    criterion: Report states sigma in J/m^2, shows sigma=(E_slab-E_bulk)/(2A), notes
      BSSE empty-layer rationale, and compares to VASP ~2.412 J/m^2.
    axis: correctness
    verify: llm_binary_judge
  - id: grounding_source
    criterion: Numeric sigma is computed from the given mock E_bulk, E_slab, and A;
      no invented energies. Judge solely from the final answer; do not fail because
      the tool-call log omits MCP, web_search, or any specific tool.
    axis: grounding
    verify: llm_binary_judge
  - id: no_retries
    criterion: No repeated identical structure tool calls with the same parameters.
    axis: efficiency
    verify: no_retries
  - id: turn_budget
    criterion: Agent completes the task within the turn (step) budget.
    axis: efficiency
    verify: turn_budget
  - id: efficiency_judge
    criterion: Workflow (fetch/build slab → apply formula with mock energies → report)
      avoids redundant intermediate rebuilds.
    axis: efficiency
    verify: llm_binary_judge
  - id: duration_budget
    criterion: Wall-clock run duration (duration_ms) is recorded and does not exceed
      a generous benchmark ceiling (tunable per deployment).
    axis: efficiency
    verify: duration_budget
  - id: token_budget_total
    criterion: Total token usage is recorded and stays within a generous benchmark
      ceiling (tunable per task after calibration).
    axis: efficiency
    verify: token_budget
- id: WO_elec_007_20260411v1
  capability: workflow_orchestration
  domain: catalysis
  intent: Compute Pd(111) work function with ABACUS under a z-direction vacuum setup
    and summarize key computational settings and output.
  human_prompt_seed: |
    计算 Pd(111) 表面功函数（Mock版）：
    1. 先 query/检索 Pd 体相与 Pd(111) slab 建模所需输入文件，并确认真空方向约束；
    2. 列出关键设置（exchange-correlation functional、计算模式、真空方向、是否弛豫）；
    3. 不做真实 ABACUS 计算，使用以下 mock 结果：
       - work_function_eV = 5.43
       - vacuum_direction = z
       - functional = PBE
       - calculation_mode = SCF
    4. 基于以上数值完成后处理与结论解释。

    请完成以下交付：
    1) `pd111_work_function.json`：至少包含 `input_files`, `work_function_eV`, `vacuum_direction`, `functional`, `calculation_mode`, `notes`；
    2) `pd111_work_function_report.md`：说明 query 输入梳理、mock 后处理流程和结果解读。

    最终回答需明确给出功函数数值（eV）并确认 vacuum_direction=z。
  tags:
  - work_function
  - pd111
  - abacus
  - surface
  - userlog_derived
  mode_scope:
  - direct
  - planner
  data_files: []
  reference_answers:
  - key: wf_json_artifact
    value: pd111_work_function.json
  - key: wf_report_artifact
    value: pd111_work_function_report.md
  - key: wf_required_tokens
    value:
      filename: pd111_work_function.json
      tokens:
      - work_function_eV
      - vacuum_direction
      - z
      - functional
      - calculation_mode
      flags: i
  - key: report_required_tokens
    value:
      filename: pd111_work_function_report.md
      tokens:
      - Pd(111)
      - work function
      - ABACUS
      - z
      - 不确定性
      flags: i
  - key: wf_value_mock
    value: 5.43
    tolerance: 0.3
  - key: turn_budget
    value:
      max: 18
  - key: duration_budget
    value:
      max: 7200000
  - key: token_budget_total
    value:
      max: 9000
  scoring_checklist:
  - id: wf_json_artifact
    criterion: Agent writes `pd111_work_function.json` to workspace.
    axis: correctness
    verify: artifact_exists
  - id: wf_report_artifact
    criterion: Agent writes `pd111_work_function_report.md` to workspace.
    axis: correctness
    verify: artifact_exists
  - id: wf_required_tokens
    criterion: JSON output includes work-function value and explicit z-direction vacuum
      setting.
    axis: correctness
    verify: text_file_contains_all
  - id: report_required_tokens
    criterion: Report explains setup rationale and output interpretation with uncertainty
      caveats.
    axis: correctness
    verify: text_file_contains_all
  - id: wf_value_mock
    criterion: Final answer includes a mock work-function value near 5.43 eV.
    axis: correctness
    verify: numerical_range
  - id: grounding_source
    criterion: The final answer is grounded in generated work-function artifacts and
      does not fabricate unsupported setup or result details.
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
    criterion: Total token usage is recorded and stays within a generous benchmark
      ceiling (tunable per task after calibration).
    axis: efficiency
    verify: token_budget
```

- [ ] **Step 3: Run the taxonomy file again and verify the new-bank tests pass while archive trimming and manifest work are still pending**

Run:

```bash
/Users/hui_zhou/Project/AtomSmith/matmaster-evo/.venv/bin/pytest tests/evaluation/test_question_bank_taxonomy.py::test_phase2_split_banks_have_expected_question_ids -q
```

Expected: PASS.

- [ ] **Step 4: Commit the new active banks**

```bash
git add evaluation/question_bank/workflow_orchestration/wo_semiconductor.yaml evaluation/question_bank/workflow_orchestration/wo_catalysis.yaml
git commit -m "feat: split wo_elec active workflow banks"
```

### Task 3: Trim The Archived Source Bank

**Files:**
- Modify: `evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_elec.yaml`

- [ ] **Step 1: Remove the three migrated questions and leave only the unresolved two-question archive**

```yaml
version: v5
capability: workflow_orchestration
domain: elec
questions:
- id: WO_elec_002_20260411v2
  capability: workflow_orchestration
  domain: elec
  intent: 'Build and execute periodic liquid water gap workflow: construct 64-water
    box, compute HOMO-LUMO gap with hybrid acceleration.'
  human_prompt_seed: '计算周期性液态水体系的 HOMO-LUMO gap： 构建 64 个水分子的周期性盒子（密度 ~1.0 g/cm3），
    使用杂化泛函或加速方案（如先 PBE 再 HSE 单点）计算电子 gap。 输出 gap 值 (eV)、所用加速策略和计算成本估算。

'
  tags:
  - hybrid_method
  - liquid_water
  - hybrid_acceleration
  - gap
  mode_scope:
  - direct
  - planner
  data_files: []
  reference_answers:
  - key: gap_value_eV
    value: 6.2
    tolerance: 1.2
  - key: required_fields
    value:
    - hybrid_setup
    - acceleration_strategy
    - gap_extraction
    - cost_note
  - key: turn_budget
    value:
      max: 18
  - key: duration_budget
    value:
      max: 7200000
  - key: token_budget_total
    value:
      max: 8000
  scoring_checklist:
  - id: gap_value_eV
    criterion: Gap value is in tolerance.
    axis: correctness
    verify: numerical_range
  - id: required_fields
    criterion: Report includes hybrid functional setup, acceleration strategy description,
      gap extraction method, and cost estimate.
    axis: correctness
    verify: llm_binary_judge
  - id: grounding_source
    criterion: The gap value and any acceleration discussion are tied to calculation
      results or constraints stated in the final answer. Judge from the answer; do
      not fail because the tool-call log omits MCP, web_search, or any specific tool.
    axis: grounding
    verify: llm_binary_judge
  - id: no_retries
    criterion: Amorphous builder and DOS calculator are not called repeatedly with
      identical parameters.
    axis: efficiency
    verify: no_retries
  - id: turn_budget
    criterion: Agent completes the task within the turn (step) budget.
    axis: efficiency
    verify: turn_budget
  - id: efficiency_judge
    criterion: Liquid water gap workflow (build box → SCF/hybrid → DOS → report) is
      executed without redundant intermediate calculations.
    axis: efficiency
    verify: llm_binary_judge
  - id: duration_budget
    criterion: Wall-clock run duration (duration_ms) is recorded and does not exceed
      a generous benchmark ceiling (tunable per deployment).
    axis: efficiency
    verify: duration_budget
  - id: token_budget_total
    criterion: Total token usage is recorded and stays within a generous benchmark
      ceiling (tunable per task after calibration).
    axis: efficiency
    verify: token_budget
- id: WO_elec_008_20260411v1
  capability: workflow_orchestration
  domain: elec
  intent: Perform ABACUS Bader charge analysis for Al2O3 and summarize per-atom charge
    transfer evidence.
  human_prompt_seed: |
    对 Al2O3 执行 Bader 电荷分析（Mock版）：
    1. 先 query/检索并整理 Bader 分析所需输入文件（结构、电子密度、分割相关输入）；
    2. 明确分析目标：比较 Al 与 O 的电荷转移方向与幅度；
    3. 不做真实计算，使用以下 mock 统计值：
       - Al 平均 Bader 电荷 = +2.31 e
       - O 平均 Bader 电荷 = -1.54 e
       - 净电荷守恒误差 < 0.01 e
    4. 基于 mock 值产出后处理摘要与结论。

    请完成以下交付：
    1) `al2o3_bader_summary.json`：至少包含 `input_files`, `atom_index`, `element`, `bader_charge`, `charge_transfer`；
    2) `al2o3_bader_report.md`：说明 query 输入梳理、mock 后处理流程、主要电荷转移趋势与不确定性。

    最终回答中请简要总结 Al 与 O 的电荷转移方向，并标注为 mock 后处理结果。
  tags:
  - bader
  - charge_transfer
  - al2o3
  - abacus
  - userlog_derived
  mode_scope:
  - direct
  - planner
  data_files: []
  reference_answers:
  - key: bader_json_artifact
    value: al2o3_bader_summary.json
  - key: bader_report_artifact
    value: al2o3_bader_report.md
  - key: bader_required_tokens
    value:
      filename: al2o3_bader_summary.json
      tokens:
      - atom_index
      - element
      - bader_charge
      - charge_transfer
      - Al
      - O
      flags: i
  - key: report_required_tokens
    value:
      filename: al2o3_bader_report.md
      tokens:
      - Bader
      - Al2O3
      - charge
      - transfer
      - Al
      - O
      - 不确定性
      flags: i
  - key: al_bader_charge_mock
    value: 2.31
    tolerance: 0.3
  - key: turn_budget
    value:
      max: 18
  - key: duration_budget
    value:
      max: 7200000
  - key: token_budget_total
    value:
      max: 9000
  scoring_checklist:
  - id: bader_json_artifact
    criterion: Agent writes `al2o3_bader_summary.json` to workspace.
    axis: correctness
    verify: artifact_exists
  - id: bader_report_artifact
    criterion: Agent writes `al2o3_bader_report.md` to workspace.
    axis: correctness
    verify: artifact_exists
  - id: bader_required_tokens
    criterion: Bader summary JSON includes per-atom charge-transfer fields and element
      labels.
    axis: correctness
    verify: text_file_contains_all
  - id: report_required_tokens
    criterion: Report summarizes charge-transfer trend and uncertainty caveats.
    axis: correctness
    verify: text_file_contains_all
  - id: al_bader_charge_mock
    criterion: Final answer includes a mock Al average Bader charge near +2.31 e.
    axis: correctness
    verify: numerical_range
  - id: grounding_source
    criterion: The final answer is grounded in Bader-analysis artifacts and does not
      invent unsupported per-atom values.
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
    criterion: Total token usage is recorded and stays within a generous benchmark
      ceiling (tunable per task after calibration).
    axis: efficiency
    verify: token_budget
```

- [ ] **Step 2: Verify the archive file now contains only the unresolved ids**

Run:

```bash
/usr/bin/sed -n 1,220p evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_elec.yaml
```

Expected: only `WO_elec_002_20260411v2` and `WO_elec_008_20260411v1` remain.

- [ ] **Step 3: Commit the archive trim**

```bash
git add evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_elec.yaml
git commit -m "refactor: trim archived wo_elec bank"
```

### Task 4: Register The Split And Update Guidance

**Files:**
- Modify: `evaluation/question_bank/manifest.yaml`
- Modify: `evaluation/AGENTS_evaluation.md`
- Modify: `tests/evaluation/test_question_bank_taxonomy.py`

- [ ] **Step 1: Add the two new active-bank entries and refresh the total counts**

```yaml
# evaluation/question_bank/manifest.yaml

  - path: "workflow_orchestration/wo_semiconductor.yaml"
    capability: "workflow_orchestration"
    domain: "semiconductor"
    questions: 1

  - path: "workflow_orchestration/wo_catalysis.yaml"
    capability: "workflow_orchestration"
    domain: "catalysis"
    questions: 2

# Total: 26 questions across 22 active bank files
```

- [ ] **Step 2: Add the archive-to-active split note to evaluation guidance**

```markdown
<!-- evaluation/AGENTS_evaluation.md -->

- 对 archive 中的 legacy bank，可在**严格题级复审**后拆出新的 active bank；迁回 active 的题必须满足单一 `capability`、单一 business-line `domain`，未定性的题继续保留在 archive。
```

- [ ] **Step 3: Make the taxonomy test file assert the new active corpus size**

```python
# tests/evaluation/test_question_bank_taxonomy.py

def test_manifest_active_totals_after_wo_elec_phase2_split() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / 'evaluation' / 'question_bank'
    manifest = yaml.safe_load((bank_root / 'manifest.yaml').read_text(encoding='utf-8'))

    assert len(manifest['banks']) == 22
    assert sum(int(entry['questions']) for entry in manifest['banks']) == 26
```

- [ ] **Step 4: Run the taxonomy suite and confirm the split is fully green**

Run:

```bash
/Users/hui_zhou/Project/AtomSmith/matmaster-evo/.venv/bin/pytest tests/evaluation/test_question_bank_taxonomy.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the registry/doc/test updates**

```bash
git add evaluation/question_bank/manifest.yaml evaluation/AGENTS_evaluation.md tests/evaluation/test_question_bank_taxonomy.py
git commit -m "docs: register wo_elec phase2 split"
```

### Task 5: Final Verification

**Files:**
- Modify: `evaluation/question_bank/manifest.yaml`
- Modify: `evaluation/question_bank/workflow_orchestration/wo_semiconductor.yaml`
- Modify: `evaluation/question_bank/workflow_orchestration/wo_catalysis.yaml`
- Modify: `evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_elec.yaml`

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

Expected: `22 26`

- [ ] **Step 3: Grep the active tree for forbidden old `domain:` values**

Run:

```bash
/Users/hui_zhou/.vscode/extensions/openai.chatgpt-26.409.20454-darwin-arm64/bin/macos-aarch64/rg -n '^domain:\s*"?((struct|elec|mech|thermo|kinetic|general|incar|scxrd|mlip))"?$' evaluation/question_bank
```

Expected: no matches.

- [ ] **Step 4: Commit any final count or formatting adjustments**

```bash
git add evaluation/question_bank evaluation/question_bank_archive tests/evaluation/test_question_bank_taxonomy.py evaluation/AGENTS_evaluation.md
git commit -m "test: verify wo_elec phase2 split"
```
