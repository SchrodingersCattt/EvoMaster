# 远端用户 Plugin 根 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/superpowers/specs/2026-06-10-remote-plugins-root-design.md` 落地远端用户 plugin 根：`remote_skill_roots = ["/personal/.matmaster/plugins", "/personal/.matmaster/skills"]`，plugins 根作为第一成员、skills 根作为第二成员，保证散装个人 skill 后扫描并可覆盖同名 plugin 成员；远端扫描收集 plugin.yaml 并为成员 skill 挂载 plugin 归属；plugin 禁用从构建前预扫描改为构建后按归属过滤。

**Architecture:** 自底向上、每步保持绿：先改扫描脚本与测试 fake 的输出合同（对现有用例零行为变化），再做 registry 解析层合同迁移与归属分流（核心任务），然后叠加 `remove_plugin_members` 与 cache 层改造（删除 `expand_disabled_plugins`），最后泛化 SkillTool 渲染、把 plugins 根接入 Bohrium 配置点使功能端到端生效。plugins 根在 registry 眼中只是普通远端根——path_access、`.settings.json` 禁用、根合并去重零改动自动跟随（§3.6）。

**Tech Stack:** Python 3.13（仓库 `.venv`）、pydantic、PyYAML、pytest、pre-commit（black `--skip-string-normalization` / isort / flake8，line-length 88，单文件上限 1000 行）。

---

## 0. 执行须知（先读）

- **测试约束（spec §6）**：严禁新增测试文件；所有新用例加在现有文件——`tests/test_skill_registry.py`、`tests/matmaster/core/test_skill_registry_cache.py`、`tests/matmaster/tools/builtin/test_skill_tool.py`、`tests/matmaster/test_bohrium_setup_injection.py`。用户全局"严禁添加任何测试"规则的适用范围是删兼容/删死代码类任务；本 spec §6 明示了测试范围与覆盖点，以 spec 为准。
- **严禁兼容/兜底逻辑**：`_configure_remote_user_skill_root` 现有的"已是 list 则追加"防御分支整体删除，直接整体赋值；`_parse_remote_skill_scan_stdout` 的旧合同（warning 后丢弃 error 条目）整体迁移，不留双轨。
- 测试命令统一 `.venv/bin/python -m pytest`（pytest.ini：`addopts = -s`，`testpaths = tests`）。格式化用 `.venv/bin/pre-commit run --files <改动文件>`。
- 引号风格：`matmaster/` 与 `tests/` 用双引号，`src/` 用单引号（black 开了 `--skip-string-normalization`，不会自动统一，跟随各文件现状）。
- 当前工作树有其他工作流的未提交改动（bohrium jobs / token-usage 等）。执行本计划前用 worktree 隔离（执行技能负责），或确认这些改动已被处理。
- 开始执行前记录起点：`git rev-parse HEAD` 的输出留作 `<计划起点 commit>`，Step 8.4 统计净代码量时使用。
- 每个 commit message 末尾附 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- **净代码量**：删除 `expand_disabled_plugins`（约 19 行）及其调用、key 预展开逻辑，可部分抵消新增；远端分流与祖先匹配是纯新增能力，主代码预期净增约 +50~60 行（spec 的"接近持平"以删除抵消为口径，按实测报告即可，不强凑）。

### 0.1 计划级裁定（spec 未明示，已据代码事实裁定）

| 事项 | 裁定 | 依据 |
|------|------|------|
| typed record 形态 | `NamedTuple` `_RemoteScanRecord(path, content, error)`，模块私有 | spec §3.3 要求 typed record、kind 由文件名导出；NamedTuple 最小且可解构 |
| 远端归属查找实现位置 | `SkillRegistry._find_remote_plugin_dir` staticmethod，紧邻 `_find_plugin_dir` | 镜像本地语义，放一起便于对照 |
| `remove_plugin_members` 与 `remove_skills` 的调用顺序 | 先 `remove_plugin_members` 再 `remove_skills` | 成员若同时被 skill 级禁用，先按归属移除才能让返回名集完整，depends_on 警告不漏报 |
| `remove_plugin_members` 是否清 `_skill_sources` | 不清，镜像现有 `remove_skills`（`registry.py:543` 也不清） | `_skill_sources` 仅构建期日志用 |
| SKILL.md error 条目的 warning 文案 | 沿用原文 `"Remote skill scan skipped %s: %s"`，从解析层迁到 `_load_remote_skills` | 合同迁移不改日志语义（§4"现有"行） |
| 真脚本测试的 OSError 构造 | 悬空 symlink（`SKILL.md -> 不存在的目标`） | `open()` 触发 FileNotFoundError ⊂ OSError，确定性强；chmod 0o000 在 root 下无效 |
| 同目录 plugin.yaml + SKILL.md 不另写测试 | 不写 | spec §3.3 定性为用户布局错误、"不做特殊救护"；§6 覆盖点清单未列 |
| `tests/matmaster/tools/builtin/test_skill_tool.py` 的 `make_skill` | 不改 | MagicMock 自动属性下现有用例行为与改造前后一致；最小改动 |
| service 层 `skill_registry_factory.build_skill_registry` | 不动 | spec §3.4/§5 明示；无生产调用方 |

### 0.2 关键代码事实速查（执行者零上下文需要；行号为计划撰写时锚点，编辑后会漂移，以符号名定位为准）

