"""Tests for cross-field validation of Bohr GPU recommendations."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.core.evaluator import BinaryEvaluator
from evaluation.core.evidence import EvidenceBundle
from evaluation.core.runner import flatten_banks, load_question_banks
from evaluation.validators.json_file import check_bohr_gpu_comparison_record

REPO_ROOT = Path(__file__).resolve().parents[2]
QUESTION_BANK_DIR = REPO_ROOT / 'evaluation' / 'question_bank'


def _record(*, recommended: str = '1 * NVIDIA V100_32g') -> dict[str, object]:
    return {
        'workload': {'framework': 'DeepMD-kit', 'atom_count': 500},
        'available_machines': [
            {
                'sku_id': 740,
                'machine_type': '1 * NVIDIA T4_16g',
                'gpu_model': 'NVIDIA T4',
                'gpu_count': 1,
                'gpu_memory_gb': 16,
                'price_cny_per_hour': 2.5,
                'has_stock': False,
            },
            {
                'sku_id': 738,
                'machine_type': '1 * NVIDIA V100_32g',
                'gpu_model': 'NVIDIA V100',
                'gpu_count': 1,
                'gpu_memory_gb': 32,
                'price_cny_per_hour': 4.5,
                'has_stock': True,
            },
            {
                'sku_id': 4675,
                'machine_type': '1 * NVIDIA A100_80g',
                'gpu_model': 'NVIDIA A100',
                'gpu_count': 1,
                'gpu_memory_gb': 80,
                'price_cny_per_hour': 10,
                'has_stock': True,
            },
        ],
        'recommendation': {
            'machine_type': recommended,
            'reason': 'Balances memory capacity, throughput, and hourly cost.',
        },
    }


def _write_record(tmp_path: Path, value: object) -> None:
    (tmp_path / 'b4_gpu_comparison.json').write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def _check(tmp_path: Path) -> tuple[bool, str]:
    return check_bohr_gpu_comparison_record(
        tmp_path,
        filename='b4_gpu_comparison.json',
    )


def test_gpu_comparison_accepts_listed_in_stock_recommendation(
    tmp_path: Path,
) -> None:
    _write_record(tmp_path, _record(recommended='  1 * NVIDIA V100_32g  '))

    ok, reason = _check(tmp_path)

    assert ok, reason


def test_gpu_comparison_rejects_unlisted_recommendation(tmp_path: Path) -> None:
    _write_record(tmp_path, _record(recommended='1 * NVIDIA H100_80g'))

    ok, reason = _check(tmp_path)

    assert not ok
    assert 'not in available_machines' in reason


def test_gpu_comparison_rejects_out_of_stock_recommendation(
    tmp_path: Path,
) -> None:
    _write_record(tmp_path, _record(recommended='1 * NVIDIA T4_16g'))

    ok, reason = _check(tmp_path)

    assert not ok
    assert 'not marked in stock' in reason


def test_gpu_comparison_checker_is_registered_with_evaluator(
    tmp_path: Path,
) -> None:
    _write_record(tmp_path, _record())
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(
        item for item in questions if item.id == 'BWO_gpu_compare_004_20260715_v4'
    )

    record = BinaryEvaluator().evaluate(
        question=question,
        answer='done',
        evidence=EvidenceBundle(
            task_id=question.id,
            workspace_dir=str(tmp_path),
            total_steps=4,
        ),
        tool_calls=[
            {
                'tool_name': 'Bash',
                'tool_args': {
                    'command': 'bohr machine list -c gpu -s job -o json',
                },
            }
        ],
    )

    assert record.criteria_results['comparison_schema'].passed is True
    assert record.criteria_results['recommendation_in_stock'].passed is True
    assert record.criteria_results['machines_queried_via_cli'].passed is True
