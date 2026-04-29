from __future__ import annotations

import os
import zipfile
from pathlib import Path

from matmaster_bohrium_transfer.archive import create_zip_store, directory_fingerprint


def test_create_zip_store_uses_no_compression_and_allows_empty_dir(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    archive = tmp_path / "input.zip"

    result = create_zip_store(input_dir, archive)

    assert result.archive_path == archive
    with zipfile.ZipFile(archive) as zf:
        assert zf.namelist() == []
        assert zf.comment == b""


def test_create_zip_store_preserves_non_ascii_names_and_stored_method(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "结构.in").write_text("data", encoding="utf-8")
    archive = tmp_path / "input.zip"

    create_zip_store(input_dir, archive)

    with zipfile.ZipFile(archive) as zf:
        info = zf.getinfo("结构.in")
        assert info.compress_type == zipfile.ZIP_STORED
        assert zf.read("结构.in") == b"data"


def test_directory_fingerprint_changes_when_file_mtime_changes(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    file_path = input_dir / "a.txt"
    file_path.write_text("aa", encoding="utf-8")
    first = directory_fingerprint(input_dir)

    file_path.write_text("bb", encoding="utf-8")
    second = directory_fingerprint(input_dir)

    assert first != second


def test_directory_fingerprint_changes_when_same_size_content_changes_with_same_mtime(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    file_path = input_dir / "a.txt"
    fixed_mtime_ns = 1_700_000_000_000_000_000

    file_path.write_text("aa", encoding="utf-8")
    os.utime(file_path, ns=(fixed_mtime_ns, fixed_mtime_ns))
    first = directory_fingerprint(input_dir)

    file_path.write_text("bb", encoding="utf-8")
    os.utime(file_path, ns=(fixed_mtime_ns, fixed_mtime_ns))
    second = directory_fingerprint(input_dir)

    assert first != second
