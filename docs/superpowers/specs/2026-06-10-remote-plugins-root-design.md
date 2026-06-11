# 远端用户 Plugin 根设计（remote plugins root）

- 日期: 2026-06-10
- 状态: 已通过 brainstorming，待写实现计划
- 范围: 仅 matmaster-evo 后端，Bohrium 远端发现通道
- 作者: Kealdoom + Claude
- 前置: `2026-06-10-skill-plugin-dual-track-design.md`。该 spec §7 把"用户自建 skill 如何归属 plugin"列为范围外；本设计承接其中的远端发现部分——让 Bohrium 远端 `/personal/.matmaster/plugins` 下的用户 plugin 被运行时识别。plugin 内容如何到达该目录（NAS 同步、前端下发）仍在范围外。

## 1. 背景与动机

双轨设计落地后，plugin 识别在各通道的覆盖不齐：

- 仓库内置 plugin（`matmaster/plugins/`）：builtin_skills_sync 双根扫描上行同步 tools-server，运行时 registry 本地扫描挂载 plugin 归属。完整。
- 本地用户根（`local_user_skills_root`）：`_load_skills` 对所有本地根统一跑 `_find_plugin_dir`（祖先目录含 plugin.yaml 即归属），plugin 识别天然可用。完整。
- 远端用户通道：Bohrium 运行时唯一远端根 `/personal/.matmaster/skills`（`src/services/agent_run_bohrium.py:40`），远端扫描脚本只收集 SKILL.md，`RemoteSkill.plugin` 恒为 None。**缺失。**

缺失的三个后果：

1. 远端 plugin 成员只能以散装 skill 形态被发现，系统提示词的 `[Plugin: name]` 分组（`matmaster/context/system_prompt.py:91` → `get_meta_info_context`）对远端失效。
2. `config/plugins.yaml` 的 plugin 级禁用对远端成员不生效（`expand_disabled_plugins` 只扫本地根）。
3. `${PLUGIN_DIR}` 占位符对远端 skill 不可替换。

代码核查确认的现状事实：

- plugin.yaml 内容在运行时的消费点恰好两个：`name` + `description` 进提示词分组；`name` 参与禁用名单匹配。`category` 运行时零消费（仅 builtin 上行同步用）。`${PLUGIN_DIR}` 替换机制存在（`skill_tool.py:93`）但仓库 15 个内置 plugin 的 SKILL.md 全部未使用该占位符。
- `derive_path_access_roots`（`matmaster/core/path_access.py:47`）已泛化遍历 `remote_skill_roots`，新根追加进列表即自动获得 read/search 权限。
- 远端 `.settings.json` 的 skill 级禁用按 remote root 逐个读取（`build_cached_skill_registry` 现有循环），新根自动被覆盖。
- service 层 `build_skill_registry`（`src/services/skill_registry_factory.py`）无生产调用方，仅测试引用。
- registry 同名覆盖语义：本地后注册根覆盖先注册根；远端覆盖本地（`remote_over_local`）；多远端根之间后扫描覆盖先扫描（`remote_over_remote`）。

## 2. 核心判断

1. **plugins 根不是新通道，而是 `remote_skill_roots` 的第二个成员。** plugin 识别是所有根的统一能力——本地根已经如此（`_find_plugin_dir` 对所有本地根生效），远端补齐到同一语义。不引入 `remote_plugin_roots` 这类专属属性，path_access、`.settings.json` 禁用、根合并去重全部自动跟随。
2. **plugin 禁用从构建前预扫描改为构建后按归属过滤。** registry 构建完成时每个 skill 已挂好 plugin 归属，按 `skill.plugin.name` 过滤即可，本地远端一个语义。`expand_disabled_plugins` 的重复 rglob 预扫描（对每个本地根再扫一遍 plugin.yaml + SKILL.md + 解析 frontmatter）整体删除。

## 3. 设计决策

### 3.1 根配置与覆盖顺序

`src/services/agent_run_bohrium.py`：

- 新增常量 `_BOHRIUM_REMOTE_USER_PLUGINS_ROOT = '/personal/.matmaster/plugins'`。
- `_configure_remote_user_skill_root` 直接整体赋值 `remote_skill_roots = [plugins, skills]`。该属性全仓单一写入点就是本函数，现有的"已是 list 则追加"防御分支一并删除，不保留合并语义。`remote_user_skills_root` 属性继续指向 skills 根（settings 合并去重时已在列表内，位置不变）。

