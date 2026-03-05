"""调试接口：tracemalloc 快照与 diff，用于排查内存分配。仅建议在非生产或内网使用。"""

import logging
import tracemalloc
from typing import Any

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(tags=['debug'])

# 用于 diff 的基线快照（按需设置）
_tracemalloc_baseline: tracemalloc.Snapshot | None = None


def _snapshot_to_stats(
    snap: tracemalloc.Snapshot, limit: int = 30
) -> list[dict[str, Any]]:
    """将快照转为按 size 排序的统计列表。"""
    stats = snap.statistics('lineno')
    out = []
    for s in stats[:limit]:
        out.append(
            {
                'size_kb': round(s.size / 1024, 2),
                'count': s.count,
                'traceback': '\n'.join(s.traceback.format()),
            }
        )
    return out


@router.get('/tracemalloc')
async def get_tracemalloc(
    baseline: bool = Query(False, description='设为 True 时将当前快照存为基线'),
    diff: bool = Query(False, description='设为 True 时返回与基线的 diff（增量分配）'),
) -> dict[str, Any]:
    """
    查看 tracemalloc 当前快照或与基线的 diff。

    用法示例：
    1. 启动应用后请求 GET .../tracemalloc?baseline=1 打基线；
    2. 执行若干轮对话；
    3. 请求 GET .../tracemalloc?diff=1 查看相对基线的增量分配（谁在涨）。
    """
    if not tracemalloc.is_tracing():
        return {
            'tracing': False,
            'message': 'tracemalloc 未启动，请在 lifespan 中调用 tracemalloc.start()',
        }

    if baseline:
        global _tracemalloc_baseline
        _tracemalloc_baseline = tracemalloc.take_snapshot()
        current_b, peak_b = tracemalloc.get_traced_memory()
        return {
            'tracing': True,
            'message': 'baseline snapshot set',
            'current_mb': round(current_b / (1024 * 1024), 2),
            'peak_mb': round(peak_b / (1024 * 1024), 2),
        }

    current = tracemalloc.take_snapshot()
    current_b, peak_b = tracemalloc.get_traced_memory()

    if diff and _tracemalloc_baseline is None:
        return {
            'tracing': True,
            'mode': 'diff',
            'error': 'no_baseline',
            'message': '请先请求 ?baseline=1 再执行对话，然后请求 ?diff=1；多 worker 时需在同一进程打基线与拉 diff',
            'current_mb': round(current_b / (1024 * 1024), 2),
            'peak_mb': round(peak_b / (1024 * 1024), 2),
        }

    if diff and _tracemalloc_baseline is not None:
        # 与基线对比，看哪些分配增加了；带完整栈便于看到是 evomaster/agent 等哪条调用链
        diff_snap = current.compare_to(_tracemalloc_baseline, 'lineno')
        top = []
        for s in diff_snap[:30]:
            if s.size_diff <= 0:
                continue
            frames = s.traceback.format()
            top.append(
                {
                    'size_diff_kb': round(s.size_diff / 1024, 2),
                    'count_diff': s.count_diff,
                    'traceback': '\n'.join(frames),
                    'traceback_frames': frames,
                }
            )
        return {
            'tracing': True,
            'mode': 'diff',
            'top_increased': top,
            'current_mb': round(current_b / (1024 * 1024), 2),
            'peak_mb': round(peak_b / (1024 * 1024), 2),
            'hint': '看 traceback_frames 里含 evomaster/src/playground 的帧，即本轮对话触发的业务侧分配链',
        }

    # 当前快照 top 分配
    return {
        'tracing': True,
        'mode': 'current',
        'top_allocations': _snapshot_to_stats(current, limit=30),
        'current_mb': round(current_b / (1024 * 1024), 2),
        'peak_mb': round(peak_b / (1024 * 1024), 2),
    }
