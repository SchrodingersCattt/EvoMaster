from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from evaluation.core.schemas import QuestionBank


def _minimal_question(*, capability: str, domain: str) -> dict:
    return {
        'id': f'{capability}_{domain}',
        'capability': capability,
        'domain': domain,
        'intent': 'taxonomy test',
        'human_prompt_seed': 'x',
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
                        capability='workflow_orchestration', domain='general'
                    )
                ],
            }
        )


def test_question_bank_requires_top_level_capability() -> None:
    with pytest.raises(ValidationError, match='top-level capability is required'):
        QuestionBank.model_validate(
            {
                'version': 'v5',
                'domain': 'general',
                'questions': [
                    _minimal_question(
                        capability='scientific_analysis', domain='general'
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
                'domain': 'elec',
                'questions': [
                    _minimal_question(
                        capability='scientific_analysis', domain='general'
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
                        capability='scientific_analysis', domain='general'
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