覆盖优先级由扫描顺序自然得出（零新逻辑）：散装个人 skill（skills 根，后扫描）> 个人 plugin 成员（plugins 根）> 本地内置（`remote_over_local` 现有语义）。

### 3.2 远端扫描脚本

`_REMOTE_SKILL_SCAN_SCRIPT`（`matmaster/skills/registry.py:27`）：os.walk 的文件名匹配从 `== "SKILL.md"` 扩为 `in {"SKILL.md", "plugin.yaml"}`，输出条目结构不变（`{path, content}` 成功、`{path, error}` 失败）。每根一次 SSH 往返的现有模式不变，两根即两次，不做合并。

### 3.3 远端加载与 plugin 归属判定

`matmaster/skills/registry.py`：

- `_parse_plugin_info` 拆出 `_parse_plugin_info_from_content(content, fallback_name)`，本地版读文件后委托。两侧解析完全一致：name 缺省回退目录名、category strip、description 默认空串。
- **解析层合同变更**：`_parse_remote_skill_scan_stdout` 不再丢弃 error 条目，返回带 `(path, content | None, error | None)` 的 typed record（kind 由文件名导出，不设显式字段）。现有"warning 后丢弃"的行为删除——该函数唯一消费方就是 `_load_remote_skills`，合同整体迁移。若不做这一步，读失败的 plugin.yaml 在分流前就消失，成员会静默降级成散装 skill，§4 的失败语义无法落地。
- `_load_remote_skills` 按文件名分流，维护两个集合：
  - plugin.yaml 条目：content 解析成功 → 有效映射 `{plugin_dir: PluginInfo}`；error 条目或坏 YAML → `invalid_plugin_dirs` 集合（记 warning）。
  - SKILL.md 条目：error 条目沿用现状 warning 跳过；正常条目在**有效与无效清单目录的并集**上沿祖先向上（不越过 root）找最近 plugin 目录——命中无效目录则该成员加载失败记 error 并跳过，命中有效目录则构造 `RemoteSkill(skill_dir, content, plugin=, plugin_dir=)`，无命中则散装注册。祖先查找语义镜像本地 `_find_plugin_dir`，只是在集合上查而非访问文件系统。
- `RemoteSkill` 的 `plugin` / `plugin_dir` 从类属性改为构造参数，`plugin_dir` 类型 `PurePosixPath`。
- 下划线跳过规则不变：成员 skill 在 `_` 目录链下时本就被跳过，plugin.yaml 在 `_` 目录下时其成员同样因目录链被跳过。

**远端目录合同**（镜像本地归属机制，实现不为远端单独造规则）：

- plugin.yaml 所在目录即 plugin 根；成员 SKILL.md 必须位于 plugin 根的**真子目录**（任意深度，推荐镜像内置惯例 `<plugin>/skills/<skill>/SKILL.md`）。
- 与 plugin.yaml **同目录**的 SKILL.md 不构成成员：归属查找从 skill 目录父级开始（`registry.py:500` 现状）。该目录会注册成一个散装 skill，并因嵌套跳过规则吞掉其子目录里本应成为成员的 SKILL.md——属用户布局错误，行为与本地一致，不做特殊救护。
- 远端根自身不作为 plugin 根（查找循环 `while current != root`）：`/personal/.matmaster/plugins/plugin.yaml` 直接放根下无效。

### 3.4 plugin 禁用：构建后过滤

- registry 新增 `remove_plugin_members(disabled_plugin_names: set[str]) -> set[str]`：遍历已注册 skill，移除 `skill.plugin.name` 命中名单者，返回被移除的 skill 名集合。
- `build_cached_skill_registry`（`matmaster/core/skill_registry_cache.py`）：`read_disabled_plugins` 保留；删除 `expand_disabled_plugins` 调用，改为构建 registry 后调用 `remove_plugin_members`；depends_on 跨界警告改用返回的名集合，逻辑等价。
- cache key：`SkillRegistryCacheKey` 从 3 元组扩为 4 元组，新增 `tuple(sorted(disabled_plugins))`（原第三元里的预展开成员名随预扫描一起消失）。
- `expand_disabled_plugins` 函数删除（`registry.py`，约 19 行）。
- 语义精化（有意为之，非回归）：旧逻辑按名预移除，远端散装 skill 与被禁 plugin 成员同名时会误伤覆盖者；新逻辑按最终注册 skill 的实际归属判定，同名覆盖者存活。
- service 层 `build_skill_registry` 不动：无生产调用方，plugin 禁用仍由其调用方负责（现有契约）。

