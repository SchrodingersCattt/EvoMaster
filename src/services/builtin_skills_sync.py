"""应用启动时将仓库内置技能同步到 matmaster-tools-server。

流程：扫描本地 skills 目录 + 读 builtin_tags.yaml →
  1. 每个技能目录打 zip → 算 sha256
  2. 上传 zip 到 tools-server（POST /skills/upload-zip，复用用户技能上传通道）
  3. 调 POST /api/v1/skills/sync-builtin，带上 object_key 等信息
  4. tools-server 侧 materialize zip → 填充 artifact_id / bundle_object_key 等字段
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import zipfile
from pathlib import Path
from typing import Any

import httpx
import yaml

from utils.env import MATMASTER_TOOLS_SERVER

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "matmaster" / "skills"
_TAGS_FILE = _SKILLS_ROOT / "builtin_tags.yaml"

_ZIP_EXCLUDE = frozenset(
    {"__pycache__", ".git", "node_modules", ".mypy_cache", ".pytest_cache", ".DS_Store"}
)


def _load_tags_config() -> dict[str, Any]:
    if not _TAGS_FILE.exists():
        logger.warning("builtin_tags.yaml not found at %s", _TAGS_FILE)
        return {}
    return yaml.safe_load(_TAGS_FILE.read_text(encoding="utf-8")) or {}


def _get_version() -> str:
    try:
        from src.utils.build_info import get_build_version

        return get_build_version() or "unknown"
    except Exception:
        return "unknown"


def _zip_skill_dir(skill_dir: Path) -> tuple[bytes, str, int, int]:
    """打包技能目录为 zip，返回 (zip_bytes, sha256, byte_size, file_count)。"""
    buf = io.BytesIO()
    file_count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in sorted(skill_dir.rglob("*")):
            if not fp.is_file():
                continue
            if any(part in _ZIP_EXCLUDE for part in fp.relative_to(skill_dir).parts):
                continue
            arcname = str(fp.relative_to(skill_dir))
            zf.write(fp, arcname)
            file_count += 1
    zip_bytes = buf.getvalue()
    sha256 = hashlib.sha256(zip_bytes).hexdigest()
    return zip_bytes, sha256, len(zip_bytes), file_count


def _upload_zip_to_tools_server(
    client: httpx.Client,
    base: str,
    zip_bytes: bytes,
    skill_name: str,
) -> str | None:
    """上传 zip 到 tools-server，返回 object_key。复用用户技能的 upload-zip 通道。"""
    url = f"{base}/api/v1/users/__builtin__/skills/upload-zip"
    try:
        files = {"file": (f"{skill_name}.zip", zip_bytes, "application/zip")}
        r = client.post(url, files=files, headers={"X-User-Id": "__builtin__"})
        r.raise_for_status()
        body = r.json()
        if body.get("code") == 0 and body.get("data"):
            return body["data"].get("object_key")
        logger.warning("upload-zip bad response for %s: %s", skill_name, body)
        return None
    except Exception as e:
        logger.warning("upload-zip failed for %s: %s", skill_name, e)
        return None


def _scan_builtin_skills(
    tags_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """扫描 _SKILLS_ROOT 下的 SKILL.md，提取元信息 + 打包 zip。"""
    skill_tags_map: dict[str, list[str]] = tags_config.get("skills", {}) or {}
    results: list[dict[str, Any]] = []

    if not _SKILLS_ROOT.exists():
        return results

    for md_path in sorted(_SKILLS_ROOT.rglob("SKILL.md")):
        skill_dir = md_path.parent
        rel = skill_dir.relative_to(_SKILLS_ROOT)
        if any(p.startswith("_") for p in rel.parts):
            continue

        content = md_path.read_text(encoding="utf-8")
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not fm_match:
            continue

        data: dict[str, str] = {}
        for line in fm_match.group(1).split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                data[key.strip()] = value.strip().strip('"').strip("'")

        name = data.get("name", skill_dir.name)
        description = data.get("description", "")
        tags = skill_tags_map.get(name)

        zip_bytes, sha256, byte_size, file_count = _zip_skill_dir(skill_dir)

        results.append(
            {
                "name": name,
                "description": description,
                "tags": tags,
                "skill_dir": skill_dir,
                "zip_bytes": zip_bytes,
                "content_sha256": sha256,
                "byte_size": byte_size,
                "file_count": file_count,
            }
        )

    return results


def sync_builtin_skills_to_tools_server() -> bool:
    """同步内置技能到 tools-server（含 zip 上传）。返回是否成功。"""
    base = (MATMASTER_TOOLS_SERVER or "").strip().rstrip("/")
    if not base:
        logger.warning("MATMASTER_TOOLS_SERVER empty, skip builtin skills sync")
        return False

    tags_config = _load_tags_config()
    skills = _scan_builtin_skills(tags_config)
    if not skills:
        logger.warning("No builtin skills found, skip sync")
        return False

    tag_definitions = tags_config.get("tag_definitions") or {}
    version = _get_version()

    sync_items: list[dict[str, Any]] = []
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        for skill in skills:
            object_key = _upload_zip_to_tools_server(
                client, base, skill["zip_bytes"], skill["name"]
            )
            item: dict[str, Any] = {
                "name": skill["name"],
                "description": skill["description"],
                "tags": skill.get("tags"),
                "content_sha256": skill["content_sha256"],
                "byte_size": skill["byte_size"],
                "file_count": skill["file_count"],
            }
            if object_key:
                item["bundle_object_key"] = object_key
            sync_items.append(item)
            logger.info(
                "builtin skill packed: name=%s sha256=%s size=%d files=%d key=%s",
                skill["name"],
                skill["content_sha256"][:12],
                skill["byte_size"],
                skill["file_count"],
                object_key or "(upload failed)",
            )

    payload = {
        "version": version,
        "tag_definitions": tag_definitions,
        "skills": sync_items,
    }

    url = f"{base}/api/v1/skills/sync-builtin"
    try:
        with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            body = r.json()
    except Exception as e:
        logger.warning("builtin skills sync failed: %s", e, exc_info=True)
        return False

    if not isinstance(body, dict) or body.get("code") != 0:
        logger.warning("builtin skills sync bad response: %s", body)
        return False

    data = body.get("data", {})
    logger.info(
        "builtin skills synced: version=%s count=%d deleted=%s inserted=%s",
        version,
        len(skills),
        data.get("deleted"),
        data.get("inserted"),
    )
    return True
