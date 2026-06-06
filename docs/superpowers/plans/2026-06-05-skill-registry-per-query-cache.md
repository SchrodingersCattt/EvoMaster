# SkillRegistry Per-Query Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让同一次 root query 内的 root Exp 与 child Exp 通过同一个 query-local cache 复用相同构造签名的 `SkillRegistry`，把重复本地扫描和远程 `/personal/.matmaster/skills` 扫描降到一次。

**Architecture:** `SkillRegistryCache` 是一个只按构造签名缓存最终 registry membership 的 query-local 容器；root `build_runtime()` 创建 cache，`_make_child_run_factory()` 通过 child `Exp(..., inherited_skill_cache=cache)` 传给子运行。缓存键只包含影响 registry membership 的 local roots、normalized remote roots、config disabled skill names；settings-derived disabled names 在 builder 内读取并随 registry 一起缓存，同一 query 中不做失效。

**Tech Stack:** Python 3.11+ via `uv run`, pytest/pytest-asyncio, dataclass/Pydantic runtime models, MatMaster `Exp`, `SkillRegistry`, `SkillTool`, `SkillRegistryResolver`, `ToolRegistry`.

---

## File Structure

**Create**
- `matmaster/core/skill_registry_cache.py` — core-layer helper for cache keys and cached registry construction from `ExpSkillsConfig` plus session roots.
- `tests/matmaster/core/test_skill_registry_cache.py` — cache key, same-query reuse, disabled-key isolation, and cross-query rebuild tests.

**Modify**
- `matmaster/skills/registry.py` — add `SkillRegistryCache` next to `SkillRegistry`; keep override semantics, `_normalize_remote_roots`, logging, and public registry API unchanged.
- `tests/test_skill_registry.py` — add `SkillRegistryCache` hit/isolation unit tests and a membership-consumer invariant test.
- `matmaster/core/exp.py` — add `inherited_skill_cache`, create one cache per root `build_runtime()`, pass cache into `_init_skill_tools()`, and inject the same cache into child `Exp`.
- `tests/matmaster/core/test_exp_skills.py` — update direct `_init_skill_tools()` callers if the signature is made explicit, and keep existing lazy MCP behavior green.
- `tests/matmaster/core/test_exp_skill_replay.py` — update direct `_init_skill_tools()` callers if the signature is made explicit, and keep active-skill replay behavior green.
- `tests/matmaster/integration/test_lazy_mcp_integration.py` — update direct `_init_skill_tools()` callers if the signature is made explicit, and keep lazy MCP integration green.
- `tests/matmaster/core/test_hook_wiring.py` — prove child factory passes the inherited cache and keep existing allow-spawn / spawn-id tests green.
- `tests/matmaster/core/test_exp_runtime_v2.py` — prove root `build_runtime()` uses a fresh cache per root query.

**Line-count guard:** `matmaster/core/exp.py` is currently 984 lines. Keep cache-key and registry-build logic in `matmaster/core/skill_registry_cache.py`; after implementation, run `uv run python .pre-commit/check_file_lines.py matmaster/core/exp.py matmaster/core/skill_registry_cache.py matmaster/skills/registry.py tests/test_skill_registry.py tests/matmaster/core/test_skill_registry_cache.py tests/matmaster/core/test_hook_wiring.py tests/matmaster/core/test_exp_runtime_v2.py`.

---

### Task 1: Add SkillRegistryCache Unit Coverage

**Files:**
- Modify: `tests/test_skill_registry.py`
- Modify later in this task: `matmaster/skills/registry.py`

- [ ] **Step 1: Write failing cache tests**

Append this class after `TestSkillRegistry` in `tests/test_skill_registry.py`:

