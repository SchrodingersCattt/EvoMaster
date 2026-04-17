# Domain Business-Line Phase 1 Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hard-cut evaluation `domain` to the five business-line values (`battery`, `catalysis`, `polymer`, `alloy`, `semiconductor`) and make the active question-bank corpus business-line-only.

**Architecture:** Tighten taxonomy validation first, then migrate the active `direct_migrate` banks and manifest to the new business-line domains. Because `load_question_banks()` eagerly loads every YAML under `evaluation/question_bank/`, Phase 1 must also move all non-phase1 banks out of the active bank tree; otherwise the new `DomainLiteral` would immediately break loading.

**Tech Stack:** Pydantic, pytest, YAML, uv, git.

---

**Assumptions for this plan**

- Treat `evaluation/question_bank/data_fitting/df_mech.yaml`, `evaluation/question_bank/workflow_orchestration/wo_general.yaml`, and `evaluation/question_bank/workflow_orchestration/wo_struct.yaml` as held-out banks in Phase 1. They are currently uncategorized in the design spec, so this plan archives them with the `needs_split_or_review` set instead of force-migrating them.
- Keep the active loader root as `evaluation/question_bank`. Held-out banks move to `evaluation/question_bank_archive/businessline_phase2/` so `evaluation.core.runner.load_question_banks()` stays unchanged.
- `polymer/*` banks already satisfy the new domain semantics and remain in place with no content edits.

### Task 1: Add Failing Tests For Business-Line-Only Taxonomy

**Files:**
- Modify: `tests/evaluation/test_question_bank_taxonomy.py`
- Modify: `tests/evaluation/test_runtime_and_structure_checks.py`

- [ ] **Step 1: Rewrite taxonomy fixtures and add failing business-line tests**

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
    'workflow_orchestration/wo_elec_adsorption.yaml': 'catalysis',
    'workflow_orchestration/wo_elec_nfpp_refactored.yaml': 'battery',
    'workflow_orchestration/wo_general_mech.yaml': 'alloy',
    'workflow_orchestration/wo_mech_struct.yaml': 'alloy',
    'workflow_orchestration/wo_mech_thermo.yaml': 'alloy',
}

VALID_BUSINESS_DOMAINS = [
    'battery',
    'catalysis',
    'polymer',
    'alloy',
    'semiconductor',
]

REMOVED_LEGACY_DOMAINS = [
    'struct',
    'elec',
    'mech',
    'thermo',
    'kinetic',
    'general',
    'incar',
    'scxrd',
    'mlip',
]


def _minimal_question(
    *, capability: str, domain: str, tags: list[str] | None = None
) -> dict:
    return {
        'id': f'{capability}_{domain}',
        'capability': capability,
        'domain': domain,
        'intent': 'taxonomy test',
        'human_prompt_seed': 'x',
        'tags': tags or [],
        'reference_answers': [{'key': 'unused', 'value': 'x'}],
        'scoring_checklist': [
            {
                'id': 'unused',
                'criterion': 'unused',
                'axis': 'correctness',
                'verify': 'llm_binary_judge',
            }
        ],
    }


@pytest.mark.parametrize('domain', VALID_BUSINESS_DOMAINS)
def test_question_item_accepts_business_line_domains(domain: str) -> None:
    from evaluation.core.schemas import QuestionItem

    item = QuestionItem(
        id=f'{domain}_ok',
        capability='scientific_analysis',
        domain=domain,
        intent='business-line domain should be accepted',
        human_prompt_seed='x',
        scoring_checklist=[
            {
                'id': 'unused',
                'criterion': 'unused',
                'axis': 'correctness',
                'verify': 'llm_binary_judge',
            }
        ],
        reference_answers=[{'key': 'unused', 'value': 'x'}],
    )
    assert item.domain == domain


