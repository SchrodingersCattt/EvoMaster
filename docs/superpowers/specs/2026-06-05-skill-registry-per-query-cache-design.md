# SkillRegistry per-query 共享缓存设计

- 日期: 2026-06-05
- 状态: 待实现
- 关联: `matmaster/core/exp.py`、`matmaster/skills/registry.py`、`matmaster/core/subagent_orchestrator.py`

## 1. 背景与问题

一次对话(一个 query / 一次 agent run)里,`SkillRegistry` 会被从零重建多次,每次都做一遍全量扫描——其中最贵的是隔着 SSH 对远程目录 `/personal/.matmaster/skills` 跑 `os.walk`。

**历史生产日志(warning 降级前)** 观测到:单个 query(耗时 134s)内,51 个 skill 各被 `overridden` 3 次,共 153 条警告;这 3 次构建发生在同一个 query、同一个 session 对象内(turn 计数器从 `turn-12` 重置回 `turn-2`,表明是 root + spawn 出的 subagent 各构建一次)。

> 注:当前工作树的 `registry.py` 已把 remote-over-local 这一最常见情形降为 `logger.debug`(`registry.py:389`),并新增 `_log_build_summary`(info)与 `_stats`。所以上述 51×3 条 warning 是**历史现象**,不再是当前 live 行为。本设计要消除的是其**根因**——重复的远程扫描与重复构造,而非日志噪音(噪音已治理)。

根因链(均已核验):

1. `Exp._init_skill_tools`(`exp.py:798`)在每次 `build_runtime` 里**无条件** `new SkillRegistry(...)`,没有任何缓存。
2. `build_runtime` 在每次进入 `runtime_scope` 时执行。
3. subagent 由 `_make_child_run_factory` 生成独立的子 `Exp`(`exp.py:185`),以**父 `ctx`** 驱动 `child_exp.run_stream(ctx, ...)`(`exp.py:186`)→ 子 Exp 复用父的 `ctx.environment` / `env.session`,但仍各自重建自己的 registry。

## 2. 关键事实(决定方案)

- **session 生命周期 = 单个 query**:`run_setup` 里 `SSHSession.open()`(`agent_run_bohrium.py:698`),`run_cleanup` 里 `ssh.close()`(`agent_run_bohrium.py:122`)。
- **远程 skill 是 session 建立时的快照**;`skill-manager` 的契约是"上传的 skill 在**下一个** agent session 生效"。
- **只有 `enabled=true` 的 exp 才构造 registry**:`direct`、`planner` 启用且配置逐字节相同(`skills_root=["matmaster/skills"]`,无 `disabled`);`explore`、`verification` `enabled=false`,在 `exp.py:769-770` 直接 `return`,根本不构造。
- **registry 构造后是 membership-只读消费**:唯一改变 `_skills` 映射的是构造后紧跟的 `remove_skills(disabled)`(`exp.py:812`);下游 `get_skill`(`exp.py:962`、`skill_tool.py:86,96`)与 `SkillRegistryResolver` 都不改 `_skills`。注:`Skill.get_full_info()` 会写惰性 `_full_info_cache`(`registry.py:130`),这是**幂等的 body 缓存**,不改 membership,不影响共享安全。
- **roots 顺序决定覆盖优先级**:`_load_skills`(`registry.py:280`)、`_load_remote_skills`(`registry.py:332`)按 `self._roots` 顺序循环,后命中覆盖先命中;`_normalize_remote_roots`(`registry.py:217`)是**顺序保留式去重**,不排序。
- **`_init_skill_tools` 当前不消费 name filter**:`exp.py:798` 构造 `SkillRegistry` 时未传 `skills=`;config 字段名为 `skill_names`(`config/exp.py:41`),线上为空(不过滤)。
- **`ExecutionEnvironment` 是 ctx 内唯一被 root/child 共享的对象,但它是 physical 层**:`run_stream` 的 `model_copy` 每次只换 `request`、保留 `environment` 引用(`exp.py:519-532`),故 root 与 child 共享同一个 `environment`、`request` 则各自不同。但 `playground.py:10` 明确把 Skill registry 列为 `ExecutionEnvironment` 的**非职责**,且 `with_bohrium`/`with_execution`(`playground.py:109/122`)会 `model_copy` 出新 environment。**结论:不把缓存挂在 `ExecutionEnvironment` 上**,改用 Exp 之间的注入通道(§4)。

## 3. 目标 / 非目标

目标:
- 同一个 query 内,root 与所有 enabled subagent **共享同一个 `SkillRegistry` 实例**,把"每 query 内 N 次构造+远程扫描"降为 1 次。
- 跨 query 自动重建,用户在两轮之间改的 skill 下一轮自动生效,**不引入任何失效逻辑**。

