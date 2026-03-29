"""CLI entrypoint for MATTER evaluation.

Can be invoked as either:
  python -m evaluation.cli   (module mode)
  python evaluation/cli.py   (script mode)
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

try:
    from .reporter import generate_rating_from_raw_runs
    from .runner import run_evaluation
    from .schemas import EvalConfig
except ImportError:
    _project_root = str(Path(__file__).resolve().parents[1])
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)
    from evaluation.reporter import generate_rating_from_raw_runs
    from evaluation.runner import run_evaluation
    from evaluation.schemas import EvalConfig


def main() -> int:
    parser = argparse.ArgumentParser(description='Run MATTER evaluation for Mat Master')
    parser.add_argument(
        '--eval-config',
        default='evaluation/config.yaml',
        help='Path to MATTER evaluation config.yaml',
    )
    parser.add_argument(
        '--mat-config',
        default=None,
        help='Optional override for Mat Master config.yaml path',
    )
    parser.add_argument('--k', type=int, default=None, help='Repeat count override')
    parser.add_argument(
        '--modes',
        nargs='+',
        choices=['direct', 'planner'],
        default=None,
        help='Modes to evaluate (default from config)',
    )
    parser.add_argument('--output-dir', default=None, help='Output directory override')
    parser.add_argument('--run-label', default=None, help='Run label override')
    parser.add_argument(
        '--question-bank-dir',
        default=None,
        help='Question bank directory override',
    )
    parser.add_argument(
        '--use-seed-prompt',
        action='store_true',
        help='Force using seed prompts (disable rewriting)',
    )
    parser.add_argument(
        '--capabilities',
        nargs='+',
        default=None,
        help='Only run questions from these capabilities (e.g. --capabilities batch_processing workflow_orchestration)',
    )
    parser.add_argument(
        '--questions',
        nargs='+',
        default=None,
        help='Only run these question IDs (e.g. --questions DF_mech_001 WO_mech_001)',
    )
    parser.add_argument(
        '--rate-only',
        action='store_true',
        help='Generate standalone rating from existing raw_runs.jsonl',
    )
    parser.add_argument(
        '--run-dir',
        default=None,
        help='Existing evaluation run directory (used with --rate-only)',
    )
    parser.add_argument(
        '--raw-runs',
        default=None,
        help='Path to raw_runs.jsonl (used with --rate-only)',
    )
    parser.add_argument(
        '--rating-prefix',
        default='interim_',
        help='Prefix for standalone rating report files',
    )
    args = parser.parse_args()

    if args.rate_only:
        raw_runs_path = _resolve_raw_runs_path(
            run_dir=args.run_dir, raw_runs=args.raw_runs
        )
        output_dir = Path(args.run_dir) if args.run_dir else raw_runs_path.parent
        result = generate_rating_from_raw_runs(
            raw_runs_path=raw_runs_path,
            output_dir=output_dir,
            prefix=args.rating_prefix,
        )
        print(
            json.dumps(
                {'raw_runs': str(raw_runs_path), **result}, ensure_ascii=False, indent=2
            )
        )
        return 0

    eval_cfg = _load_yaml(Path(args.eval_config))
    if args.mat_config is not None:
        eval_cfg['mat_config_path'] = args.mat_config
    if args.k is not None:
        eval_cfg['k'] = args.k
    if args.modes is not None:
        eval_cfg['modes'] = args.modes
    if args.output_dir is not None:
        eval_cfg['output_dir'] = args.output_dir
    if args.run_label is not None:
        eval_cfg['run_label'] = args.run_label
    if args.question_bank_dir is not None:
        eval_cfg['question_bank_dir'] = args.question_bank_dir
    if args.use_seed_prompt:
        eval_cfg['use_seed_prompt'] = True
    if args.capabilities is not None:
        eval_cfg['include_capabilities'] = args.capabilities
    if args.questions is not None:
        eval_cfg['include_question_ids'] = args.questions

    config = EvalConfig.model_validate(eval_cfg)
    result = run_evaluation(config)
    print(
        json.dumps(
            {'run_dir': result['run_dir'], 'report_paths': result['report_paths']},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _resolve_raw_runs_path(*, run_dir: str | None, raw_runs: str | None) -> Path:
    if raw_runs:
        path = Path(raw_runs)
    elif run_dir:
        path = Path(run_dir) / 'raw_runs.jsonl'
    else:
        raise ValueError('rate-only mode requires --run-dir or --raw-runs')
    if not path.exists():
        raise FileNotFoundError(f'raw runs file not found: {path}')
    return path


_ENV_PATTERN = re.compile(r'\$\{([A-Za-z0-9_]+)\}')


def _substitute_env(value: Any) -> Any:
    """Recursively replace ``${VAR}`` with ``os.environ.get('VAR', '')``."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ''), value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(item) for item in value]
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f'Config not found: {path}')
    try:
        from dotenv import load_dotenv

        for parent in [path.parent] + list(path.parent.parents):
            env_file = parent / '.env'
            if env_file.exists():
                load_dotenv(env_file, override=True)
                break
        else:
            load_dotenv(override=True)
    except ImportError:
        pass
    raw = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    return _substitute_env(raw)


if __name__ == '__main__':
    raise SystemExit(main())
