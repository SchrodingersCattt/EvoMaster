# Skill 双轨与 Plugin 层 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/superpowers/specs/2026-06-10-skill-plugin-dual-track-design.md` 落地 skill 双轨制：新增 `matmaster/plugins/` 兄弟根承载 15 个 plugin（30 skill），扁平轨保留 11 个 skill，删除 10 个 playground skill 与 skill_type 死代码。

**Architecture:** 运行时改动先行落地（plugins 根不存在时所有新代码自然退化为现状，每步保持绿）；随后一次性迁移脚本完成目录重排与资产内引用修复；最后删死代码、全量验证。skill 命中/载入链路零变化，触点仅限：registry 的 plugin 归属、注册门控、列表分组渲染、`${PLUGIN_DIR}` 替换、sync 双根。

**Tech Stack:** Python 3.13（仓库 `.venv`）、pydantic、PyYAML、pytest（asyncio_mode=auto）、pre-commit（black/isort/flake8，--max-line-length=88）。

---

## 0. 执行须知（先读）

- **测试约束（用户规则 + spec §8，覆盖默认 TDD 流程）**：严禁新增测试文件、严禁新增测试用例；受影响的现有测试只做布局适配更新。净代码量预期持平或下降。
- **严禁兼容/兜底逻辑**：所有迁移动作走外部脚本 `scripts/migrate_to_plugins.py`；主代码只写终态逻辑（"plugins 根不存在 → 扫不到东西"是终态语义，不是兼容分支）。
- 测试命令统一用 `.venv/bin/python -m pytest`；格式化用 `pre-commit run --files <改动文件>`（若 `pre-commit` 不在 PATH，用 `uvx pre-commit`）。仓库 lint 是 flake8+black+isort，**不是 ruff**。
- 每个 commit message 末尾附 `Co-Authored-By:` 行（按当前 harness 惯例）。
- 开始前确认工作树干净：`git status --porcelain` 为空（当前分支 `codex/provider-stage1` 有一处 docs 改动，先让用户处理或提交）。

### 0.1 计划级裁定（spec 未明示，已据代码事实裁定）

| 事项 | 裁定 | 依据 |
|------|------|------|
| `playground-skills/_common/`（longtask_runtime.py + reference/） | 随剪枝删除 | 全仓 grep：仅被待删的 manuscript-scribe / lit-data-organizer / deep-survey 引用 |
| `playground-skills/polyFF/`（assets + scripts，无 SKILL.md） | 随剪枝删除 | 仅被待删的 poly-forcefield 引用 |
| `playground-skills/bohrium-job/`（无 SKILL.md） | 随剪枝删除 | 空壳：`scripts/` 下只有 `__pycache__`；无任何路径引用 |
| composition-optimization（保留 skill）正文引用 deep-survey / lit-data-organizer / manuscript-scribe（均待删） | spec 漏列的硬引用，迁移脚本内定点改写 | 见 Task 5 `CONTENT_FIXES` |
| `src/services/skill_registry_factory.py` | **不改** | 全仓 grep：无生产调用方（仅测试调用）；门控展开只接入核心通道 `build_cached_skill_registry` |
| plugins.yaml 文件缺失 | 等价于 `disabled_plugins: []`（全部启用） | 门控语义"留空即全部启用"的自然延伸；config_dir 未配置的环境（大量测试 fixture）无需创建该文件 |
| 列表分组渲染格式 | 扁平行 `[Skill: name] desc` 维持原样；plugin 块为 `[Plugin: name] desc` + 两空格缩进的成员行 | 不破坏现有断言（`"[Skill: x]" in ctx`）；spec §3.6 只要求"归组展示" |
| 生产 skills_root 配置点 | `matmaster/exps/direct.toml:91`、`matmaster/exps/planner.toml:481`（`agent_run_service.py:325` 经 `load_exp_config` 读取） | 全仓 grep 确认无其他生产配置点；仓库外部署无 skills_root 覆盖 |
| `evaluation/` 生成物（semantic_coverage_report.json、uncovered_rules_full.md 等） | 不清扫 | 历史快照，非活代码；活的评测资产（cases.yaml、loop_prompts.py、AGENTS_evaluation.md）照常清扫 |
| `docs/superpowers/` 下历史 spec/plan | 不清扫 | 历史文档 |

### 0.2 关键代码事实速查（执行者零上下文需要）

- `matmaster/skills/registry.py`（493 行）：`Skill`（112-138）、`RemoteSkill`（141-155）、`_parse_meta_info_from_content`（158-182）、`SkillRegistry._load_skills`（298-340）、`get_meta_info_context`（485-492）。skill_type 链：`SkillTypeLiteral`（67）、`_parse_skill_type`（70-80）、`SkillMetaInfo.skill_type`（93）、known_keys 表项（163）、构造传参（178）。
- `matmaster/tools/builtin/skill_tool.py`：`${SKILL_DIR}` 单点替换（92）、depends_on 递归（95-98）、`_render_skill_dir`（117-133，含 remote_project_root 映射）。
- `matmaster/core/skill_registry_cache.py`（91 行）：缓存键三元组（本地根 / 远程根 / 禁用名单），`build_cached_skill_registry`（42-91），`config_disabled_skill_names` 在 62 行计算、**先于**缓存键。
- `src/services/builtin_skills_sync.py`（289 行）：`_SKILLS_ROOT`（29）、`_load_tags_config`（38-68）、`_scan_builtin_skills`（157-212）。无任何测试覆盖该模块。
- 51 个 skill 实测分布：顶层 27、`lazymcp/` 6、`planner/` 5、`playground-skills/` 13（另有 `_common`、`bohrium-job`、`polyFF` 三个非 skill 目录）。`quantum_espresso` 目录名是下划线。
- depends_on 实测：retrieve-structure → `mcp-mat-struct-db, mcp-mat-doc`；composition-optimization → `mcp-mat-compdart, mcp-mat-struct-db, mcp-mat-doc`。与 spec §3.5 一致。

---

### Task 1: Registry plugin 归属 + 列表分组渲染

**Files:**
- Modify: `matmaster/skills/registry.py`

- [ ] **Step 1.1: 加 yaml import**

`matmaster/skills/registry.py` 第 22 行 `from pydantic import BaseModel, Field` 之后加一行（isort 顺序由收尾 pre-commit 校正）：