非目标:
- 不做跨 query 持久化缓存。
- 不改 `registry.py` 的 override 语义、override 日志分级、`_normalize_remote_roots`、公共 API。
- 不把缓存挂到 `ExecutionEnvironment`(physical 层,见 §2 末)。
- 不顺手恢复 `skill_names` filter 语义(超出本次 scope,YAGNI)。
- 不支持"单个 query 执行途中改 skill 即时生效"(无插入文件操作的时机,非真实需求)。

## 4. 方案:注入式共享缓存(per-query)

### 4.1 共享载体与注入通道

新增轻量类 `SkillRegistryCache`(runtime 层,放 `matmaster/skills/registry.py` 同模块或 `matmaster/core/`):

```python
class SkillRegistryCache:
    """Per-query 共享:按构造签名缓存已构造好的 SkillRegistry。"""
    def __init__(self) -> None:
        self._by_key: dict[tuple, SkillRegistry] = {}

    def get_or_build(self, key: tuple, builder: Callable[[], SkillRegistry]) -> SkillRegistry:
        cached = self._by_key.get(key)
        if cached is None:
            cached = builder()       # builder 内含 new SkillRegistry + remove_skills
            self._by_key[key] = cached
        return cached
```

注入通道——缓存由 root 创建、经 `_make_child_run_factory` 闭包注入 child,**全程不经过 `ctx`/`environment`**:

```
root Exp.build_runtime()
  skill_cache = self._inherited_skill_cache or SkillRegistryCache()   # root 在此新建(局部变量)
  _init_skill_tools(..., skill_cache)                                 # 用 get_or_build
  orchestrator = SubagentOrchestrator(
      child_run_factory=self._make_child_run_factory(ctx, skill_cache) # 闭包多捕获 skill_cache
  )
        └─> child_run_factory 内:
              Exp(load_exp_config(name), allow_spawn=False,
                  inherited_skill_cache=skill_cache)        # 构造期注入,不污染 run_stream
                └─> child build_runtime():
                      skill_cache = self._inherited_skill_cache        # 复用,不新建
                      _init_skill_tools(..., skill_cache)              # get_or_build 命中
```

为什么这样最干净:
- **不违反分层**:缓存是 runtime 层对象,经 Exp 注入,`ExecutionEnvironment` 一字不动。
- **不污染运行时签名**:依赖在**构造期**注入(`Exp(...)` 参数),不碰 `run_stream(ctx, task, ...)`;`_make_child_run_factory` 仅内部多一个参数。

### 4.2 缓存键(构造签名)

只纳入**影响 registry 内容**的参数;顺序敏感字段保序,集合语义字段排序:

```python
key = (
    tuple(str(p) for p in local_roots),                 # 保序(覆盖优先级依赖顺序)
    tuple(_normalize_remote_roots(remote_roots)),       # 复用 registry 的保序去重
    tuple(sorted(config_disabled_skill_names)),         # 集合语义,排序;取自 exp config,无 I/O
)
```

说明:
- `local_roots` / `remote_roots` **保序不排序**——`registry.py:280/332` 的覆盖优先级依赖 root 顺序,排序会把"集合相同顺序不同"错误折叠。
- `remote_roots` 用 `_normalize_remote_roots(...)` 输出,与 registry 内部实际加载顺序一致。
- **不含 `name_filter`**——`_init_skill_tools` 当前不传 `skills=`,`skill_names` 不影响 registry 内容(§2)。若将来恢复 filter 语义,需同步把 `tuple(sorted(skill_names)) or None` 入键并传 `skills=`。
- settings / remote-settings 派生的 disabled(`_disabled_skill_names_from_settings(root)` 等)是 **本地 roots / (远程 roots + session) 的纯函数**,roots 入键已隐含其相同性;同一 query 内 session 相同,故不单独入键。此假设显式记录;若将来 settings 的 disabled 依赖 root/session 之外的输入,需扩展键。
- `cache_dir` / `config_dir` / `mcp_*` 不影响 registry 内容,不入键。

当前 `direct` / `planner` 签名完全相同 → query 内 100% 命中,只构造一次。配置分化(换 roots / 加 disabled)→ 签名不同 → 各自独立实例,不串味。

### 4.3 查/填逻辑 —— `Exp._init_skill_tools`

`exp.py:798` 现有构造点改为经 `skill_cache.get_or_build`:

```python
key = _skill_registry_cache_key(roots, remote_roots, config_disabled_skill_names)
def _build() -> SkillRegistry:
    reg = SkillRegistry(roots, remote_session=..., remote_roots=remote_roots)
    if disabled_skill_names:          # config + settings + remote-settings,与现状一致
        reg.remove_skills(disabled_skill_names)
    return reg
skill_registry = skill_cache.get_or_build(key, _build)
self._skill_registry = skill_registry
```

