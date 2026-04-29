"""Worker / run_agent：从 matmaster-tools-server 拉取用户自定义 Skill，落盘并参与 SkillSync。

- 列表：GET /api/v1/users/{user_id}/skills（X-User-Id）
- 包：GET /api/v1/artifacts/{artifact_id}/download（原始 zip）
- 解压到 ``{project_root}/runs/.user_skills_cache/{user_id}/{skill_id}/``，供 ExpSkillsConfig.skills_root 与 Bohrium 同步使用。
"""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from utils.env import MATMASTER_TOOLS_SERVER

if TYPE_CHECKING:
    from matmaster.config.exp import ExpConfig


@dataclass
class UserSkillsSyncResult:
    roots: list[Path] = field(default_factory=list)
    disabled_builtin_names: set[str] = field(default_factory=set)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_USER_SKILLS_CACHE_SEG = re.compile(r'[^a-zA-Z0-9._-]+')


def _safe_user_segment(user_id: str) -> str:
    s = (user_id or '').strip()
    if not s:
        return 'unknown'
    cleaned = _USER_SKILLS_CACHE_SEG.sub('_', s).strip('_') or 'unknown'
    return cleaned[:128]


def _safe_extract_zip(zip_bytes: bytes, dest: Path) -> None:
    if not zip_bytes:
        raise ValueError('empty zip')
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        try:
            z.testzip()
        except zipfile.BadZipFile as e:
            raise ValueError('invalid zip') from e
        for info in z.infolist():
            if info.is_dir():
                continue
            name = info.filename
            parts = [
                p for p in name.replace('\\', '/').split('/') if p not in ('', '.')
            ]
            if any(p == '..' for p in parts):
                continue
            rel = '/'.join(parts)
            if not rel:
                continue
            target = dest / rel
            try:
                target.resolve().relative_to(dest.resolve())
            except ValueError:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info.filename))


def _meta_path(dest: Path) -> Path:
    return dest / '.user_skill_meta.json'


def materialize_user_skills_for_run(
    user_id: str,
    *,
    project_root: Path,
) -> UserSkillsSyncResult:
    """同步返回用户技能目录列表 + 被用户关闭的 builtin 技能名集合。"""
    uid = (user_id or '').strip()
    if not uid:
        return UserSkillsSyncResult()

    base = (MATMASTER_TOOLS_SERVER or '').strip().rstrip('/')
    if not base:
        logger.warning('MATMASTER_TOOLS_SERVER empty, skip user skills sync')
        return UserSkillsSyncResult()

    list_url = f'{base}/api/v1/users/{uid}/skills'
    headers = {'X-User-Id': uid}
    roots: list[Path] = []
    cache_root = project_root / 'runs' / '.user_skills_cache' / _safe_user_segment(uid)

    try:
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            r = client.get(list_url, headers=headers)
            r.raise_for_status()
            payload: dict[str, Any] = r.json()
    except Exception as e:
        logger.warning(
            'user skills list failed user_id=%s: %s',
            uid,
            e,
            exc_info=True,
        )
        return UserSkillsSyncResult()

    if not isinstance(payload, dict) or payload.get('code') != 0:
        logger.warning(
            'user skills list bad response user_id=%s payload=%s', uid, payload
        )
        return UserSkillsSyncResult()

    data = payload.get('data')
    if not isinstance(data, list) or not data:
        return UserSkillsSyncResult()

    disabled_builtin_names: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        source = (item.get('source') or '').strip().lower()
        enabled = item.get('enabled', True)
        if source == 'builtin':
            if not enabled:
                name = (item.get('name') or '').strip()
                if name:
                    disabled_builtin_names.add(name)
            continue
        status = (item.get('status') or '').strip().lower()
        if status != 'ready':
            continue
        if not enabled:
            continue
        skill_id = (item.get('id') or '').strip()
        artifact_id = (item.get('artifact_id') or '').strip()
        if not skill_id or not artifact_id:
            continue

        dest = cache_root / skill_id
        sha = item.get('content_sha256')
        if isinstance(sha, str):
            sha = sha.strip().lower()
        else:
            sha = None

        meta = {}
        mp = _meta_path(dest)
        if mp.exists() and dest.is_dir():
            try:
                meta = json.loads(mp.read_text(encoding='utf-8'))
            except Exception:
                meta = {}
            if sha and meta.get('content_sha256') == sha:
                roots.append(dest)
                continue

        dl = f'{base}/api/v1/artifacts/{artifact_id}/download'
        try:
            with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
                dr = client.get(dl, headers=headers)
                dr.raise_for_status()
                zbytes = dr.content
        except Exception as e:
            logger.warning(
                'user skill bundle download failed skill_id=%s artifact_id=%s: %s',
                skill_id,
                artifact_id,
                e,
            )
            continue

        try:
            if dest.exists():
                shutil.rmtree(dest)
            _safe_extract_zip(zbytes, dest)
            mp.write_text(
                json.dumps(
                    {
                        'content_sha256': sha,
                        'artifact_id': artifact_id,
                        'skill_id': skill_id,
                    },
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )
        except Exception as e:
            logger.warning(
                'user skill extract failed skill_id=%s: %s',
                skill_id,
                e,
                exc_info=True,
            )
            try:
                if dest.exists():
                    shutil.rmtree(dest)
            except Exception:
                pass
            continue

        roots.append(dest)

    if roots:
        logger.info(
            'user skills materialized: user_id=%s count=%s dirs=%s',
            uid,
            len(roots),
            [str(p) for p in roots],
        )
    if disabled_builtin_names:
        logger.info(
            'disabled builtin skills: user_id=%s names=%s',
            uid,
            disabled_builtin_names,
        )
    return UserSkillsSyncResult(
        roots=roots,
        disabled_builtin_names=disabled_builtin_names,
    )


def merge_user_skill_roots_into_exp_config(
    exp_config: ExpConfig,
    extra_roots: list[Path],
    disabled_skill_names: set[str] | None = None,
) -> ExpConfig:
    """将本机用户 skill 目录追加到 ``ExpConfig.skills.skills_root``，
    并将禁用技能名写入 ``disabled_skill_names``。"""
    skills = exp_config.skills
    updates: dict = {}

    if extra_roots:
        raw = skills.skills_root
        if isinstance(raw, list):
            merged = [str(x) for x in raw if x]
        else:
            merged = [str(raw).strip()] if (raw or '').strip() else []
        for p in extra_roots:
            merged.append(str(p.resolve()))
        updates['skills_root'] = merged

    if disabled_skill_names:
        updates['disabled_skill_names'] = sorted(disabled_skill_names)

    if not updates:
        return exp_config

    return exp_config.model_copy(
        update={'skills': skills.model_copy(update=updates)}
    )
