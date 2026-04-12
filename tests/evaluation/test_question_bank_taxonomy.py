from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from evaluation.core.schemas import QuestionBank

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


def test_question_bank_rejects_mismatched_top_level_capability_hint() -> None:
    with pytest.raises(ValidationError, match='top-level capability'):
        QuestionBank.model_validate(
            {
                'version': 'v5',
                'capability': 'scientific_analysis',
                'questions': [
                    _minimal_question(
                        capability='workflow_orchestration', domain='battery'
                    )
                ],
            }
        )


def test_question_bank_requires_top_level_capability() -> None:
    with pytest.raises(ValidationError, match='top-level capability is required'):
        QuestionBank.model_validate(
            {
                'version': 'v5',
                'domain': 'battery',
                'questions': [
                    _minimal_question(
                        capability='scientific_analysis', domain='battery'
                    )
                ],
            }
        )


def test_question_bank_rejects_mismatched_top_level_domain_hint() -> None:
    with pytest.raises(ValidationError, match='top-level domain'):
        QuestionBank.model_validate(
            {
                'version': 'v5',
                'capability': 'scientific_analysis',
                'domain': 'catalysis',
                'questions': [
                    _minimal_question(
                        capability='scientific_analysis', domain='battery'
                    )
                ],
            }
        )


def test_question_bank_requires_top_level_domain() -> None:
    with pytest.raises(ValidationError, match='top-level domain is required'):
        QuestionBank.model_validate(
            {
                'version': 'v5',
                'capability': 'scientific_analysis',
                'questions': [
                    _minimal_question(
                        capability='scientific_analysis', domain='battery'
                    )
                ],
            }
        )


def test_question_bank_rejects_question_tag_matching_own_capability() -> None:
    with pytest.raises(ValidationError, match='must not repeat question capability'):
        QuestionBank.model_validate(
            {
                'version': 'v5',
                'capability': 'scientific_analysis',
                'domain': 'battery',
                'questions': [
                    _minimal_question(
                        capability='scientific_analysis',
                        domain='battery',
                        tags=['scientific_analysis'],
                    )
                ],
            }
        )


def test_question_bank_rejects_question_tag_matching_own_domain() -> None:
    with pytest.raises(ValidationError, match='must not repeat question domain'):
        QuestionBank.model_validate(
            {
                'version': 'v5',
                'capability': 'scientific_analysis',
                'domain': 'battery',
                'questions': [
                    _minimal_question(
                        capability='scientific_analysis',
                        domain='battery',
                        tags=['battery'],
                    )
                ],
            }
        )


def test_question_bank_rejects_generic_process_tag() -> None:
    with pytest.raises(ValidationError, match='generic process tag'):
        QuestionBank.model_validate(
            {
                'version': 'v5',
                'capability': 'scientific_analysis',
                'domain': 'battery',
                'questions': [
                    _minimal_question(
                        capability='scientific_analysis',
                        domain='battery',
                        tags=['workflow'],
                    )
                ],
            }
        )


def test_question_bank_rejects_noncanonical_tag_alias() -> None:
    with pytest.raises(ValidationError, match="use canonical tag 'srtio3'"):
        QuestionBank.model_validate(
            {
                'version': 'v5',
                'capability': 'scientific_analysis',
                'domain': 'battery',
                'questions': [
                    _minimal_question(
                        capability='scientific_analysis',
                        domain='battery',
                        tags=['SrTiO3'],
                    )
                ],
            }
        )


def test_question_bank_rejects_tag_with_noncanonical_case() -> None:
    with pytest.raises(ValidationError, match="use canonical tag 'hea'"):
        QuestionBank.model_validate(
            {
                'version': 'v5',
                'capability': 'scientific_analysis',
                'domain': 'battery',
                'questions': [
                    _minimal_question(
                        capability='scientific_analysis',
                        domain='battery',
                        tags=['HEA'],
                    )
                ],
            }
        )


@pytest.mark.parametrize('domain', VALID_BUSINESS_DOMAINS)
def test_question_bank_accepts_business_line_domains(domain: str) -> None:
    bank = QuestionBank.model_validate(
        {
            'version': 'v5',
            'capability': 'scientific_analysis',
            'domain': domain,
            'questions': [
                _minimal_question(capability='scientific_analysis', domain=domain)
            ],
        }
    )
    assert bank.domain == domain


@pytest.mark.parametrize('domain', REMOVED_LEGACY_DOMAINS)
def test_question_bank_rejects_removed_legacy_domains(domain: str) -> None:
    with pytest.raises(ValidationError, match=domain):
        QuestionBank.model_validate(
            {
                'version': 'v5',
                'capability': 'scientific_analysis',
                'domain': domain,
                'questions': [
                    _minimal_question(
                        capability='scientific_analysis',
                        domain=domain,
                    )
                ],
            }
        )


def test_manifest_bank_metadata_matches_bank_files() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bank_root = repo_root / 'evaluation' / 'question_bank'
    manifest = yaml.safe_load((bank_root / 'manifest.yaml').read_text(encoding='utf-8'))

    for entry in manifest['banks']:
        bank_path = bank_root / entry['path']
        raw_bank = yaml.safe_load(bank_path.read_text(encoding='utf-8'))
        assert len(raw_bank['questions']) == entry['questions'], entry['path']

        manifest_cap = entry.get('capability')
        bank_cap = raw_bank.get('capability')
        assert bank_cap is not None, entry['path']
        assert manifest_cap == bank_cap, entry['path']

        manifest_domain = entry.get('domain')
        bank_domain = raw_bank.get('domain')
        assert bank_domain is not None, entry['path']
        assert manifest_domain == bank_domain, entry['path']


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
        assert {q['domain'] for q in raw_bank['questions']} == {expected_domain}, (
            rel_path
        )