- `matmaster/skills/registry.py`（565 行）：`_REMOTE_SKILL_SCAN_SCRIPT`（27-52）、`_parse_plugin_info`（96-103）、`RemoteSkill`（156-172，plugin/plugin_dir 当前是类属性）、`_remote_skill_scan_command`（206-211）、`_parse_remote_skill_scan_stdout`（214-231，当前丢弃 error 条目）、`read_disabled_plugins`（249-255，保留）、`expand_disabled_plugins`（258-276，待删）、`_load_remote_skills`（395-478）、`_find_plugin_dir`（499-507）、`remove_skills`（543-546）、`get_meta_info_context`（548-564）。
- `matmaster/core/skill_registry_cache.py`（114 行）：import 块（8-15）、`SkillRegistryCacheKey` 3 元组（27）、`skill_registry_cache_key`（34-44）、`build_cached_skill_registry`（47-113，`expand_disabled_plugins` 调用在 67-73）。
- `matmaster/tools/builtin/skill_tool.py`：占位符渲染（90-96）、`_render_skill_dir`（121-125）、`_render_local_dir`（127-139）、`_PROJECT_ROOT`（18）。
- `src/services/agent_run_bohrium.py`：常量（40）、`_configure_remote_user_skill_root`（132-139）、唯一调用点（697）。该属性全仓单一写入点就是此函数。
- 测试：`tests/test_skill_registry.py` 的 `FakeRemoteSkillSession`（60-86，只放行 `/SKILL.md`）；`tests/matmaster/core/test_skill_registry_cache.py` 的 `FakeRemoteSkillSession`（30-59，多 `root` 首参与会话属性）；`tests/matmaster/test_bohrium_setup_injection.py::test_configure_remote_user_skill_root_on_ssh_session`（176-187）。
- 同名覆盖语义（现状，零改动）：本地后根覆盖先根；远端覆盖本地（`remote_over_local`）；多远端根后扫描覆盖先扫描（`remote_over_remote`）。plugins 根排第一、skills 根排第二 ⇒ 散装个人 skill 覆盖同名 plugin 成员。
- `_find_plugin_dir` 语义（远端镜像对象）：从 `skill_dir.parent` 起查，`while current != root` ⇒ 与 plugin.yaml 同目录的 SKILL.md 不构成成员、根目录自身不作 plugin 根。
- `ExpSkillsConfig`（`matmaster/config/exp.py:35`）：`config_dir: str = ""`、`disabled_skill_names: list[str]`、`skills_root: str | list[str]`。
- `expand_disabled_plugins` / `read_disabled_plugins` 全仓引用仅 `skill_registry_cache.py`；`RemoteSkill`、`_parse_remote_skill_scan_stdout`、`skill_registry_cache_key` 在 registry/cache 模块与其测试之外零引用。改造无外溢。

---

### Task 1: 远端扫描脚本收集 plugin.yaml（真脚本测试）

**Files:**
- Modify: `matmaster/skills/registry.py`（`_REMOTE_SKILL_SCAN_SCRIPT`）
- Test: `tests/test_skill_registry.py`

- [ ] **Step 1.1: 写失败测试（真实脚本对 tmp 目录树执行）**

`tests/test_skill_registry.py` 顶部 import 块加两行（`import shlex` 之后）：

```python
import subprocess
import sys
```

在 `skill_tree` fixture 之后、`class TestSkill` 之前加模块级测试：

```python
def test_remote_scan_script_collects_skills_plugins_and_errors(
    tmp_path: Path,
) -> None:
    """真实扫描脚本：收集 SKILL.md 与 plugin.yaml，读失败产出 error 条目。"""
    from matmaster.skills.registry import _REMOTE_SKILL_SCAN_SCRIPT

    root = tmp_path / "plugins"
    _write(root / "chem-pack" / "plugin.yaml", "name: chem-pack\n")
    _write(root / "chem-pack" / "skills" / "calc" / "SKILL.md", SKILL_MD_CALC)
    _write(root / "notes" / "README.md", "ignored")
    broken = root / "broken" / "SKILL.md"
    broken.parent.mkdir(parents=True)
    broken.symlink_to(tmp_path / "missing-target")

    result = subprocess.run(
        [sys.executable, "-c", _REMOTE_SKILL_SCAN_SCRIPT, str(root)],
        capture_output=True,
        text=True,
        check=True,
    )
    items = {item["path"]: item for item in json.loads(result.stdout)}

    manifest = str(root / "chem-pack" / "plugin.yaml")
    skill_md = str(root / "chem-pack" / "skills" / "calc" / "SKILL.md")
    assert set(items) == {manifest, skill_md, str(broken)}
    assert items[manifest]["content"] == "name: chem-pack\n"
    assert "full body of the calculator skill" in items[skill_md]["content"]
    assert items[str(broken)]["error"]
    assert "content" not in items[str(broken)]
```

- [ ] **Step 1.2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_skill_registry.py::test_remote_scan_script_collects_skills_plugins_and_errors -q`
Expected: FAIL，AssertionError——`set(items)` 缺 plugin.yaml 条目（当前脚本只收 SKILL.md）。

- [ ] **Step 1.3: 改扫描脚本文件名匹配**

`matmaster/skills/registry.py` 的 `_REMOTE_SKILL_SCAN_SCRIPT` 内（行 37）：

```python
        if filename not in {"SKILL.md", "plugin.yaml"}:
            continue
```

（替换原 `if filename != "SKILL.md":`，脚本其余部分与输出条目结构不变。）

- [ ] **Step 1.4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_skill_registry.py -q`
Expected: 全绿（新用例 PASS；既有远端用例不受影响——payload 多出的 plugin.yaml 条目在现有 fake 中不会产生，真实脚本路径只被本用例触达）。

- [ ] **Step 1.5: Commit**