```python
from pydantic import BaseModel, Field

import yaml
```

实际 Edit：
```
old: from pydantic import BaseModel, Field
new: import yaml
     from pydantic import BaseModel, Field
```

- [ ] **Step 1.2: 新增 PluginInfo 与清单解析**

在 `SkillMetaInfo` 类定义（96 行 `extras` 字段行）之后、`# Skill` 分节注释之前插入：

```python
# ---------------------------------------------------------------------------
# PluginInfo
# ---------------------------------------------------------------------------


class PluginInfo(BaseModel):
    """Plugin 元信息，从 plugin.yaml 瘦清单解析。"""

    name: str
    category: str | None = None
    description: str = ""


def _parse_plugin_info(manifest_path: Path) -> PluginInfo:
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    category = raw.get("category")
    return PluginInfo(
        name=str(raw.get("name") or manifest_path.parent.name),
        category=str(category).strip() if category else None,
        description=str(raw.get("description") or ""),
    )
```

- [ ] **Step 1.3: Skill / RemoteSkill 增加 plugin 属性**

`Skill.__init__`（112-115 行）改为：

```python
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
```

`RemoteSkill` 类头（141-144 行）改为（远程根全是扁平用户 skill，无 plugin 归属）：

```python
class RemoteSkill:
    """Skill loaded from a session-backed remote root."""

    is_remote: bool = True
    plugin: PluginInfo | None = None
    plugin_dir: Path | None = None
```

- [ ] **Step 1.4: `_load_skills` 接入 plugin 归属探测**

整体替换 `_load_skills`（298-340 行）为：

```python
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
```

- [ ] **Step 1.5: 新增 `_find_plugin_dir` 静态方法**

放在 `_has_underscore_ancestor`（446 行）之前：

```python
    @staticmethod
    def _find_plugin_dir(skill_dir: Path, root: Path) -> Path | None:
        """skill_dir 到 root 之间第一个含 plugin.yaml 的祖先目录。"""
        current = skill_dir.parent
        while current != root and current != current.parent:
            if (current / "plugin.yaml").exists():
                return current
            current = current.parent
        return None
```

- [ ] **Step 1.6: `get_meta_info_context` 分组渲染**

整体替换（485-492 行）：

```python
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
```

- [ ] **Step 1.7: 跑受影响测试（plugins 根尚不存在，行为应不变）**

```bash
.venv/bin/python -m pytest tests/test_skill_registry.py tests/matmaster/tools/test_skill_meta_extras.py tests/test_skill_tool.py tests/matmaster/core/test_skill_registry_cache.py -q
```
预期：全部 PASS。

- [ ] **Step 1.8: Commit**

```bash
git add matmaster/skills/registry.py
git commit -m "feat(skills): plugin attribution via plugin.yaml ancestry + grouped skill listing"
```

---

### Task 2: SkillTool `${PLUGIN_DIR}` 替换

**Files:**
- Modify: `matmaster/tools/builtin/skill_tool.py`

- [ ] **Step 2.1: execute() 中平行替换 `${PLUGIN_DIR}`**

90-92 行：

```python
            body = skill.get_full_info()
            skill_dir = self._render_skill_dir(skill)
            body = body.replace("${SKILL_DIR}", skill_dir)
```

改为（扁平 skill 的 `plugin_dir` 为 None → 不替换，正文中的 `${PLUGIN_DIR}` 原样保留，作者错误醒目可见，spec §3.4）：

```python
            body = skill.get_full_info()
            skill_dir = self._render_skill_dir(skill)
            body = body.replace("${SKILL_DIR}", skill_dir)
            if skill.plugin_dir is not None:
                body = body.replace(
                    "${PLUGIN_DIR}", self._render_local_dir(skill.plugin_dir)
                )
```

- [ ] **Step 2.2: 抽取 `_render_local_dir`（复用 remote_project_root 映射）**

整体替换 `_render_skill_dir`（117-133 行）为：

```python
    def _render_skill_dir(self, skill: Skill) -> str:
        skill_path = skill.skill_path
        if getattr(skill, "is_remote", False):
            return str(skill_path)
        return self._render_local_dir(skill_path)

    def _render_local_dir(self, path: Path) -> str:
        local_abs = path if path.is_absolute() else path.resolve()

        session = self._session
        remote_project_root = getattr(session, "remote_project_root", None)
        if remote_project_root:
            try:
                rel = local_abs.relative_to(_PROJECT_ROOT)
                return str(PurePosixPath(remote_project_root) / rel.as_posix())
            except ValueError:
                pass

        return str(local_abs)
```

- [ ] **Step 2.3: 跑受影响测试**

```bash
.venv/bin/python -m pytest tests/test_skill_tool.py tests/matmaster/tools/ tests/test_skill_registry.py -q
```
预期：全部 PASS。

- [ ] **Step 2.4: Commit**

```bash
git add matmaster/tools/builtin/skill_tool.py
git commit -m "feat(skills): resolve \${PLUGIN_DIR} to owning plugin root in SkillTool"
```

---

### Task 3: 注册门控（config/plugins.yaml）

**Files:**
- Create: `config/plugins.yaml`
- Modify: `matmaster/skills/registry.py`（追加两个模块级函数）
- Modify: `matmaster/core/skill_registry_cache.py`

- [ ] **Step 3.1: 创建 `config/plugins.yaml`**

```yaml
# Plugin 注册门控：列出的 plugin 整体禁用（其成员 skill 不注册）。
# 留空即全部启用。示例：disabled_plugins: [gpumd]
disabled_plugins: []
```

- [ ] **Step 3.2: registry.py 追加门控展开函数**

放在 `_normalize_remote_roots`（218-230 行）之后、`SkillRegistryCache` 之前：

```python
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
```

- [ ] **Step 3.3: skill_registry_cache.py 接线**

imports（1-20 行）改为：

```python
from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from matmaster.config.exp import ExpSkillsConfig
from matmaster.skills.registry import (
    SkillRegistry,
    SkillRegistryCache,
    _normalize_remote_roots,
    expand_disabled_plugins,
    read_disabled_plugins,
)
from matmaster.skills.settings import (
    disabled_skill_names_from_remote_settings as _disabled_skill_names_from_remote_settings,
)
from matmaster.skills.settings import (
    disabled_skill_names_from_settings as _disabled_skill_names_from_settings,
)
from matmaster.skills.settings import local_user_skills_root as _local_user_skills_root
from matmaster.skills.settings import remote_skill_roots as _remote_skill_roots

logger = logging.getLogger(__name__)
```

