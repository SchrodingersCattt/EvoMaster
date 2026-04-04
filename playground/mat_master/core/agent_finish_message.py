"""Finish message normalisation, file URI prefixing, and finish report upload."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .execution_journal import ExecutionJournal


def extract_json_from_reply(content: str) -> str | None:
    """Extract JSON object from LLM reply (raw JSON or ```json ... ```)."""
    text = (content or '').strip()
    if '```json' in text:
        start = text.find('```json') + 7
        end = text.find('```', start)
        if end > start:
            return text[start:end].strip()
    if '```' in text:
        start = text.find('```') + 3
        end = text.find('```', start)
        if end > start:
            return text[start:end].strip()
    start = text.find('{')
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def add_file_uri_prefix(text: str, workspace_path: str = '') -> str:
    """Convert local paths to file:// URIs in non-code text."""
    _SCHEME_OR_ANCHOR = re.compile(r'^(?:https?|ftp|file|mailto)://|^#', re.IGNORECASE)
    _WIN_ABS = re.compile(r'^[A-Za-z]:[/\\]')

    def _win_path_to_uri(path: str) -> str:
        return 'file:///' + path.replace('\\', '/')

    parts = re.split(r'(```[\s\S]*?```|`[^`\n]+`)', text)
    result: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            result.append(part)
            continue
        stashed: list[str] = []

        def _stash(m: re.Match, _s: list[str] = stashed) -> str:
            _s.append(m.group(0))
            return f'\x00URL{len(_s) - 1:04d}\x00'

        proc = re.sub(r'(?:https?|ftp|file)://[^\s\)\]\,;"\'<>]+', _stash, part)
        proc = re.sub(
            r'(?<![a-zA-Z0-9_.:-])(/[^\s\)\]\,;"\'<>*#]+)',
            r'file://\1',
            proc,
        )
        proc = re.sub(
            r'(?<![a-zA-Z0-9_.])([A-Za-z]:[/\\][^\s\)\]\,;"\'<>*#]+)',
            lambda m: _win_path_to_uri(m.group(1)),
            proc,
        )

        def _fix_md_link(m: re.Match) -> str:
            link_text, target = m.group(1), m.group(2)
            if (
                '\x00' in target
                or _SCHEME_OR_ANCHOR.match(target)
                or target.startswith('/')
                or _WIN_ABS.match(target)
            ):
                return m.group(0)
            if workspace_path:
                ws = workspace_path.rstrip('/').rstrip('\\').replace('\\', '/')
                prefix = 'file:///' if re.match(r'^[A-Za-z]:/', ws) else 'file://'
                return f'[{link_text}]({prefix}{ws}/{target})'
            return m.group(0)

        proc = re.sub(r'\[([^\]]*)\]\(([^)\s]+)\)', _fix_md_link, proc)
        for idx, url in enumerate(stashed):
            proc = proc.replace(f'\x00URL{idx:04d}\x00', url)
        result.append(proc)
    return ''.join(result)


def normalise_finish_message(raw: str, workspace_abs: str) -> str:
    """Normalize finish message: literal \\n, URL line breaks, file:// URIs."""
    if not raw:
        return raw
    text = raw.strip()
    text = text.replace('\\n', '\n').replace('\\r', '\r')
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('http://') or stripped.startswith('https://'):
            if out and out[-1].strip():
                out.append('')
            out.append(line)
        else:
            out.append(line)
    return add_file_uri_prefix('\n'.join(out), workspace_path=workspace_abs)


def generate_finish_report(
    logger: logging.Logger,
    execution_journal: ExecutionJournal,
    finish_message: str,
    task_completed: str,
    workspace_path: str,
) -> tuple[str | None, str]:
    """Upload finish report to OSS; return (report_url, normalised_message)."""
    workspace_abs = str(Path(workspace_path).absolute()) if workspace_path else ''
    normalised = normalise_finish_message(finish_message or '', workspace_abs)

    if '## Execution Details' not in normalised and execution_journal.entries:
        details_md = execution_journal.get_execution_details_md()
        if details_md:
            normalised = normalised.rstrip() + '\n\n' + details_md

    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    header = (
        '# Task Finish Report\n\n'
        f'**Generated**: {now_str}  \n'
        f'**Status**: `{task_completed}`\n\n---\n\n'
    )
    md_content = header + (normalised or '*(no message)*')

    tmp_path = None
    try:
        from evomaster.adaptors.calculation.oss_io import upload_file_to_oss  # noqa: PLC0415

        fd, tmp_path = tempfile.mkstemp(suffix='_finish_report.md')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(md_content)
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            raise

        report_url = upload_file_to_oss(
            Path(tmp_path),
            workspace_root=Path(tmp_path).parent,
            oss_prefix='matmaster_evo/finish_reports',
        )
        logger.info('Finish report uploaded: %s', report_url)
        return report_url, normalised
    except Exception as e:
        logger.warning('generate_finish_report: OSS upload failed: %s', e)
        return None, normalised
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