```bash
git add matmaster/skills/registry.py tests/test_skill_registry.py
git commit -m "feat(skills): 远端扫描脚本收集 plugin.yaml

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 两份 FakeRemoteSkillSession 同步扫描合同

fake 模拟的是脚本输出合同；不同步则 plugin.yaml 进不了被测逻辑、error 条目无法注入，后续远端 plugin 用例会假绿（spec §6）。本任务对现有用例零行为变化（现有 payload 不含 plugin.yaml，errors 缺省为空）。

**Files:**
- Modify: `tests/test_skill_registry.py:60-86`
- Modify: `tests/matmaster/core/test_skill_registry_cache.py:30-59`

- [ ] **Step 2.1: 替换 tests/test_skill_registry.py 的 fake**

整体替换 `class FakeRemoteSkillSession`：

```python
class FakeRemoteSkillSession:
    def __init__(
        self,
        files: dict[str, str],
        errors: dict[str, str] | None = None,
    ) -> None:
        self._files = files
        self._errors = errors or {}
        self.exec_calls: list[str] = []
        self.read_calls: list[str] = []

    def _all_paths(self) -> list[str]:
        return sorted([*self._files, *self._errors])

    def path_exists(self, path: str) -> bool:
        prefix = path.rstrip("/") + "/"
        return any(
            candidate == path or candidate.startswith(prefix)
            for candidate in self._all_paths()
        )

    def exec_bash(self, command: str, timeout: int | None = None) -> dict[str, object]:
        self.exec_calls.append(command)
        root = shlex.split(command)[-1].rstrip("/")
        prefix = root + "/"
        payload: list[dict[str, str]] = []
        for path in self._all_paths():
            if not path.startswith(prefix):
                continue
            if not path.endswith(("/SKILL.md", "/plugin.yaml")):
                continue
            if path in self._errors:
                payload.append({"path": path, "error": self._errors[path]})
            else:
                payload.append({"path": path, "content": self._files[path]})
        return {"exit_code": 0, "stdout": json.dumps(payload)}

    def read_file(self, path: str, encoding: str = "utf-8") -> str:
        self.read_calls.append(path)
        return self._files[path]
```

- [ ] **Step 2.2: 替换 tests/matmaster/core/test_skill_registry_cache.py 的 fake**

整体替换 `class FakeRemoteSkillSession`（保留 `root` 首参与三个会话属性）：

```python
class FakeRemoteSkillSession:
    def __init__(
        self,
        root: str,
        files: dict[str, str],
        errors: dict[str, str] | None = None,
    ) -> None:
        self.remote_user_skills_root = root
        self.remote_skill_roots: list[str] = []
        self.local_user_skills_root: str | None = None
        self._files = files
        self._errors = errors or {}
        self.exec_calls: list[str] = []
        self.read_calls: list[str] = []

    def _all_paths(self) -> list[str]:
        return sorted([*self._files, *self._errors])

    def path_exists(self, path: str) -> bool:
        prefix = path.rstrip("/") + "/"
        return any(
            candidate == path or candidate.startswith(prefix)
            for candidate in self._all_paths()
        )

    def exec_bash(self, command: str, timeout: int | None = None) -> dict[str, object]:
        self.exec_calls.append(command)
        root = shlex.split(command)[-1].rstrip("/")
        prefix = root + "/"
        payload: list[dict[str, str]] = []
        for path in self._all_paths():
            if not path.startswith(prefix):
                continue
            if not path.endswith(("/SKILL.md", "/plugin.yaml")):
                continue
            if path in self._errors:
                payload.append({"path": path, "error": self._errors[path]})
            else:
                payload.append({"path": path, "content": self._files[path]})
        return {"exit_code": 0, "stdout": json.dumps(payload)}

    def read_file(self, path: str, encoding: str = "utf-8") -> str:
        self.read_calls.append(path)
        return self._files[path]
```

- [ ] **Step 2.3: 跑两份测试文件确认全绿**

Run: `.venv/bin/python -m pytest tests/test_skill_registry.py tests/matmaster/core/test_skill_registry_cache.py -q`
Expected: 全 PASS（合同同步，零行为变化）。

- [ ] **Step 2.4: Commit**

```bash
git add tests/test_skill_registry.py tests/matmaster/core/test_skill_registry_cache.py
git commit -m "test(skills): fake 远端会话同步扫描合同（plugin.yaml 与 error 条目）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: registry 远端合同迁移与 plugin 归属（核心）

**Files:**
- Modify: `matmaster/skills/registry.py`
- Test: `tests/test_skill_registry.py`

- [ ] **Step 3.1: 写失败测试（6 个用例）**

`tests/test_skill_registry.py`：在 `SKILL_MD_NO_FRONTMATTER` 常量之后加一个复用常量：

```python
SKILL_MD_MEMBER = """\
---
name: member-skill
description: Plugin member skill
---
Member body.
"""
```

在 `class TestSkillRegistry` 之后、`class TestSkillRegistryCache` 之前加新测试类：