```python
class TestSkillRegistryCache:
    def test_cache_hit_reuses_same_registry_instance(self, tmp_path: Path) -> None:
        from matmaster.skills.registry import SkillRegistry, SkillRegistryCache

        cache = SkillRegistryCache()
        calls = 0

        def build() -> SkillRegistry:
            nonlocal calls
            calls += 1
            return SkillRegistry(tmp_path / "missing")

        key = ((str(tmp_path / "missing"),), (), ())
        first = cache.get_or_build(key, build)
        second = cache.get_or_build(key, build)

        assert first is second
        assert calls == 1

    def test_cache_key_isolates_different_signatures(self, tmp_path: Path) -> None:
        from matmaster.skills.registry import SkillRegistry, SkillRegistryCache

        cache = SkillRegistryCache()
        first = cache.get_or_build(
            ((str(tmp_path / "a"),), (), ()),
            lambda: SkillRegistry(tmp_path / "a"),
        )
        second = cache.get_or_build(
            ((str(tmp_path / "b"),), (), ()),
            lambda: SkillRegistry(tmp_path / "b"),
        )

        assert first is not second

    @pytest.mark.asyncio
    async def test_skill_consumers_do_not_mutate_registry_membership(
        self,
        tmp_path: Path,
    ) -> None:
        from types import SimpleNamespace

        from matmaster.context.skill_resolver import SkillRegistryResolver
        from matmaster.tools.builtin.skill_tool import SkillTool
        from matmaster.types.events import SkillHitEvent

        skill_dir = tmp_path / "stable-skill"
        _write(
            skill_dir / "SKILL.md",
            "---\n"
            "name: stable-skill\n"
            "description: Stable skill\n"
            "---\n"
            "Stable body\n",
        )

        class GuardedRegistry:
            def __init__(self) -> None:
                from matmaster.skills.registry import Skill

                self._skills = {"stable-skill": Skill(skill_dir)}
                self.removed = False

            def get_skill(self, name: str):
                return self._skills.get(name)

            def get_all_skills(self):
                return list(self._skills.values())

            def get_meta_info_context(self) -> str:
                return "\n".join(
                    f"[Skill: {skill.meta_info.name}] {skill.meta_info.description}"
                    for skill in self._skills.values()
                )

            def remove_skills(self, names: set[str]) -> None:
                self.removed = True
                raise AssertionError("runtime consumer must not change membership")

        registry = GuardedRegistry()
        tool = SkillTool(skill_registry=registry)
        result = await tool.execute({"skill": "stable-skill"})
        assert "Stable body" in result

        resolver = SkillRegistryResolver(registry)
        resolved = resolver(
            (
                SkillHitEvent(
                    source="agent",
                    skill_name="stable-skill",
                ),
            )
        )
        assert resolved[0].name == "stable-skill"
        assert registry.removed is False
```

- [ ] **Step 2: Run the new tests red**

Run:

```bash
uv run pytest tests/test_skill_registry.py::TestSkillRegistryCache -q
```

Expected: FAIL with `ImportError` or `AttributeError` for missing `SkillRegistryCache`.

- [ ] **Step 3: Add `SkillRegistryCache`**

In `matmaster/skills/registry.py`, change the imports and add the class immediately after `_normalize_remote_roots()`:

```python
from collections.abc import Callable
```

```python
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
```

Do not change `SkillRegistry.__init__()`, `_load_skills()`, `_load_remote_skills()`, `remove_skills()`, or registry logging in this task.

- [ ] **Step 4: Run the cache tests green and commit**

Run:

```bash
uv run pytest tests/test_skill_registry.py::TestSkillRegistryCache -q
git add matmaster/skills/registry.py tests/test_skill_registry.py
git commit -m "feat(skills): add skill registry query cache"
```

Expected: pytest PASS, commit succeeds.

---

### Task 2: Add Core Cache-Key And Builder Helper

**Files:**
- Create: `matmaster/core/skill_registry_cache.py`
- Create: `tests/matmaster/core/test_skill_registry_cache.py`

- [ ] **Step 1: Write helper tests**

Create `tests/matmaster/core/test_skill_registry_cache.py`:

```python
from __future__ import annotations

import json
import shlex
from pathlib import Path

from matmaster.config.exp import ExpSkillsConfig
from matmaster.core.skill_registry_cache import (
    build_cached_skill_registry,
    skill_registry_cache_key,
)
from matmaster.skills.registry import SkillRegistryCache


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _skill_body(name: str, description: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n"
        f"{description} body\n"
    )


class FakeRemoteSkillSession:
    def __init__(self, root: str, files: dict[str, str]) -> None:
        self.remote_user_skills_root = root
        self.remote_skill_roots: list[str] = []
        self.local_user_skills_root: str | None = None
        self._files = files
        self.exec_calls: list[str] = []
        self.read_calls: list[str] = []

    def path_exists(self, path: str) -> bool:
        prefix = path.rstrip("/") + "/"
        return any(
            candidate == path or candidate.startswith(prefix)
            for candidate in self._files
        )

    def exec_bash(self, command: str, timeout: int | None = None) -> dict[str, object]:
        self.exec_calls.append(command)
        root = shlex.split(command)[-1].rstrip("/")
        prefix = root + "/"
        payload = [
            {"path": path, "content": self._files[path]}
            for path in sorted(self._files)
            if path.endswith("/SKILL.md") and path.startswith(prefix)
        ]
        return {"exit_code": 0, "stdout": json.dumps(payload)}

    def read_file(self, path: str, encoding: str = "utf-8") -> str:
        self.read_calls.append(path)
        return self._files[path]


def test_cache_key_preserves_local_root_order_and_normalizes_remote_roots(
    tmp_path: Path,
) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"

    key_ab = skill_registry_cache_key(
        local_roots=[root_a, root_b],
        remote_roots=["/personal/.matmaster/skills", "/personal/.matmaster/skills/"],
        config_disabled_skill_names=["zeta", "alpha"],
    )
    key_ba = skill_registry_cache_key(
        local_roots=[root_b, root_a],
        remote_roots=["/personal/.matmaster/skills/"],
        config_disabled_skill_names=["alpha", "zeta"],
    )

    assert key_ab[0] == (str(root_a), str(root_b))
    assert key_ab[1] == ("/personal/.matmaster/skills",)
    assert key_ab[2] == ("alpha", "zeta")
    assert key_ba[0] == (str(root_b), str(root_a))
    assert key_ab != key_ba


def test_build_cached_skill_registry_reuses_remote_scan_with_same_signature(
    tmp_path: Path,
) -> None:
    local_root = tmp_path / "local"
    _write(local_root / "local-skill" / "SKILL.md", _skill_body("local-skill", "Local"))
    remote_root = "/personal/.matmaster/skills"
    session = FakeRemoteSkillSession(
        remote_root,
        {
            f"{remote_root}/remote-skill/SKILL.md": _skill_body(
                "remote-skill",
                "Remote",
            )
        },
    )
    skills_cfg = ExpSkillsConfig(
        enabled=True,
        skills_root=[str(local_root)],
    )
    cache = SkillRegistryCache()

    first = build_cached_skill_registry(
        skills_cfg=skills_cfg,
        session=session,
        skill_cache=cache,
    )
    second = build_cached_skill_registry(
        skills_cfg=skills_cfg,
        session=session,
        skill_cache=cache,
    )

    assert first is second
    assert first is not None
    assert first.get_skill("remote-skill") is not None
    assert len(session.exec_calls) == 1


def test_build_cached_skill_registry_isolates_config_disabled_names(
    tmp_path: Path,
) -> None:
    remote_root = "/personal/.matmaster/skills"
    session = FakeRemoteSkillSession(
        remote_root,
        {
            f"{remote_root}/remote-skill/SKILL.md": _skill_body(
                "remote-skill",
                "Remote",
            )
        },
    )
    cache = SkillRegistryCache()
    enabled_cfg = ExpSkillsConfig(enabled=True, skills_root=[])
    disabled_cfg = ExpSkillsConfig(
        enabled=True,
        skills_root=[],
        disabled_skill_names=["remote-skill"],
    )

    visible = build_cached_skill_registry(
        skills_cfg=enabled_cfg,
        session=session,
        skill_cache=cache,
    )
    hidden = build_cached_skill_registry(
        skills_cfg=disabled_cfg,
        session=session,
        skill_cache=cache,
    )

    assert visible is not hidden
    assert visible is not None
    assert hidden is not None
    assert visible.get_skill("remote-skill") is not None
    assert hidden.get_skill("remote-skill") is None
    assert len(session.exec_calls) == 2


def test_new_query_cache_rebuilds_registry_after_remote_skill_change(
    tmp_path: Path,
) -> None:
    remote_root = "/personal/.matmaster/skills"
    session = FakeRemoteSkillSession(
        remote_root,
        {
            f"{remote_root}/remote-skill/SKILL.md": _skill_body(
                "remote-skill",
                "Old Remote",
            )
        },
    )
    skills_cfg = ExpSkillsConfig(enabled=True, skills_root=[])

    old_registry = build_cached_skill_registry(
        skills_cfg=skills_cfg,
        session=session,
        skill_cache=SkillRegistryCache(),
    )
    session._files[f"{remote_root}/remote-skill/SKILL.md"] = _skill_body(
        "remote-skill",
        "New Remote",
    )
    new_registry = build_cached_skill_registry(
        skills_cfg=skills_cfg,
        session=session,
        skill_cache=SkillRegistryCache(),
    )

    assert old_registry is not None
    assert new_registry is not None
    assert old_registry.get_skill("remote-skill").meta_info.description == "Old Remote"
    assert new_registry.get_skill("remote-skill").meta_info.description == "New Remote"
    assert len(session.exec_calls) == 2
```

