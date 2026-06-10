"""MatMaster-native Skill & SkillRegistry

与 evomaster/skills/base.py 解耦的独立实现。

核心差异：
- 无 ABC 抽象基类，Skill 为具体类（不需要 to_context_string 抽象）
- SkillRegistry 支持多根目录 (list[Path]) + rglob 递归发现
- 后注册的根目录覆盖先注册的同名 skill
- 跳过 _ 前缀目录和嵌套在其他 skill 子目录内的 SKILL.md
"""

from __future__ import annotations

import json
import logging
import re
import shlex
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, Literal, NamedTuple

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_REMOTE_SKILL_SCAN_SCRIPT = r"""
import json
import os
import sys

root = sys.argv[1]
items = []
for current, dirs, files in os.walk(root):
    dirs[:] = sorted(dirs)
    for filename in sorted(files):
        if filename not in {"SKILL.md", "plugin.yaml"}:
            continue
        path = os.path.join(current, filename)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError as exc:
            items.append({"path": path, "error": str(exc)})
            continue
        items.append({"path": path, "content": content})
items.sort(key=lambda item: item.get("path", ""))
print(json.dumps(items, ensure_ascii=False))
""".strip()


def _optional_strip(value: str | None) -> str | None:
    if value is None:
        return None
    s = value.strip()
    return s if s else None


def _parse_depends_on_list(raw: str | None) -> list[str]:
    if raw is None or not raw.strip():
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


# ---------------------------------------------------------------------------
# SkillMetaInfo
# ---------------------------------------------------------------------------


class SkillMetaInfo(BaseModel):
    """Skill 元信息，从 SKILL.md 的 frontmatter 解析。"""

    name: str = Field(description="技能名称")
    description: str = Field(description="技能描述")
    mcp_server: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict, description="扩展字段")


# ---------------------------------------------------------------------------
# PluginInfo
# ---------------------------------------------------------------------------


class PluginInfo(BaseModel):
    """Plugin 元信息，从 plugin.yaml 瘦清单解析。"""

    name: str
    category: str | None = None
    description: str = ""


def _parse_plugin_info_from_content(content: str, *, fallback_name: str) -> PluginInfo:
    raw = yaml.safe_load(content) or {}
    category = raw.get("category")
    return PluginInfo(
        name=str(raw.get("name") or fallback_name),
        category=str(category).strip() if category else None,
        description=str(raw.get("description") or ""),
    )


def _parse_plugin_info(manifest_path: Path) -> PluginInfo:
    return _parse_plugin_info_from_content(
        manifest_path.read_text(encoding="utf-8"),
        fallback_name=manifest_path.parent.name,
    )


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------


class Skill:
    """单个 Skill 的运行时表示。

    从磁盘上的 skill 目录加载：
    - SKILL.md frontmatter → meta_info (SkillMetaInfo)
    - SKILL.md body → full_info (延迟缓存)
    """

    def __init__(
        self,
        skill_path: Path,
        *,
        plugin: PluginInfo | None = None,
        plugin_dir: Path | None = None,
    ) -> None:
        self.skill_path = skill_path
        self.plugin = plugin
        self.plugin_dir = plugin_dir
        self.meta_info = self._parse_meta_info()
        self._full_info_cache: str | None = None

    # -- meta_info parsing --------------------------------------------------

    def _parse_meta_info(self) -> SkillMetaInfo:
        skill_md = self.skill_path / "SKILL.md"
        if not skill_md.exists():
            raise FileNotFoundError(f"SKILL.md not found in {self.skill_path}")

        content = skill_md.read_text(encoding="utf-8")
        return _parse_meta_info_from_content(
            content, fallback_name=self.skill_path.name
        )

    # -- full_info ----------------------------------------------------------

    def get_full_info(self) -> str:
        """返回 SKILL.md frontmatter 之后的 body 部分（缓存）。"""
        if self._full_info_cache is not None:
            return self._full_info_cache

        content = (self.skill_path / "SKILL.md").read_text(encoding="utf-8")
        self._full_info_cache = _extract_full_info(content)
        return self._full_info_cache


