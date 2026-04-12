"""Two-letter filename prefixes for question bank YAML (per capability)."""

from __future__ import annotations

# Stable, unique 2-letter codes for manifest / filesystem names (see AGENTS_evaluation.md).
CAPABILITY_TO_TWO_LETTER: dict[str, str] = {
    'batch_processing': 'bp',
    'data_diagnosis': 'dd',
    'execution_contract': 'ec',
    'input_generation': 'ig',
    'safety_refusal': 'sf',
    'scientific_analysis': 'sa',
    'structure_construction': 'sc',
    'structure_retrieval': 'rt',
    'workflow_orchestration': 'wo',
}

TWO_LETTER_TO_CAPABILITY: dict[str, str] = {
    v: k for k, v in CAPABILITY_TO_TWO_LETTER.items()
}


def bank_yaml_basename(*, capability: str, domain: str) -> str:
    """Return ``{xx}_{domain}.yaml`` for use under ``question_bank/<capability>/``."""
    try:
        prefix = CAPABILITY_TO_TWO_LETTER[capability]
    except KeyError as e:
        raise ValueError(f'unknown capability {capability!r}') from e
    return f'{prefix}_{domain}.yaml'