```python
class TestRemotePluginAttribution:
    """远端 plugin.yaml 归属：挂载、祖先匹配、root 边界、失败语义、覆盖顺序。"""

    ROOT = "/personal/.matmaster/plugins"

    def test_remote_member_mounts_nearest_plugin(self) -> None:
        """成员挂最近祖先 PluginInfo；name 缺省回退目录名；散装不受影响。"""
        from matmaster.skills.registry import SkillRegistry

        session = FakeRemoteSkillSession(
            {
                f"{self.ROOT}/outer/plugin.yaml": "description: Outer pack\n",
                f"{self.ROOT}/outer/inner/plugin.yaml": (
                    "name: inner-pack\ndescription: Inner\n"
                ),
                f"{self.ROOT}/outer/inner/skills/member/SKILL.md": SKILL_MD_MEMBER,
                f"{self.ROOT}/outer/skills/outer-member/SKILL.md": (
                    "---\nname: outer-member\ndescription: Outer member\n---\nBody\n"
                ),
                f"{self.ROOT}/loose/SKILL.md": (
                    "---\nname: loose-skill\ndescription: Loose\n---\nBody\n"
                ),
            }
        )

        reg = SkillRegistry([], remote_session=session, remote_roots=[self.ROOT])

        member = reg.get_skill("member-skill")
        assert member is not None
        assert member.plugin is not None
        assert member.plugin.name == "inner-pack"
        assert member.plugin.description == "Inner"
        assert member.plugin_dir == PurePosixPath(f"{self.ROOT}/outer/inner")
        outer_member = reg.get_skill("outer-member")
        assert outer_member is not None
        assert outer_member.plugin is not None
        assert outer_member.plugin.name == "outer"
        assert outer_member.plugin.description == "Outer pack"
        loose = reg.get_skill("loose-skill")
        assert loose is not None
        assert loose.plugin is None
        assert loose.plugin_dir is None

    def test_plugin_yaml_at_root_is_ignored(self) -> None:
        """远端根自身不作 plugin 根：根下 plugin.yaml 不产生归属。"""
        from matmaster.skills.registry import SkillRegistry

        session = FakeRemoteSkillSession(
            {
                f"{self.ROOT}/plugin.yaml": "name: root-pack\n",
                f"{self.ROOT}/some-skill/SKILL.md": (
                    "---\nname: some-skill\ndescription: S\n---\nBody\n"
                ),
            }
        )

        reg = SkillRegistry([], remote_session=session, remote_roots=[self.ROOT])

        skill = reg.get_skill("some-skill")
        assert skill is not None
        assert skill.plugin is None

    def test_invalid_plugin_manifest_fails_members(self) -> None:
        """plugin.yaml 读失败或坏 YAML：成员加载失败；他组不受影响。"""
        from matmaster.skills.registry import SkillRegistry

        session = FakeRemoteSkillSession(
            {
                f"{self.ROOT}/bad-yaml/plugin.yaml": "{ name: [unbalanced",
                f"{self.ROOT}/bad-yaml/skills/y/SKILL.md": (
                    "---\nname: y-skill\ndescription: Y\n---\nBody\n"
                ),
                f"{self.ROOT}/unreadable/skills/z/SKILL.md": (
                    "---\nname: z-skill\ndescription: Z\n---\nBody\n"
                ),
                f"{self.ROOT}/ok/plugin.yaml": "name: ok-pack\n",
                f"{self.ROOT}/ok/skills/w/SKILL.md": (
                    "---\nname: w-skill\ndescription: W\n---\nBody\n"
                ),
            },
            errors={
                f"{self.ROOT}/unreadable/plugin.yaml": "Permission denied",
            },
        )

        reg = SkillRegistry([], remote_session=session, remote_roots=[self.ROOT])

        assert reg.get_skill("y-skill") is None
        assert reg.get_skill("z-skill") is None
        assert reg.get_skill("w-skill") is not None

    def test_skill_error_entry_skipped(self) -> None:
        """SKILL.md error 条目：跳过该 skill，不影响同根其余条目。"""
        from matmaster.skills.registry import SkillRegistry

        session = FakeRemoteSkillSession(
            {
                f"{self.ROOT}/ok/plugin.yaml": "name: ok-pack\n",
                f"{self.ROOT}/ok/skills/good/SKILL.md": (
                    "---\nname: good-skill\ndescription: G\n---\nBody\n"
                ),
            },
            errors={
                f"{self.ROOT}/ok/skills/broken/SKILL.md": "I/O error",
            },
        )

        reg = SkillRegistry([], remote_session=session, remote_roots=[self.ROOT])

        names = {s.meta_info.name for s in reg.get_all_skills()}
        assert names == {"good-skill"}

    def test_skills_root_overrides_plugins_root_member(self) -> None:
        """根顺序覆盖：散装个人 skill（skills 根，后扫描）覆盖同名 plugin 成员。"""
        from matmaster.skills.registry import SkillRegistry

        skills_root = "/personal/.matmaster/skills"
        session = FakeRemoteSkillSession(
            {
                f"{self.ROOT}/pack/plugin.yaml": "name: pack\n",
                f"{self.ROOT}/pack/skills/dup/SKILL.md": (
                    "---\nname: dup-skill\ndescription: Member version\n---\nBody\n"
                ),
                f"{skills_root}/dup/SKILL.md": (
                    "---\nname: dup-skill\ndescription: Loose version\n---\nBody\n"
                ),
            }
        )

        reg = SkillRegistry(
            [],
            remote_session=session,
            remote_roots=[self.ROOT, skills_root],
        )

        dup = reg.get_skill("dup-skill")
        assert dup is not None
        assert dup.meta_info.description == "Loose version"
        assert dup.plugin is None

    def test_meta_info_context_groups_remote_members(self) -> None:
        """提示词分组：远端成员归组在 [Plugin: ...] 名下。"""
        from matmaster.skills.registry import SkillRegistry

        session = FakeRemoteSkillSession(
            {
                f"{self.ROOT}/chem-pack/plugin.yaml": (
                    "name: chem-pack\ndescription: Chemistry toolkit\n"
                ),
                f"{self.ROOT}/chem-pack/skills/member/SKILL.md": SKILL_MD_MEMBER,
            }
        )

        reg = SkillRegistry([], remote_session=session, remote_roots=[self.ROOT])
        ctx = reg.get_meta_info_context()

        assert "[Plugin: chem-pack] Chemistry toolkit" in ctx
        assert "  [Skill: member-skill] Plugin member skill" in ctx
```

- [ ] **Step 3.2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_skill_registry.py::TestRemotePluginAttribution -q`
Expected: 6 个 FAIL。典型失败：`member.plugin is not None` 断言失败（现状 `RemoteSkill.plugin` 恒 None）；invalid-manifest 用例中 `y-skill`/`z-skill` 仍被注册。

- [ ] **Step 3.3: 实现——按文件自上而下五处编辑 `matmaster/skills/registry.py`**

(a) 行 20 typing import 加 `NamedTuple`：

```python
from typing import Any, Literal, NamedTuple
```

(b) 替换 `_parse_plugin_info`（原 96-103 行）为拆分后的两个函数：

```python
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
```

(c) 替换 `class RemoteSkill`（原 156-172 行）——`plugin`/`plugin_dir` 从类属性改为构造参数：

```python
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
```

(d) 替换 `_parse_remote_skill_scan_stdout`（原 214-231 行）——error 条目不再丢弃，前置 `_RemoteScanRecord` 定义：

```python
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
```

(e) `_load_remote_skills`：将每根循环内自 `skill_dirs: set[PurePosixPath] = set()`（原 429 行）至循环体末尾 `skill_dirs.add(skill_dir)`（原 478 行）的整段替换为：

```python
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
```

(f) 在 `_find_plugin_dir`（原 499-507 行）之后加镜像方法：

```python
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
```

- [ ] **Step 3.4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_skill_registry.py -q`
Expected: 全 PASS（含 `TestRemotePluginAttribution` 6 个新用例与全部既有远端用例）。