class RemoteSkill:
    """Skill loaded from a session-backed remote root."""

    is_remote: bool = True

    def __init__(
        self,
        skill_path: PurePosixPath,
        content: str,
        *,
        plugin: PluginInfo | None = None,
        plugin_dir: PurePosixPath | None = None,
    ) -> None:
        self.skill_path = skill_path
        self.plugin = plugin
        self.plugin_dir = plugin_dir
        self.meta_info = _parse_meta_info_from_content(
            content,
            fallback_name=skill_path.name,
        )
        self._full_info_cache = _extract_full_info(content)

    def get_full_info(self) -> str:
        return self._full_info_cache


def _parse_meta_info_from_content(content: str, *, fallback_name: str) -> SkillMetaInfo:
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not fm_match:
        raise ValueError("Invalid SKILL.md: no frontmatter")

    known_keys = {"name", "description", "mcp_server", "depends_on"}
    data: dict[str, str] = {}
    for line in fm_match.group(1).split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip()

    extras = {k: v for k, v in data.items() if k not in known_keys}

    return SkillMetaInfo(
        name=data.get("name", fallback_name),
        description=data.get("description", ""),
        mcp_server=_optional_strip(data.get("mcp_server")),
        depends_on=_parse_depends_on_list(data.get("depends_on")),
        extras=extras,
    )


def _extract_full_info(content: str) -> str:
    body_match = re.search(r"^---\s*\n.*?\n---\s*\n(.*)$", content, re.DOTALL)
    return body_match.group(1).strip() if body_match else content


def _remote_skill_scan_command(root: PurePosixPath) -> str:
    return (
        "python3 -c "
        f"{shlex.quote(_REMOTE_SKILL_SCAN_SCRIPT)} "
        f"{shlex.quote(str(root))}"
    )


class _RemoteScanRecord(NamedTuple):
    """远端扫描脚本的单条输出：content 与 error 二选一，kind 由文件名导出。"""

    path: PurePosixPath
    content: str | None
    error: str | None


def _parse_remote_skill_scan_stdout(stdout: Any) -> list[_RemoteScanRecord]:
    payload = json.loads(str(stdout or "[]"))
    if not isinstance(payload, list):
        raise ValueError("remote skill scan payload must be a list")

    records: list[_RemoteScanRecord] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str):
            continue
        if item.get("error"):
            records.append(
                _RemoteScanRecord(PurePosixPath(path), None, str(item["error"]))
            )
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        records.append(_RemoteScanRecord(PurePosixPath(path), content, None))
    return records