- [ ] **Step 2: Run helper tests red**

Run:

```bash
uv run pytest tests/matmaster/core/test_skill_registry_cache.py -q
```

Expected: FAIL with missing `matmaster.core.skill_registry_cache`.

- [ ] **Step 3: Implement the helper module**

Create `matmaster/core/skill_registry_cache.py`:

```python
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from matmaster.config.exp import ExpSkillsConfig
from matmaster.skills.registry import (
    SkillRegistry,
    SkillRegistryCache,
    _normalize_remote_roots,
)
from matmaster.skills.settings import (
    disabled_skill_names_from_remote_settings as _disabled_skill_names_from_remote_settings,
)
from matmaster.skills.settings import (
    disabled_skill_names_from_settings as _disabled_skill_names_from_settings,
)
from matmaster.skills.settings import local_user_skills_root as _local_user_skills_root
from matmaster.skills.settings import remote_skill_roots as _remote_skill_roots

SkillRegistryCacheKey = tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]


def _normalized_names(names: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(name.strip() for name in names if name.strip()))


def skill_registry_cache_key(
    *,
    local_roots: list[Path],
    remote_roots: list[str],
    config_disabled_skill_names: Iterable[str],
) -> SkillRegistryCacheKey:
    return (
        tuple(str(root) for root in local_roots),
        tuple(_normalize_remote_roots(remote_roots)),
        _normalized_names(config_disabled_skill_names),
    )


def build_cached_skill_registry(
    *,
    skills_cfg: ExpSkillsConfig,
    session: Any | None,
    skill_cache: SkillRegistryCache,
) -> SkillRegistry | None:
    roots_raw = skills_cfg.skills_root
    if isinstance(roots_raw, list):
        roots = [Path(root) for root in roots_raw if root]
    else:
        roots = [Path(roots_raw)] if roots_raw else []

    local_user_skills_root = _local_user_skills_root(session)
    if local_user_skills_root is not None:
        roots.append(local_user_skills_root)

    remote_roots = _remote_skill_roots(session)
    if not roots and not remote_roots:
        return None

    config_disabled_skill_names = _normalized_names(skills_cfg.disabled_skill_names)
    disabled_skill_names = set(config_disabled_skill_names)
    for root in roots:
        disabled_skill_names.update(_disabled_skill_names_from_settings(root))
    if remote_roots and session is not None:
        for remote_root in remote_roots:
            disabled_skill_names.update(
                _disabled_skill_names_from_remote_settings(session, remote_root)
            )

    key = skill_registry_cache_key(
        local_roots=roots,
        remote_roots=remote_roots,
        config_disabled_skill_names=config_disabled_skill_names,
    )

    def build() -> SkillRegistry:
        registry = SkillRegistry(
            roots,
            remote_session=session if remote_roots else None,
            remote_roots=remote_roots,
        )
        if disabled_skill_names:
            registry.remove_skills(disabled_skill_names)
        return registry

    return skill_cache.get_or_build(key, build)
```

