"""调试接口：tracemalloc 快照与 diff，用于排查内存分配。仅建议在非生产或内网使用。"""

import logging
import tracemalloc
from typing import Any

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(tags=['debug'])

# 用于 diff 的基线快照（按需设置）
_tracemalloc_baseline: tracemalloc.Snapshot | None = None

# diff 时只返回前 N 条、且仅前几条带 traceback_frames，避免快照/序列化占满内存把服务干崩
_DIFF_TOP_N = 12
_DIFF_FRAMES_TOP_N = 3


def _snapshot_to_stats(
    snap: tracemalloc.Snapshot, limit: int = 20
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
    快照/diff 较吃内存，若进程已占用较高可能触发 OOM，接口内已做条数限制与异常捕获。
    """
    if not tracemalloc.is_tracing():
        return {
            'tracing': False,
            'message': 'tracemalloc 未启动，请在 lifespan 中调用 tracemalloc.start()',
        }

    try:
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
                'message': '请先请求 ?baseline=1 再执行对话，然后请求 ?diff=1',
                'current_mb': round(current_b / (1024 * 1024), 2),
                'peak_mb': round(peak_b / (1024 * 1024), 2),
            }

        if diff and _tracemalloc_baseline is not None:
            diff_snap = current.compare_to(_tracemalloc_baseline, 'lineno')
            top = []
            for i, s in enumerate(diff_snap):
                if s.size_diff <= 0:
                    continue
                if len(top) >= _DIFF_TOP_N:
                    break
                frames = s.traceback.format()
                entry: dict[str, Any] = {
                    'size_diff_kb': round(s.size_diff / 1024, 2),
                    'count_diff': s.count_diff,
                    'traceback': '\n'.join(frames),
                }
                if i < _DIFF_FRAMES_TOP_N:
                    entry['traceback_frames'] = frames
                top.append(entry)
            return {
                'tracing': True,
                'mode': 'diff',
                'top_increased': top,
                'current_mb': round(current_b / (1024 * 1024), 2),
                'peak_mb': round(peak_b / (1024 * 1024), 2),
            }

        return {
            'tracing': True,
            'mode': 'current',
            'top_allocations': _snapshot_to_stats(current, limit=20),
            'current_mb': round(current_b / (1024 * 1024), 2),
            'peak_mb': round(peak_b / (1024 * 1024), 2),
        }
    except MemoryError as e:
        logger.warning('tracemalloc endpoint MemoryError: %s', e)
        return {
            'tracing': True,
            'error': 'memory_error',
            'message': '快照或 diff 时内存不足，可先重启或减少负载后再试；或只打 baseline 后少跑几轮再 diff',
        }
    except Exception as e:
        logger.exception('tracemalloc endpoint error: %s', e)
        return {
            'tracing': True,
            'error': 'server_error',
            'message': str(e),
        }