62 行：

```python
    config_disabled_skill_names = _normalized_names(skills_cfg.disabled_skill_names)
```

改为（展开发生在缓存键计算**之前**，键三元组自动含展开名 → plugins.yaml 变更后缓存失效自动正确，注册侧无新逻辑，spec §3.5）：

```python
    plugin_disabled = expand_disabled_plugins(
        roots,
        read_disabled_plugins(Path(skills_cfg.config_dir) / "plugins.yaml"),
    )
    config_disabled_skill_names = _normalized_names(
        [*skills_cfg.disabled_skill_names, *plugin_disabled]
    )
```

builder 末尾（87-89 行）：

```python
        if disabled_skill_names:
            registry.remove_skills(disabled_skill_names)
        return registry
```

改为（depends_on 条款：指向被禁 plugin 成员 → warning，不阻断，spec §3.5）：

```python
        if disabled_skill_names:
            registry.remove_skills(disabled_skill_names)
        if plugin_disabled:
            for skill in registry.get_all_skills():
                broken = [
                    dep
                    for dep in skill.meta_info.depends_on
                    if dep in plugin_disabled
                ]
                if broken:
                    logger.warning(
                        "Skill %r depends_on member(s) of disabled plugin(s): %s",
                        skill.meta_info.name,
                        ", ".join(broken),
                    )
        return registry
```

说明：`ExpSkillsConfig.config_dir` 默认 `""` → `Path("plugins.yaml")` 不存在 → 空集，未配置 config_dir 的环境天然无门控。`config_dir="config"` 的测试读到本任务创建的空名单文件，行为不变。

- [ ] **Step 3.4: 跑受影响测试**

```bash
.venv/bin/python -m pytest tests/matmaster/core/test_skill_registry_cache.py tests/matmaster/core/test_exp_skills.py tests/matmaster/devshell/ -q
```
预期：全部 PASS。

- [ ] **Step 3.5: Commit**

```bash
git add config/plugins.yaml matmaster/skills/registry.py matmaster/core/skill_registry_cache.py
git commit -m "feat(skills): plugin-level disable gate via config/plugins.yaml expansion"
```

---

### Task 4: builtin_skills_sync 双根改造

**Files:**
- Modify: `src/services/builtin_skills_sync.py`

- [ ] **Step 4.1: 双根常量**

29 行 `_SKILLS_ROOT` 定义后增加一行：

```python
_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "matmaster" / "skills"
_PLUGINS_ROOT = Path(__file__).resolve().parents[2] / "matmaster" / "plugins"
```

- [ ] **Step 4.2: 重构扫描——提取 `_build_skill_item` + 双根 `_scan_builtin_skills`**

整体替换 `_scan_builtin_skills`（157-212 行）为以下两个函数（payload 结构不变，tools-server 无感，spec §3.7；plugin 成员 category 取 plugin.yaml、group 即 plugin 名 → `tags=[plugin_name]`）：

```python
def _build_skill_item(
    skill_dir: Path,
    *,
    category: str | None,
    tags: list[str] | None,
) -> dict[str, Any] | None:
    """解析单个 skill 目录：frontmatter 提取 + zip 打包。无 frontmatter 返回 None。"""
    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not fm_match:
        return None

    data: dict[str, str] = {}
    for line in fm_match.group(1).split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip().strip('"').strip("'")

    name = data.get("name", skill_dir.name)
    zip_bytes, sha256, byte_size, file_count = _zip_skill_dir(skill_dir)
    tools = _load_tools_from_cache(data.get("mcp_server"))

    item: dict[str, Any] = {
        "name": name,
        "description": data.get("description", ""),
        "category": category,
        "tags": tags,
        "skill_dir": skill_dir,
        "zip_bytes": zip_bytes,
        "content_sha256": sha256,
        "byte_size": byte_size,
        "file_count": file_count,
    }
    if tools is not None:
        item["tools"] = tools
    return item


def _scan_builtin_skills(
    tags_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """扫描双根：扁平轨 category/tags 取自 builtin_tags，plugin 成员取自 plugin.yaml。"""
    skill_tags_map: dict[str, list[str]] = tags_config.get("skills", {}) or {}
    skill_category_map: dict[str, str] = tags_config.get("skill_categories", {}) or {}
    results: list[dict[str, Any]] = []

    if _SKILLS_ROOT.exists():
        for md_path in sorted(_SKILLS_ROOT.rglob("SKILL.md")):
            skill_dir = md_path.parent
            rel = skill_dir.relative_to(_SKILLS_ROOT)
            if any(p.startswith("_") for p in rel.parts):
                continue
            item = _build_skill_item(skill_dir, category=None, tags=None)
            if item is None:
                continue
            item["category"] = skill_category_map.get(item["name"])
            item["tags"] = skill_tags_map.get(item["name"])
            results.append(item)

    if _PLUGINS_ROOT.exists():
        for manifest_path in sorted(_PLUGINS_ROOT.rglob("plugin.yaml")):
            plugin_dir = manifest_path.parent
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            plugin_name = str(manifest.get("name") or plugin_dir.name)
            raw_category = manifest.get("category")
            category = str(raw_category).strip() if raw_category else None
            for md_path in sorted(plugin_dir.rglob("SKILL.md")):
                skill_dir = md_path.parent
                rel = skill_dir.relative_to(plugin_dir)
                if any(p.startswith("_") for p in rel.parts):
                    continue
                item = _build_skill_item(
                    skill_dir, category=category, tags=[plugin_name]
                )
                if item is not None:
                    results.append(item)

    return results
```

- [ ] **Step 4.3: 冒烟验证（迁移前应仍是 51 项，行为与现状一致）**

```bash
.venv/bin/python -c "
from src.services.builtin_skills_sync import _load_tags_config, _scan_builtin_skills
items = _scan_builtin_skills(_load_tags_config())
print('items:', len(items))
assert len(items) == 51, len(items)
"
```
预期输出 `items: 51`（_PLUGINS_ROOT 不存在 → 第二段为 no-op）。

