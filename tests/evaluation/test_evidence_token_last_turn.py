"""EvidenceExtractor: token usage from last model turn only."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.core.evaluator_helpers import build_llm_context, check_token_budget
from evaluation.core.evidence import EvidenceBundle, EvidenceExtractor, TokenUsage
from evaluation.core.schemas import QuestionItem


def test_token_usage_from_usage_dict_openai_shape() -> None:
    u = TokenUsage.from_usage_dict(
        {
            'prompt_tokens': 100,
            'completion_tokens': 20,
            'total_tokens': 120,
        }
    )
    assert u.prompt_tokens == 100
    assert u.completion_tokens == 20
    assert u.total_tokens == 120


def test_extractor_uses_last_step_by_step_id(tmp_path: Path) -> None:
    traj = [
        {
            'status': 'completed',
            'trajectory': {
                'task_id': 't1',
                'steps': [
                    {
                        'step_id': 1,
                        'assistant_message': {
                            'meta': {
                                'usage': {
                                    'prompt_tokens': 10,
                                    'completion_tokens': 1,
                                    'total_tokens': 11,
                                }
                            }
                        },
                        'tool_responses': [],
                    },
                    {
                        'step_id': 3,
                        'assistant_message': {
                            'meta': {
                                'usage': {
                                    'prompt_tokens': 500,
                                    'completion_tokens': 50,
                                    'total_tokens': 550,
                                }
                            }
                        },
                        'tool_responses': [],
                    },
                    {
                        'step_id': 2,
                        'assistant_message': {
                            'meta': {
                                'usage': {
                                    'prompt_tokens': 999,
                                    'completion_tokens': 9,
                                    'total_tokens': 1008,
                                }
                            }
                        },
                        'tool_responses': [],
                    },
                ],
            },
        }
    ]
    p = tmp_path / 'traj.json'
    p.write_text(json.dumps(traj), encoding='utf-8')
    ev = EvidenceExtractor().extract(p, task_id='t1', final_answer='')
    assert ev.token_usage_last_turn.prompt_tokens == 500
    assert ev.token_usage_last_turn.completion_tokens == 50
    assert ev.token_usage_last_turn.total_tokens == 550
    assert ev.token_usage_run.prompt_tokens == 10 + 500 + 999
    assert ev.token_usage_run.completion_tokens == 1 + 50 + 9
    assert ev.token_usage_run.total_tokens == 11 + 550 + 1008


def test_extractor_tie_same_step_id_prefers_later(tmp_path: Path) -> None:
    traj = [
        {
            'status': 'completed',
            'trajectory': {
                'task_id': 't1',
                'steps': [
                    {
                        'step_id': 2,
                        'assistant_message': {
                            'meta': {
                                'usage': {
                                    'prompt_tokens': 1,
                                    'completion_tokens': 0,
                                    'total_tokens': 1,
                                }
                            }
                        },
                        'tool_responses': [],
                    },
                    {
                        'step_id': 2,
                        'assistant_message': {
                            'meta': {
                                'usage': {
                                    'prompt_tokens': 2,
                                    'completion_tokens': 0,
                                    'total_tokens': 2,
                                }
                            }
                        },
                        'tool_responses': [],
                    },
                ],
            },
        }
    ]
    p = tmp_path / 'traj.json'
    p.write_text(json.dumps(traj), encoding='utf-8')
    ev = EvidenceExtractor().extract(p, task_id='t1', final_answer='')
    assert ev.token_usage_last_turn.prompt_tokens == 2


def test_extractor_token_usage_last_turn_raw_total_matches_budget_check(
    tmp_path: Path,
) -> None:
    """``token_budget`` uses last turn raw ``total_tokens`` (no cache subtraction)."""
    traj = [
        {
            'status': 'completed',
            'trajectory': {
                'task_id': 't1',
                'steps': [
                    {
                        'step_id': 1,
                        'assistant_message': {
                            'meta': {
                                'usage': {
                                    'prompt_tokens': 1000,
                                    'completion_tokens': 10,
                                    'total_tokens': 5000,
                                    'cache_read_tokens': 4000,
                                }
                            }
                        },
                        'tool_responses': [],
                    },
                    {
                        'step_id': 2,
                        'assistant_message': {
                            'meta': {
                                'usage': {
                                    'prompt_tokens': 100,
                                    'completion_tokens': 5,
                                    'total_tokens': 200,
                                    'cache_read_tokens': 0,
                                }
                            }
                        },
                        'tool_responses': [],
                    },
                ],
            },
        }
    ]
    p = tmp_path / 'traj.json'
    p.write_text(json.dumps(traj), encoding='utf-8')
    ev = EvidenceExtractor().extract(p, task_id='t1', final_answer='')
    assert ev.token_usage_last_turn.total_tokens == 200
    assert ev.token_usage_run.total_tokens_effective == (5000 - 4000) + 200
    ok, reason = check_token_budget(evidence=ev, expected={'max': 250})
    assert ok is True
    assert 'last_turn_total_tokens=200' in reason
    ok2, _ = check_token_budget(evidence=ev, expected={'max': 150})
    assert ok2 is False


def test_build_llm_context_shows_last_turn_prompt_tokens() -> None:
    q = QuestionItem(
        id='Q',
        capability='structure_construction',
        domain='struct',
        intent='test',
        human_prompt_seed='x',
        scoring_checklist=[
            {
                'id': 'x',
                'criterion': 'c',
                'axis': 'correctness',
                'verify': 'llm_binary_judge',
            }
        ],
        reference_answers=[{'key': 'x', 'value': True}],
    )
    ev = EvidenceBundle(
        task_id='t',
        token_usage_last_turn=TokenUsage(prompt_tokens=123, completion_tokens=7),
        total_steps=3,
    )
    ctx = build_llm_context(question=q, answer='a', evidence=ev)
    assert 'Last turn prompt tokens: 123' in ctx
    assert 'completion_tokens=7' in ctx
