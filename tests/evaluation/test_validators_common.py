"""Tests for the shared validator helpers in evaluation.validators._common."""

from __future__ import annotations

import os
from pathlib import Path

from evaluation.validators._common import (
    collect_positive_ids,
    is_identifier_key,
    positive_int,
    resolve_file,
)


def test_resolve_file_exact_relative_path(tmp_path: Path) -> None:
    target = tmp_path / 'sub' / 'record.json'
    target.parent.mkdir()
    target.write_text('{}', encoding='utf-8')

    assert resolve_file(tmp_path, 'sub/record.json') == target


def test_resolve_file_finds_nested_basename(tmp_path: Path) -> None:
    target = tmp_path / 'results' / 'record.json'
    target.parent.mkdir()
    target.write_text('{}', encoding='utf-8')

    assert resolve_file(tmp_path, 'record.json') == target


def test_resolve_file_glob_pattern_finds_nested_file(tmp_path: Path) -> None:
    target = tmp_path / 'results' / 'structure.cif'
    target.parent.mkdir()
    target.write_text('data_x', encoding='utf-8')

    assert resolve_file(tmp_path, '*.cif') == target


def test_resolve_file_newest_match_wins(tmp_path: Path) -> None:
    old = tmp_path / 'backup' / 'record.json'
    old.parent.mkdir()
    old.write_text('{}', encoding='utf-8')
    new = tmp_path / 'results' / 'record.json'
    new.parent.mkdir()
    new.write_text('{}', encoding='utf-8')
    os.utime(old, (1_000_000_000, 1_000_000_000))
    os.utime(new, (2_000_000_000, 2_000_000_000))

    assert resolve_file(tmp_path, 'record.json') == new


def test_resolve_file_missing_returns_none(tmp_path: Path) -> None:
    assert resolve_file(tmp_path, 'record.json') is None


def test_resolve_file_root_mode_ignores_nested_files(tmp_path: Path) -> None:
    nested = tmp_path / 'sub' / 'record.json'
    nested.parent.mkdir()
    nested.write_text('{}', encoding='utf-8')

    assert resolve_file(tmp_path, 'record.json', workspace_resolve='root') is None
    assert resolve_file(tmp_path, 'sub/record.json', workspace_resolve='root') is None

    direct = tmp_path / 'record.json'
    direct.write_text('{}', encoding='utf-8')
    assert resolve_file(tmp_path, 'record.json', workspace_resolve='root') == direct


def test_identifier_keys_accept_natural_namings() -> None:
    assert is_identifier_key('bohr_id')
    assert is_identifier_key('jobId')
    assert is_identifier_key('resubmitted_job_id')
    assert is_identifier_key('task_identifier')
    assert not is_identifier_key('group_id')
    assert not is_identifier_key('job_group_id')
    assert not is_identifier_key('temperature_K')


def test_collect_positive_ids_walks_nested_values() -> None:
    data = {
        'task': {'bohr_id': '20400001', 'group_id': 99},
        'history': [{'jobIds': [1, 2]}, {'note': 'no ids here'}],
        'flags': {'ok': True},
    }

    assert collect_positive_ids(data) == {20400001, 1, 2}


def test_positive_int_rejects_bools_and_non_digits() -> None:
    assert positive_int(True) is None
    assert positive_int(0) is None
    assert positive_int('-3') is None
    assert positive_int('12') == 12