- [ ] **Step 4.4: Commit**

```bash
git add src/services/builtin_skills_sync.py
git commit -m "feat(sync): dual-root builtin skills scan, plugin members tagged via plugin.yaml"
```

---

### Task 5: 迁移脚本 `scripts/migrate_to_plugins.py`

**Files:**
- Create: `scripts/migrate_to_plugins.py`

一次性脚本，职责边界：**只动 skill 资产**（`matmaster/skills/`、`matmaster/plugins/`、`builtin_tags.yaml`、存留 SKILL.md 的 frontmatter 与正文定点修复），不动任何 python 业务代码与测试（那些在 Task 6 用 Edit 完成）。

- [ ] **Step 5.1: 写入完整脚本**

```python
"""一次性迁移：skill 双轨化（spec 2026-06-10 §3.3/§4/§6.1）。跑完即弃。

用法：python scripts/migrate_to_plugins.py
失败恢复：git checkout -- matmaster/ && git clean -fd matmaster/
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "matmaster" / "skills"
PLUGINS = REPO / "matmaster" / "plugins"

# --- §4 剪枝：10 个 skill + 3 个仅被它们引用/空壳的非 skill 目录 ---
DELETE_DIRS = [
    "compliance-guardian",
    "deep-survey",
    "lit-data-organizer",
    "manuscript-scribe",
    "md-analysis",
    "poly-forcefield",
    "poly-generator",
    "result-analysis",
    "tasker-polar-surface",
    "vaspkit-postprocess",
    "_common",  # 仅被上列待删 skill 引用
    "bohrium-job",  # 空壳：scripts/ 下仅 __pycache__
    "polyFF",  # 仅被 poly-forcefield 引用
]

# --- §3.3 plugin 清单：plugin 名 → (category, {成员目录名: 相对 SKILLS 的现路径}) ---
PLUGINS_SPEC: dict[str, tuple[str, dict[str, str]]] = {
    "atomic-structure-ops": (
        "structure-modeling",
        {
            "atomic-structure": "atomic-structure",
            "inspect-atomic-structure": "inspect-atomic-structure",
            "build-crystal-from-params": "build-crystal-from-params",
            "transform-atomic-structure": "transform-atomic-structure",
            "assemble-atomic-structure": "assemble-atomic-structure",
            "operate-molecular-crystal": "operate-molecular-crystal",
            "sample-atomic-structures": "sample-atomic-structures",
        },
    ),
    "structure-search": (
        "structure-modeling",
        {
            "mcp-mat-struct-db": "lazymcp/mcp-mat-struct-db",
            "retrieve-structure": "retrieve-structure",
        },
    ),
    "abacus": ("simulation", {"abacus": "abacus", "pyatb": "pyatb"}),
    "mlips": ("simulation", {"mlips": "mlips", "aissq-explorer": "aissq-explorer"}),
    "data-mining": (
        "analysis",
        {
            "mcp-mat-compdart": "lazymcp/mcp-mat-compdart",
            "composition-optimization": "playground-skills/composition-optimization",
        },
    ),
    "task-planning": (
        "workflow-system",
        {
            "plan-writer": "planner/plan-writer",
            "plan-checker": "planner/plan-checker",
            "plan-executor": "plan-executor",
            "spec-writer": "planner/spec-writer",
            "acceptance-writer": "planner/acceptance-writer",
            "stack-checker": "planner/stack-checker",
        },
    ),
    "vasp": ("simulation", {"vasp": "vasp"}),
    "cp2k": ("simulation", {"cp2k": "cp2k"}),
    "quantum-espresso": ("simulation", {"quantum_espresso": "quantum_espresso"}),
    "abinit": ("simulation", {"abinit": "abinit"}),
    "pyscf": ("simulation", {"pyscf": "pyscf"}),
    "orca": ("simulation", {"orca": "orca"}),
    "lammps": ("simulation", {"lammps": "lammps"}),
    "gromacs": ("simulation", {"gromacs": "gromacs"}),
    "gpumd": ("simulation", {"gpumd": "gpumd"}),
}

# --- 幸存者上移扁平根：相对 SKILLS 的现路径 → 顶层目录名 ---
MOVE_TO_FLAT = {
    "lazymcp/mcp-mat-doc": "mcp-mat-doc",
    "lazymcp/mcp-mat-xrd": "mcp-mat-xrd",
    "lazymcp/mcp-mat-nmr": "mcp-mat-nmr",
    "lazymcp/mcp-mat-electron-microscope": "mcp-mat-electron-microscope",
    "playground-skills/pxrd-refinement": "pxrd-refinement",
    "playground-skills/checkcif-validator": "checkcif-validator",
}

LEGACY_SUBDIRS = ["lazymcp", "planner", "playground-skills"]

DELETED_SKILL_NAMES = DELETE_DIRS[:10]

BUILTIN_TAGS_REDUCED = """\
categories:
  analysis:
    groups:
      general-data-analysis:
        skills:
          - data-analysis
      characterization:
        skills:
          - pxrd-refinement
          - checkcif-validator
          - mcp-mat-xrd
          - mcp-mat-nmr
          - mcp-mat-electron-microscope

  research-writing:
    groups:
      literature:
        skills:
          - mcp-mat-doc
      academic-writing:
        skills:
          - proposal-review

  workflow-system:
    groups:
      system-tools:
        skills:
          - skill-manager
          - image-manager
          - session-analyzer
"""

# --- skill 资产内旧引用定点修复（路径为迁移后的新位置）---
_GROMACS_OLD = (
    "## Post-Processing\n"
    "\n"
    "After job completion, use the **md-analysis** skill for trajectory "
    "analysis (RMSD, RMSF, RDF, MSD, H-bonds, energy).\n"
)
_TASKER_OLD = (
    "   Use `../playground-skills/tasker-polar-surface/SKILL.md`. If the material\n"
    "   and Miller index are not in local lookup data, search literature before\n"
    "   finalizing the provisional Tasker type. Always validate the actual slab after\n"
    "   construction.\n"
)
_TASKER_NEW = (
    "   Handle the polar analysis inline: classify the Tasker type from the\n"
    "   stacking sequence, prefer nonpolar or symmetric terminations, and search\n"
    "   literature when the material and Miller index are unfamiliar. Always\n"
    "   validate the actual slab after construction.\n"
)
_COMPOPT_STEP2_OLD = (
    "   - If not provided (or if literature search is planned regardless):\n"
    "     - Call `deep-survey` to collect evidence. Depth choice: `--depth brief`"
    " for seed-only sub-step (3-5 calls, no report); `--depth standard` for"
    " concise survey file + evidence (6-8 calls); `--depth deep` only when user"
    " explicitly wants a comprehensive review.\n"
    "     - `deep-survey` always produces `collected_<topic>.json`. Pass it to"
    " `lit-data-organizer` (build_lit_table.py) to build the canonical evidence"
    " table before sampling seeds.\n"
)
_COMPOPT_STEP2_NEW = (
    "   - If not provided (or if literature search is planned regardless),\n"
    "     collect literature evidence with `mat_doc_*` / `mat_sn_*` tools and\n"
    "     record candidate compositions with sources before sampling seeds.\n"
)
_COMPOPT_TABLE_OLD = (
    "| Initial data: No, Surrogate: Yes | deep-survey -> lit-data-organizer ->"
    " seeds -> composition->structure if needed -> run DART GA |\n"
    "| Initial data: No, Surrogate: No | deep-survey -> lit-data-organizer ->"
    " seeds -> composition->structure if needed -> screening/fallback |\n"
)
_COMPOPT_TABLE_NEW = (
    "| Initial data: No, Surrogate: Yes | literature search -> seeds ->"
    " composition->structure if needed -> run DART GA |\n"
    "| Initial data: No, Surrogate: No | literature search -> seeds ->"
    " composition->structure if needed -> screening/fallback |\n"
)

CONTENT_FIXES: list[tuple[str, str, str]] = [
    ("plugins/gromacs/skills/gromacs/SKILL.md", _GROMACS_OLD, ""),
    (
        "plugins/atomic-structure-ops/skills/atomic-structure/SKILL.md",
        _TASKER_OLD,
        _TASKER_NEW,
    ),
    (
        "plugins/atomic-structure-ops/skills/inspect-atomic-structure/SKILL.md",
        "matmaster/skills/playground-skills/retrieve-structure/scripts/assess_structure.py",
        "matmaster/plugins/structure-search/skills/retrieve-structure/scripts/assess_structure.py",
    ),
    (
        "plugins/abacus/skills/abacus/references/output_params.md",
        " For scripted extraction and plots after a run, see"
        " `matmaster/skills/playground-skills/result-analysis`"
        " (`parse_abacus.py`, `plot_publication.py`).",
        "",
    ),
    (
        "plugins/data-mining/skills/composition-optimization/SKILL.md",
        _COMPOPT_STEP2_OLD,
        _COMPOPT_STEP2_NEW,
    ),
    (
        "plugins/data-mining/skills/composition-optimization/SKILL.md",
        _COMPOPT_TABLE_OLD,
        _COMPOPT_TABLE_NEW,
    ),
]


def _move(src: Path, dst: Path) -> None:
    assert src.is_dir(), f"missing source: {src}"
    assert not dst.exists(), f"destination exists: {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _strip_skill_type(md_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8")
    match = re.match(r"^(---\s*\n)(.*?)(\n---\s*\n)", text, re.DOTALL)
    if not match:
        return
    kept = [
        line
        for line in match.group(2).split("\n")
        if not line.strip().startswith("skill_type:")
    ]
    new_text = text[: match.start(2)] + "\n".join(kept) + text[match.end(2) :]
    if new_text != text:
        md_path.write_text(new_text, encoding="utf-8")


def _apply_content_fixes() -> None:
    for rel, old, new in CONTENT_FIXES:
        path = REPO / "matmaster" / rel
        text = path.read_text(encoding="utf-8")
        count = text.count(old)
        assert count == 1, f"{path}: expected 1 occurrence, found {count}:\n{old!r}"
        path.write_text(text.replace(old, new).rstrip() + "\n", encoding="utf-8")


def _purge_caches(root: Path) -> None:
    for junk in root.rglob("__pycache__"):
        shutil.rmtree(junk)
    for junk in root.rglob(".DS_Store"):
        junk.unlink()


def _verify() -> None:
    flat = sorted(p.parent.name for p in SKILLS.rglob("SKILL.md"))
    assert len(flat) == 11, f"flat skills != 11: {flat}"
    manifests = sorted(PLUGINS.rglob("plugin.yaml"))
    assert len(manifests) == 15, f"plugin.yaml != 15: {manifests}"
    members = sorted(p.parent.name for p in PLUGINS.rglob("SKILL.md"))
    assert len(members) == 30, f"plugin members != 30: {members}"
    for legacy in LEGACY_SUBDIRS:
        assert not (SKILLS / legacy).exists(), f"legacy dir survives: {legacy}"
    pool = "\n".join(
        p.read_text(encoding="utf-8")
        for root in (SKILLS, PLUGINS)
        for p in root.rglob("*.md")
    )
    for name in DELETED_SKILL_NAMES:
        assert name not in pool, f"stale reference to deleted skill: {name}"
    assert "skill_type:" not in pool, "skill_type frontmatter survives"
    print(f"OK: flat={len(flat)} plugins={len(manifests)} members={len(members)}")


def main() -> None:
    assert SKILLS.is_dir(), SKILLS
    assert not PLUGINS.exists(), f"{PLUGINS} already exists"

    # 1. 剪枝
    for rel in DELETE_DIRS:
        target = SKILLS / "playground-skills" / rel
        assert target.is_dir(), f"missing delete target: {target}"
        shutil.rmtree(target)

    # 2. 入轨：30 个 skill 进 plugins/<plugin>/skills/<skill>/，并写瘦清单
    for plugin_name, (category, member_map) in PLUGINS_SPEC.items():
        plugin_dir = PLUGINS / plugin_name
        for member_dir_name, src_rel in member_map.items():
            _move(SKILLS / src_rel, plugin_dir / "skills" / member_dir_name)
        members = ", ".join(member_map)
        manifest = (
            f"name: {plugin_name}\n"
            f"category: {category}\n"
            f'description: "{plugin_name} plugin（成员: {members}；'
            f'描述占位，待人工补全）"\n'
        )
        (plugin_dir / "plugin.yaml").write_text(manifest, encoding="utf-8")

    # 3. 幸存者上移扁平根
    for src_rel, dst_name in MOVE_TO_FLAT.items():
        _move(SKILLS / src_rel, SKILLS / dst_name)

    # 4. 解散三个物理子目录（清掉缓存垃圾后必须为空）
    for legacy in LEGACY_SUBDIRS:
        legacy_dir = SKILLS / legacy
        _purge_caches(legacy_dir)
        leftovers = sorted(p.name for p in legacy_dir.iterdir())
        assert not leftovers, f"{legacy_dir} not empty: {leftovers}"
        legacy_dir.rmdir()

    # 5. builtin_tags 缩减为扁平轨标签目录
    (SKILLS / "builtin_tags.yaml").write_text(BUILTIN_TAGS_REDUCED, encoding="utf-8")

    # 6. 全部存留 SKILL.md 剥除 skill_type
    for root in (SKILLS, PLUGINS):
        for md_path in root.rglob("SKILL.md"):
            _strip_skill_type(md_path)

    # 7. skill 资产内旧引用定点修复
    _apply_content_fixes()

    _verify()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5.2: 语法与风格检查**

```bash
.venv/bin/python -m py_compile scripts/migrate_to_plugins.py
pre-commit run --files scripts/migrate_to_plugins.py || true
```
black/isort 若有重排，接受其结果。

- [ ] **Step 5.3: Commit（仅脚本）**

```bash
git add scripts/migrate_to_plugins.py
git commit -m "chore(skills): add one-shot migrate_to_plugins script"
```

---

### Task 6: 执行迁移 + 全仓引用更新（一次原子提交）

**Files:**
- Run: `scripts/migrate_to_plugins.py`（产生 `matmaster/plugins/` 等全部布局变更）
- Modify: `matmaster/exps/direct.toml:91`、`matmaster/exps/planner.toml:481`
- Modify: `matmaster/devshell/dev.yaml.example:26-27`
- Modify: `matmaster/skills/__init__.py`
- Modify: `tests/evaluation/test_devshell_agent_sdk_tools.py:297,315,610`
- Modify: `tests/matmaster/devshell/test_devshell_mcp_skill_filter.py:56,66,126`
- Modify: `tests/matmaster/integration/test_bohrium_execution_contract.py:46`
- Modify: `tests/matmaster/test_import_audit.py:108`
- Modify: `tests/test_skill_docs.py`
- Modify: `evaluation/skill_trigger/cases.yaml`
- Modify: `evaluation/devshell_agent/loop_prompts.py`
- Modify: `evaluation/AGENTS_evaluation.md:174`

- [ ] **Step 6.1: 执行迁移脚本**

```bash
.venv/bin/python scripts/migrate_to_plugins.py
```
预期输出：`OK: flat=11 plugins=15 members=30`。

若 `CONTENT_FIXES` 断言失败（old 串空白差异），用 Read 打开对应文件核对原文、修正脚本中的 old 串后重置重跑：
```bash
git checkout -- matmaster/ && git clean -fd matmaster/ && .venv/bin/python scripts/migrate_to_plugins.py
```

- [ ] **Step 6.2: 注册根配置加 plugins 根**

`matmaster/exps/direct.toml`（91 行）与 `matmaster/exps/planner.toml`（481 行），同一处编辑各做一次：

```
old: skills_root = ["matmaster/skills"]
new: skills_root = ["matmaster/skills", "matmaster/plugins"]
```

- [ ] **Step 6.3: dev.yaml.example 示例更新**

```
old: #   skills_root:
     #     - "matmaster/skills/lazymcp"