- 缓存的是"已 `remove_skills` 后的最终 registry"。命中时**整个跳过**:本地 `rglob`、远程 `exec_bash` 扫描、对象构造、override、settings 读取、`remove_skills` 全省。
- `service` 层 `build_skill_registry`(`src/services/skill_registry_factory.py`)当前仅测试调用,本设计不涉及。

### 4.4 安全不变量(membership)

> **不变量 I1(membership)**:`SkillRegistry` 在构造 + `remove_skills(disabled)` 之后,其 `_skills` 映射不再改变。

这是"共享一个实例"正确性的前提。下游只读 `_skills`(`get_skill`/`get_all_skills`/`get_meta_info_context`);`Skill.get_full_info()` 的惰性 body 缓存是幂等写、不触 membership,允许。新增测试守护此不变量(§6)。将来谁想给 registry 加运行时 membership mutate(动态注册/删除 skill),测试会失败,迫使重新评估共享语义。

并发说明:subagent 在生产中串行 spawn(历史日志中三批 override 相隔 300ms / 50s,非并发),共享 registry 的 `get_or_build` 与 `Skill` body 缓存无需加锁。若将来 subagent 改并发,需给 `get_or_build` 加锁或接受首次竞争(body 缓存幂等,无正确性风险)。

### 4.5 生命周期

`skill_cache` 是 root `build_runtime` 的**局部变量**,不挂 `self` 长期状态、不挂 environment。一次 root build_runtime 调用链(含其 spawn 的所有 child)= 一次 query;调用链结束即释放。**无论 root Exp 实例是否跨 query 复用都不泄漏**——每次 root build_runtime 新建一个 cache。跨 query 自动重建,改过的 skill 下一轮生效,**失效逻辑为零**。

## 5. 改动清单

1. `matmaster/skills/registry.py`:新增 `SkillRegistryCache` 类(§4.1)。override 语义 / 日志分级 / `_normalize_remote_roots` / 公共 API **不改**。
2. `matmaster/core/exp.py`:
   - `Exp.__init__` 新增可选参数 `inherited_skill_cache: SkillRegistryCache | None = None` → `self._inherited_skill_cache`;
   - `build_runtime` 入口:`skill_cache = self._inherited_skill_cache or SkillRegistryCache()`(局部变量);
   - 新增 helper `_skill_registry_cache_key(...)`(§4.2);
   - `_init_skill_tools` 改为 `skill_cache.get_or_build(key, _build)`(§4.3),需把 `skill_cache` 传入该方法;
   - `_make_child_run_factory(ctx, skill_cache)` 闭包多捕获 `skill_cache`,创建 child 时 `Exp(..., inherited_skill_cache=skill_cache)`。
3. 测试:守护 I1 + 验证 per-query 共享与跨 query 重建(§6)。

净改动以 exp.py 内联查填 + `SkillRegistryCache` 一个小类为主,不碰 physical environment、不改 run_stream 签名。

## 6. 测试计划

- `test_skill_registry_cache_hit`:同一 `SkillRegistryCache`、相同签名,两次 `get_or_build` 只调一次 builder(打桩计数),返回**同一实例**。
- `test_skill_registry_cache_key_order_sensitive`:roots 顺序不同 → 键不同 → 两个独立实例(守护 P1)。
- `test_skill_registry_cache_key_isolation`:disabled 不同 → 键不同 → 互不污染。
- `test_skill_registry_cache_membership_invariant`(守护 I1):消费路径(SkillTool / Resolver)不调用改变 `_skills` 的方法;允许 `Skill` 幂等 body cache。
- `test_child_inherits_skill_cache`:root spawn child 时,child 经构造参数拿到同一个 `SkillRegistryCache`,query 内远程扫描只发生一次。
- `test_skill_cache_per_query_rebuild`:新一轮 build_runtime 新建 cache(模拟下一个 query 改 skill 后自动生效)。

## 7. 风险与权衡

- **多轮对话每轮仍各扫一次远程**:per-query 方案的有意取舍,换来零失效复杂度与"改 skill 自动生效"。每轮成本为 1 次远程 `os.walk`,可接受。
- **不变量 I1 是隐式契约**:靠 §6 测试 + 代码注释守护;违反会被测试挡下。
- **settings-disabled 纯函数假设**:见 §4.2,已显式记录,违反时需扩展键。
- **`inherited_skill_cache` 注入正确性**:依赖 `_make_child_run_factory` 闭包正确捕获并传递;`test_child_inherits_skill_cache` 守护。

## 8. 回滚

改动集中在 exp.py 查填逻辑 + 一个 `SkillRegistryCache` 类 + Exp 的一个构造参数。回滚 = 移除该类与注入分支,恢复无条件 `new SkillRegistry`。无数据迁移、无持久化状态、不碰 environment,回滚无副作用。
