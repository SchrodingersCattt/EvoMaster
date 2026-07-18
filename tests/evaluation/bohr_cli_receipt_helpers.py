"""Shared factory for Bohr-CLI receipt records used across bohr test modules."""

from __future__ import annotations

from evaluation.core.evidence import BohrCliReceiptRecord


def make_receipt(**values) -> BohrCliReceiptRecord:
    return BohrCliReceiptRecord.model_validate(
        {
            'schema_version': 'bohr_cli_receipt_v1',
            'exit_code': 0,
            'ok': True,
            **values,
        }
    )