- [ ] **Step 3.5: Commit**

```bash
git add matmaster/skills/registry.py tests/test_skill_registry.py
git commit -m "feat(skills): 远端根挂载 plugin 归属，扫描合同迁移为 typed record

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: registry.remove_plugin_members

**Files:**
- Modify: `matmaster/skills/registry.py`（`remove_skills` 之后）
- Test: `tests/test_skill_registry.py`

- [ ] **Step 4.1: 写失败测试**

`tests/test_skill_registry.py`：在 `TestRemotePluginAttribution` 类之后加：

```python
class TestRemovePluginMembers:
    def test_removes_local_and_remote_members(self, tmp_path: Path) -> None:
        """按归属移除本地+远端成员并返回名集合；散装与他组 plugin 不动。"""
        from matmaster.skills.registry import SkillRegistry

        local_root = tmp_path / "plugins"
        _write(local_root / "p1" / "plugin.yaml", "name: p1\n")
        _write(
            local_root / "p1" / "skills" / "alpha" / "SKILL.md",
            "---\nname: alpha\ndescription: A\n---\nBody\n",
        )
        _write(
            local_root / "beta" / "SKILL.md",
            "---\nname: beta\ndescription: B\n---\nBody\n",
        )
        remote_root = "/personal/.matmaster/plugins"
        session = FakeRemoteSkillSession(
            {
                f"{remote_root}/p1/plugin.yaml": "name: p1\n",
                f"{remote_root}/p1/skills/gamma/SKILL.md": (
                    "---\nname: gamma\ndescription: G\n---\nBody\n"
                ),
                f"{remote_root}/p2/plugin.yaml": "name: p2\n",
                f"{remote_root}/p2/skills/delta/SKILL.md": (
                    "---\nname: delta\ndescription: D\n---\nBody\n"
                ),
            }
        )
        reg = SkillRegistry(
            local_root,
            remote_session=session,
            remote_roots=[remote_root],
        )

        removed = reg.remove_plugin_members({"p1"})

        assert removed == {"alpha", "gamma"}
        assert reg.get_skill("alpha") is None
        assert reg.get_skill("gamma") is None
        assert reg.get_skill("beta") is not None
        assert reg.get_skill("delta") is not None

    def test_same_name_override_survives(self) -> None:
        """同名覆盖者按实际归属判定不误伤：散装覆盖者存活且不进返回集。"""
        from matmaster.skills.registry import SkillRegistry

        plugins_root = "/personal/.matmaster/plugins"
        skills_root = "/personal/.matmaster/skills"
        session = FakeRemoteSkillSession(
            {
                f"{plugins_root}/pack/plugin.yaml": "name: pack\n",
                f"{plugins_root}/pack/skills/dup/SKILL.md": (
                    "---\nname: dup-skill\ndescription: Member version\n---\nBody\n"
                ),
                f"{skills_root}/dup/SKILL.md": (
                    "---\nname: dup-skill\ndescription: Loose version\n---\nBody\n"
                ),
            }
        )
        reg = SkillRegistry(
            [],
            remote_session=session,
            remote_roots=[plugins_root, skills_root],
        )

        removed = reg.remove_plugin_members({"pack"})

        assert removed == set()
        dup = reg.get_skill("dup-skill")
        assert dup is not None
        assert dup.meta_info.description == "Loose version"

    def test_empty_disabled_set_is_noop(self, tmp_path: Path) -> None:
        from matmaster.skills.registry import SkillRegistry

        reg = SkillRegistry(tmp_path / "missing")
        assert reg.remove_plugin_members(set()) == set()
```

- [ ] **Step 4.2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_skill_registry.py::TestRemovePluginMembers -q`
Expected: FAIL，`AttributeError: 'SkillRegistry' object has no attribute 'remove_plugin_members'`。

- [ ] **Step 4.3: 实现**

`matmaster/skills/registry.py`，`remove_skills` 方法之后：

```python
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
```

- [ ] **Step 4.4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_skill_registry.py -q`
Expected: 全 PASS。

- [ ] **Step 4.5: Commit**

```bash
git add matmaster/skills/registry.py tests/test_skill_registry.py
git commit -m "feat(skills): registry 新增 remove_plugin_members 构建后按归属过滤

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: cache 层——4 元组 key、构建后过滤、删除 expand_disabled_plugins

**Files:**
- Modify: `matmaster/core/skill_registry_cache.py`
- Modify: `matmaster/skills/registry.py`（删 `expand_disabled_plugins`）
- Test: `tests/matmaster/core/test_skill_registry_cache.py`

- [ ] **Step 5.1: 更新既有 cache key 测试 + 写新测试**

`tests/matmaster/core/test_skill_registry_cache.py` 顶部 import 块加（`import json` 之前/之后按 isort）：

```python
import logging
```

并在第三方 import 区加：

```python
import pytest
```

替换既有 `test_cache_key_preserves_local_root_order_and_normalizes_remote_roots`：

```python
def test_cache_key_preserves_root_order_and_normalizes_remote_roots(
    tmp_path: Path,
) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    plugins_root = "/personal/.matmaster/plugins"
    skills_root = "/personal/.matmaster/skills"

    key_ab = skill_registry_cache_key(
        local_roots=[root_a, root_b],
        remote_roots=[plugins_root, skills_root, f"{skills_root}/"],
        config_disabled_skill_names=["zeta", "alpha"],
        disabled_plugins=["pack-b", "pack-a"],
    )
    key_ba = skill_registry_cache_key(
        local_roots=[root_b, root_a],
        remote_roots=[skills_root, plugins_root],
        config_disabled_skill_names=["alpha", "zeta"],
        disabled_plugins=[],
    )

    assert key_ab[0] == (str(root_a), str(root_b))
    assert key_ab[1] == (plugins_root, skills_root)
    assert key_ab[2] == ("alpha", "zeta")
    assert key_ab[3] == ("pack-a", "pack-b")
    assert key_ba[0] == (str(root_b), str(root_a))
    assert key_ba[1] == (skills_root, plugins_root)
    assert key_ba[3] == ()
    assert key_ab != key_ba
```

