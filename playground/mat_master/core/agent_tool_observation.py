"""Tool observation formatting, auto-save, compact/summarize for MatMasterAgent."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any


def format_bash_observation(observation: str, info: dict[str, Any]) -> dict[str, Any]:
    """Build structured JSON object for ``execute_bash`` results."""
    exit_code = info.get('exit_code', -1)
    has_error = 'error' in info
    if has_error:
        status = 'error'
    elif exit_code != 0 and exit_code != -1:
        status = 'error'
    else:
        status = 'success'
    return {
        'status': status,
        'output': observation,
        'exit_code': exit_code,
        'working_dir': info.get('working_dir', ''),
    }


def to_json_value(value: Any) -> Any:
    """Convert observation payload to a JSON-compatible value when possible."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return ''
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def format_tool_observation(
    logger: logging.Logger,
    tool_name: str,
    observation: Any,
    info: dict[str, Any],
) -> str:
    """Return JSON text for every tool observation."""
    obs_type = type(observation).__name__
    if isinstance(observation, dict):
        logger.debug(
            '[observation] before _format_tool_observation tool=%s type=%s keys=%s',
            tool_name,
            obs_type,
            list(observation.keys())[:8],
        )
    elif isinstance(observation, str):
        logger.debug(
            '[observation] before _format_tool_observation tool=%s type=%s len=%s head=%s',
            tool_name,
            obs_type,
            len(observation),
            (observation[:80] + '...') if len(observation) > 80 else observation,
        )
    else:
        logger.debug(
            '[observation] before _format_tool_observation tool=%s type=%s',
            tool_name,
            obs_type,
        )
    if tool_name == 'execute_bash':
        obs_str = observation if isinstance(observation, str) else str(observation)
        payload = format_bash_observation(obs_str, info)
    else:
        status = 'error' if 'error' in info else 'success'
        if (
            tool_name == 'use_skill'
            and info.get('action') == 'run_script'
            and isinstance(info.get('exit_code'), int)
            and info['exit_code'] != 0
        ):
            status = 'error'
        payload = {
            'status': status,
            'observation': to_json_value(observation),
        }
        if info:
            payload['info'] = info
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def auto_save_tool_output(
    logger: logging.Logger,
    session: Any,
    tool_name: str,
    observation: Any,
    *,
    save_patterns: list[str],
    save_counter: int,
    step_count: int,
) -> tuple[str | None, int]:
    """Write matching tool observations to ``_tmp/tool_outputs/<tool_name>/``.

    Returns ``(remote_path or None, new_counter)``. Counter increments once per
    attempt, matching MatMasterAgent behavior.
    """
    if observation is None:
        return None, save_counter
    if not any(tool_name.startswith(p) for p in save_patterns):
        return None, save_counter
    workspace = getattr(session.config, 'workspace_path', '') or getattr(
        session, 'working_dir', ''
    )
    if not workspace:
        return None, save_counter
    save_counter = save_counter + 1
    try:
        base = workspace.rstrip('/')
        safe_name = re.sub(r'[^\w\-.]', '_', tool_name)
        suffix = uuid.uuid4().hex[:8]
        if isinstance(observation, str):
            stripped = observation.strip()
            ext = (
                '.json'
                if stripped.startswith('{') or stripped.startswith('[')
                else '.txt'
            )
            payload = observation
        else:
            ext = '.json'
            payload = json.dumps(observation, ensure_ascii=False, indent=2, default=str)
        rel = f'_tmp/tool_outputs/{safe_name}/step_{step_count}_{suffix}{ext}'
        remote_path = f'{base}/{rel}'
        session.write_file(remote_path, payload, encoding='utf-8')
        logger.info('Auto-saved tool output to %s', remote_path)
        return remote_path, save_counter
    except Exception as e:
        logger.warning('Auto-save tool output failed: %s', e)
        return None, save_counter


def parse_tool_observation_to_dict(observation: Any) -> dict | None:
    """Parse tool observation to a dict if possible (strip auto-saved suffix from str)."""
    if isinstance(observation, dict):
        return observation
    if not isinstance(observation, str):
        return None
    stripped = observation.strip()
    stripped = re.sub(r'\n\n\[Auto-saved to:[^\]]*\]$', '', stripped).strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {'code': 0, 'message': 'success', 'data': parsed}
        return None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def compact_mat_sn_papers_observation(
    tool_name: str,
    observation: Any,
    saved_path: str | None,
) -> dict | None:
    """After disk save, shrink mat_sn paper search JSON for LLM context."""
    if not saved_path or not tool_name.startswith('mat_sn_search-papers'):
        return None
    payload = parse_tool_observation_to_dict(observation)
    if payload is None:
        return None
    code = payload.get('code')
    if code is not None and code != 0:
        return None

    papers: list = []
    if isinstance(payload.get('data'), list):
        papers = payload['data']
    elif isinstance(payload.get('papers'), list):
        papers = payload['papers']
    elif isinstance(payload.get('results'), list):
        papers = payload['results']
    elif isinstance(payload.get('items'), list):
        papers = payload['items']
    else:
        for v in payload.values():
            if isinstance(v, list) and v:
                papers = v
                break

    preview_cap = 5
    preview: list[dict[str, Any]] = []
    for p in papers[:preview_cap]:
        if not isinstance(p, dict):
            preview.append({'snippet': str(p)[:200]})
            continue
        preview.append(
            {
                'title': (p.get('enName') or p.get('title') or p.get('Title') or ''),
                'doi': p.get('doi') or p.get('DOI') or '',
                'paperUrl': p.get('paperUrl') or p.get('url') or '',
                'score': p.get('score', ''),
            }
        )

    return {
        'code': 0 if code is None else code,
        'message': payload.get('message', 'success'),
        'data_count': len(papers),
        'preview': preview,
        'full_result_path': saved_path,
        '_note': (
            'Full JSON (including abstracts) is saved at full_result_path. '
            'Read that file via str_replace_editor view, execute_bash, or skills '
            '(collect_evidence, build_lit_table); do not assume full text is in chat.'
        ),
    }


