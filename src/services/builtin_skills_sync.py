"""应用启动时将仓库内置技能 / 插件同步到 matmaster-tools-server。

流程：扫描本地 skills / plugins 目录 →
  1. 每个技能目录 / plugin 整包打 zip → 算 sha256
  2. 上传 zip 到 tools-server（复用对应 __builtin__ upload-zip 通道）
  3. 调 sync-builtin，带上 object_key 等信息
  4. tools-server 侧 materialize zip → 填充 artifact_id / bundle_object_key 等字段
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Any

import httpx

from matmaster.skills.registry import parse_plugin_info, parse_skill_meta_info
from utils.env import MATMASTER_TOOLS_SERVER

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "matmaster" / "skills"
_PLUGINS_ROOT = Path(__file__).resolve().parents[2] / "matmaster" / "plugins"
_CACHE_DIR = Path(__file__).resolve().parents[2] / "matmaster" / "cache"

_ZIP_EXCLUDE = frozenset(
    {"__pycache__", ".git", "node_modules", ".mypy_cache", ".pytest_cache", ".DS_Store"}
)


def _get_version() -> str:
    try:
        from src.utils.build_info import get_build_version

        return get_build_version() or "unknown"
    except Exception:
        return "unknown"


def _get_build_seq() -> int:
    try:
        from src.utils.build_info import get_build_seq

        return get_build_seq()
    except Exception:
        return 0


def _load_tools_from_cache(mcp_server: str | None) -> list[dict[str, str]] | None:
    """Load tool name+description from cached MCP schema for an mcp-loader skill."""
    if not mcp_server:
        return None
    cache_file = _CACHE_DIR / f"{mcp_server}.json"
    if not cache_file.exists():
        logger.warning("No cached schema for MCP server '%s'", mcp_server)
        return None
    try:
        schemas = json.loads(cache_file.read_text(encoding="utf-8"))
        return [
            {"name": t["name"], "description": t.get("description", "")}
            for t in schemas
        ]
    except Exception as e:
        logger.warning("Failed to load cache for '%s': %s", mcp_server, e)
        return None


_FIXED_ZIP_DATE = (2024, 1, 1, 0, 0, 0)


def _zip_tree(root: Path, *, arc_prefix: str = "") -> tuple[bytes, str, int, int]:
    """打包目录树为 zip，返回 (zip_bytes, sha256, byte_size, file_count)。

    使用固定时间戳确保相同文件内容产生相同 sha256，避免不同机器/容器
    因 mtime 差异导致 sha256 变化从而触发不必要的前端全量同步。

    arc_prefix 非空时，所有 arcname 前缀该值（用于 plugin 整包保留顶层目录名）。
    """
    buf = io.BytesIO()
    file_count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in sorted(root.rglob("*")):
            if not fp.is_file():
                continue
            if any(part in _ZIP_EXCLUDE for part in fp.relative_to(root).parts):
                continue
            arcname = f"{arc_prefix}{fp.relative_to(root)}"
            info = zipfile.ZipInfo(arcname, date_time=_FIXED_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, fp.read_bytes())
            file_count += 1
    zip_bytes = buf.getvalue()
    sha256 = hashlib.sha256(zip_bytes).hexdigest()
    return zip_bytes, sha256, len(zip_bytes), file_count


def _zip_skill_dir(skill_dir: Path) -> tuple[bytes, str, int, int]:
    """打包单个 skill 目录（arcname 相对 skill 根，无顶层目录）。"""
    return _zip_tree(skill_dir)


def _zip_plugin_dir(plugin_dir: Path) -> tuple[bytes, str, int, int]:
    """打包整个 plugin 目录，保留顶层目录名（= plugin 名，D18）。

    zip 内条目形如 ``<plugin名>/plugin.yaml`` / ``<plugin名>/skills/...``，
    与 tools-server plugin 解析器「单一顶层目录」契约及前端 NAS 解包路径一致。
    """
    return _zip_tree(plugin_dir, arc_prefix=f"{plugin_dir.name}/")


def _upload_zip_to_tools_server(
    client: httpx.Client,
    base: str,
    zip_bytes: bytes,
    asset_name: str,
    *,
    path_segment: str = "skills",
) -> str | None:
    """上传 zip 到 tools-server，返回 object_key。

    path_segment 选 ``skills`` / ``plugins``，复用对应 __builtin__ upload-zip 通道。
    """
    url = f"{base}/api/v1/users/__builtin__/{path_segment}/upload-zip"
    try:
        files = {"file": (f"{asset_name}.zip", zip_bytes, "application/zip")}
        r = client.post(url, files=files, headers={"X-User-Id": "__builtin__"})
        r.raise_for_status()
        body = r.json()
        if body.get("code") == 0 and body.get("data"):
            return body["data"].get("object_key")
        logger.warning("upload-zip bad response for %s: %s", asset_name, body)
        return None
    except Exception as e:
        logger.warning("upload-zip failed for %s: %s", asset_name, e)
        return None


def _build_skill_item(skill_dir: Path) -> dict[str, Any] | None:
    """解析单个 skill 目录：frontmatter 提取 + zip 打包。无 frontmatter 返回 None。"""
    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    try:
        meta = parse_skill_meta_info(content, fallback_name=skill_dir.name)
    except ValueError:
        return None

    zip_bytes, sha256, byte_size, file_count = _zip_skill_dir(skill_dir)
    tools = _load_tools_from_cache(meta.mcp_server)

    item: dict[str, Any] = {
        "name": meta.name,
        "description": meta.description,
        "skill_dir": skill_dir,
        "zip_bytes": zip_bytes,
        "content_sha256": sha256,
        "byte_size": byte_size,
        "file_count": file_count,
    }
    if tools is not None:
        item["tools"] = tools
    return item


def _scan_builtin_skills() -> list[dict[str, Any]]:
    """扫描扁平轨 `matmaster/skills/`，散装 skill 无 category/tags（E1）。

    Plugin 轨不再压平进 /skills（D6 翻转）：plugin 成员改由
    ``_scan_builtin_plugins`` 整包发往 /plugins。
    """
    results: list[dict[str, Any]] = []

    if _SKILLS_ROOT.exists():
        for md_path in sorted(_SKILLS_ROOT.rglob("SKILL.md")):
            skill_dir = md_path.parent
            rel = skill_dir.relative_to(_SKILLS_ROOT)
            if any(p.startswith("_") for p in rel.parts):
                continue
            item = _build_skill_item(skill_dir)
            if item is None:
                continue
            results.append(item)

    return results


def _enumerate_plugin_members(plugin_dir: Path) -> list[dict[str, str]]:
    """递归枚举 plugin 成员 skill（D14）：dir 取相对 plugin 根的最近目录，跳 `_` 前缀链。"""
    members: list[dict[str, str]] = []
    for md_path in sorted(plugin_dir.rglob("SKILL.md")):
        skill_dir = md_path.parent
        rel = skill_dir.relative_to(plugin_dir)
        if any(p.startswith("_") for p in rel.parts):
            continue
        try:
            meta = parse_skill_meta_info(
                md_path.read_text(encoding="utf-8"), fallback_name=skill_dir.name
            )
        except ValueError:
            continue
        rel_str = "" if str(rel) == "." else str(rel)
        members.append(
            {
                "name": meta.name,
                "description": meta.description,
                "dir": rel_str,
            }
        )
    return members


def _scan_builtin_plugins() -> list[dict[str, Any]]:
    """扫描 `matmaster/plugins/`，每个 plugin 产出整包条目（含成员列表，D14）。"""
    results: list[dict[str, Any]] = []
    if not _PLUGINS_ROOT.exists():
        return results
    for manifest_path in sorted(_PLUGINS_ROOT.rglob("plugin.yaml")):
        plugin_dir = manifest_path.parent
        rel = plugin_dir.relative_to(_PLUGINS_ROOT)
        if any(p.startswith("_") for p in rel.parts):
            continue
        plugin = parse_plugin_info(manifest_path)
        members = _enumerate_plugin_members(plugin_dir)
        if not members:
            logger.warning("plugin %s has no member skills, skip", plugin.name)
            continue
        zip_bytes, sha256, byte_size, file_count = _zip_plugin_dir(plugin_dir)
        results.append(
            {
                "name": plugin.name,
                "description": plugin.description,
                "category": plugin.category,
                "member_skills": members,
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

    skills = _scan_builtin_skills()
    if not skills:
        logger.warning("No builtin skills found, skip sync")
        return False

    version = _get_version()
    build_seq = _get_build_seq()

    sync_items: list[dict[str, Any]] = []
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        for skill in skills:
            object_key = _upload_zip_to_tools_server(
                client, base, skill["zip_bytes"], skill["name"]
            )
            item: dict[str, Any] = {
                "name": skill["name"],
                "description": skill["description"],
                "content_sha256": skill["content_sha256"],
                "byte_size": skill["byte_size"],
                "file_count": skill["file_count"],
            }
            if object_key:
                item["bundle_object_key"] = object_key
            if skill.get("tools"):
                item["tools"] = skill["tools"]
            sync_items.append(item)
            logger.info(
                "builtin skill packed: name=%s sha256=%s size=%d files=%d key=%s",
                skill["name"],
                skill["content_sha256"][:12],
                skill["byte_size"],
                skill["file_count"],
                object_key or "(upload failed)",
            )

    payload: dict[str, Any] = {
        "version": version,
        "build_seq": build_seq,
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


def sync_builtin_plugins_to_tools_server() -> bool:
    """同步内置 plugin 到 tools-server（整包 zip 上传 + /plugins/sync-builtin）。

    与 skill 同步独立、互不阻塞；plugin 成员不再压平进 /skills（D6 翻转）。
    """
    base = (MATMASTER_TOOLS_SERVER or "").strip().rstrip("/")
    if not base:
        logger.warning("MATMASTER_TOOLS_SERVER empty, skip builtin plugins sync")
        return False

    plugins = _scan_builtin_plugins()
    if not plugins:
        logger.warning("No builtin plugins found, skip sync")
        return False

    version = _get_version()
    build_seq = _get_build_seq()

    sync_items: list[dict[str, Any]] = []
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        for plugin in plugins:
            object_key = _upload_zip_to_tools_server(
                client,
                base,
                plugin["zip_bytes"],
                plugin["name"],
                path_segment="plugins",
            )
            item: dict[str, Any] = {
                "name": plugin["name"],
                "description": plugin["description"],
                "category": plugin.get("category"),
                "member_skills": plugin["member_skills"],
                "content_sha256": plugin["content_sha256"],
                "byte_size": plugin["byte_size"],
                "file_count": plugin["file_count"],
            }
            if object_key:
                item["bundle_object_key"] = object_key
            sync_items.append(item)
            logger.info(
                "builtin plugin packed: name=%s members=%d sha256=%s size=%d key=%s",
                plugin["name"],
                len(plugin["member_skills"]),
                plugin["content_sha256"][:12],
                plugin["byte_size"],
                object_key or "(upload failed)",
            )

    payload: dict[str, Any] = {
        "version": version,
        "build_seq": build_seq,
        "plugins": sync_items,
    }

    url = f"{base}/api/v1/plugins/sync-builtin"
    try:
        with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            body = r.json()
    except Exception as e:
        logger.warning("builtin plugins sync failed: %s", e, exc_info=True)
        return False

    if not isinstance(body, dict) or body.get("code") != 0:
        logger.warning("builtin plugins sync bad response: %s", body)
        return False

    data = body.get("data", {})
    logger.info(
        "builtin plugins synced: version=%s count=%d deleted=%s inserted=%s",
        version,
        len(plugins),
        data.get("deleted"),
        data.get("inserted"),
    )
    return True