new: #   skills_root:
     #     - "matmaster/skills"
     #     - "matmaster/plugins"
```

- [ ] **Step 6.4: `matmaster/skills/__init__.py` docstring 更新**

```
old: 层级定位：
     - matmaster/skills/lazymcp/  — LazyMCP 技能（活跃路径，由技能注册表扫描 SKILL.md 加载）
     - .archive/evomaster-skills/ — 归档的框架通用技能（pdf、rag、mcp-builder 等，仅参考用途）
new: 层级定位：
     - matmaster/skills/<skill>/  — 扁平轨独立技能（由技能注册表扫描 SKILL.md 加载）
     - matmaster/plugins/<plugin>/skills/<skill>/ — plugin 轨强关联技能簇（兄弟根，同机制扫描）
     - .archive/evomaster-skills/ — 归档的框架通用技能（pdf、rag、mcp-builder 等，仅参考用途）
```

- [ ] **Step 6.5: 测试文件路径更新（4 个文件 8 处）**

`tests/evaluation/test_devshell_agent_sdk_tools.py`（297、315、610 行，3 处相同字面量，replace_all）：
```
old: matmaster/skills/result-analysis/SKILL.md
new: matmaster/skills/data-analysis/SKILL.md
```

`tests/matmaster/devshell/test_devshell_mcp_skill_filter.py`（56、66 行，2 处相同字面量，replace_all）：
```
old: matmaster/skills/lazymcp/mcp-mat-struct-db
new: matmaster/plugins/structure-search/skills/mcp-mat-struct-db
```
（126 行）：
```
old: matmaster/skills/sample-atomic-structures
new: matmaster/plugins/atomic-structure-ops/skills/sample-atomic-structures
```

`tests/matmaster/integration/test_bohrium_execution_contract.py`（46 行）：
```
old: 'skills': {'skills_root': 'matmaster/skills/lazymcp'},
new: 'skills': {'skills_root': 'matmaster/skills'},
```

`tests/matmaster/test_import_audit.py`（108 行，KNOWN_VIOLATIONS 条目随目录移动改路径）：
```
old: "matmaster/skills/retrieve-structure/scripts/fetch_web_structure.py:L30",
new: "matmaster/plugins/structure-search/skills/retrieve-structure/scripts/fetch_web_structure.py:L30",
```

- [ ] **Step 6.6: `tests/test_skill_docs.py` 双根化（仅改扫描根，不加用例）**

文件头部 import 区改为：

```python
from __future__ import annotations

