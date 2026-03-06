#!/usr/bin/env python3
"""
离线分析 tracemalloc 快照 diff，找出「哪里」导致内存增长。

用法（先 GET /api/v1/debug/tracemalloc/dump?tag=baseline 与 ?tag=current 下载两个 .dump 到本机后）:
  python scripts/analyze_tracemalloc_diff.py baseline.dump current.dump
  python scripts/analyze_tracemalloc_diff.py baseline.dump current.dump -b traceback   # 按完整调用栈分组，可看到 pydantic 等是哪个调用链创建的

输出：按 size_diff 排序的 top N 条，带 traceback。默认按 lineno 分组；-b traceback 按调用栈分组便于定位具体对象来源。
"""

import argparse
import sys
import tracemalloc


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Load two tracemalloc snapshot dumps and print diff (top allocations since baseline).'
    )
    parser.add_argument(
        'baseline_dump', help='Path to baseline snapshot dump (e.g. baseline.dump)'
    )
    parser.add_argument(
        'current_dump', help='Path to current snapshot dump (e.g. current.dump)'
    )
    parser.add_argument(
        '-n',
        '--top',
        type=int,
        default=30,
        help='Number of top entries to print (default: 30)',
    )
    parser.add_argument(
        '-b',
        '--by',
        choices=('lineno', 'traceback'),
        default='lineno',
        help="Group by 'lineno' (file:line) or 'traceback' (full call stack). Use traceback to see which call chain created the allocations (e.g. which code path into pydantic) (default: lineno)",
    )
    args = parser.parse_args()

    try:
        baseline = tracemalloc.Snapshot.load(args.baseline_dump)
    except OSError as e:
        print(f'Failed to load baseline {args.baseline_dump}: {e}', file=sys.stderr)
        sys.exit(1)
    try:
        current = tracemalloc.Snapshot.load(args.current_dump)
    except OSError as e:
        print(f'Failed to load current {args.current_dump}: {e}', file=sys.stderr)
        sys.exit(1)

    diff = current.compare_to(baseline, args.by)
    print(f'[ Top {args.top} increased allocations (group by {args.by}) ]\n')
    n = 0
    for s in diff:
        if s.size_diff <= 0:
            continue
        n += 1
        if n > args.top:
            break
        size_kb = s.size_diff / 1024
        print(f'#{n}: +{size_kb:.1f} KiB (+{s.count_diff} blocks)')
        for line in s.traceback.format():
            print(f'  {line}')
        print()
    if n == 0:
        print('No positive size_diff (no increase since baseline).')


if __name__ == '__main__':
    main()