def _normalize_remote_roots(remote_roots: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw_root in remote_roots:
        root_text = raw_root.strip()
        if not root_text:
            continue
        normalized = str(PurePosixPath(root_text))
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def read_disabled_plugins(plugins_config_path: Path) -> set[str]:
    """读取 plugins.yaml 的 disabled_plugins 名单；文件缺失等价于留空（全部启用）。"""
    if not plugins_config_path.is_file():
        return set()
    raw = yaml.safe_load(plugins_config_path.read_text(encoding="utf-8")) or {}
    disabled = raw.get("disabled_plugins") or []
    return {str(name).strip() for name in disabled if str(name).strip()}


def expand_disabled_plugins(roots: list[Path], disabled_plugins: set[str]) -> set[str]:
    """把被禁 plugin 展开为其成员 skill 名集合（灌入现有 disabled 通道）。"""
    if not disabled_plugins:
        return set()
    member_names: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for manifest_path in sorted(root.rglob("plugin.yaml")):
            plugin_dir = manifest_path.parent
            if _parse_plugin_info(manifest_path).name not in disabled_plugins:
                continue
            for md_path in sorted(plugin_dir.rglob("SKILL.md")):
                meta = _parse_meta_info_from_content(
                    md_path.read_text(encoding="utf-8"),
                    fallback_name=md_path.parent.name,
                )
                member_names.add(meta.name)
    return member_names


class SkillRegistryCache:
    """Per-query cache for fully built SkillRegistry instances."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[tuple[str, ...], ...], SkillRegistry] = {}

    def get_or_build(
        self,
        key: tuple[tuple[str, ...], ...],
        builder: Callable[[], SkillRegistry],
    ) -> SkillRegistry:
        cached = self._by_key.get(key)
        if cached is None:
            cached = builder()
            self._by_key[key] = cached
        return cached


# ---------------------------------------------------------------------------
# SkillRegistry
# ---------------------------------------------------------------------------


class SkillRegistry:
    """多根目录递归 Skill 注册中心。

    - 支持单个 Path 或 list[Path] 作为根目录
    - rglob("SKILL.md") 递归发现
    - 跳过 _ 前缀目录、跳过嵌套在其他 skill 子目录内的 SKILL.md
    - 后注册根目录的同名 skill 覆盖先注册的
    """

    def __init__(
        self,
        skills_root: Path | list[Path],
        skills: list[str] | None = None,
        *,
        remote_session: Any | None = None,
        remote_roots: list[str] | None = None,
    ) -> None:
        if isinstance(skills_root, Path):
            self._roots = [skills_root]
        else:
            self._roots = list(skills_root)

        self._skills: dict[str, Skill | RemoteSkill] = {}
        self._skill_sources: dict[str, Literal["local", "remote"]] = {}
        self._stats = {
            "local_loaded": 0,
            "remote_loaded": 0,
            "local_over_local": 0,
            "remote_over_local": 0,
            "remote_over_remote": 0,
        }
        normalized_remote_roots = _normalize_remote_roots(remote_roots or [])
        self._load_skills(skills)
        self._load_remote_skills(
            remote_session=remote_session,
            remote_roots=normalized_remote_roots,
            name_filter=skills,
        )
        self._log_build_summary(normalized_remote_roots)

    # -- discovery ----------------------------------------------------------

    def _load_skills(self, name_filter: list[str] | None = None) -> None:
        for root in self._roots:
            if not root.exists():
                continue
            # 收集此 root 下所有 SKILL.md，按路径排序以保证确定性
            skill_md_paths = sorted(root.rglob("SKILL.md"))

            # 预计算已知 skill 目录，用于判断嵌套
            skill_dirs: set[Path] = set()
            plugin_cache: dict[Path, PluginInfo] = {}

            for md_path in skill_md_paths:
                skill_dir = md_path.parent

                # 跳过：目录链上任何一级以 _ 开头
                if self._has_underscore_ancestor(skill_dir, root):
                    continue

                # 跳过：嵌套在已注册 skill 目录的子目录内
                if self._is_nested_under(skill_dir, skill_dirs):
                    continue

                try:
                    plugin_dir = self._find_plugin_dir(skill_dir, root)
                    if plugin_dir is not None and plugin_dir not in plugin_cache:
                        plugin_cache[plugin_dir] = _parse_plugin_info(
                            plugin_dir / "plugin.yaml"
                        )
                    plugin = plugin_cache[plugin_dir] if plugin_dir else None
                    skill = Skill(skill_dir, plugin=plugin, plugin_dir=plugin_dir)
                except Exception:
                    logger.error(
                        "Failed to load skill from %s", skill_dir, exc_info=True
                    )
                    continue

                if name_filter is not None and skill.meta_info.name not in name_filter:
                    continue

                if skill.meta_info.name in self._skills:
                    self._stats["local_over_local"] += 1
                    logger.warning(
                        "Skill %r overridden by %s",
                        skill.meta_info.name,
                        skill_dir,
                    )
                self._skills[skill.meta_info.name] = skill
                self._skill_sources[skill.meta_info.name] = "local"
                self._stats["local_loaded"] += 1
                skill_dirs.add(skill_dir)

    def _load_remote_skills(
        self,
        *,
        remote_session: Any | None,
        remote_roots: list[str],
        name_filter: list[str] | None = None,
    ) -> None:
        if remote_session is None:
            return
        for raw_root in remote_roots:
            root_text = raw_root.strip()
            if not root_text:
                continue
            root = PurePosixPath(root_text)
            try:
                if not remote_session.path_exists(str(root)):
                    continue
                result = remote_session.exec_bash(
                    _remote_skill_scan_command(root),
                    timeout=10,
                )
            except Exception:
                logger.warning(
                    "Failed to scan remote skill root %s", root, exc_info=True
                )
                continue
            if result.get("exit_code") != 0:
                logger.warning(
                    "Remote skill scan failed root=%s exit_code=%s",
                    root,
                    result.get("exit_code"),
                )
                continue

            try:
                remote_records = _parse_remote_skill_scan_stdout(result.get("stdout"))
            except Exception:
                logger.warning(
                    "Remote skill scan returned invalid payload root=%s",
                    root,
                    exc_info=True,
                )
                continue

            plugin_infos: dict[PurePosixPath, PluginInfo] = {}
            invalid_plugin_dirs: set[PurePosixPath] = set()
            skill_records: list[_RemoteScanRecord] = []
            for record in remote_records:
                if record.path.name != "plugin.yaml":
                    skill_records.append(record)
                    continue
                plugin_dir = record.path.parent
                if record.error is not None:
                    logger.warning(
                        "Remote plugin manifest unreadable %s: %s",
                        record.path,
                        record.error,
                    )
                    invalid_plugin_dirs.add(plugin_dir)
                    continue
                try:
                    plugin_infos[plugin_dir] = _parse_plugin_info_from_content(
                        record.content or "",
                        fallback_name=plugin_dir.name,
                    )
                except Exception:
                    logger.warning(
                        "Remote plugin manifest invalid %s",
                        record.path,
                        exc_info=True,
                    )
                    invalid_plugin_dirs.add(plugin_dir)
            known_plugin_dirs = set(plugin_infos) | invalid_plugin_dirs

            skill_dirs: set[PurePosixPath] = set()
            for record in skill_records:
                if record.error is not None:
                    logger.warning(
                        "Remote skill scan skipped %s: %s",
                        record.path,
                        record.error,
                    )
                    continue
                skill_dir = record.path.parent
                if self._has_underscore_ancestor(skill_dir, root):
                    continue
                if self._is_nested_under(skill_dir, skill_dirs):
                    continue
                plugin_dir = self._find_remote_plugin_dir(
                    skill_dir, root, known_plugin_dirs
                )
                if plugin_dir is not None and plugin_dir in invalid_plugin_dirs:
                    logger.error(
                        "Failed to load remote skill from %s: "
                        "invalid plugin manifest %s",
                        skill_dir,
                        plugin_dir / "plugin.yaml",
                    )
                    continue
                plugin = plugin_infos[plugin_dir] if plugin_dir is not None else None
                try:
                    skill = RemoteSkill(
                        skill_dir,
                        record.content or "",
                        plugin=plugin,
                        plugin_dir=plugin_dir,
                    )
                except Exception:
                    logger.error(
                        "Failed to load remote skill from %s",
                        skill_dir,
                        exc_info=True,
                    )
                    continue
                if name_filter is not None and skill.meta_info.name not in name_filter:
                    continue
                if skill.meta_info.name in self._skills:
                    previous_source = self._skill_sources.get(skill.meta_info.name)
                    if previous_source == "local":
                        self._stats["remote_over_local"] += 1
                        logger.debug(
                            "Skill %r selected from remote root %s over local "
                            "fallback %s",
                            skill.meta_info.name,
                            skill_dir,
                            self._skills[skill.meta_info.name].skill_path,
                        )
                    else:
                        self._stats["remote_over_remote"] += 1
                        logger.warning(
                            "Skill %r overridden by %s",
                            skill.meta_info.name,
                            skill_dir,
                        )
                self._skills[skill.meta_info.name] = skill
                self._skill_sources[skill.meta_info.name] = "remote"
                self._stats["remote_loaded"] += 1
                skill_dirs.add(skill_dir)

    def _log_build_summary(self, remote_roots: list[str]) -> None:
        local_fallback = sum(
            1 for source in self._skill_sources.values() if source == "local"
        )
        logger.info(
            "Skill registry built: local_roots=%d remote_roots=%d final=%d "
            "local_loaded=%d remote_loaded=%d remote_over_local=%d "
            "local_over_local=%d remote_over_remote=%d local_fallback=%d",
            len(self._roots),
            len(remote_roots),
            len(self._skills),
            self._stats["local_loaded"],
            self._stats["remote_loaded"],
            self._stats["remote_over_local"],
            self._stats["local_over_local"],
            self._stats["remote_over_remote"],
            local_fallback,
        )

    @staticmethod
    def _find_plugin_dir(skill_dir: Path, root: Path) -> Path | None:
        """skill_dir 到 root 之间第一个含 plugin.yaml 的祖先目录。"""
        current = skill_dir.parent
        while current != root and current != current.parent:
            if (current / "plugin.yaml").exists():
                return current
            current = current.parent
        return None

    @staticmethod
    def _find_remote_plugin_dir(
        skill_dir: PurePosixPath,
        root: PurePosixPath,
        plugin_dirs: set[PurePosixPath],
    ) -> PurePosixPath | None:
        """skill_dir 到 root 之间最近的已知 plugin 目录（镜像 _find_plugin_dir）。"""
        current = skill_dir.parent
        while current != root and current != current.parent:
            if current in plugin_dirs:
                return current
            current = current.parent
        return None

    @staticmethod
    def _has_underscore_ancestor(
        skill_dir: Path | PurePosixPath,
        root: Path | PurePosixPath,
    ) -> bool:
        """skill_dir 到 root 之间是否有以 _ 开头的目录名。"""
        current = skill_dir
        while current != root and current != current.parent:
            if current.name.startswith("_"):
                return True
            current = current.parent
        return False

    @staticmethod
    def _is_nested_under(
        skill_dir: Path | PurePosixPath,
        known_skill_dirs: set[Path] | set[PurePosixPath],
    ) -> bool:
        """skill_dir 是否嵌套在某个已知 skill 目录的子目录内。"""
        parent = skill_dir.parent
        while parent != parent.parent:
            if parent in known_skill_dirs:
                return True
            parent = parent.parent
        return False

    # -- public API ---------------------------------------------------------

    def get_skill(self, name: str) -> Skill | RemoteSkill | None:
        return self._skills.get(name)

    def get_all_skills(self) -> list[Skill | RemoteSkill]:
        return list(self._skills.values())

    def remove_skills(self, names: set[str]) -> None:
        """按名称移除已注册的 skill。"""
        for name in names:
            self._skills.pop(name, None)

    def remove_plugin_members(self, disabled_plugin_names: set[str]) -> set[str]:
        """移除归属于被禁 plugin 的 skill，返回被移除的 skill 名集合。"""
        if not disabled_plugin_names:
            return set()
        removed = {
            name
            for name, skill in self._skills.items()
            if skill.plugin is not None
            and skill.plugin.name in disabled_plugin_names
        }
        for name in removed:
            del self._skills[name]
        return removed

    def get_meta_info_context(self) -> str:
        """可用 skill 汇总：扁平 skill 逐条列出，plugin 成员归组在 plugin 名下。"""
        flat_lines: list[str] = []
        grouped: dict[str, tuple[PluginInfo, list[str]]] = {}
        for skill in self._skills.values():
            line = f"[Skill: {skill.meta_info.name}] {skill.meta_info.description}"
            plugin = skill.plugin
            if plugin is None:
                flat_lines.append(line)
            else:
                grouped.setdefault(plugin.name, (plugin, []))[1].append(line)

        lines = flat_lines
        for plugin, member_lines in grouped.values():
            lines.append(f"[Plugin: {plugin.name}] {plugin.description}")
            lines.extend(f"  {member}" for member in member_lines)
        return "\n".join(lines)