from itertools import chain
from pathlib import Path

import yaml

_SKILL_DOC_ROOTS = (Path("matmaster/skills"), Path("matmaster/plugins"))


def _iter_skill_docs(pattern: str) -> chain[Path]:
    return chain.from_iterable(
        sorted(root.rglob(pattern)) for root in _SKILL_DOC_ROOTS
    )
```

`test_skill_docs_do_not_reference_legacy_skill_dispatch_api` 内：
```
old:     skills_root = Path("matmaster/skills")
（删除该行）
old:     for path in sorted(skills_root.rglob("*.md")):
new:     for path in _iter_skill_docs("*.md"):
```

`test_skill_description_length_limit` 内：
```
old:     skills_root = Path("matmaster/skills")
（删除该行）
old:     for path in sorted(skills_root.rglob("SKILL.md")):
new:     for path in _iter_skill_docs("SKILL.md"):
```

- [ ] **Step 6.7: 评测资产清扫**

`evaluation/skill_trigger/cases.yaml`：
1. 删除整个 `- skill: tasker-polar-surface` 条目块（从 `  - skill: tasker-polar-surface` 行起，含其 positive 12 行与 negative 5 行，至下一个 `  - skill: checkcif-validator` 块前的空行止；先 Read 拿到逐字内容再整块删除）。
2. 删除 4 行指向已删 skill 的负例（注意对齐空格以 Read 结果为准）：
   - `      - "搭一个 20 mer 的 PVA 链"                               # → poly-generator`
   - `      - "Generate a 15-mer polypropylene chain for downstream MD" # → poly-generator`
   - `      - "切 LiCoO2 (003) slab，极性面，auto-fix 偶极"           # → tasker-polar-surface`（出现 2 次：transform 块与 operate-molecular-crystal 块，replace_all）
   - `      - "ZnO (0001) 是不是极性面，先判一下再切"                 # → tasker-polar-surface`

