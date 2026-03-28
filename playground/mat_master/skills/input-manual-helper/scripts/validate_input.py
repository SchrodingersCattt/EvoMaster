"""
validate_input.py — 调用 Validator 对输入文件做静态校验，并输出 JSON 报告。

用法
----
  python validate_input.py --input_file /path/to/cp2k.inp --software CP2K
  python validate_input.py --input_file /path/to/abacus/INPUT --software ABACUS
  python validate_input.py --input_file /path/to/cp2k.inp --software CP2K --json_out diag.json

本脚本永远 exit 0，校验结果（包括 error 级别的诊断）写入 --json_out 文件
或直接打印到 stdout，不影响 exit code。这样提交流水线可以在拿到报告后
自行决定是否阻止提交。

输出 JSON 格式
--------------
{
  "software": "cp2k",
  "input_file": "/path/to/cp2k.inp",
  "status": "ok" | "warnings" | "errors",
  "summary": "2 errors, 1 warnings, 0 infos",
  "diagnostics": [
    {
      "severity": "error" | "warning" | "info",
      "message": "...",
      "param": "...",        // 可为空字符串
      "suggestion": "...",   // 可为 null
      "line": 12             // 可为 null
    },
    ...
  ]
}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Validate a computational chemistry input file and produce a JSON report.'
    )
    parser.add_argument(
        '--input_file',
        required=True,
        help='Path to the input file to validate.',
    )
    parser.add_argument(
        '--software',
        required=True,
        help='Software name (e.g. CP2K, ORCA, QE, LAMMPS, ABINIT, ABACUS).',
    )
    parser.add_argument(
        '--json_out',
        default=None,
        help='Path to write JSON diagnostic report. If omitted, prints to stdout.',
    )
    parser.add_argument(
        '--data-dir',
        help='Ignored; kept for backward compatibility.',
    )

    args = parser.parse_args()
    input_path = Path(args.input_file)

    # ------------------------------------------------------------------ #
    # 1. 读取输入文件
    # ------------------------------------------------------------------ #
    if not input_path.exists():
        # 即使文件不存在也 exit 0，写入一条 error 诊断
        result = _build_result(
            software=args.software,
            input_file=str(input_path),
            diags_dicts=[
                {
                    'severity': 'error',
                    'message': f"Input file not found: {input_path}",
                    'param': '',
                    'suggestion': None,
                    'line': None,
                }
            ],
        )
        _output(result, args.json_out)
        sys.exit(0)

    text = input_path.read_text(encoding='utf-8', errors='replace')

    # ------------------------------------------------------------------ #
    # 2. 查找并运行 Validator
    # ------------------------------------------------------------------ #
    diags_dicts: list[dict] = []
    try:
        # validators/ 目录与 scripts/ 同级，需要添加到 sys.path
        _skill_dir = Path(__file__).parent.parent
        if str(_skill_dir) not in sys.path:
            sys.path.insert(0, str(_skill_dir))

        from validators.base import ValidatorRegistry  # type: ignore[import]

        registry = ValidatorRegistry()
        validator = registry.get_validator(args.software)

        if validator is None:
            diags_dicts.append(
                {
                    'severity': 'info',
                    'message': f"No validator registered for software '{args.software}'. Skipping static analysis.",
                    'param': '',
                    'suggestion': None,
                    'line': None,
                }
            )
        else:
            raw_diags = validator.validate_text(text, source=str(input_path))
            for d in raw_diags:
                diags_dicts.append(
                    {
                        'severity': d.severity,
                        'message': d.message,
                        'param': d.param if d.param else '',
                        'suggestion': (
                            d.suggestion if hasattr(d, 'suggestion') else None
                        ),
                        'line': d.line if hasattr(d, 'line') else None,
                    }
                )
    except Exception as exc:  # noqa: BLE001
        diags_dicts.append(
            {
                'severity': 'warning',
                'message': f"Validator raised unexpected error: {exc}",
                'param': '',
                'suggestion': None,
                'line': None,
            }
        )

    # ------------------------------------------------------------------ #
    # 3. 输出结果
    # ------------------------------------------------------------------ #
    result = _build_result(
        software=args.software,
        input_file=str(input_path),
        diags_dicts=diags_dicts,
    )
    _output(result, args.json_out)
    sys.exit(0)


def _build_result(
    software: str,
    input_file: str,
    diags_dicts: list[dict],
) -> dict:
    """组装标准输出 dict。"""
    n_errors = sum(1 for d in diags_dicts if d.get('severity') == 'error')
    n_warnings = sum(1 for d in diags_dicts if d.get('severity') == 'warning')
    n_infos = sum(1 for d in diags_dicts if d.get('severity') == 'info')

    if n_errors > 0:
        status = 'errors'
    elif n_warnings > 0:
        status = 'warnings'
    else:
        status = 'ok'

    summary = f"{n_errors} errors, {n_warnings} warnings, {n_infos} infos"

    return {
        'software': software.lower(),
        'input_file': input_file,
        'status': status,
        'summary': summary,
        'diagnostics': diags_dicts,
    }


def _output(result: dict, json_out: str | None) -> None:
    """写入 JSON 文件或打印到 stdout。"""
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if json_out:
        Path(json_out).write_text(text, encoding='utf-8')
        # 同时打印摘要到 stdout 便于日志查看
        print(
            f"[validate_input] {result['software']} — {result['status']}: {result['summary']}"
        )
        print(f"[validate_input] JSON report written to: {json_out}")
    else:
        print(text)


if __name__ == '__main__':
    main()
