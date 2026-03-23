"""list_machines.py - 查询 Bohrium 可用机型列表（CPU/GPU）。

调用 GET /openapi/v1/calc/list?page=1&pageSize=512&scene=job&isVirtualNode=false
    &chooseType=cpu|gpu&productLine=bohrium

AK 来源：
- 线上：由 build_bohrium_skill_remote_env 注入 BOHRIUM_ACCESS_KEY 环境变量
- 本地调试：从 .env 文件加载（python-dotenv）
"""

import argparse
import json
import os
import sys

import requests

try:
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv()
except ImportError:
    pass

ACCESS_KEY = os.environ.get('BOHRIUM_ACCESS_KEY', '').strip()
_ENV = os.environ.get('SERVICE_ENV', '').strip()
_URL_PART = f'.{_ENV}' if _ENV and _ENV not in ('prod', 'production') else ''
_DEFAULT_BASE = f'https://openapi{_URL_PART}.dp.tech' if _URL_PART else 'https://open.bohrium.com'
OPENAPI_BASE = os.environ.get('BOHRIUM_BASE_URL', _DEFAULT_BASE).rstrip('/')

_HEADER = {'accessKey': ACCESS_KEY, 'Accept': 'application/json'}


def _get(path: str, params: dict | None = None, timeout: int = 30) -> dict:
    response = requests.get(
        f'{OPENAPI_BASE}{path}',
        headers=_HEADER,
        params=params or {},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def list_machines(choose_type: str = 'cpu') -> list[dict]:
    """查询机型列表。

    Args:
        choose_type: 'cpu' 或 'gpu'
    """
    data = _get(
        '/openapi/v1/calc/list',
        params={
            'page': 1,
            'pageSize': 512,
            'scene': 'job',
            'isVirtualNode': 'false',
            'chooseType': choose_type,
            'productLine': 'bohrium',
        },
    )
    records = (data.get('data') or {}).get('items') or []
    return records


def _matches_keyword(record: dict, keyword: str) -> bool:
    """判断机型记录是否包含关键词（大小写不敏感）。"""
    kw = keyword.lower()
    name = str(record.get('skuEnName') or record.get('skuName') or '').lower()
    return kw in name


def _extract_machine_info(record: dict) -> dict:
    """提取机型的关键字段。"""
    entry: dict = {}
    for key in (
        'skuEnName',
        'cpuCoreNum',
        'memory',
        'gpu',
        'gpuCoreNum',
        'price',
        'hasStock',
    ):
        val = record.get(key)
        if val is not None and val != '':
            entry[key] = val
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description='列出 Bohrium 可用机型')
    parser.add_argument(
        '--type',
        choices=['cpu', 'gpu'],
        default='cpu',
        help="机型类型：cpu 或 gpu（默认 cpu）",
    )
    parser.add_argument(
        '--keyword',
        default=None,
        help='过滤关键词（大小写不敏感），如 --keyword c32',
    )
    parser.add_argument(
        '--max-results',
        type=int,
        default=50,
        help='最多返回条数（默认 50）',
    )
    args = parser.parse_args()

    if not ACCESS_KEY:
        print(json.dumps({'success': False, 'error': 'BOHRIUM_ACCESS_KEY not set'}))
        sys.exit(1)

    try:
        all_machines = list_machines(choose_type=args.type)
    except Exception as exc:
        print(json.dumps({'success': False, 'error': f'list_machines failed: {exc}'}))
        sys.exit(1)

    if args.keyword:
        filtered = [r for r in all_machines if _matches_keyword(r, args.keyword)]
    else:
        filtered = all_machines

    results = [_extract_machine_info(r) for r in filtered[: args.max_results]]

    print(
        json.dumps(
            {
                'success': True,
                'type': args.type,
                'total_found': len(filtered),
                'returned': len(results),
                'keyword': args.keyword,
                'machines': results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == '__main__':
    main()