@pytest.mark.parametrize('domain', REMOVED_LEGACY_DOMAINS)
def test_question_item_rejects_removed_legacy_domains(domain: str) -> None:
    from evaluation.core.schemas import QuestionItem

    with pytest.raises(ValidationError, match=domain):
        QuestionItem(
            id='legacy_domain',
            capability='scientific_analysis',
            domain=domain,
            intent='legacy domain should be rejected',
            human_prompt_seed='x',
            scoring_checklist=[
                {
                    'id': 'unused',
                    'criterion': 'unused',
                    'axis': 'correctness',
                    'verify': 'llm_binary_judge',
                }
            ],
            reference_answers=[{'key': 'unused', 'value': 'x'}],
        )
```

```python
# tests/evaluation/test_question_bank_taxonomy.py

def test_active_question_banks_use_only_business_line_domains() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / 'evaluation' / 'question_bank'

    for bank_path in sorted(bank_root.glob('*/*.yaml')):
        if bank_path.name == 'manifest.yaml':
            continue
        raw_bank = yaml.safe_load(bank_path.read_text(encoding='utf-8'))
        assert raw_bank['domain'] in VALID_BUSINESS_DOMAINS, bank_path.as_posix()
        assert {q['domain'] for q in raw_bank['questions']} == {raw_bank['domain']}


def test_direct_migrate_banks_match_phase1_domain_mapping() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / 'evaluation' / 'question_bank'

    for rel_path, expected_domain in DIRECT_MIGRATE_DOMAIN_EXPECTATIONS.items():
        raw_bank = yaml.safe_load((bank_root / rel_path).read_text(encoding='utf-8'))
        assert raw_bank['domain'] == expected_domain, rel_path
        assert {q['domain'] for q in raw_bank['questions']} == {expected_domain}, rel_path
```

```python
# tests/evaluation/test_runtime_and_structure_checks.py

def test_eval_run_record_serializes_duration_ms() -> None:
    from evaluation.core.schemas import EvalRunRecord

    r = EvalRunRecord(
        question_id='Q',
        capability='structure_construction',
        domain='battery',
        mode='direct',
        repeat_idx=0,
        prompt='p',
        answer='a',
        run_status='completed',
        duration_ms=1234,
    )
    dumped = json.loads(r.model_dump_json())
    assert dumped['duration_ms'] == 1234


def test_question_item_rejects_removed_domain_optical() -> None:
    from evaluation.core.schemas import QuestionItem

    with pytest.raises(ValidationError, match='optical'):
        QuestionItem(
            id='OP',
            capability='structure_construction',
            domain='optical',
            intent='legacy domain should be rejected',
            human_prompt_seed='x',
            scoring_checklist=[
                {
                    'id': 'unused',
                    'criterion': 'unused',
                    'axis': 'correctness',
                    'verify': 'llm_binary_judge',
                }
            ],
            reference_answers=[{'key': 'unused', 'value': 'x'}],
        )


def test_safety_questions_also_count_token_and_duration_efficiency() -> None:
    from evaluation.core.schemas import QuestionItem, SafetyVetoRecord, TokenUsageRecord

    evaluator = BinaryEvaluator()
    q = QuestionItem(
        id='SR',
        capability='safety_refusal',
        domain='battery',
        intent='refuse harmful request',
        human_prompt_seed='x',
        scoring_checklist=[
            {
                'id': 'unused',
                'criterion': 'unused',
                'axis': 'correctness',
                'verify': 'llm_binary_judge',
            }
        ],
        reference_answers=[],
    )
    evaluator.evaluate_safety = lambda **kwargs: SafetyVetoRecord(  # type: ignore[method-assign]
        triggered=False,
        reason='',
        risk_not_detected=True,
        detail_non_leakage=True,
        safe_redirection=True,
    )
    rec = evaluator.evaluate(
        question=q,
        answer='I cannot help with that.',
        token_usage=TokenUsageRecord(
            prompt_tokens=10, completion_tokens=5, total_tokens=20
        ),
        duration_ms=1000,
    )
    assert rec.correctness_total == 1
    assert rec.efficiency_total == 2
    assert rec.criteria_results['token_budget_total'].passed is True
    assert rec.criteria_results['duration_budget'].passed is True
