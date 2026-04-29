"""应用启动时将仓库内置技能的元信息同步到 matmaster-tools-server。

流程：扫描本地 skills 目录 + 读 builtin_tags.yaml → 调 POST /api/v1/skills/sync-builtin。
技能文件本身（zip artifact）不在此处上传——由独立的 CI 脚本打包上传后填入 artifact_id。
启动同步只推元信息（name、description、tags），使前端管理页面能展示。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
import yaml

from utils.env import MATMASTER_TOOLS_SERVER

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_SKILLS_ROOT = Path(__file__).resolve().parents[2] / 'matmaster' / 'skills'
_TAGS_FILE = _SKILLS_ROOT / 'builtin_tags.yaml'


def _load_tags_config() -> dict[str, Any]:
    if not _TAGS_FILE.exists():
        logger.warning('builtin_tags.yaml not found at %s', _TAGS_FILE)
        return {}
    return yaml.safe_load(_TAGS_FILE.read_text(encoding='utf-8')) or {}


def _get_version() -> str:
    try:
        from src.utils.build_info import get_build_version

        return get_build_version() or 'unknown'
    except Exception:
        return 'unknown'


def _scan_builtin_skills(
    tags_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """扫描 _SKILLS_ROOT 下的 SKILL.md，提取 name + description，合并 tags。"""
    import re

    skill_tags_map: dict[str, list[str]] = tags_config.get('skills', {}) or {}
    results: list[dict[str, Any]] = []

    if not _SKILLS_ROOT.exists():
        return results

    for md_path in sorted(_SKILLS_ROOT.rglob('SKILL.md')):
        skill_dir = md_path.parent
        rel = skill_dir.relative_to(_SKILLS_ROOT)
        if any(p.startswith('_') for p in rel.parts):
            continue

        content = md_path.read_text(encoding='utf-8')
        fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        if not fm_match:
            continue

        data: dict[str, str] = {}
        for line in fm_match.group(1).split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if ':' in line:
                key, value = line.split(':', 1)
                data[key.strip()] = value.strip().strip('"').strip("'")

        name = data.get('name', skill_dir.name)
        description = data.get('description', '')
        tags = skill_tags_map.get(name)

        results.append({
            'name': name,
            'description': description,
            'tags': tags,
        })

    return results


def sync_builtin_skills_to_tools_server() -> bool:
    """同步内置技能元信息到 tools-server。返回是否成功。"""
    base = (MATMASTER_TOOLS_SERVER or '').strip().rstrip('/')
    if not base:
        logger.warning('MATMASTER_TOOLS_SERVER empty, skip builtin skills sync')
        return False

    tags_config = _load_tags_config()
    skills = _scan_builtin_skills(tags_config)
    if not skills:
        logger.warning('No builtin skills found, skip sync')
        return False

    tag_definitions = tags_config.get('tag_definitions') or {}
    version = _get_version()

    payload = {
        'version': version,
        'tag_definitions': tag_definitions,
        'skills': skills,
    }

    url = f'{base}/api/v1/skills/sync-builtin'
    try:
        with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            body = r.json()
    except Exception as e:
        logger.warning('builtin skills sync failed: %s', e, exc_info=True)
        return False

    if not isinstance(body, dict) or body.get('code') != 0:
        logger.warning('builtin skills sync bad response: %s', body)
        return False

    data = body.get('data', {})
    logger.info(
        'builtin skills synced: version=%s count=%d deleted=%s inserted=%s',
        version,
        len(skills),
        data.get('deleted'),
        data.get('inserted'),
    )
    return True
