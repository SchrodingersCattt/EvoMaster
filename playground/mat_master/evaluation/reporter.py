"""Report writers for MATTER evaluation."""

import json
from pathlib import Path

from .schemas import EvalRunRecord, EvaluationSummary


def write_reports(
    *,
    output_dir: Path,
    records: list[EvalRunRecord],
    summary: EvaluationSummary,
    prefix: str = '',
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_runs_path = output_dir / f"{prefix}raw_runs.jsonl"
    by_question_path = output_dir / f"{prefix}scores_by_question.json"
    by_level_path = output_dir / f"{prefix}scores_by_level.json"
    final_report_path = output_dir / f"{prefix}final_report.md"

    with raw_runs_path.open('w', encoding='utf-8') as handle:
        for record in records:
            handle.write(
                json.dumps(record.model_dump(), ensure_ascii=False, default=str)
            )
            handle.write('\n')

    by_question_path.write_text(
        json.dumps(summary.by_question, ensure_ascii=False, indent=2, default=str),
        encoding='utf-8',
    )
    by_level_path.write_text(
        json.dumps(
            {
                'by_level': summary.by_level,
                'by_mode': summary.by_mode,
                'overall': summary.overall,
                'safety': summary.safety,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding='utf-8',
    )
    final_report_path.write_text(_render_markdown(summary), encoding='utf-8')

    return {
        'raw_runs': str(raw_runs_path),
        'scores_by_question': str(by_question_path),
        'scores_by_level': str(by_level_path),
        'final_report': str(final_report_path),
    }


def append_raw_run(
    *, output_dir: Path, record: EvalRunRecord, filename: str = 'raw_runs.jsonl'
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_runs_path = output_dir / filename
    with raw_runs_path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record.model_dump(), ensure_ascii=False, default=str))
        handle.write('\n')
    return raw_runs_path


def load_records_from_jsonl(path: Path) -> list[EvalRunRecord]:
    records: list[EvalRunRecord] = []
    if not path.exists():
        raise FileNotFoundError(f"raw runs file not found: {path}")
    with path.open('r', encoding='utf-8') as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
                records.append(EvalRunRecord.model_validate(payload))
            except Exception:
                # Allow mid-run rating while the last line may be incomplete.
                continue
    return records


def generate_rating_from_raw_runs(
    *,
    raw_runs_path: Path,
    output_dir: Path | None = None,
    prefix: str = 'interim_',
) -> dict[str, object]:
    from .aggregator import build_summary

    records = load_records_from_jsonl(raw_runs_path)
    if not records:
        raise ValueError(f"no valid records found in {raw_runs_path}")
    summary = build_summary(records)
    target_dir = output_dir or raw_runs_path.parent
    report_paths = write_reports(
        output_dir=target_dir, records=records, summary=summary, prefix=prefix
    )
    return {
        'total_runs': summary.total_runs,
        'overall': summary.overall,
        'report_paths': report_paths,
    }


def _render_markdown(summary: EvaluationSummary) -> str:
    lines: list[str] = [
        '# MATTER Evaluation Report',
        '',
        '## Overall',
        f"- Total runs: {summary.total_runs}",
        f"- Mean score: {summary.overall.get('mean', 0.0):.4f}",
        f"- Std score: {summary.overall.get('std', 0.0):.4f}",
        f"- 95% CI half-width: {summary.overall.get('ci95_half_width', 0.0):.4f}",
        f"- Safety veto rate: {summary.overall.get('safety_veto_rate', 0.0):.4f}",
        '',
        '## By Level',
    ]

    for level, stats in sorted(summary.by_level.items()):
        lines.append(
            f"- `{level}`: n={stats.get('n', 0)}, mean={stats.get('mean', 0.0):.4f}, std={stats.get('std', 0.0):.4f}"
        )

    lines.extend(['', '## By Mode'])
    for mode, stats in sorted(summary.by_mode.items()):
        lines.append(
            f"- `{mode}`: n={stats.get('n', 0)}, mean={stats.get('mean', 0.0):.4f}, std={stats.get('std', 0.0):.4f}"
        )

    lines.extend(['', '## Safety'])
    lines.append(f"- Triggered count: {summary.safety.get('triggered_count', 0)}")
    lines.append(f"- Triggered rate: {summary.safety.get('triggered_rate', 0.0):.4f}")
    lines.append(f"- Any triggered: {summary.safety.get('any_triggered', False)}")

    lines.extend(['', '## Per Question (mode split)'])
    for key, row in sorted(summary.by_question.items()):
        lines.append(
            f"- `{key}`: n={row.get('n', 0)}, mean={row.get('mean', 0.0):.4f}, std={row.get('std', 0.0):.4f}, safety_veto_count={row.get('safety_veto_count', 0)}"
        )
    lines.append('')
    return '\n'.join(lines)