### 3.5 SkillTool 的 ${PLUGIN_DIR} 远端渲染

`matmaster/tools/builtin/skill_tool.py`：`plugin_dir` 渲染现在无条件走 `_render_local_dir`，远端路径会被错误处理。把 `_render_skill_dir` 的 is_remote 分支泛化为 `_render_dir(skill, path)`，skill_dir 与 plugin_dir 共用：远端直接 `str(path)`，本地走现有 remote_project_root 映射。机制对齐，当前无实际使用者。

### 3.6 零改动自动跟随清单

以下接缝不需要任何代码改动，列出以备实现阶段核对：

| 接缝 | 跟随原因 |
|---|---|
| 路径访问（path_access.py） | 已遍历 `remote_skill_roots` |
| 远端 skill 级禁用 | cache 层对每个 remote root 读 `.settings.json`，plugins 根的名单自动生效 |
| 提示词 plugin 分组（get_meta_info_context） | 按 `skill.plugin` 分组，挂载后自动归组 |
| 根合并去重（settings.remote_skill_roots） | 通用列表逻辑 |
| builtin 上行同步（builtin_skills_sync.py） | 与远端发现通道正交 |

## 4. 错误处理

| 故障 | 行为 | 新增/现有 |
|---|---|---|
| 远端根不存在 | `path_exists` 检查后跳过该根 | 现有 |
| 扫描脚本失败/超时/坏 payload | warning 后跳过该根 | 现有 |
| SKILL.md 读取失败（error 条目） | warning 后跳过该 skill | 现有 |
| plugin.yaml 异常（读取失败的 error 条目，或坏 YAML 解析失败） | 对齐本地语义：清单目录进 `invalid_plugin_dirs`，命中它的成员 skill 加载失败记 error（§3.3）。本地 `_load_skills` 中两种故障同样都落进 try 块导致成员跳过 | 新增 |
| SKILL.md 无 frontmatter | error 后跳过该 skill | 现有 |

## 5. 范围外

- plugin 内容如何到达 `/personal/.matmaster/plugins`（NAS 同步、前端下发、用户手动）——双轨 spec §7 的剩余部分，另开 spec。
- service 层 `build_skill_registry` 的 plugin 禁用接入。
- `category` 字段的运行时消费。
- 远端 plugin 级独立禁用配置（远端用户可用现有 `.settings.json` 禁单个成员 skill，或直接删目录）。
- 远端多根扫描的 SSH 往返合并优化。

## 6. 测试与代码量约束

- 不新增测试文件，测试加在现有文件：`tests/test_skill_registry.py`（远端挂载、祖先匹配、根顺序覆盖、`remove_plugin_members`）、`tests/matmaster/core/test_skill_registry_cache.py`（cache key 4 元组、构建后过滤、depends_on 警告）、skill_tool 现有测试处（远端 `${PLUGIN_DIR}` 渲染）。实现计划阶段精确定位。
- **fake session 必须随扫描合同同步**：两份 `FakeRemoteSkillSession`（`tests/test_skill_registry.py:77`、`tests/matmaster/core/test_skill_registry_cache.py:50`）现在只放行 `path.endswith("/SKILL.md")` 且无法产出 error 条目——若不同步，plugin.yaml 根本进不了被测逻辑，远端 plugin 用例会假绿。改为：payload 收录 SKILL.md 与 plugin.yaml 两种文件，并支持注入 error 条目。
- **扫描脚本本体用真脚本验证**：fake 模拟的是脚本的输出合同，脚本自身的收集行为（含 plugin.yaml、error 条目格式）用本地 `python3 -c` 对 tmp 目录树执行真实 `_REMOTE_SKILL_SCAN_SCRIPT` 字符串断言，避免 fake 与脚本形成双真相源漂移。
- 覆盖点：扫描脚本收集 plugin.yaml；远端成员挂载 PluginInfo/plugin_dir 与最近祖先匹配；root 边界（plugin.yaml 在 root 外的祖先不生效）；plugins→skills 根同名覆盖顺序；`remove_plugin_members` 移除本地+远端成员、返回名集合、同名覆盖者不误伤；plugin.yaml 异常（读失败/坏 YAML）时成员加载失败；提示词分组含远端成员。
- 净代码量：删除 `expand_disabled_plugins`（约 19 行）与其调用、预展开 cache key 逻辑，抵消远端挂载与分流的新增；预期接近持平。
