"""Tests for standards-based JSON file schema validation."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from evaluation.core.schemas import QuestionBank
from evaluation.validators.json_file import check_json_file_schema

ROADMAP_SCHEMA = {
    'type': 'object',
    'required': ['concepts', 'key_figures'],
    'properties': {
        'concepts': {
            'type': 'array',
            'minItems': 1,
            'items': {'type': 'string', 'minLength': 1},
        },
        'key_figures': {
            'type': 'array',
            'minItems': 1,
            'items': {
                'type': 'object',
                'required': ['name'],
                'properties': {'name': {'type': 'string', 'minLength': 1}},
            },
        },
    },
}


def _write_json(tmp_path, value) -> None:
    (tmp_path / 'roadmap.json').write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def test_json_file_schema_accepts_nested_valid_document(tmp_path) -> None:
    _write_json(
        tmp_path,
        {
            'concepts': ['bulk-boundary correspondence'],
            'key_figures': [{'name': 'Shoucheng Zhang'}],
        },
    )

    ok, reason = check_json_file_schema(
        tmp_path,
        filename='roadmap.json',
        schema=ROADMAP_SCHEMA,
    )

    assert ok, reason
    assert 'matching the configured schema' in reason


def test_json_file_schema_rejects_empty_required_array(tmp_path) -> None:
    _write_json(
        tmp_path,
        {'concepts': [], 'key_figures': [{'name': 'Shoucheng Zhang'}]},
    )

    ok, reason = check_json_file_schema(
        tmp_path,
        filename='roadmap.json',
        schema=ROADMAP_SCHEMA,
    )

    assert not ok
    assert '$.concepts' in reason
    assert 'non-empty' in reason


def test_json_file_schema_reports_nested_path_for_missing_property(tmp_path) -> None:
    _write_json(
        tmp_path,
        {'concepts': ['surface states'], 'key_figures': [{}]},
    )

    ok, reason = check_json_file_schema(
        tmp_path,
        filename='roadmap.json',
        schema=ROADMAP_SCHEMA,
    )

    assert not ok
    assert '$.key_figures[0]' in reason
    assert "'name' is a required property" in reason


def test_json_file_schema_requires_a_schema(tmp_path) -> None:
    _write_json(tmp_path, {'concepts': ['surface states'], 'key_figures': []})

    ok, reason = check_json_file_schema(
        tmp_path,
        filename='roadmap.json',
        schema=None,
    )

    assert not ok
    assert 'no valid schema provided' in reason


def test_json_file_schema_rejects_invalid_schema_definition(tmp_path) -> None:
    _write_json(tmp_path, {'concepts': ['surface states'], 'key_figures': []})

    ok, reason = check_json_file_schema(
        tmp_path,
        filename='roadmap.json',
        schema={'type': 'not-a-json-type'},
    )

    assert not ok
    assert 'invalid schema' in reason


def test_json_file_schema_supports_non_object_documents(tmp_path) -> None:
    _write_json(tmp_path, ['surface states'])

    ok, reason = check_json_file_schema(
        tmp_path,
        filename='roadmap.json',
        schema={
            'type': 'array',
            'minItems': 1,
            'items': {'type': 'string'},
        },
    )

    assert ok, reason


def _bank_with_schema(schema) -> dict:
    return {
        'version': 'v5',
        'capability': 'scientific_analysis',
        'domain': 'agnostic',
        'questions': [
            {
                'id': 'SA_schema_test_001',
                'capability': 'scientific_analysis',
                'domain': 'agnostic',
                'intent': 'test JSON Schema configuration validation',
                'human_prompt_seed': 'write result.json',
                'reference_answers': [
                    {
                        'key': 'result_schema',
                        'value': {'filename': 'result.json', 'schema': schema},
                    }
                ],
                'scoring_checklist': [
                    {
                        'id': 'result_schema',
                        'criterion': 'result.json matches its schema',
                        'verify': 'json_file_schema',
                    }
                ],
            }
        ],
    }


def test_question_bank_rejects_invalid_json_schema_definition() -> None:
    with pytest.raises(ValidationError, match='invalid JSON Schema'):
        QuestionBank.model_validate(_bank_with_schema({'type': 'not-a-json-type'}))


def test_question_bank_rejects_legacy_required_keys_configuration() -> None:
    raw = _bank_with_schema({'type': 'object'})
    value = raw['questions'][0]['reference_answers'][0]['value']
    value['required_keys'] = ['concepts']

    with pytest.raises(ValidationError, match='unsupported keys.*required_keys'):
        QuestionBank.model_validate(raw)
