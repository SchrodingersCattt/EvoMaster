#!/usr/bin/env python3
"""
Build a canonical NotebookLM-style evidence table from structured literature data.

This script merges structured JSON outputs from PDF extraction and web extraction
pipelines into one canonical row schema and exports CSV or JSONL.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '_common'))
from build_lit_table_dedup import annotate_conflicts, deduplicate_rows  # noqa: E402
from build_lit_table_export import write_output  # noqa: E402
from build_lit_table_ingest import ingest_and_cache_normalized_rows  # noqa: E402
from build_lit_table_io import (  # noqa: E402
    auto_discover_tool_outputs,
    load_rows,
    load_schema,
    resolve_input_paths,
    rows_file_for,
    save_rows,
)
from build_lit_table_survey import is_survey_input_from_metadata  # noqa: E402
from longtask_runtime import (  # noqa: E402
    STATUS_COMPLETED,
    STATUS_FATAL_ERROR,
    STATUS_RETRYABLE_ERROR,
    append_event,
    build_result,
    emit_result,
    init_or_load_state,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Merge structured literature JSON files into one canonical evidence table '
            'and export CSV or JSONL.'
        )
    )
    parser.add_argument(
        '--input_json',
        nargs='*',
        default=[],
        help='Input JSON files (space-separated).',
    )
    parser.add_argument(
        '--input_dir',
        help='Directory containing JSON files to ingest.',
    )
    parser.add_argument(
        '--source_type',
        choices=['auto', 'pdf', 'web', 'survey'],
        default='auto',
        help="Source type for ingested records. Use 'survey' when input is survey contract JSON (or rely on source_kind in JSON).",
    )
    parser.add_argument(
        '--schema',
        help='Optional schema JSON file for alias/default overrides.',
    )
    parser.add_argument(
        '--dedup_keys',
        default='source_url_or_path,quote_text,property_name,property_value',
        help='Comma-separated canonical keys used for deduplication.',
    )
    parser.add_argument(
        '--output',
        default=None,
        help='Output file path.',
    )
    parser.add_argument(
        '--format',
        choices=['csv', 'jsonl'],
        default=None,
        help='Output format.',
    )
    parser.add_argument(
        '--stage',
        choices=[
            'all',
            'ingest',
            'normalize',
            'template',
            'fill',
            'enrich',
            'dedup',
            'conflict',
            'export',
            'status',
        ],
        default='all',
        help='Run a specific stage or the full pipeline (default: all).',
    )
    parser.add_argument(
        '--enrich_rows',
        default=None,
        help='Path to agent-generated enrich_rows.json to load before dedup (overrides state[enrich_rows_file]).',
    )
    parser.add_argument(
        '--state',
        default='_tmp/lit_data/state.json',
        help='State file path for resumable workflow.',
    )
    parser.add_argument(
        '--resume',
        action='store_true',
        help='Resume using existing state/caches when available.',
    )
    args = parser.parse_args()
    if not args.input_json and not args.input_dir:
        parser.error('At least one of --input_json or --input_dir is required.')
    if args.stage in {'all', 'export'} and (not args.output or not args.format):
        parser.error("--output and --format are required for stage 'all' and 'export'.")
    return args


def main() -> None:
    try:
        args = parse_args()
        state_path = Path(args.state)
        events_path = state_path.parent / 'events.jsonl'
        result_path = state_path.parent / 'result.json'
        state = init_or_load_state(
            state_path=state_path,
            task_type='lit_data_table',
            stage=args.stage,
            resume=args.resume,
            extra={
                'dedup_keys': [
                    k.strip() for k in args.dedup_keys.split(',') if k.strip()
                ],
                'source_type': args.source_type,
            },
        )

        if args.stage == 'status':
            emit_result(
                build_result(
                    status=STATUS_COMPLETED,
                    stage='status',
                    message='Loaded workflow state.',
                    result_path=result_path,
                    payload={'state': state},
                )
            )
            return

        schema_cfg = load_schema(args.schema)
        input_paths = resolve_input_paths(args.input_json, args.input_dir)
        dedup_keys = [k.strip() for k in args.dedup_keys.split(',') if k.strip()]

        normalized_rows: list[dict[str, str]]
        raw_count = 0
        normalized_path = rows_file_for(state_path, 'normalized')
        if args.stage in {'all', 'ingest', 'normalize'}:
            normalized_rows, state = ingest_and_cache_normalized_rows(
                input_paths=input_paths,
                source_type=args.source_type,
                schema_cfg=schema_cfg,
                state=state,
                state_path=state_path,
                resume=args.resume,
            )
            raw_count = int(state.get('ingest_stats', {}).get('raw_records', 0) or 0)

            if len(normalized_rows) == 0:
                fallback_paths = auto_discover_tool_outputs(input_paths)
                if fallback_paths:
                    print(
                        json.dumps(
                            {
                                'info': 'Primary inputs yielded 0 records; auto-discovering raw tool outputs.',
                                'fallback_files_found': len(fallback_paths),
                            },
                            ensure_ascii=False,
                        )
                    )
                    fallback_rows, state = ingest_and_cache_normalized_rows(
                        input_paths=fallback_paths,
                        source_type=args.source_type,
                        schema_cfg=schema_cfg,
                        state=state,
                        state_path=state_path,
                        resume=False,
                    )
                    if fallback_rows:
                        normalized_rows = fallback_rows
                        raw_count = int(
                            state.get('ingest_stats', {}).get('raw_records', 0) or 0
                        )
                        input_paths = fallback_paths
            elif (
                is_survey_input_from_metadata(input_paths)
                or args.source_type == 'survey'
            ):
                supplement_paths = auto_discover_tool_outputs(input_paths)
                existing_resolved = {str(p.resolve()) for p in input_paths}
                extra = [
                    p
                    for p in supplement_paths
                    if str(p.resolve()) not in existing_resolved
                ]
                if extra:
                    print(
                        json.dumps(
                            {
                                'info': 'Survey-only input; supplementing with tool_outputs.',
                                'supplement_files_found': len(extra),
                            },
                            ensure_ascii=False,
                        )
                    )
                    supplement_rows, state = ingest_and_cache_normalized_rows(
                        input_paths=extra,
                        source_type=args.source_type,
                        schema_cfg=schema_cfg,
                        state=state,
                        state_path=state_path,
                        resume=False,
                    )
                    if supplement_rows:
                        seen_dedup = set()
                        merged = []
                        keys_use = dedup_keys or ['source_url_or_path', 'quote_text']
                        for row in normalized_rows + supplement_rows:
                            key = tuple(row.get(k) for k in keys_use)
                            if key in seen_dedup:
                                continue
                            seen_dedup.add(key)
                            merged.append(row)
                        normalized_rows = merged
                        raw_count = len(normalized_rows)
                        state.setdefault('ingest_stats', {})['raw_records'] = raw_count
                        state.setdefault('ingest_stats', {})[
                            'normalized_records'
                        ] = raw_count

            save_rows(normalized_path, normalized_rows)
            state['normalized_rows_file'] = str(normalized_path)
            write_json(state_path, state)
            append_event(
                events_path=events_path,
                status=STATUS_COMPLETED,
                stage='normalize',
                message='Normalized rows prepared.',
                payload=state.get('ingest_stats', {}),
            )
            if args.stage in {'ingest', 'normalize'}:
                emit_result(
                    build_result(
                        status=STATUS_COMPLETED,
                        stage=args.stage,
                        message=f"Stage '{args.stage}' completed.",
                        result_path=result_path,
                        payload={
                            'input_files': [str(p) for p in input_paths],
                            'normalized_rows': len(normalized_rows),
                            'normalized_rows_file': str(normalized_path),
                        },
                    )
                )
                return
        else:
            if not normalized_path.exists():
                emit_result(
                    build_result(
                        status=STATUS_RETRYABLE_ERROR,
                        stage=args.stage,
                        message='Missing normalized stage data. Run --stage normalize first or use --stage all.',
                        result_path=result_path,
                    )
                )
                return
            normalized_rows = load_rows(normalized_path)

        rows_for_dedup: list[dict[str, str]] = normalized_rows
        enrich_rows_file = args.enrich_rows or state.get('enrich_rows_file')
        if enrich_rows_file:
            enrich_path = Path(enrich_rows_file)
            if enrich_path.exists():
                enriched_rows = load_rows(enrich_path)
                rows_for_dedup = [
                    r for r in enriched_rows if r.get('enrich_keep') != 'false'
                ]
                state['enrich_rows_file'] = str(enrich_path)
                write_json(state_path, state)

        deduped_path = rows_file_for(state_path, 'deduped')
        deduped_rows: list[dict[str, str]]
        dropped = 0
        if args.stage in {'all', 'dedup'}:
            deduped_rows, dropped = deduplicate_rows(rows_for_dedup, dedup_keys)
            save_rows(deduped_path, deduped_rows)
            state['deduped_rows_file'] = str(deduped_path)
            state['dedup_stats'] = {
                'deduplicated_records': len(deduped_rows),
                'dropped_duplicates': dropped,
            }
            write_json(state_path, state)
            append_event(
                events_path=events_path,
                status=STATUS_COMPLETED,
                stage='dedup',
                message='Dedup stage completed.',
                payload=state['dedup_stats'],
            )
            if args.stage == 'dedup':
                emit_result(
                    build_result(
                        status=STATUS_COMPLETED,
                        stage='dedup',
                        message="Stage 'dedup' completed.",
                        result_path=result_path,
                        payload=state['dedup_stats'],
                    )
                )
                return
        else:
            if not deduped_path.exists():
                emit_result(
                    build_result(
                        status=STATUS_RETRYABLE_ERROR,
                        stage=args.stage,
                        message='Missing dedup stage data. Run --stage dedup first or use --stage all.',
                        result_path=result_path,
                    )
                )
                return
            deduped_rows = load_rows(deduped_path)

        conflict_path = rows_file_for(state_path, 'conflicts')
        conflicts = 0
        if args.stage in {'all', 'conflict'}:
            conflicts = annotate_conflicts(deduped_rows)
            save_rows(conflict_path, deduped_rows)
            state['conflict_rows_file'] = str(conflict_path)
            state['conflict_stats'] = {'conflict_groups': conflicts}
            write_json(state_path, state)
            append_event(
                events_path=events_path,
                status=STATUS_COMPLETED,
                stage='conflict',
                message='Conflict annotation completed.',
                payload=state['conflict_stats'],
            )
            if args.stage == 'conflict':
                emit_result(
                    build_result(
                        status=STATUS_COMPLETED,
                        stage='conflict',
                        message="Stage 'conflict' completed.",
                        result_path=result_path,
                        payload=state['conflict_stats'],
                    )
                )
                return
        else:
            if not conflict_path.exists():
                emit_result(
                    build_result(
                        status=STATUS_RETRYABLE_ERROR,
                        stage=args.stage,
                        message='Missing conflict stage data. Run --stage conflict first or use --stage all.',
                        result_path=result_path,
                    )
                )
                return
            deduped_rows = load_rows(conflict_path)

        output_path = Path(args.output) if args.output else None
        if args.stage in {'all', 'export'}:
            if output_path is None or not args.format:
                emit_result(
                    build_result(
                        status=STATUS_FATAL_ERROR,
                        stage='export',
                        message='Export requires --output and --format.',
                        result_path=result_path,
                    )
                )
                sys.exit(1)
            write_output(deduped_rows, output_path, args.format)
            state['output_file'] = str(output_path)
            state['output_format'] = args.format
            write_json(state_path, state)

        summary = {
            'status': 'ok',
            'table_name': 'lit_evidence_table',
            'input_files': [str(p) for p in input_paths],
            'raw_records': raw_count
            or int(state.get('ingest_stats', {}).get('raw_records', 0) or 0),
            'normalized_records': int(
                state.get('ingest_stats', {}).get(
                    'normalized_records', len(normalized_rows)
                )
            ),
            'deduplicated_records': len(deduped_rows),
            'dropped_duplicates': dropped
            or int(state.get('dedup_stats', {}).get('dropped_duplicates', 0) or 0),
            'conflict_groups': conflicts
            or int(state.get('conflict_stats', {}).get('conflict_groups', 0) or 0),
            'output_file': str(output_path) if output_path else '',
            'output_format': args.format or '',
            'dedup_keys': dedup_keys,
            'stage': args.stage,
            'state_file': str(state_path),
        }
        append_event(
            events_path=events_path,
            status=STATUS_COMPLETED,
            stage=args.stage,
            message=f"Stage '{args.stage}' completed.",
            payload=summary,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        emit_result(
            build_result(
                status=STATUS_COMPLETED,
                stage=args.stage,
                message=f"Stage '{args.stage}' completed.",
                result_path=result_path,
                payload=summary,
            )
        )
    except Exception as exc:
        error = {'status': 'error', 'message': str(exc)}
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        emit_result(
            build_result(
                status=STATUS_FATAL_ERROR,
                stage='build_lit_table',
                message=f"Fatal error: {exc}",
            )
        )
        sys.exit(1)


if __name__ == '__main__':
    main()