```

- [ ] **Step 2: Run the taxonomy-focused tests and confirm they fail before implementation**

Run: `uv run pytest tests/evaluation/test_question_bank_taxonomy.py tests/evaluation/test_runtime_and_structure_checks.py -q`

Expected: FAIL because `evaluation/core/schemas.py` still allows legacy domains, the active question-bank tree still contains old-domain YAMLs, and the manifest still points at legacy-domain banks.

### Task 2: Tighten Schema And Evaluation Docs To Business-Line Semantics

**Files:**
- Modify: `evaluation/core/schemas.py`
- Modify: `evaluation/AGENTS_evaluation.md`
- Modify: `evaluation/README_CN.md`

- [ ] **Step 1: Replace the domain enum in `schemas.py` with the five business-line literals**

```python
# evaluation/core/schemas.py

DomainLiteral = Literal[
    'battery',
    'catalysis',
    'polymer',
    'alloy',
    'semiconductor',
]
```

- [ ] **Step 2: Update normative evaluation docs so `domain` means business line only**

```markdown
<!-- evaluation/AGENTS_evaluation.md -->

| **`domain`** | Subject（业务线 / 应用场景） | **单选**（枚举之一） | 这道题最终服务于哪条业务线？仅允许 `battery` / `catalysis` / `polymer` / `alloy` / `semiconductor`。 |

`domain` 当前只允许以下取值：

`battery` / `catalysis` / `polymer` / `alloy` / `semiconductor`

- `domain` 不再承载 `struct` / `elec` / `mech` / `thermo` / `kinetic` 这类物理轴。
- `domain` 不再承载 `general` / `incar` / `scxrd` / `mlip` 这类泛化、方法或工具轴。
- 材料对象、方法、软件、专题线统一放入 `tags`。
- 未纳入本轮业务线迁移的 bank 必须移出 `evaluation/question_bank/`，不能与新 domain 混放。
```

```markdown
<!-- evaluation/README_CN.md -->

## 当前 domain 语义

- `domain` 仅表示业务线 / 应用场景，不再表示物理子域或方法轴。
- 当前 active question bank 只允许 `battery`、`catalysis`、`polymer`、`alloy`、`semiconductor`。
- 未完成业务线归类的历史 bank 存放到 `evaluation/question_bank_archive/businessline_phase2/`，不参与默认加载。
```

- [ ] **Step 3: Run the schema-level tests that should pass after the enum cutover**

Run: `uv run pytest tests/evaluation/test_question_bank_taxonomy.py::test_question_bank_rejects_mismatched_top_level_domain_hint tests/evaluation/test_runtime_and_structure_checks.py::test_question_item_accepts_business_line_domains tests/evaluation/test_runtime_and_structure_checks.py::test_question_item_rejects_removed_legacy_domains -q`

Expected: PASS. The active-bank integration tests should still be failing until Task 3 is finished.

- [ ] **Step 4: Commit the schema/doc cutover**

```bash
git add evaluation/core/schemas.py evaluation/AGENTS_evaluation.md evaluation/README_CN.md tests/evaluation/test_question_bank_taxonomy.py tests/evaluation/test_runtime_and_structure_checks.py
git commit -m "refactor: redefine evaluation domains as business lines"
```

### Task 3: Migrate The Active Phase 1 Banks And Rewrite The Manifest

**Files:**
- Modify: `evaluation/question_bank/manifest.yaml`
- Modify: `evaluation/question_bank/batch_processing/bp_elec.yaml`
- Modify: `evaluation/question_bank/co2rr_reproduction/co2rr_bp_struct.yaml`
- Modify: `evaluation/question_bank/co2rr_reproduction/co2rr_sa_elec.yaml`
- Modify: `evaluation/question_bank/co2rr_reproduction/co2rr_sa_general.yaml`
- Modify: `evaluation/question_bank/co2rr_reproduction/co2rr_wo_mech.yaml`
- Modify: `evaluation/question_bank/co2rr_reproduction/wo_co2rr_unit_ops.yaml`
- Modify: `evaluation/question_bank/data_fitting/df_elec.yaml`
- Modify: `evaluation/question_bank/scientific_analysis/sa_elec.yaml`
- Modify: `evaluation/question_bank/scientific_analysis/sa_mech.yaml`
- Modify: `evaluation/question_bank/structure_construction/sc_elec_adsorption.yaml`
- Modify: `evaluation/question_bank/workflow_orchestration/wo_elec_adsorption.yaml`
- Modify: `evaluation/question_bank/workflow_orchestration/wo_elec_nfpp_refactored.yaml`
- Modify: `evaluation/question_bank/workflow_orchestration/wo_general_mech.yaml`
- Modify: `evaluation/question_bank/workflow_orchestration/wo_mech_struct.yaml`
- Modify: `evaluation/question_bank/workflow_orchestration/wo_mech_thermo.yaml`

- [ ] **Step 1: Update the manifest entries so the active registry is phase1-only and business-line-only**

```yaml
# evaluation/question_bank/manifest.yaml

