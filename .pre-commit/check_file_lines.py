#!/usr/bin/env python3
"""Pre-commit hook: 单文件行数不超过 1000 行（与 AGENTS.md 约定一致）。"""

import sys

MAX_LINES = 1000


def main() -> int:
    if len(sys.argv) < 2:
        return 0
    over: list[tuple[str, int]] = []
    for path in sys.argv[1:]:
        try:
            with open(path, 'rb') as f:
                n = sum(1 for _ in f)
            if n > MAX_LINES:
                over.append((path, n))
        except OSError as e:
            print(f"check_file_lines: cannot read {path}: {e}", file=sys.stderr)
            return 1
    if over:
        print(
            f"单文件行数不得超过 {MAX_LINES} 行（见 AGENTS.md）。以下文件超限：",
            file=sys.stderr,
        )
        for path, n in sorted(over, key=lambda x: -x[1]):
            print(f"  {path}: {n} 行", file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
