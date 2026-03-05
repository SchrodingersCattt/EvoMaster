"""调试接口：进程内存 RSS 基线/差值（轻量）；tracemalloc 当前快照与 dump 到文件（diff 在进程外做）。仅建议在非生产或内网使用。"""

import logging
import os
import tracemalloc
from typing import Any

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(tags=['debug'])

# 进程 RSS 基线（字节），用于 /memory 的 diff
_rss_baseline_bytes: int | None = None

# tracemalloc 快照 dump 目录（可写），不设则用 /tmp/tracemalloc_dumps
_TRACEMALLOC_DUMP_DIR = os.getenv('TRACEMALLOC_DUMP_DIR', '/tmp/tracemalloc_dumps')


def _get_process_rss_bytes() -> int | None:
    """当前进程 RSS（字节）。Linux 下读 /proc/self/status，其他平台返回 None。"""
    try:
        with open('/proc/self/status', encoding='utf-8') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    # "VmRSS:    12345 kB"
                    return int(line.split()[1]) * 1024
    except (OSError, IndexError, ValueError):
        pass
    return None


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


@router.get('/memory')
async def get_memory(
    baseline: bool = Query(False, description='设为 True 时把当前进程 RSS 存为基线'),
    diff: bool = Query(
        False, description='设为 True 时返回当前 RSS 与基线差值（需先设 baseline）'
    ),
) -> dict[str, Any]:
    """
    进程内存 RSS：仅读 /proc/self/status，几乎不占内存，适合 2G 等小规格。
    用法：先 GET ?baseline=1，再执行几轮对话，再 GET ?diff=1 看 rss_delta_mb 是否持续增大。
    """
    rss_bytes = _get_process_rss_bytes()
    if rss_bytes is None:
        return {
            'available': False,
            'message': 'RSS 仅支持 Linux（/proc/self/status）',
        }

    rss_mb = round(rss_bytes / (1024 * 1024), 2)
    out: dict[str, Any] = {
        'available': True,
        'rss_mb': rss_mb,
    }

    if baseline:
        global _rss_baseline_bytes
        _rss_baseline_bytes = rss_bytes
        out['message'] = 'baseline 已记录，可执行几轮对话后请求 ?diff=1'
        out['baseline_mb'] = rss_mb
        return out

    if _rss_baseline_bytes is not None:
        out['baseline_mb'] = round(_rss_baseline_bytes / (1024 * 1024), 2)
        out['rss_delta_mb'] = round(
            (rss_bytes - _rss_baseline_bytes) / (1024 * 1024), 2
        )
    elif diff:
        out['message'] = '请先请求 ?baseline=1 再请求 ?diff=1'

    return out


@router.get('/tracemalloc/dump')
async def tracemalloc_dump(
    tag: str = Query(
        ...,
        description='快照标签，如 baseline / current / after_5_runs，用于文件名 {tag}.dump',
    ),
) -> dict[str, Any]:
    """
    将当前 tracemalloc 快照写入文件。
    用法：先 GET ?tag=baseline，跑几轮对话，再 GET ?tag=current；在 Pod 内可直接 GET /tracemalloc/diff-from-disk 得到「哪里分配」。
    """
    if not tracemalloc.is_tracing():
        return {
            'ok': False,
            'message': 'tracemalloc 未启动',
        }
    safe_tag = (
        ''.join(c if c.isalnum() or c in '-_' else '_' for c in tag.strip())
        or 'snapshot'
    )
    try:
        os.makedirs(_TRACEMALLOC_DUMP_DIR, exist_ok=True)
        path = os.path.join(_TRACEMALLOC_DUMP_DIR, f'{safe_tag}.dump')
        snap = tracemalloc.take_snapshot()
        snap.dump(path)
        return {
            'ok': True,
            'path': path,
            'message': f'已写入 {path}；再 dump 另一份后请求 GET /tracemalloc/diff-from-disk 即可',
        }
    except MemoryError as e:
        logger.warning('tracemalloc dump MemoryError: %s', e)
        return {
            'ok': False,
            'error': 'memory_error',
            'message': '快照时内存不足',
        }
    except Exception as e:
        logger.exception('tracemalloc dump error: %s', e)
        return {'ok': False, 'error': 'server_error', 'message': str(e)}