banks:
  - path: "batch_processing/bp_elec.yaml"
    capability: "batch_processing"
    domain: "catalysis"
    questions: 1

  - path: "co2rr_reproduction/wo_co2rr_unit_ops.yaml"
    capability: "structure_construction"
    domain: "catalysis"
    questions: 3

  - path: "co2rr_reproduction/co2rr_wo_mech.yaml"
    capability: "workflow_orchestration"
    domain: "catalysis"
    questions: 1

  - path: "co2rr_reproduction/co2rr_sa_elec.yaml"
    capability: "scientific_analysis"
    domain: "catalysis"
    questions: 1

  - path: "co2rr_reproduction/co2rr_bp_struct.yaml"
    capability: "batch_processing"
    domain: "catalysis"
    questions: 1

  - path: "co2rr_reproduction/co2rr_sa_general.yaml"
    capability: "scientific_analysis"
    domain: "catalysis"
    questions: 1

  - path: "workflow_orchestration/wo_elec_nfpp_refactored.yaml"
    capability: "workflow_orchestration"
    domain: "battery"
    questions: 2

  - path: "scientific_analysis/sa_elec.yaml"
    capability: "scientific_analysis"
    domain: "battery"
    questions: 1

  - path: "scientific_analysis/sa_mech.yaml"
    capability: "scientific_analysis"
    domain: "alloy"
    questions: 1

  - path: "workflow_orchestration/wo_mech_struct.yaml"
    capability: "workflow_orchestration"
    domain: "alloy"
    questions: 1

  - path: "workflow_orchestration/wo_mech_thermo.yaml"
    capability: "workflow_orchestration"
    domain: "alloy"
    questions: 1

  - path: "workflow_orchestration/wo_general_mech.yaml"
    capability: "workflow_orchestration"
    domain: "alloy"
    questions: 1

  - path: "data_fitting/df_elec.yaml"
    capability: "scientific_analysis"
    domain: "semiconductor"
    questions: 1
```

- [ ] **Step 2: Rewrite each phase1 bank’s top-level and per-question `domain` fields**

```yaml
# evaluation/question_bank/co2rr_reproduction/co2rr_sa_elec.yaml

version: v5
capability: scientific_analysis
domain: catalysis
questions:
- id: CR_WO_limiting_potential_20260412
  capability: scientific_analysis
  domain: catalysis
  tags:
  - co2rr_repro
  - limiting_potential
  - co2rr
  - her
  - free_energy
```

```yaml
# evaluation/question_bank/workflow_orchestration/wo_elec_nfpp_refactored.yaml

version: "v5"
capability: "workflow_orchestration"
domain: "battery"

questions:
  - id: "WO_elec_003_20260404"
    capability: "workflow_orchestration"
    domain: "battery"
    tags: ["nfpp", "doping", "formation_energy", "battery"]
```

```yaml
# evaluation/question_bank/scientific_analysis/sa_mech.yaml

version: v5
capability: scientific_analysis
domain: alloy
questions:
- id: WO_general_steel_008_20260411v1
  capability: scientific_analysis
  domain: alloy
  tags:
  - userlog_derived
  - steel
  - tensile_strength
  - composition_tuning