def summarize_large_tool_observation(
    tool_name: str,
    observation: Any,
    saved_path: str | None,
    *,
    summarize_patterns: list[str],
    summarize_threshold: int,
) -> str | None:
    """Replace oversized string observations with a short summary + path note."""
    if not isinstance(observation, str):
        return None
    if len(observation) <= summarize_threshold:
        return None
    if not any(tool_name.startswith(p) for p in summarize_patterns):
        return None

    path_note = f'\n\n[Full result saved to: {saved_path}]' if saved_path else ''

    payload: dict | list | None = None
    try:
        stripped = observation.strip()
        _obs_clean = re.sub(r'\n\n\[Auto-saved to:[^\]]*\]$', '', stripped).strip()
        payload = json.loads(_obs_clean)
    except Exception:
        pass

    if tool_name.startswith('mat_sn_search-papers'):
        papers: list[dict] = []
        if isinstance(payload, dict):
            for key in ('papers', 'results', 'data', 'items'):
                if isinstance(payload.get(key), list):
                    papers = payload[key]
                    break
            if not papers and isinstance(payload.get('total'), int):
                for v in payload.values():
                    if isinstance(v, list) and v:
                        papers = v
                        break
        elif isinstance(payload, list):
            papers = payload

        if papers:
            total = len(papers)
            top_n = min(5, total)
            lines = [
                f'[Tool: {tool_name}] Returned {total} papers. Top {top_n} titles (no abstracts).',
                '',
            ]
            for i, p in enumerate(papers[:top_n], 1):
                if not isinstance(p, dict):
                    lines.append(f'{i}. {str(p)[:120]}')
                    continue
                title = (
                    p.get('enName') or p.get('title') or p.get('Title') or '(no title)'
                )
                doi = p.get('doi') or p.get('DOI') or p.get('url') or ''
                year = p.get('year') or p.get('Year') or p.get('published_year') or ''
                score = (
                    p.get('score')
                    or p.get('relevance_score')
                    or p.get('similarity')
                    or ''
                )
                parts = [f'{i}. {title}']
                if doi:
                    parts.append(f'   DOI/URL: {doi}')
                meta = []
                if year:
                    meta.append(f'year={year}')
                if score:
                    meta.append(f'score={score}')
                if meta:
                    parts.append(f'   {", ".join(meta)}')
                lines.extend(parts)
                lines.append('')
            if total > top_n:
                lines.append(f'… and {total - top_n} more papers.{path_note}')
            else:
                lines.append(path_note.strip() if path_note else '')
            return '\n'.join(lines).rstrip()

    if tool_name.startswith('mat_sg_'):
        structures: list[dict] = []
        if isinstance(payload, dict):
            for key in ('structures', 'results', 'data', 'items'):
                if isinstance(payload.get(key), list):
                    structures = payload[key]
                    break
            if not structures:
                structures = [payload]
        elif isinstance(payload, list):
            structures = payload

        if structures:
            total = len(structures)
            top_n = min(20, total)
            lines = [
                f'[Tool: {tool_name}] Returned {total} structure(s). Top {top_n} shown:',
                '',
            ]
            for i, s in enumerate(structures[:top_n], 1):
                if not isinstance(s, dict):
                    lines.append(f'{i}. {str(s)[:120]}')
                    continue
                formula = (
                    s.get('formula')
                    or s.get('Formula')
                    or s.get('reduced_formula')
                    or s.get('pretty_formula')
                    or '?'
                )
                sg = (
                    s.get('space_group')
                    or s.get('spacegroup')
                    or s.get('space_group_symbol')
                    or s.get('sg')
                    or '?'
                )
                e_hull = (
                    s.get('energy_above_hull')
                    or s.get('e_above_hull')
                    or s.get('stability')
                )
                mat_id = s.get('material_id') or s.get('id') or s.get('mp_id') or ''
                parts = [f'{i}. {formula}  SG={sg}']
                if e_hull is not None:
                    parts[0] += f'  e_above_hull={e_hull}'
                if mat_id:
                    parts[0] += f'  id={mat_id}'
                lines.extend(parts)
            if total > top_n:
                lines.append(f'… and {total - top_n} more.{path_note}')
            else:
                lines.append(path_note.strip() if path_note else '')
            return '\n'.join(lines).rstrip()

    items: list = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ('results', 'data', 'items', 'papers', 'structures'):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break

    total_items = len(items) if items else None
    preview_lines: list[str] = []
    for item in items[:5]:
        preview_lines.append(f'  - {str(item)[:150]}')

    summary_parts = [
        f'[Tool: {tool_name}] Large observation ({len(observation):,} chars).'
    ]
    if total_items is not None:
        summary_parts.append(f'Total items: {total_items}.')
    if preview_lines:
        summary_parts.append('Preview (first 5):')
        summary_parts.extend(preview_lines)
    if path_note:
        summary_parts.append(path_note.strip())
    return '\n'.join(summary_parts)