文件末尾加共享辅助与四个新测试：

```python
def _plugin_tree(root: Path) -> None:
    _write(root / "pack" / "plugin.yaml", "name: pack\n")
    _write(
        root / "pack" / "skills" / "member" / "SKILL.md",
        _skill_body("member-skill", "Member"),
    )
    _write(root / "loose" / "SKILL.md", _skill_body("loose-skill", "Loose"))


def test_disabled_plugin_filters_members_after_build(tmp_path: Path) -> None:
    local_root = tmp_path / "plugins"
    _plugin_tree(local_root)
    config_dir = tmp_path / "config"
    _write(config_dir / "plugins.yaml", "disabled_plugins:\n  - pack\n")
    skills_cfg = ExpSkillsConfig(
        enabled=True,
        skills_root=[str(local_root)],
        config_dir=str(config_dir),
    )

    registry = build_cached_skill_registry(
        skills_cfg=skills_cfg,
        session=None,
        skill_cache=SkillRegistryCache(),
    )

    assert registry is not None
    assert registry.get_skill("member-skill") is None
    assert registry.get_skill("loose-skill") is not None


def test_disabled_plugin_filters_remote_members(tmp_path: Path) -> None:
    plugins_root = "/personal/.matmaster/plugins"
    session = FakeRemoteSkillSession(
        plugins_root,
        {
            f"{plugins_root}/pack/plugin.yaml": "name: pack\n",
            f"{plugins_root}/pack/skills/member/SKILL.md": _skill_body(
                "member-skill", "Member"
            ),
        },
    )
    config_dir = tmp_path / "config"
    _write(config_dir / "plugins.yaml", "disabled_plugins:\n  - pack\n")
    skills_cfg = ExpSkillsConfig(
        enabled=True,
        skills_root=[],
        config_dir=str(config_dir),
    )

    registry = build_cached_skill_registry(
        skills_cfg=skills_cfg,
        session=session,
        skill_cache=SkillRegistryCache(),
    )

    assert registry is not None
    assert registry.get_skill("member-skill") is None


def test_disabled_plugin_warns_cross_boundary_depends_on(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    local_root = tmp_path / "plugins"
    _plugin_tree(local_root)
    _write(
        local_root / "dependent" / "SKILL.md",
        "---\n"
        "name: dependent-skill\n"
        "description: Depends on member\n"
        "depends_on: member-skill\n"
        "---\n"
        "Body\n",
    )
    config_dir = tmp_path / "config"
    _write(config_dir / "plugins.yaml", "disabled_plugins:\n  - pack\n")
    skills_cfg = ExpSkillsConfig(
        enabled=True,
        skills_root=[str(local_root)],
        config_dir=str(config_dir),
    )

    with caplog.at_level(
        logging.WARNING, logger="matmaster.core.skill_registry_cache"
    ):
        registry = build_cached_skill_registry(
            skills_cfg=skills_cfg,
            session=None,
            skill_cache=SkillRegistryCache(),
        )

    assert registry is not None
    assert "dependent-skill" in caplog.text
    assert "member-skill" in caplog.text


def test_cache_key_isolates_disabled_plugins(tmp_path: Path) -> None:
    local_root = tmp_path / "plugins"
    _plugin_tree(local_root)
    enabled_dir = tmp_path / "cfg-enabled"
    enabled_dir.mkdir()
    disabled_dir = tmp_path / "cfg-disabled"
    _write(disabled_dir / "plugins.yaml", "disabled_plugins:\n  - pack\n")
    cache = SkillRegistryCache()

    visible = build_cached_skill_registry(
        skills_cfg=ExpSkillsConfig(
            enabled=True,
            skills_root=[str(local_root)],
            config_dir=str(enabled_dir),
        ),
        session=None,
        skill_cache=cache,
    )
    hidden = build_cached_skill_registry(
        skills_cfg=ExpSkillsConfig(
            enabled=True,
            skills_root=[str(local_root)],
            config_dir=str(disabled_dir),
        ),
        session=None,
        skill_cache=cache,
    )

    assert visible is not None
    assert hidden is not None
    assert visible is not hidden
    assert visible.get_skill("member-skill") is not None
    assert hidden.get_skill("member-skill") is None
```

- [ ] **Step 5.2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/matmaster/core/test_skill_registry_cache.py -q`
Expected: FAIL。key 测试报 `TypeError: skill_registry_cache_key() got an unexpected keyword argument 'disabled_plugins'`；`test_disabled_plugin_filters_remote_members` 在旧逻辑下 member-skill 仍可见（`expand_disabled_plugins` 只扫本地根）。其余两个 plugin 用例在旧逻辑下本就通过（行为保持锚点），key 签名报错会先挡住。

- [ ] **Step 5.3: 重写 `matmaster/core/skill_registry_cache.py`**

import 块（8-15 行）去掉 `expand_disabled_plugins`：

```python
from matmaster.skills.registry import (
    SkillRegistry,
    SkillRegistryCache,
    _normalize_remote_roots,
    read_disabled_plugins,
)
```

key 类型（27 行）扩为 4 元组：

```python
SkillRegistryCacheKey = tuple[
    tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]
]
```

`skill_registry_cache_key`（34-44 行）加第 4 元：

```python
def skill_registry_cache_key(
    *,
    local_roots: list[Path],
    remote_roots: list[str],
    config_disabled_skill_names: Iterable[str],
    disabled_plugins: Iterable[str],
) -> SkillRegistryCacheKey:
    return (
        tuple(str(root) for root in local_roots),
        tuple(_normalize_remote_roots(remote_roots)),
        _normalized_names(config_disabled_skill_names),
        _normalized_names(disabled_plugins),
    )