@router.get('/tracemalloc/diff-from-disk')
async def tracemalloc_diff_from_disk(
    baseline_tag: str = Query(
        'baseline', description='基线 dump 的 tag，对应文件 {tag}.dump'
    ),
    current_tag: str = Query('current', description='当前 dump 的 tag'),
    top: int = Query(15, ge=1, le=50, description='返回前 N 条'),
) -> dict[str, Any]:
    """
    从已存在的 dump 文件做 diff，直接返回「哪里分配」的 top 列表。
    在 Pod 内用法：先调两次 dump?tag=baseline 和 dump?tag=current，再调本接口即可，无需拷文件或跑脚本。
    若内存紧张可能 OOM，可改把两个 .dump 拷到本机用 scripts/analyze_tracemalloc_diff.py。
    """

    def safe_tag(t: str) -> str:
        return (
            ''.join(c if c.isalnum() or c in '-_' else '_' for c in t.strip())
            or 'snapshot'
        )

    base_path = os.path.join(_TRACEMALLOC_DUMP_DIR, f'{safe_tag(baseline_tag)}.dump')
    curr_path = os.path.join(_TRACEMALLOC_DUMP_DIR, f'{safe_tag(current_tag)}.dump')
    if not os.path.isfile(base_path):
        return {
            'ok': False,
            'error': 'no_baseline',
            'message': f'基线文件不存在: {base_path}',
            'path': base_path,
        }
    if not os.path.isfile(curr_path):
        return {
            'ok': False,
            'error': 'no_current',
            'message': f'当前文件不存在: {curr_path}',
            'path': curr_path,
        }

    try:
        baseline_snap = tracemalloc.Snapshot.load(base_path)
        current_snap = tracemalloc.Snapshot.load(curr_path)
        diff = current_snap.compare_to(baseline_snap, 'lineno')
        top_list = []
        for s in diff:
            if s.size_diff <= 0:
                continue
            if len(top_list) >= top:
                break
            top_list.append(
                {
                    'size_diff_kb': round(s.size_diff / 1024, 2),
                    'count_diff': s.count_diff,
                    'traceback': '\n'.join(s.traceback.format()),
                }
            )
        return {'ok': True, 'top_increased': top_list}
    except MemoryError as e:
        logger.warning('tracemalloc diff-from-disk MemoryError: %s', e)
        return {
            'ok': False,
            'error': 'memory_error',
            'message': '读盘做 diff 时内存不足，建议把两个 .dump 拷到本机用 scripts/analyze_tracemalloc_diff.py',
        }
    except OSError as e:
        logger.warning('tracemalloc diff-from-disk OSError: %s', e)
        return {'ok': False, 'error': 'io_error', 'message': str(e)}
    except Exception as e:
        logger.exception('tracemalloc diff-from-disk error: %s', e)
        return {'ok': False, 'error': 'server_error', 'message': str(e)}


@router.get('/tracemalloc')
async def get_tracemalloc() -> dict[str, Any]:
    """
    tracemalloc 当前快照（按 lineno 的 top 分配）。仅一次 take_snapshot，无 diff。
    若要看进程内存是否随对话增长，请用 GET /api/v1/debug/memory（?baseline=1 与 ?diff=1）。
    """
    if not tracemalloc.is_tracing():
        return {
            'tracing': False,
            'message': 'tracemalloc 未启动',
        }

    try:
        current = tracemalloc.take_snapshot()
        current_b, peak_b = tracemalloc.get_traced_memory()
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
            'message': '快照时内存不足；可用 GET /api/v1/debug/memory 查看进程 RSS',
        }
    except Exception as e:
        logger.exception('tracemalloc endpoint error: %s', e)
        return {
            'tracing': True,
            'error': 'server_error',
            'message': str(e),
        }
