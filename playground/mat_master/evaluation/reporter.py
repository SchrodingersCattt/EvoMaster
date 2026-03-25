"""Report writers for MATTER evaluation."""

import json
from pathlib import Path
from typing import Any

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
    by_model_path = output_dir / f"{prefix}scores_by_model.json"
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
    by_model_path.write_text(
        json.dumps(summary.by_model, ensure_ascii=False, indent=2, default=str),
        encoding='utf-8',
    )
    final_report_path.write_text(_render_markdown(summary), encoding='utf-8')

    return {
        'raw_runs': str(raw_runs_path),
        'scores_by_question': str(by_question_path),
        'scores_by_level': str(by_level_path),
        'scores_by_model': str(by_model_path),
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


# ---------------------------------------------------------------------------
# Markdown rendering helpers
# ---------------------------------------------------------------------------

def _fmt(value: Any, precision: int = 4) -> str:
    """Format a numeric value or return 'N/A' for None."""
    if value is None:
        return 'N/A'
    try:
        return f"{float(value):.{precision}f}"
    except (TypeError, ValueError):
        return str(value)


def _render_markdown(summary: EvaluationSummary) -> str:
    lines: list[str] = [
        '# MATTER Evaluation Report',
        '',
        '## Overall',
        f"- Total runs: {summary.total_runs}",
        f"- Mean band score: {_fmt(summary.overall.get('mean'))}",
        f"- Std: {_fmt(summary.overall.get('std'))}",
        f"- 95% CI half-width: {_fmt(summary.overall.get('ci95_half_width'))}",
        f"- Safety veto rate: {_fmt(summary.overall.get('safety_veto_rate'))}",
        '',
        '### Three-Dimensional Scores (overall)',
        '| Dimension | Mean | Std |',
        '|-----------|------|-----|',
        f"| accuracy  | {_fmt(summary.overall.get('accuracy_mean'))} | "
        f"{_fmt(summary.overall.get('accuracy_std'))} |",
        f"| grounding | {_fmt(summary.overall.get('grounding_mean'))} | "
        f"{_fmt(summary.overall.get('grounding_std'))} |",
        f"| efficiency | {_fmt(summary.overall.get('efficiency_mean'))} | "
        f"{_fmt(summary.overall.get('efficiency_std'))} |",
        f"| **strict_final** | {_fmt(summary.overall.get('strict_final_mean'))} | — |",
        f"| **analysis_final** | {_fmt(summary.overall.get('analysis_final_mean'))} | — |",
        '',
        '## By Level',
        '| Level | n | mean | accuracy | grounding | efficiency | strict_final | analysis_final |',
        '|-------|---|------|----------|-----------|------------|--------------|----------------|',
    ]

    for level, stats in sorted(summary.by_level.items()):
        lines.append(
            f"| `{level}` | {stats.get('n', 0)} "
            f"| {_fmt(stats.get('mean'))} "
            f"| {_fmt(stats.get('accuracy_mean'))} "
            f"| {_fmt(stats.get('grounding_mean'))} "
            f"| {_fmt(stats.get('efficiency_mean'))} "
            f"| {_fmt(stats.get('strict_final_mean'))} "
            f"| {_fmt(stats.get('analysis_final_mean'))} |"
        )

    lines.extend([
        '',
        '## By Mode',
        '| Mode | n | mean | accuracy | grounding | efficiency | strict_final | analysis_final |',
        '|------|---|------|----------|-----------|------------|--------------|----------------|',
    ])
    for mode, stats in sorted(summary.by_mode.items()):
        lines.append(
            f"| `{mode}` | {stats.get('n', 0)} "
            f"| {_fmt(stats.get('mean'))} "
            f"| {_fmt(stats.get('accuracy_mean'))} "
            f"| {_fmt(stats.get('grounding_mean'))} "
            f"| {_fmt(stats.get('efficiency_mean'))} "
            f"| {_fmt(stats.get('strict_final_mean'))} "
            f"| {_fmt(stats.get('analysis_final_mean'))} |"
        )

    lines.extend(['', '## Safety'])
    lines.append(f"- Triggered count: {summary.safety.get('triggered_count', 0)}")
    lines.append(f"- Triggered rate: {_fmt(summary.safety.get('triggered_rate'))}")
    lines.append(f"- Any triggered: {summary.safety.get('any_triggered', False)}")

    # Model comparison table (v4)
    if summary.by_model:
        lines.extend([
            '',
            '## Model Comparison',
            '| Model | n | band_score | strict_final | analysis_final | tokens/run (mean) | total_tokens |',
            '|-------|---|-----------|--------------|----------------|-------------------|--------------|',
        ])
        for model_key, stats in sorted(summary.by_model.items()):
            lines.append(
                f"| `{model_key}` | {stats.get('n', 0)} "
                f"| {_fmt(stats.get('mean'))} "
                f"| {_fmt(stats.get('strict_final_mean'))} "
                f"| {_fmt(stats.get('analysis_final_mean'))} "
                f"| {_fmt(stats.get('total_tokens_mean'), precision=0)} "
                f"| {stats.get('total_tokens_sum', 0)} |"
            )

    lines.extend(['', '## Per Question (mode split)'])
    lines.append(
        '| Question:Mode | n | mean | accuracy | grounding | efficiency | strict_final | safety_veto |'
    )
    lines.append(
        '|---------------|---|------|----------|-----------|------------|--------------|-------------|'
    )
    for key, row in sorted(summary.by_question.items()):
        lines.append(
            f"| `{key}` | {row.get('n', 0)} "
            f"| {_fmt(row.get('mean'))} "
            f"| {_fmt(row.get('accuracy_mean'))} "
            f"| {_fmt(row.get('grounding_mean'))} "
            f"| {_fmt(row.get('efficiency_mean'))} "
            f"| {_fmt(row.get('strict_final_mean'))} "
            f"| {row.get('safety_veto_count', 0)} |"
        )
    lines.append('')
    return '\n'.join(lines)