`evaluation/devshell_agent/loop_prompts.py`（2 处）：
```
old: - **`playground-skills/` 计划废弃**；建议新建 Skill 时路径应为 ``matmaster/skills/<skill_id>/``。
new: - 建议新建 Skill 时路径为 ``matmaster/skills/<skill_id>/``；强关联簇放 ``matmaster/plugins/<plugin>/skills/<skill_id>/``。
```
```
old: - **`playground-skills/` 计划废弃**：提案中建议新建 Skill 时，路径应为 ``matmaster/skills/<skill_id>/``。
new: - 提案中建议新建 Skill 时，路径为 ``matmaster/skills/<skill_id>/``；强关联簇放 ``matmaster/plugins/<plugin>/skills/<skill_id>/``。
```

`evaluation/AGENTS_evaluation.md`（174 行）：
```
old: 、`analysis_post_md`（对应 md-analysis）
new: （空串，删除该例）
```

- [ ] **Step 6.8: 残留 sweep 自检（除历史文档与生成物外应零命中）**

```bash
grep -rn "playground-skills\|skills/lazymcp\|skills/planner" \
  matmaster/ src/ tests/ config/ scripts/ evaluation/ app.py utils/ \
  --include="*.py" --include="*.yaml" --include="*.toml" --include="*.md" --include="*.example" \
  | grep -v "migrate_to_plugins.py\|semantic_coverage_report\|uncovered_rules"
```
预期：零输出（脚本自身与评测生成物除外）。同样 sweep 10 个被删 skill 名：
```bash
for n in compliance-guardian deep-survey lit-data-organizer manuscript-scribe md-analysis poly-forcefield poly-generator result-analysis tasker-polar-surface vaspkit-postprocess; do
  grep -rn "$n" matmaster/ src/ tests/ config/ evaluation/skill_trigger/ evaluation/devshell_agent/ --include="*.py" --include="*.yaml" --include="*.md" | grep -v migrate_to_plugins;
done
```
预期：零输出。

- [ ] **Step 6.9: 全量测试**

```bash
.venv/bin/python -m pytest tests/ -q
```
预期：全部 PASS（任何失败先按 systematic-debugging 定位，多半是漏改的路径字面量）。

- [ ] **Step 6.10: 原子提交（布局迁移 + 引用更新）**

```bash
git add -A
git commit -m "refactor(skills)!: migrate to dual-track layout — 15 plugins (30 skills) + 11 flat, prune 10 playground skills"
```

---

### Task 7: skill_type 死代码全链删除

**Files:**
- Modify: `matmaster/skills/registry.py`
- Modify: `tests/test_skill_registry.py`
- Modify: `tests/matmaster/tools/test_skill_meta_extras.py`

前置：Task 6 已把全部 SKILL.md 的 `skill_type:` frontmatter 剥除，此时删解析链不会把仓内 skill 的该字段漏进 extras。

- [ ] **Step 7.1: registry.py 删链**

删除以下四段（行号为 Task 1 改造前的原始参照，实际以 Read 为准）：

1. `SkillTypeLiteral = Literal["operator", "mcp-loader", "orchestrator"]`（及其前后空行收拢）
2. 整个 `_parse_skill_type` 函数（70-80）
3. `SkillMetaInfo` 中的 `skill_type: SkillTypeLiteral | None = None` 字段行
4. `_parse_meta_info_from_content` 中：
   ```
   old: known_keys = {"name", "description", "skill_type", "mcp_server", "depends_on"}
   new: known_keys = {"name", "description", "mcp_server", "depends_on"}
   ```
   ```
   old:         skill_type=_parse_skill_type(data.get("skill_type")),
   （删除该行）
   ```

检查 `Literal` import 仍被 `_skill_sources: dict[str, Literal["local", "remote"]]` 使用 → **保留** `from typing import Any, Literal`。

- [ ] **Step 7.2: 测试断言删除（只删不增）**

`tests/test_skill_registry.py`：
- 删除整个 `test_parse_frontmatter_skill_type` 方法（176-180）。
- 删除整个 `test_parse_frontmatter_skill_type_operator` 方法（207-221）。

`tests/matmaster/tools/test_skill_meta_extras.py`，`test_extras_defaults_empty` 内：
```
old:         assert info.skill_type is None
（删除该行）
```

- [ ] **Step 7.3: 全仓 skill_type 残留确认**

```bash
grep -rn "skill_type" matmaster/ src/ tests/ config/ scripts/ --include="*.py" --include="*.yaml" --include="*.md" | grep -v migrate_to_plugins
```
预期：零输出。

- [ ] **Step 7.4: 跑测试 + Commit**