```

```yaml
# evaluation/question_bank/data_fitting/df_elec.yaml

version: v5
capability: scientific_analysis
domain: semiconductor
questions:
- id: DF_elec_001_20260404
  capability: scientific_analysis
  domain: semiconductor
  tags:
  - bandgap
  - classification
  - electronic_structure
```

- [ ] **Step 3: Keep only phase1 banks in the active tree by moving every held-out legacy-domain bank to the archive root**

```bash
mkdir -p evaluation/question_bank_archive/businessline_phase2/batch_processing
mkdir -p evaluation/question_bank_archive/businessline_phase2/data_diagnosis
mkdir -p evaluation/question_bank_archive/businessline_phase2/data_fitting
mkdir -p evaluation/question_bank_archive/businessline_phase2/execution_contract
mkdir -p evaluation/question_bank_archive/businessline_phase2/input_generation
mkdir -p evaluation/question_bank_archive/businessline_phase2/safety_refusal
mkdir -p evaluation/question_bank_archive/businessline_phase2/scientific_analysis
mkdir -p evaluation/question_bank_archive/businessline_phase2/structure_construction
mkdir -p evaluation/question_bank_archive/businessline_phase2/structure_retrieval
mkdir -p evaluation/question_bank_archive/businessline_phase2/workflow_orchestration

git mv evaluation/question_bank/batch_processing/bp_struct.yaml evaluation/question_bank_archive/businessline_phase2/batch_processing/bp_struct.yaml
git mv evaluation/question_bank/data_diagnosis/dd_general.yaml evaluation/question_bank_archive/businessline_phase2/data_diagnosis/dd_general.yaml
git mv evaluation/question_bank/data_fitting/df_mech.yaml evaluation/question_bank_archive/businessline_phase2/data_fitting/df_mech.yaml
git mv evaluation/question_bank/data_fitting/df_scxrd.yaml evaluation/question_bank_archive/businessline_phase2/data_fitting/df_scxrd.yaml
git mv evaluation/question_bank/data_fitting/df_thermo.yaml evaluation/question_bank_archive/businessline_phase2/data_fitting/df_thermo.yaml
git mv evaluation/question_bank/execution_contract/direct_contract.yaml evaluation/question_bank_archive/businessline_phase2/execution_contract/direct_contract.yaml
git mv evaluation/question_bank/input_generation/ig_abacus.yaml evaluation/question_bank_archive/businessline_phase2/input_generation/ig_abacus.yaml
git mv evaluation/question_bank/input_generation/ig_abacus_mech.yaml evaluation/question_bank_archive/businessline_phase2/input_generation/ig_abacus_mech.yaml
git mv evaluation/question_bank/input_generation/ig_abacus_thermo.yaml evaluation/question_bank_archive/businessline_phase2/input_generation/ig_abacus_thermo.yaml
git mv evaluation/question_bank/input_generation/ig_incar.yaml evaluation/question_bank_archive/businessline_phase2/input_generation/ig_incar.yaml
git mv evaluation/question_bank/safety_refusal/sr_general.yaml evaluation/question_bank_archive/businessline_phase2/safety_refusal/sr_general.yaml
git mv evaluation/question_bank/scientific_analysis/sa_general.yaml evaluation/question_bank_archive/businessline_phase2/scientific_analysis/sa_general.yaml
git mv evaluation/question_bank/structure_construction/sc_struct.yaml evaluation/question_bank_archive/businessline_phase2/structure_construction/sc_struct.yaml
git mv evaluation/question_bank/structure_retrieval/sr_struct_db.yaml evaluation/question_bank_archive/businessline_phase2/structure_retrieval/sr_struct_db.yaml
git mv evaluation/question_bank/workflow_orchestration/wo_elec.yaml evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_elec.yaml
git mv evaluation/question_bank/workflow_orchestration/wo_elec_thermo.yaml evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_elec_thermo.yaml
git mv evaluation/question_bank/workflow_orchestration/wo_general.yaml evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_general.yaml
git mv evaluation/question_bank/workflow_orchestration/wo_kinetic.yaml evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_kinetic.yaml
git mv evaluation/question_bank/workflow_orchestration/wo_mech.yaml evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_mech.yaml
git mv evaluation/question_bank/workflow_orchestration/wo_mlip_dpa.yaml evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_mlip_dpa.yaml
git mv evaluation/question_bank/workflow_orchestration/wo_mlip_dpa_kinetic.yaml evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_mlip_dpa_kinetic.yaml
git mv evaluation/question_bank/workflow_orchestration/wo_mlip_dpa_mech.yaml evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_mlip_dpa_mech.yaml
git mv evaluation/question_bank/workflow_orchestration/wo_mlip_dpa_mlip.yaml evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_mlip_dpa_mlip.yaml
git mv evaluation/question_bank/workflow_orchestration/wo_mlip_dpa_thermo.yaml evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_mlip_dpa_thermo.yaml
git mv evaluation/question_bank/workflow_orchestration/wo_struct.yaml evaluation/question_bank_archive/businessline_phase2/workflow_orchestration/wo_struct.yaml
```

- [ ] **Step 4: Run the active-corpus verification after the manifest rewrite and archive move**

Run: `uv run pytest tests/evaluation/test_question_bank_taxonomy.py -q`

Expected: PASS.

Run: `uv run python -c "from pathlib import Path; from evaluation.core.runner import load_question_banks; banks = load_question_banks(Path('evaluation/question_bank')); print(len(banks), sum(len(bank.questions) for bank in banks))"`

Expected: `20 23`

- [ ] **Step 5: Commit the active-bank cutover**

```bash
git add evaluation/question_bank evaluation/question_bank_archive tests/evaluation/test_question_bank_taxonomy.py
git commit -m "refactor: activate business-line question banks only"
```

### Task 4: Run Final Verification And Residue Audits

**Files:**
- Modify: `evaluation/question_bank/manifest.yaml`
- Modify: `evaluation/AGENTS_evaluation.md`
- Modify: `evaluation/README_CN.md`

- [ ] **Step 1: Refresh final comments and examples so counts and examples match the new active corpus**

```yaml
# evaluation/question_bank/manifest.yaml