```

`build_cached_skill_registry`：把自 `plugin_disabled = expand_disabled_plugins(`（原 67 行）至函数末尾的部分替换为：

```python
    disabled_plugins = read_disabled_plugins(
        Path(skills_cfg.config_dir) / "plugins.yaml"
    )
    config_disabled_skill_names = _normalized_names(skills_cfg.disabled_skill_names)

    key = skill_registry_cache_key(
        local_roots=roots,
        remote_roots=remote_roots,
        config_disabled_skill_names=config_disabled_skill_names,
        disabled_plugins=disabled_plugins,
    )

    def build() -> SkillRegistry:
        # Resolving disabled names reads .settings.json files; the remote reads
        # are SSH round-trips. Keep this inside the memoized builder so repeated
        # spawns within one query reuse it instead of re-reading on cache hits.
        disabled_skill_names = set(config_disabled_skill_names)
        for root in roots:
            disabled_skill_names.update(_disabled_skill_names_from_settings(root))
        if remote_roots and session is not None:
            for remote_root in remote_roots:
                disabled_skill_names.update(
                    _disabled_skill_names_from_remote_settings(session, remote_root)
                )
        registry = SkillRegistry(
            roots,
            remote_session=session if remote_roots else None,
            remote_roots=remote_roots,
        )
        removed_members = registry.remove_plugin_members(disabled_plugins)
        if disabled_skill_names:
            registry.remove_skills(disabled_skill_names)
        if removed_members:
            for skill in registry.get_all_skills():
                broken = [
                    dep
                    for dep in skill.meta_info.depends_on
                    if dep in removed_members
                ]
                if broken:
                    logger.warning(
                        "Skill %r depends_on member(s) of disabled plugin(s): %s",
                        skill.meta_info.name,
                        ", ".join(broken),
                    )
        return registry

    return skill_cache.get_or_build(key, build)
```

- [ ] **Step 5.4: 删除 `expand_disabled_plugins`**

`matmaster/skills/registry.py`：整体删除 `expand_disabled_plugins` 函数（原 258-276 行，含 docstring 约 19 行）。然后确认全仓无残留引用：

Run: `grep -rn "expand_disabled_plugins" --include="*.py" . | grep -v ".venv"`
Expected: 无输出。

- [ ] **Step 5.5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/matmaster/core/test_skill_registry_cache.py tests/test_skill_registry.py -q`
Expected: 全 PASS。

- [ ] **Step 5.6: Commit**

```bash
git add matmaster/core/skill_registry_cache.py matmaster/skills/registry.py tests/matmaster/core/test_skill_registry_cache.py
git commit -m "feat(skills): plugin 禁用改为构建后归属过滤，cache key 扩为 4 元组

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: SkillTool 的 ${PLUGIN_DIR} 远端渲染

**Files:**
- Modify: `matmaster/tools/builtin/skill_tool.py`
- Test: `tests/matmaster/tools/builtin/test_skill_tool.py`

- [ ] **Step 6.1: 写失败测试**

`tests/matmaster/tools/builtin/test_skill_tool.py`：顶部 `from pathlib import Path` 改为：

```python
from pathlib import Path, PurePosixPath
```

`TestSkillExecution` 类内加用例（旧代码会把命中本地工程根前缀的远端 plugin_dir 误映射到 remote_project_root，本用例钉死"远端原样直出"）：

```python
    def test_remote_plugin_dir_not_remapped_through_project_root(self):
        """远端 skill 的 plugin_dir 原样直出，不走 remote_project_root 本地映射。"""
        from matmaster.tools.builtin.skill_tool import _PROJECT_ROOT

        remote_plugin_dir = PurePosixPath(_PROJECT_ROOT.as_posix()) / "plugins/pack"
        session = MagicMock()
        session.remote_project_root = "/remote/proj"
        skill = make_skill(body="Plugin root: ${PLUGIN_DIR}")
        skill.is_remote = True
        skill.skill_path = remote_plugin_dir / "skills/member"
        skill.plugin_dir = remote_plugin_dir
        tool = SkillTool(session=session, skill_registry=make_registry(skill=skill))
        result = asyncio.run(tool.execute({"skill": "member"}))
        assert f"Plugin root: {remote_plugin_dir}" in result
        assert "/remote/proj" not in result.split("Plugin root: ")[1]
```

- [ ] **Step 6.2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/matmaster/tools/builtin/test_skill_tool.py -q`
Expected: 新用例 FAIL——旧代码对 plugin_dir 无条件走 `_render_local_dir`，`relative_to(_PROJECT_ROOT)` 命中后被映射为 `/remote/proj/plugins/pack`。

- [ ] **Step 6.3: 实现——`_render_skill_dir` 泛化为 `_render_dir`**

`matmaster/tools/builtin/skill_tool.py`，`execute` 内（原 90-96 行）改为：

```python
            body = skill.get_full_info()
            skill_dir = self._render_dir(skill, skill.skill_path)
            body = body.replace("${SKILL_DIR}", skill_dir)
            if skill.plugin_dir is not None:
                body = body.replace(
                    "${PLUGIN_DIR}", self._render_dir(skill, skill.plugin_dir)
                )
```

删除 `_render_skill_dir`（原 121-125 行），原位替换为：

```python
    def _render_dir(self, skill: Skill, path: Path | PurePosixPath) -> str:
        if getattr(skill, "is_remote", False):
            return str(path)
        return self._render_local_dir(path)
```

（`_render_local_dir` 不动。）

- [ ] **Step 6.4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/matmaster/tools/builtin/test_skill_tool.py tests/test_skill_tool.py tests/matmaster/tools/test_skill_tool_callback.py -q`
Expected: 全 PASS。

- [ ] **Step 6.5: Commit**

```bash
git add matmaster/tools/builtin/skill_tool.py tests/matmaster/tools/builtin/test_skill_tool.py
git commit -m "feat(tools): SkillTool 经 _render_dir 渲染远端 \${PLUGIN_DIR}

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Bohrium 注册 plugins 根（功能端到端生效）

**Files:**
- Modify: `src/services/agent_run_bohrium.py:40,132-139`
- Test: `tests/matmaster/test_bohrium_setup_injection.py:176-187`

- [ ] **Step 7.1: 更新既有测试（含整体赋值语义）**

替换 `test_configure_remote_user_skill_root_on_ssh_session`（该文件已 import `SimpleNamespace`）：

```python
    def test_configure_remote_user_skill_root_on_ssh_session(self):
        from src.services.agent_run_bohrium import (
            _BOHRIUM_REMOTE_USER_PLUGINS_ROOT,
            _BOHRIUM_REMOTE_USER_SKILLS_ROOT,
            _configure_remote_user_skill_root,
        )

        session = SimpleNamespace(remote_skill_roots=['/stale'])

        _configure_remote_user_skill_root(session)

        assert session.remote_user_skills_root == _BOHRIUM_REMOTE_USER_SKILLS_ROOT
        assert session.remote_skill_roots == [
            _BOHRIUM_REMOTE_USER_PLUGINS_ROOT,
            _BOHRIUM_REMOTE_USER_SKILLS_ROOT,
        ]
```

- [ ] **Step 7.2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/matmaster/test_bohrium_setup_injection.py -q`
Expected: FAIL，`ImportError: cannot import name '_BOHRIUM_REMOTE_USER_PLUGINS_ROOT'`。

- [ ] **Step 7.3: 实现（注意本文件用单引号）**

`src/services/agent_run_bohrium.py` 现有 `_BOHRIUM_REMOTE_USER_SKILLS_ROOT` 下一行新增 plugins 常量，不重复定义 skills 常量：

```python
_BOHRIUM_REMOTE_USER_PLUGINS_ROOT = '/personal/.matmaster/plugins'
```

整体替换 `_configure_remote_user_skill_root`（原 132-139 行，删除"已是 list 则追加"防御分支，整体赋值，plugins 根在前）：

```python
def _configure_remote_user_skill_root(ssh_session: Any) -> None:
    ssh_session.remote_user_skills_root = _BOHRIUM_REMOTE_USER_SKILLS_ROOT
    ssh_session.remote_skill_roots = [
        _BOHRIUM_REMOTE_USER_PLUGINS_ROOT,
        _BOHRIUM_REMOTE_USER_SKILLS_ROOT,
    ]
```

- [ ] **Step 7.4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/matmaster/test_bohrium_setup_injection.py -q`
Expected: 全 PASS。

- [ ] **Step 7.5: Commit**

```bash
git add src/services/agent_run_bohrium.py tests/matmaster/test_bohrium_setup_injection.py
git commit -m "feat(bohrium): /personal/.matmaster/plugins 注册为第一远端 skill 根

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: 全量验证、零改动清单核对、格式化收尾

- [ ] **Step 8.1: 全量测试**

Run: `.venv/bin/python -m pytest -q`
Expected: 全 PASS，无新增 failure/error（`-s` 下输出较多，看末行统计）。

- [ ] **Step 8.2: 核对 §3.6 零改动自动跟随清单（只读检查，不改码）**

```bash
grep -n "remote_skill_roots" matmaster/core/path_access.py
grep -n "remote_skill_roots\|remote_user_skills_root" matmaster/skills/settings.py
grep -rn "get_meta_info_context" matmaster/context/system_prompt.py
grep -rn "remote" src/services/builtin_skills_sync.py | head -3
```

Expected：path_access 仍遍历 `remote_skill_roots` 列表（plugins 根自动获 read/search）；settings 的根合并/远端 `.settings.json` 函数签名未被本计划触碰；system_prompt 仍经 `get_meta_info_context` 渲染分组；builtin_skills_sync 与远端发现正交、零改动。任何一项需要改码即偏离 spec，停下复核。

- [ ] **Step 8.3: pre-commit 格式化与收尾提交**

```bash
.venv/bin/pre-commit run --files matmaster/skills/registry.py matmaster/core/skill_registry_cache.py matmaster/tools/builtin/skill_tool.py src/services/agent_run_bohrium.py tests/test_skill_registry.py tests/matmaster/core/test_skill_registry_cache.py tests/matmaster/tools/builtin/test_skill_tool.py tests/matmaster/test_bohrium_setup_injection.py
```

若 black/isort/pyupgrade/autoflake 改写了文件：先对同一文件集重跑上述 pre-commit 命令，直到 no-op 通过；然后重跑 `.venv/bin/python -m pytest -q` 确认仍绿后追加提交：

```bash
git add -u
git commit -m "style: format remote plugins root changes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 8.4: 报告净代码量**

```bash
git diff <计划起点 commit>..HEAD --stat -- matmaster src
```

报告主代码（matmaster/ + src/，不含 tests/）净增量。预期约 +50~60 行（远端分流与祖先匹配为纯新增能力，`expand_disabled_plugins` 删除部分抵消）；如显著超出，复核是否引入了 spec 外逻辑。

---

## 完成定义

- spec §3.1–§3.5 全部落地；§3.6 清单核对通过；§4 错误表中"新增"行（plugin.yaml 异常 → 成员加载失败）有测试钉死。
- §6 覆盖点逐项有测试：脚本收集 plugin.yaml（Task 1）、远端挂载与最近祖先（Task 3）、root 边界（Task 3）、plugins→skills 覆盖顺序（Task 3）、`remove_plugin_members` 本地+远端/返回集/同名不误伤（Task 4）、cache key 4 元组与构建后过滤与 depends_on 警告（Task 5）、远端 `${PLUGIN_DIR}`（Task 6）。
- 两份 fake 与真实脚本输出合同一致（Task 1 真脚本测试 + Task 2 同步），无双真相源。
- `expand_disabled_plugins` 全仓零引用；`.venv/bin/python -m pytest -q` 全绿；`.venv/bin/pre-commit run --files ...` 对同一文件集 no-op 通过。
