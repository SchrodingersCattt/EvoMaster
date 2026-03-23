"""list_images.py - 查询 Bohrium 公共镜像列表。

分两步：
1. GET /openapi/v2/image/public?page=1&pageSize=1000  → 获取镜像 ID 列表
2. 对每个 ID，GET /openapi/v2/image/public/{id}/version?current=1&pageSize=10&page=1
   → 获取具体镜像地址（image_address）

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
OPENAPI_BASE = os.environ.get('BOHRIUM_BASE_URL', 'https://openapi.dp.tech').rstrip('/')

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


def list_image_ids(page_size: int = 1000) -> list[dict]:
    """获取公共镜像 ID 列表。"""
    data = _get(
        '/openapi/v2/image/public',
        params={'page': 1, 'pageSize': page_size},
    )
    records = (data.get('data') or {}).get('items') or []
    return records


def get_image_versions(image_id: int) -> list[dict]:
    """获取单个镜像的版本列表（含 url/image address）。"""
    data = _get(
        f'/openapi/v2/image/public/{image_id}/version',
        params={
            'current': 1,
            'pageSize': 10,
            'page': 1,
            'resourceType': '',
            'version': '',
        },
    )
    records = (data.get('data') or {}).get('items') or []
    return records


def _matches_keyword(record: dict, keyword: str) -> bool:
    """判断镜像记录是否包含关键词（大小写不敏感）。"""
    kw = keyword.lower()
    name = str(record.get('name') or record.get('imageName') or '').lower()
    desc = str(record.get('description') or '').lower()
    return kw in name or kw in desc


def main() -> None:
    parser = argparse.ArgumentParser(description='列出 Bohrium 公共镜像')
    parser.add_argument(
        '--keyword',
        default=None,
        help='过滤关键词（大小写不敏感），如 --keyword gromacs',
    )
    parser.add_argument(
        '--max-results',
        type=int,
        default=20,
        help='最多返回条数（默认 20）',
    )
    args = parser.parse_args()

    if not ACCESS_KEY:
        print(json.dumps({'success': False, 'error': 'BOHRIUM_ACCESS_KEY not set'}))
        sys.exit(1)

    try:
        all_images = list_image_ids()
    except Exception as exc:
        print(json.dumps({'success': False, 'error': f'list_image_ids failed: {exc}'}))
        sys.exit(1)

    # 先按关键词过滤 ID 列表记录
    if args.keyword:
        filtered = [r for r in all_images if _matches_keyword(r, args.keyword)]
    else:
        filtered = all_images

    # 最多取 max_results 条，依次查版本
    results = []
    for record in filtered[: args.max_results]:
        img_id = record.get('id') or record.get('imageId')
        if img_id is None:
            continue
        try:
            versions = get_image_versions(int(img_id))
        except Exception as exc:
            versions = [{'error': str(exc)}]

        # 提取有用字段：url 是实际 docker image address
        version_list = []
        for v in versions:
            entry: dict = {}
            for key in ('url', 'version', 'resourceType', 'desc', 'size'):
                val = v.get(key)
                if val is not None and val != '':
                    entry[key] = val
            if entry:
                version_list.append(entry)

        results.append(
            {
                'id': img_id,
                'name': record.get('name') or record.get('imageName') or '',
                'description': record.get('description') or '',
                'versions': version_list,
            }
        )

    print(
        json.dumps(
            {
                'success': True,
                'total_found': len(filtered),
                'returned': len(results),
                'keyword': args.keyword,
                'images': results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == '__main__':
    main()