# Total: 23 questions across 20 active bank files
```

```markdown
<!-- evaluation/AGENTS_evaluation.md / evaluation/README_CN.md -->

- Active banks live under `evaluation/question_bank/`.
- Held-out legacy-domain banks live under `evaluation/question_bank_archive/businessline_phase2/`.
- `--slices` domain filters now accept only `battery`, `catalysis`, `polymer`, `alloy`, `semiconductor`.
```

- [ ] **Step 2: Run the targeted pytest suite**

Run: `uv run pytest tests/evaluation/test_question_bank_taxonomy.py tests/evaluation/test_runtime_and_structure_checks.py tests/evaluation/test_slice_parser.py -q`

Expected: PASS.

- [ ] **Step 3: Run the loader smoke test and the legacy-domain residue grep**

Run: `uv run python -c "from pathlib import Path; from evaluation.core.runner import load_question_banks; load_question_banks(Path('evaluation/question_bank')); print('question banks loaded')"`

Expected: `question banks loaded`

Run: `/Users/hui_zhou/.vscode/extensions/openai.chatgpt-26.409.20454-darwin-arm64/bin/macos-aarch64/rg -n '^domain:\\s*\"?(struct|elec|mech|thermo|kinetic|general|incar|scxrd|mlip)\"?$' evaluation/question_bank tests/evaluation/test_question_bank_taxonomy.py tests/evaluation/test_runtime_and_structure_checks.py evaluation/core/schemas.py evaluation/AGENTS_evaluation.md evaluation/README_CN.md`

Expected: no matches.

- [ ] **Step 4: Commit the final verification/doc cleanup**

```bash
git add evaluation/question_bank/manifest.yaml evaluation/AGENTS_evaluation.md evaluation/README_CN.md
git commit -m "docs: finalize evaluation business-line taxonomy"
```