This helper intentionally does not read or include `skill_names`, `cache_dir`, `config_dir`, `mcp_config_file`, `mcp_runtime_file`, or `mcp_runtime_patch`, because those fields do not affect registry membership in current `Exp._init_skill_tools()`.

- [ ] **Step 4: Run helper tests green and commit**

Run:

```bash
uv run pytest tests/matmaster/core/test_skill_registry_cache.py -q
git add matmaster/core/skill_registry_cache.py tests/matmaster/core/test_skill_registry_cache.py
git commit -m "feat(core): build cached skill registries"
```

Expected: pytest PASS, commit succeeds.

---

### Task 3: Wire Cache Through Exp Runtime Assembly

**Files:**
- Modify: `matmaster/core/exp.py`
- Modify: `tests/matmaster/core/test_exp_runtime_v2.py`
- Modify: `tests/matmaster/core/test_exp_skills.py`
- Modify: `tests/matmaster/core/test_exp_skill_replay.py`
- Modify: `tests/matmaster/integration/test_lazy_mcp_integration.py`

- [ ] **Step 1: Write the root per-query cache test**

Add this test near `TestBuildRuntimeCompactorEventSink` in `tests/matmaster/core/test_exp_runtime_v2.py`:

```python
@pytest.mark.asyncio
async def test_root_build_runtime_creates_fresh_skill_cache_per_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import matmaster.core.skill_registry_cache as cache_module
    from matmaster.config.exp import ExpConfig
    from matmaster.core.exp import Exp

    observed_caches: list[object] = []

    def record_cache(*, skills_cfg, session, skill_cache):
        observed_caches.append(skill_cache)
        return None

    monkeypatch.setattr(
        cache_module,
        "build_cached_skill_registry",
        record_cache,
    )
    cfg = ExpConfig.model_validate(
        {
            "name": "direct",
            "skills": {"enabled": True},
        }
    )
    exp = Exp(cfg)
    ctx = AgentRunContext(
        environment=ExecutionEnvironment(
            workdir=tmp_path,
            execution_workdir=str(tmp_path),
            session_type="local",
            cache_area=tmp_path / "cache",
        ),
        request=AgentRunRequest(llm_provider=_MockProvider()),
    )

    await exp.build_runtime(ctx)
    await exp.build_runtime(ctx)

    assert len(observed_caches) == 2
    assert observed_caches[0] is not observed_caches[1]
```

- [ ] **Step 2: Run the root cache test red**

Run:

```bash
uv run pytest tests/matmaster/core/test_exp_runtime_v2.py::test_root_build_runtime_creates_fresh_skill_cache_per_query -q
```

Expected: FAIL because `build_runtime()` does not create or pass a `SkillRegistryCache`.

- [ ] **Step 3: Add cache injection fields to `Exp`**

In `matmaster/core/exp.py`, extend the `TYPE_CHECKING` block and constructor:

```python
if TYPE_CHECKING:
    from matmaster.skills.registry import SkillRegistryCache
    from matmaster.types.messages import Message
```

```python
    def __init__(
        self,
        config: ExpConfig,
        *,
        allow_spawn: bool = True,
        exclude_subagents: frozenset[str] | None = None,
        inherited_skill_cache: SkillRegistryCache | None = None,
    ) -> None:
        self._config = config
        self._allow_spawn = allow_spawn
        self._exclude_subagents: frozenset[str] = exclude_subagents or frozenset()
        self._inherited_skill_cache = inherited_skill_cache
```

Keep the existing `_cleanup_callbacks`, `_skill_registry`, `_skill_resolver`, and logger assignments after these lines.

- [ ] **Step 4: Pass cache from `build_runtime()` into `_init_skill_tools()` and child factory**

In `build_runtime()` immediately after `env = ctx.environment` and `request = ctx.request`, add:

```python
        from matmaster.skills.registry import SkillRegistryCache

        skill_cache = self._inherited_skill_cache or SkillRegistryCache()
```

Change the skill initialization call from:

```python
            self._init_skill_tools(ctx, registry, skills_config=skills, catalog=catalog)
```

to:

```python
            self._init_skill_tools(
                ctx,
                registry,
                skills_config=skills,
                catalog=catalog,
                skill_cache=skill_cache,
            )
```

Change the orchestrator child factory line from:

```python
                    child_run_factory=self._make_child_run_factory(ctx),
```

to:

```python
                    child_run_factory=self._make_child_run_factory(ctx, skill_cache),
```

- [ ] **Step 5: Replace direct registry construction inside `_init_skill_tools()`**

Change `_init_skill_tools()` signature to require the cache as a keyword-only argument:

```python
    def _init_skill_tools(
        self,
        ctx: AgentRunContext,
        registry: ToolRegistry,
        skills_config: dict[str, Any] | None = None,
        catalog: Any | None = None,
        *,
        skill_cache: SkillRegistryCache,
    ) -> None:
```

Replace the current roots / remote roots / disabled-name registry construction block with:

```python
        from matmaster.core.skill_registry_cache import build_cached_skill_registry
        from matmaster.tools.builtin.skill_tool import SkillTool
        from matmaster.tools.lazy_mcp import LazyMCPConnector, LazyMCPTool
        from matmaster.tools.schema_cache import ToolSchemaCache

        skill_registry = build_cached_skill_registry(
            skills_cfg=skills_cfg,
            session=env.session,
            skill_cache=skill_cache,
        )
        if skill_registry is None:
            self.logger.warning(
                "skills.enabled=true but no skill roots are available, skipping skill init"
            )
            return
```

Remove the old local import of `SkillRegistry`, the old roots construction, the old `remote_roots` lookup, and the old `disabled_skill_names` loop from `_init_skill_tools()`. Keep the MCP runtime config, `SkillTool`, active-skill replay, and `self._skill_registry = skill_registry` logic unchanged.

- [ ] **Step 6: Update direct `_init_skill_tools()` test callers**

For every direct test call found by this command:

```bash
rg -n "exp\\._init_skill_tools\\(" tests matmaster
```

add an explicit cache:

```python
from matmaster.skills.registry import SkillRegistryCache

exp._init_skill_tools(
    ctx,
    registry,
    skill_cache=SkillRegistryCache(),
)
```

When the existing call already passes `catalog=catalog`, keep the existing arguments and add `skill_cache=SkillRegistryCache()` as the last keyword argument:

```python
exp._init_skill_tools(
    ctx,
    registry,
    catalog=catalog,
    skill_cache=SkillRegistryCache(),
)
```

The direct caller files expected from the current checkout are:

```text
tests/matmaster/core/test_exp_skills.py
tests/matmaster/core/test_exp_skill_replay.py
tests/matmaster/integration/test_lazy_mcp_integration.py
```

- [ ] **Step 7: Run Exp skill tests green and commit**

Run:

```bash
uv run pytest tests/matmaster/core/test_exp_runtime_v2.py::test_root_build_runtime_creates_fresh_skill_cache_per_query -q
uv run pytest tests/matmaster/core/test_exp_skills.py tests/matmaster/core/test_exp_skill_replay.py tests/matmaster/integration/test_lazy_mcp_integration.py -q
git add matmaster/core/exp.py tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/core/test_exp_skills.py tests/matmaster/core/test_exp_skill_replay.py tests/matmaster/integration/test_lazy_mcp_integration.py
git commit -m "feat(core): use per-query skill registry cache"
```

Expected: all listed pytest commands PASS, commit succeeds.

---

### Task 4: Wire Cache Into Child Exp Factory

**Files:**
- Modify: `matmaster/core/exp.py`
- Modify: `tests/matmaster/core/test_hook_wiring.py`

- [ ] **Step 1: Write child cache inheritance test**

Add this test after `test_child_run_factory_constructs_child_exp_with_allow_spawn_false()` in `tests/matmaster/core/test_hook_wiring.py`:

```python
@pytest.mark.asyncio
async def test_child_run_factory_passes_inherited_skill_cache(
    self,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import matmaster.core.exp as exp_module
    from matmaster.skills.registry import SkillRegistryCache

    captured_caches: list[object] = []
    original_exp = exp_module.Exp

    class RecordingExp(original_exp):
        def __init__(
            self,
            config,
            *,
            allow_spawn: bool = True,
            inherited_skill_cache=None,
        ) -> None:
            captured_caches.append(inherited_skill_cache)
            super().__init__(
                config,
                allow_spawn=allow_spawn,
                inherited_skill_cache=inherited_skill_cache,
            )

        async def run_stream(self, *args, **kwargs):
            if False:
                yield None

    monkeypatch.setattr(exp_module, "Exp", RecordingExp)
    ctx = AgentRunContext(
        environment=ExecutionEnvironment(
            workdir=tmp_path,
            execution_workdir=str(tmp_path / "exec"),
            session_type="local",
            cache_area=tmp_path / "cache",
            session_id="session-1",
        ),
        request=AgentRunRequest(llm_provider=MockLLMProvider()),
    )
    cache = SkillRegistryCache()
    parent = original_exp(ExpConfig(name="parent"))
    factory = parent._make_child_run_factory(ctx, cache)

    with patch(
        "matmaster.config.loader.load_exp_config",
        return_value=ExpConfig(name="direct"),
    ):
        child_stream = factory("direct", "summarize this task", spawn_id="x")

    assert captured_caches[-1] is cache
    await child_stream.aclose()
```

- [ ] **Step 2: Run child cache test red**

Run:

```bash
uv run pytest tests/matmaster/core/test_hook_wiring.py::TestSpawnGuardWiring::test_child_run_factory_passes_inherited_skill_cache -q
```

Expected: FAIL because `_make_child_run_factory()` does not accept or pass the cache yet.

- [ ] **Step 3: Update `_make_child_run_factory()`**

In `matmaster/core/exp.py`, change the method signature:

```python
    def _make_child_run_factory(
        self,
        ctx: AgentRunContext,
        skill_cache: SkillRegistryCache,
    ) -> Callable[..., AsyncIterator[Any]]:
```

Change child `Exp` construction inside the closure:

```python
            child_exp = Exp(
                load_exp_config(exp_name),
                allow_spawn=False,
                inherited_skill_cache=skill_cache,
            )
```

- [ ] **Step 4: Update existing child factory tests**

In `tests/matmaster/core/test_hook_wiring.py`, import or instantiate `SkillRegistryCache` in each test that calls `_make_child_run_factory(ctx)`, then pass it explicitly:

```python
from matmaster.skills.registry import SkillRegistryCache

factory = parent._make_child_run_factory(ctx, SkillRegistryCache())
```

For the `RecordingExp` class in `test_child_run_factory_constructs_child_exp_with_allow_spawn_false()`, use this constructor:

```python
def __init__(
    self,
    config,
    *,
    allow_spawn: bool = True,
    inherited_skill_cache=None,
) -> None:
    created_allow_spawn.append(allow_spawn)
    super().__init__(
        config,
        allow_spawn=allow_spawn,
        inherited_skill_cache=inherited_skill_cache,
    )
```

For the `RecordingExp` class in `test_child_run_factory_does_not_pass_skill_resolver_to_child_exp()`, use this constructor:

```python
def __init__(
    self,
    config,
    *,
    allow_spawn: bool = True,
    inherited_skill_cache=None,
) -> None:
    self.config = config
    self.allow_spawn = allow_spawn
    self.inherited_skill_cache = inherited_skill_cache
```

- [ ] **Step 5: Run hook wiring tests green and commit**

Run:

```bash
uv run pytest tests/matmaster/core/test_hook_wiring.py::TestSpawnGuardWiring -q
git add matmaster/core/exp.py tests/matmaster/core/test_hook_wiring.py
git commit -m "feat(core): inherit skill cache in child exp"
```

Expected: pytest PASS, commit succeeds.

---

### Task 5: Final Verification And Quality Gates

**Files:**
- Verify all files changed in Tasks 1-4.

- [ ] **Step 1: Run focused registry and Exp tests**

Run:

```bash
uv run pytest tests/test_skill_registry.py tests/matmaster/core/test_skill_registry_cache.py -q
uv run pytest tests/matmaster/core/test_exp_runtime_v2.py::test_root_build_runtime_creates_fresh_skill_cache_per_query tests/matmaster/core/test_hook_wiring.py::TestSpawnGuardWiring -q
uv run pytest tests/matmaster/core/test_exp_skills.py tests/matmaster/core/test_exp_skill_replay.py tests/matmaster/integration/test_lazy_mcp_integration.py -q
```

Expected: all commands PASS.

- [ ] **Step 2: Run line-count guard**

Run:

```bash
uv run python .pre-commit/check_file_lines.py matmaster/core/exp.py matmaster/core/skill_registry_cache.py matmaster/skills/registry.py tests/test_skill_registry.py tests/matmaster/core/test_skill_registry_cache.py tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/core/test_hook_wiring.py tests/matmaster/core/test_exp_skills.py tests/matmaster/core/test_exp_skill_replay.py tests/matmaster/integration/test_lazy_mcp_integration.py
```

Expected: command exits 0 and reports no file over 1000 lines.

- [ ] **Step 3: Run changed-file style hooks**

Run:

```bash
uv run pre-commit run --files matmaster/core/exp.py matmaster/core/skill_registry_cache.py matmaster/skills/registry.py tests/test_skill_registry.py tests/matmaster/core/test_skill_registry_cache.py tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/core/test_hook_wiring.py tests/matmaster/core/test_exp_skills.py tests/matmaster/core/test_exp_skill_replay.py tests/matmaster/integration/test_lazy_mcp_integration.py
```

Expected: hooks PASS. If a formatter rewrites files, rerun the focused pytest commands from Step 1.

- [ ] **Step 4: Inspect final diff for scope**

Run:

```bash
git diff --stat
git diff -- matmaster/core/exp.py matmaster/core/skill_registry_cache.py matmaster/skills/registry.py tests/test_skill_registry.py tests/matmaster/core/test_skill_registry_cache.py tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/core/test_hook_wiring.py tests/matmaster/core/test_exp_skills.py tests/matmaster/core/test_exp_skill_replay.py tests/matmaster/integration/test_lazy_mcp_integration.py
```

Expected: diff is limited to per-query cache, Exp injection, direct caller updates, and tests. No `ExecutionEnvironment` fields, no `run_stream()` signature change, no `skill_names` filtering restoration, and no cross-query cache state.

- [ ] **Step 5: Commit verification-ready result**

Run:

```bash
git status --short
git add matmaster/core/exp.py matmaster/core/skill_registry_cache.py matmaster/skills/registry.py tests/test_skill_registry.py tests/matmaster/core/test_skill_registry_cache.py tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/core/test_hook_wiring.py tests/matmaster/core/test_exp_skills.py tests/matmaster/core/test_exp_skill_replay.py tests/matmaster/integration/test_lazy_mcp_integration.py
git commit -m "test(core): verify skill registry query cache"
```

Expected: only intended implementation files are staged, commit succeeds, and any pre-existing unrelated untracked spec files remain unstaged unless the implementer is explicitly asked to include them.

---

## Self-Review Checklist

- Spec coverage: Tasks 1-2 implement `SkillRegistryCache`, cache key semantics, disabled-name isolation, and cross-query rebuild. Tasks 3-4 implement root creation plus child injection. Task 5 covers focused verification, line-count guard, and scope inspection.
- Boundary coverage: The plan keeps cache out of `ExecutionEnvironment`, does not touch `run_stream()` signature, does not change `SkillRegistry` override behavior, and does not restore `skill_names` filtering.
- Current-code alignment: The plan accounts for the current 984-line `matmaster/core/exp.py` by moving key/build logic to `matmaster/core/skill_registry_cache.py`, while keeping `Exp` responsible for runtime injection.
- Risk note: If future code allows a skills-disabled root to spawn multiple skills-enabled children before any registry has been cached, the current no-lock `SkillRegistryCache` can duplicate first builds under concurrent Agent calls. Current `direct` and `planner` roots prebuild the registry before Agent is exposed, so this is not a correctness issue for the present implementation.