```bash
.venv/bin/python -m pytest tests/test_skill_registry.py tests/matmaster/tools/test_skill_meta_extras.py tests/ -q
git add matmaster/skills/registry.py tests/test_skill_registry.py tests/matmaster/tools/test_skill_meta_extras.py
git commit -m "refactor(skills): drop dead skill_type chain from registry"
```

---

### Task 8: 终验（注册总账 / sync 冒烟 / lint / 净代码量）

- [ ] **Step 8.1: 注册总账校验（spec §5：41 = 11 扁平 + 30 plugin 成员，15 plugin）**

```bash
.venv/bin/python -c "
from pathlib import Path
from matmaster.skills.registry import SkillRegistry
reg = SkillRegistry([Path('matmaster/skills'), Path('matmaster/plugins')])
skills = reg.get_all_skills()
flat = [s for s in skills if s.plugin is None]
plugged = [s for s in skills if s.plugin is not None]
plugins = sorted({s.plugin.name for s in plugged})
print('total/flat/plugged/plugins:', len(skills), len(flat), len(plugged), len(plugins))
assert (len(skills), len(flat), len(plugged), len(plugins)) == (41, 11, 30, 15)
ctx = reg.get_meta_info_context()
assert '[Plugin: abacus]' in ctx and '[Skill: data-analysis]' in ctx
print(ctx.splitlines()[0]); print('...'); print(len(ctx.splitlines()), 'lines')
"
```

- [ ] **Step 8.2: 门控冒烟（gpumd 禁用 → 成员展开 + depends_on 跨界 warning 可见）**

```bash
.venv/bin/python -c "
from pathlib import Path
from matmaster.skills.registry import expand_disabled_plugins, read_disabled_plugins
names = expand_disabled_plugins([Path('matmaster/plugins')], {'gpumd', 'structure-search'})
print(sorted(names))
assert names == {'gpumd', 'mcp-mat-struct-db', 'retrieve-structure'}
assert read_disabled_plugins(Path('config/plugins.yaml')) == set()
"
```

- [ ] **Step 8.3: sync 冒烟（41 项、plugin 成员 category/tags 来自清单）**

```bash
.venv/bin/python -c "
from src.services.builtin_skills_sync import _load_tags_config, _scan_builtin_skills
items = _scan_builtin_skills(_load_tags_config())
assert len(items) == 41, len(items)
abacus = next(i for i in items if i['name'] == 'abacus')
assert abacus['category'] == 'simulation' and abacus['tags'] == ['abacus']
flat = next(i for i in items if i['name'] == 'data-analysis')
assert flat['category'] == 'analysis' and flat['tags'] == ['general-data-analysis']
print('sync scan OK:', len(items))
"
```

- [ ] **Step 8.4: `${PLUGIN_DIR}` 冒烟**

```bash
.venv/bin/python -c "
import asyncio
from pathlib import Path
from matmaster.skills.registry import SkillRegistry
from matmaster.tools.builtin.skill_tool import SkillTool
reg = SkillRegistry([Path('matmaster/skills'), Path('matmaster/plugins')])
tool = SkillTool(skill_registry=reg)
out = asyncio.run(tool.execute({'skill': 'abacus'}))
assert '\${PLUGIN_DIR}' not in out
print(out.splitlines()[0])
"
```
（abacus SKILL.md 当前未用 `${PLUGIN_DIR}`，断言意在确认调用链通；plugin 共享资源的实际使用由后续 skill 内容工作引入。）

- [ ] **Step 8.5: lint 全部改动文件 + 全量测试**

```bash
git diff --name-only main...HEAD -- '*.py' | xargs pre-commit run --files
.venv/bin/python -m pytest tests/ -q
```
black/isort 若产生格式修正，`git add -A && git commit -m "style: apply pre-commit formatting"`。

- [ ] **Step 8.6: 净代码量回顾（spec §8：预期持平或下降）**

```bash
git diff --stat main...HEAD -- '*.py' | tail -3
```
将 insertions/deletions 摘要写进最终汇报（删除项：10 skill 目录、skill_type 链、builtin_tags 約 3/4 条目；新增项：plugin 归属/门控/分组/双根/迁移脚本）。

---

## 自审记录（spec 覆盖核对）

| Spec 条款 | 对应任务 |
|-----------|---------|
| §3.1 兄弟根布局、包代码不动 | Task 5/6（registry.py/settings.py 无移动，import 零变更） |
| §3.2 瘦清单（name/category/description，无 version，成员靠扫描） | Task 5 生成、Task 1 解析、Task 4 消费 |
| §3.3 15 plugin / 30 成员、混合类型、三子目录解散 | Task 5 `PLUGINS_SPEC`/`MOVE_TO_FLAT`/`LEGACY_SUBDIRS` |
| §3.4 `${PLUGIN_DIR}`（扁平 skill 不替换） | Task 2 |
| §3.5 门控展开进现有 disabled 通道、缓存键自动正确、depends_on warning 不阻断 | Task 3 |
| §3.6 列表分组（纯展示，命中/载入零变化） | Task 1 Step 1.6 |
| §3.7 builtin_tags 缩减 11 + sync 双根、payload 不变 | Task 5（缩减）+ Task 4（双根） |
| §4 剪枝 13 保 3 删 10 | Task 5（含 §0.1 裁定的 3 个非 skill 死目录） |
| §5 总账 41 = 30 + 11 | Task 8 Step 8.1 |
| §6.1 迁移脚本六步 | Task 5（步骤 1-2-3-4-5 对应脚本 1-2/3-5-6，残留清扫拆为脚本 CONTENT_FIXES + Task 6 代码编辑） |
| §6.2 registry/门控/skill_tool/渲染/sync 改造 | Task 1/3/2/1/4；`skill_registry_factory.py` 经核查无生产调用方，不改（§0.1） |
| §6.3 skill_type 全链死代码 | Task 7 |
| §7 范围外（前端 UX、用户 plugin、机型去重、路由联动） | 未触碰 |
| §8 不新增测试、净代码量 | 全程约束 + Task 8 Step 8.6 |

已知偏差（均有依据）：composition-optimization 旧引用修复为 spec 漏列项的补全；`_common`/`polyFF`/`bohrium-job` 删除为 playground 解散的必然推论（§0.1）。
