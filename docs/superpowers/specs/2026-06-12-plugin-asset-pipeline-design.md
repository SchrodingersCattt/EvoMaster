# Plugin 资产管线设计（前端 → tools-server → NAS → evo 运行时）

- 日期: 2026-06-12
- 状态: 已确认决策（§7 D1–D18）；实现计划见 `docs/superpowers/plans/2026-06-12-plugin-asset-pipeline.md`
- 范围: 跨三仓 —— `scimaster-bohr-chat`（前端）、`matmaster-tools-server`（资产后端）、`matmaster-evo`（运行时与 builtin 同步）
- 作者: Kealdoom + Claude
- 前置:
  - `2026-06-10-remote-plugins-root-design.md` —— 该 spec 的 §5「范围外」明确把"plugin 内容如何到达 `/personal/.matmaster/plugins`（NAS 同步、前端下发、用户手动）"列为"另开 spec"。**本 spec 即承接该条**：负责 plugin 内容如何从前端流到远端 plugins 根；evo 运行时如何识别该根由前置 spec 负责，本 spec 不重复定义。
  - `2026-06-10-skill-plugin-dual-track-design.md` —— plugin 与扁平 skill 双轨并存的本仓模型来源。

## 1. 背景与动机

skill 资产已经全程打通三仓：前端 `pages/skills` UI → `tools-server` user_skill CRUD（OSS + DB）→ 前端 `services/skill-nas-sync.ts` 下载 artifact zip 解包写入 Bohrium NAS `/personal/.matmaster/skills/{builtin,user}/<name>/` → `matmaster-evo` 运行时 `SkillRegistry` 扫描远端根。

plugin 没有对应管线。代码核查确认的现状不对称：

1. **evo 本地** plugin 是一等公民（`matmaster/plugins/<p>/plugin.yaml + skills/`，`get_meta_info_context` 渲染 `[Plugin: name]` 分组，`matmaster/skills/registry.py:631`）。
2. **evo 远端** 当前唯一远端根是 `/personal/.matmaster/skills`（`src/services/agent_run_bohrium.py`），远端只识别散装 skill，`RemoteSkill.plugin` 恒为 None —— 前置 spec 正在补这半。
3. **builtin 同步** `src/services/builtin_skills_sync.py` 的 `_scan_builtin_skills` 把 plugin 成员**压平成散装 skill**（`tags=[plugin.name], category=plugin.category`）上传给 tools-server。
4. **tools-server** 完全没有 plugin 实体（仅 skill + `tags` + `source(builtin|user)`）。
5. **前端** 同步器只写 `/skills/{builtin,user}/`，扁平结构，无 `/plugins/` 目录。

后果：用户 plugin 无法以 plugin 形态被运行时识别；plugin 的 `description`、plugin 级开关、`[Plugin:]` 分组对"非本仓内置"的 plugin 全部失效。

## 2. 核心判断

1. **以 evo 模型为唯一真相源，资产层反向对齐。** evo 运行时只有一种分组维度 —— plugin（`skill.plugin`，由目录祖先 `plugin.yaml` 推导）。散装 skill 永远扁平 `[Skill:]`，无组。`Skill.meta_info.category` 是运行时死字段。因此前端/tools-server 不得发明第二种"运行时组"；要让某个组影响 agent，必须把它做成 plugin。

2. **plugin 与 skill 是两类实体，tools-server 用独立表，不复用 skill 表。** 依据：
   - 上传单元不同：plugin 是一个整包 zip（`plugin.yaml` + `skills/`），与 skill 包平级而非其子集。
   - 名字空间不同：plugin `vasp` 与 skill `vasp` 不应撞唯一约束。
   - 开关粒度不同：plugin 级 toggle 映射 evo「构建后按 `skill.plugin.name` 过滤」；skill 级 toggle 映射 `.settings.json` 按名禁用。
   - NAS 落点不同：`/plugins/<p>/...` vs `/skills/{builtin,user}/<name>/`。
   两表对两根、两类资产、两个同步器，一一镜像 evo 的两条远端根，避免 `kind` 分支与 sync 分叉。

3. **plugin 成员在 tools-server 是派生展示信息，不是可查询的独立行。** evo 远端按目录祖先重新推导成员归属（前置 spec §3.3），tools-server 无需为成员单独建行。成员清单存 plugin 行的 JSON 字段，仅供前端分组展示。成员级搜索/单独开关不在范围内，故不建子表。

4. **归属唯一不变量：每个 skill 恰好一个归属 —— 要么散装，要么属于唯一一个 plugin，无跨 plugin 共用、无"既散装又是成员"。** 推论：
   - 成员归属无歧义，NAS 上一个成员只存在于其 plugin 目录下（`/plugins/<p>/skills/<member>/`），绝不同时出现在 `/skills/` 散装根。
   - 因此 builtin 同步不得双写（§4.4）：双写会让同一成员在两根并存，触发 evo 覆盖顺序把分组覆盖掉，直接违反本不变量。
   - 名字空间上 plugin 名与散装 skill 名各自独立（独立表，判断 2），不存在同名成员与散装 skill 抢归属。

## 3. 端到端数据流（目标态）

```text
[matmaster-evo 启动] builtin_skills_sync
  扁平轨: matmaster/skills/**/SKILL.md → POST tools-server /skills/sync-builtin（现状不变）
  plugin 轨: matmaster/plugins/<p>/ 整个目录树打 zip（plugin.yaml + skills/ + shared/ 等）
            → POST tools-server /plugins/sync-builtin（新增）
            停止把 plugin 成员压成 tags 发 /skills（改向）

[用户在前端]
  pages/plugins UI → api/user-plugin.ts → tools-server plugin CRUD（OSS 直传 + DB）

[前端同步器 plugin-nas-sync.ts（新增）]
  getUserPluginsList → 下载 plugin artifact zip → 解包【保持 plugin 完整目录树】写入
   /personal/.matmaster/plugins/<plugin>/plugin.yaml
   /personal/.matmaster/plugins/<plugin>/skills/<skill>/SKILL.md
   /personal/.matmaster/plugins/<plugin>/shared/...（及任意辅助目录，原样保留）
   + /personal/.matmaster/plugins/.manifest.json（按 plugin 名记 sha 增量 diff）
   + /personal/.matmaster/plugins/.settings.json（plugin 级禁用：{disabled_plugins:[plugin名]}，D16）

[matmaster-evo 运行时]（由前置 spec 实现）
  remote_skill_roots = [plugins 根, skills 根]
  扫描收 plugin.yaml + SKILL.md → 按祖先挂 plugin 归属 → [Plugin:] 分组
  plugin 级禁用 = 构建后按 skill.plugin.name 过滤
```

## 4. 设计决策

### 4.1 NAS 目录契约（三仓集合点，硬约束来自前置 spec §3.3）

```text
/personal/.matmaster/plugins/
  <plugin>/plugin.yaml                 # category + description（不含 name；身份 = 目录名 <plugin>，D18）
  <plugin>/skills/<skill>/SKILL.md     # 成员必须位于 plugin 根的【真子目录】
  <plugin>/shared/...                  # 可选：plugin 级共享资源（如 plotting/shared/mm_style.py）
  <plugin>/<其他辅助目录>/...           # 任意非 skills 辅助目录，原样保留
  .manifest.json                       # 建议 {plugins: {<plugin>: <sha256>}}
  .settings.json                       # plugin 级禁用：{disabled_plugins: [<plugin名>]}（D16，按 plugin 名，不下钻成员）
```

前端写文件时**必须**遵守，否则 evo 不认：
- **plugin 身份 = 目录名，唯一来源（D18）**。`plugin.yaml` 只留 `category` + `description`，**不含也不读 `name`**；plugin 名 = 其目录名 `<plugin>`，同时充当 `user_plugins.name`、NAS 目录名、`disabled_plugins` 匹配键、`${PLUGIN_DIR}` 末段。**evo 改为直接用目录名、不读 yaml 的 name**（`registry.py` 现为 `name = raw.get("name") or fallback`，去掉 `raw.get("name")`，`PluginInfo.name = 目录名`，无 fallback 概念）。由此唯一来源是**代码层硬保证**：即便某 yaml 仍写了 `name`，evo 直接无视，无漂移。tools-server 不再为正确性校验 name，仅在 yaml 含 `name` 时 lint 警告（该字段已废弃），不阻断。
- `plugin.yaml` 在 plugin 根目录；plugins 根自身不放 `plugin.yaml`（evo 查找循环 `while current != root`）。
- 成员 `SKILL.md` 在 plugin 根的真子目录（推荐 `<plugin>/skills/<skill>/`）。与 `plugin.yaml` 同目录的 `SKILL.md` 不构成成员，反而会吞掉子目录 —— 属布局错误，行为与本地一致，不救护。
- `_` 前缀目录链下的 skill 被跳过（与本地一致）。
- **plugin 根下允许 `skills/` 之外的辅助目录**（`shared/` 等）。这些目录**不含 SKILL.md，天然不会被当成成员**，但必须随包原样同步。成员 skill 通过 `${PLUGIN_DIR}/shared/...` 引用它们（见 §4.7），故同步须保留 plugin 完整目录树，不得只挑 `plugin.yaml` + `skills/`。

### 4.2 tools-server 数据模型（独立表）与 API

两张表，完全镜像 skill 侧（`user_skills` + `user_skill_settings`）。**`enabled` 不是 plugin 行的列**，而是独立 settings 表 —— 因为 builtin plugin 是共享行（`user_id=NULL`），每个用户的开关必须分开存。

表 1 `user_plugins`（镜像 `user_skills`）：

```sql
CREATE TABLE IF NOT EXISTS user_plugins (
  id              VARCHAR(64)  NOT NULL PRIMARY KEY,
  source          VARCHAR(16)  NOT NULL DEFAULT 'user' COMMENT 'builtin / user',
  user_id         VARCHAR(128) NULL COMMENT 'builtin 为 NULL',
  name            VARCHAR(256) NOT NULL COMMENT 'plugin 名 = bundle 顶层目录名（非 plugin.yaml，D18）',
  description     VARCHAR(1024) NULL COMMENT '来自 plugin.yaml，进 evo [Plugin:] 行',
  category        VARCHAR(64)  NULL COMMENT '来自 plugin.yaml，前端按大类分组 plugin 列表',
  member_skills   JSON         NULL COMMENT '派生展示 [{name,description,dir}]',
  status          VARCHAR(32)  NOT NULL DEFAULT 'uploading' COMMENT 'uploading/ready/failed',
  bundle_object_key VARCHAR(1024) NULL COMMENT 'OSS 上 plugin 整包 zip key',
  content_sha256  CHAR(64)     NULL COMMENT '整包 SHA256，manifest 增量 diff',
  byte_size       BIGINT UNSIGNED NULL,
  file_count      INT UNSIGNED NULL,
  artifact_id     VARCHAR(64)  NULL COMMENT 'artifact_resources.id（plugin 整包，复用 artifact 服务）',
  extra           JSON         NULL,
  created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_user_plugins_user     (user_id),
  KEY idx_user_plugins_source   (source),
  KEY idx_user_plugins_status   (status),
  KEY idx_user_plugins_category (category),
  KEY idx_user_plugins_created  (user_id, created_at),
  UNIQUE KEY uq_user_plugins_name (user_id, name, source)   -- D17：plugin 名驱动 NAS 目录/${PLUGIN_DIR}/禁用匹配，不容重名
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户 / 内置 Plugin';
```

表 2 `user_plugin_settings`（镜像 `user_skill_settings`，plugin 级开关；无记录=开启）：

```sql
CREATE TABLE IF NOT EXISTS user_plugin_settings (
  user_id     VARCHAR(128) NOT NULL,
  plugin_name VARCHAR(256) NOT NULL COMMENT '对应 user_plugins.name',
  source      VARCHAR(16)  NOT NULL DEFAULT 'user' COMMENT 'builtin / user；调用方按对应 plugin 的 source 显式写入，default 仅兜底（与 user_plugins 对齐）',
  enabled     TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '0=关闭，无记录视为开启',
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, plugin_name, source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户 Plugin 开关偏好';
```

字段说明：
- **`category` 来自 `plugin.yaml`，是 plugin 级一等列**（与被删的 skill.category 不同）。区别关键：skill.category 删 `builtin_tags.yaml` 后无来源、恒 NULL 才成死字段；plugin.category 在 `plugin.yaml` 里**始终有真实来源**，用于前端按大类分组 plugin 列表。现有词表：`simulation`（11 个）、`structure-modeling`、`analysis`、`reporting`、`workflow-system`。evo 运行时仍不消费它（运行时分组只认 plugin 本身），但它在前端是有效分组维度，非死字段。第一版不强约束为固定枚举，按 plugin.yaml 原值入库。
- **为何不按子目录推导 category。** 评估过「把 builtin plugin 按 `matmaster/plugins/<category>/<plugin>/` 归类、category 取父目录名、删 plugin.yaml 字段」。结论否决：(1) 目录派生只对仓库内 builtin 成立；(2) 未来要支持「外部用户注册插件、开放给全平台」（见 §8），这些插件不在我们的仓库目录树里，category 只能来自提交时的 `plugin.yaml`/表单 —— 一旦外部插件出现，目录派生立刻源头分裂。故 `plugin.yaml` 字段是唯一能统一 builtin + 外部插件的来源。仓库内是否按 category 归子目录，纯属物理整洁，与 category 来源解耦，可后续单独决定，不作为 category 机制。
- **`member_skills` 为 `[{name, description, dir}]`**：`dir` 是成员在包内的相对目录（如 `skills/plot-chart`），让前端「点成员 → 取内容」可直接 `GET /artifacts/{artifact_id}/content?path={dir}/SKILL.md`，无需先 `/files` 猜路径。成员按 `SKILL.md` 位置枚举，`shared/` 等无 SKILL.md 的目录不入此列表。
- **嵌套成员按"拍平 + 最近目录"枚举（D14）**：evo 允许成员 skill 多层嵌套（`skills/<a>/<b>/SKILL.md`）。枚举规则 = **递归收集 plugin 包内所有 `SKILL.md`**，每个成员的 `dir` 取其 `SKILL.md` 所在的**最近目录相对路径**（如 `skills/a/b`）。`member_skills` 是**扁平列表，不携带层级**——前端按 plugin 分组展示，组内成员平铺，不再渲染 skill 间的目录树。与 evo 远端「按目录祖先挂 plugin 归属」一致：归属只认最近祖先 `plugin.yaml`，中间目录层数不影响归属，故展示侧也无需还原层级。

成员完整内容获取（无需为成员单独建 artifact）：整个 plugin 是**一个** artifact，成员 SKILL.md / references / scripts、以及 `shared/` 资源都在同一包内按相对路径寻址 —— `GET /artifacts/{artifact_id}/files?path=skills/<skill>/` 列树、`/content?path=skills/<skill>/SKILL.md` 读文件。

新增 API（镜像 `user_skill_api.py` / `skill_admin_api.py`，路径前缀 `/users/{uid}/plugins` 与 `/plugins`）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/users/{uid}/plugins` | 列出用户可见 plugin（builtin + 自己的 user），JOIN settings 带 enabled |
| POST | `/users/{uid}/plugins/upload-zip` 与 `/upload-url` | 上传 plugin 整包，两通道都做（镜像 skill 现状：直传 zip + 预签名 URL）（D15） |
| POST | `/users/{uid}/plugins` | 建记录；**name = bundle 顶层目录名（D18，不读 yaml name；yaml 若含 name 仅 lint 警告）**，解包 `plugin.yaml` 取 description/category；按 `SKILL.md` 枚举成员填 `member_skills`（含 `dir`，`name` 取 frontmatter `name`、缺失 fallback 目录名）；保留 `shared/` 等辅助目录；同名（`user_id+name+source`）已存在则拒（D17） |
| DELETE | `/users/{uid}/plugins/{id}` | 删记录（不自动清 OSS） |
| PATCH | `/users/{uid}/plugins/toggle` | plugin 级开关，写 `user_plugin_settings` |
| POST | `/plugins/sync-builtin` | 给 evo builtin 同步用，删旧 builtin 全量重插 |

服务端解包校验：`plugin.yaml` 缺失/坏 YAML → `status=failed`；无任何成员 `SKILL.md` → 创建时拒绝（D7）。

### 4.3 前端同步器与 UI

- `src/api/user-plugin.ts`（新）：对接 tools-server plugin API，类型镜像 `user-skill.ts`。
- `src/services/plugin-nas-sync.ts`（新）：镜像 `skill-nas-sync.ts`，差异点：
  - 目标根 `/personal/.matmaster/plugins/`；
  - 解包时**保持 plugin 完整目录树**（`plugin.yaml` 在 `<plugin>/` 根、成员在 `<plugin>/skills/<skill>/`、`shared/` 等辅助目录原样保留），不像 skill 那样铺平到 `<name>/`，也不得只挑 `plugin.yaml` + `skills/`；
  - manifest 形状 `{plugins: {<plugin>: sha}}`；
  - 仅同步 `status=ready` 的 plugin；按 sha 增量；删除 manifest 中已不存在的 plugin 目录；
  - plugin 级禁用落地（D16）：plugin 是**唯一开关单元，不下钻到成员 skill**。用户关掉某 plugin 时，直接把该 **plugin 名**写进 `/plugins/.settings.json` 的 `disabled_plugins` 列表，**不展开成员名**。evo 远端读 `disabled_plugins` 喂给 `remove_plugin_members`（按 `skill.plugin.name` 过滤）。好处：成员名跨 plugin 是否撞名与禁用无关，彻底无歧义；语义与 tools-server `user_plugin_settings`（按 plugin_name）一致。
- 触发时机与 skill 并列：登录后全量、每轮发送前增量。两个同步器互不阻塞。
- UI：plugin 管理页（或在现有 skills 页加 plugin 分区），按 plugin 分组展示其 member_skills；plugin 整体开关。

### 4.4 `builtin_skills_sync.py` 改向

`_scan_builtin_skills` 当前对 `_PLUGINS_ROOT` 把成员压成 `tags=[plugin.name]` 的散装 skill（`src/services/builtin_skills_sync.py:211-224`）。按 evo 模型，这是错配。改为：

- 扁平轨（`matmaster/skills/`）→ 维持发 `/skills/sync-builtin`，不变。
- plugin 轨（`matmaster/plugins/<p>/`）→ 整个 plugin 目录树打一个 zip（含 `plugin.yaml` + `skills/` + `shared/` 等所有子目录），发新的 `/plugins/sync-builtin`，保留 plugin 实体。`_zip_skill_dir` 现以单 skill 目录为单位，plugin 轨需以整个 plugin 根为单位打包。
- **不双写**：翻转时停止把 plugin 成员压成 tag 发 `/skills`，成员只走 `/plugins`。双写会让成员在 `/skills`+`/plugins` 两根并存，触发 evo 覆盖顺序（skills 根优先）把分组覆盖掉，违反归属唯一不变量（§2 判断 4）。
- **有序翻转（依赖顺序）**：① evo `remote-plugins-root` 落地（evo 认识 `/plugins` 根）—— **已完成**（`agent_run_bohrium.py:135` 已把 `_BOHRIUM_REMOTE_USER_PLUGINS_ROOT` 配进 `remote_skill_roots`，`registry.py` 已有 `remove_plugin_members` / `read_disabled_plugins`）→ ② tools-server plugin API + 前端 plugin 同步器上线（`/plugins` 有数据）+ D16/D18 两处 evo 契约增量 → ③ evo builtin 同步从「压平发 `/skills`」一刀切翻转为「整包发 `/plugins`」。翻转前 evo 必须已能扫 `/plugins`（①已满足），否则成员会短暂从 `/skills` 消失又未被 `/plugins` 接住。

### 4.5 evo 运行时（引用 + 两处契约增量）

远端 plugins 根识别、扫描脚本收 `plugin.yaml`、祖先归属、plugin 级构建后过滤（`remove_plugin_members`）、`${PLUGIN_DIR}` 远端渲染 —— 主体由前置 `2026-06-10-remote-plugins-root-design.md` 定义。本 spec 的契约只需保证前端把文件摆成 §4.1 的形状。

**契约增量一（D16，需前置 spec 配合）**：前置 spec 的 plugin 级禁用输入 `read_disabled_plugins` 当前只读静态 `config/plugins.yaml`（非按用户）。本管线要求 cache 层**额外**读取 plugins 根 `/plugins/.settings.json` 的 `disabled_plugins` 字段，与 `config/plugins.yaml` 的禁用名单**并集**后喂给 `remove_plugin_members`。这是 per-user plugin 开关的落地点：改动范围小（cache 层加一处 NAS 读取 + 一个字段解析），但前置 spec 的 `read_disabled_plugins` 需扩成"静态 config ∪ 远端 .settings.json"。

**契约增量二（D18，本 spec 拥有的 evo 改动）**：`matmaster/skills/registry.py` 的 `_parse_plugin_info_from_content` 改为**不读 `raw.get("name")`**，`PluginInfo.name` 恒取传入的目录名（`parse_plugin_info` 已传 `manifest_path.parent.name`）。去掉 `name = raw.get("name") or fallback` 的 `or` 分支，使 plugin 身份在代码层唯一锚定目录名，local/remote 两处扫描一致。`PluginInfo` 仍保留 `name` 字段（值=目录名），`category`/`description` 继续从 yaml 读。配套：删 16 个 builtin plugin.yaml 的 `name:` 行；加 parity 测试断言 evo 与 tools-server 对同一 bundle 取名一致（均=目录名）。

### 4.6 skill 表瘦身：删除 `category` 与 `tags`（D9 的直接推论）

删除 `builtin_tags.yaml`（D9）且 plugin 成员不再压成 tag（D6）后，tools-server `user_skills` 表的 `category` / `tags` 两列**无人写、无人读**：

- 现状写入点：仅 `sync_builtin`（取自 `builtin_tags.yaml`）；user skill 创建路径从不写这两列。
- 现状消费点：`category` 仅前端筛选；`tags` 仅 by-tag 的 toggle / list 过滤。evo 运行时两者都不读。
- 变更后：来源全部消失（group 删除、plugin 改走 plugins 表）→ 两列恒为 NULL，by-tag toggle 匹配不到任何记录 → 整条 by-tag 链路成死代码。

连带改动（跨三仓，按 D6 有序：先停写入侧，再 DROP 列）：

| 仓库 | 改动 |
|---|---|
| tools-server | migration `drop_user_skills_category_tags.sql`：DROP `category`、`tags` 两列与 `idx_user_skills_category` 索引 |
| tools-server | `models/user_skill.py`：删 `UserSkillOut.category/tags`、`SyncBuiltinSkillItem.category/tags`、`SkillToggleRequest.tag` |
| tools-server | `services/user_skill_service.py`：删 `_row_to_out` 与 `sync_builtin` 的 category/tags、`toggle()` 的 by-tag 分支、`list_for_user` 的 `tag` 参数 |
| tools-server | dao：`list_for_user_with_settings` 去掉 tag 过滤 |
| matmaster-evo | `builtin_skills_sync.py`：停止给 sync item 塞 category/tags（与 D9 删 `_load_tags_config` 一并） |
| 前端 | `api/user-skill.ts` `UserSkillOut` 去掉 category/tags；skills 页去掉"按分类筛选 / 按 tag 切换"UI |

保留字段（仍在用）：`id / user_id / source / name / description / status / enabled / tools / artifact_id / bundle_object_key / content_sha256 / byte_size / file_count / extra / created_at / updated_at`。

> DROP 列是破坏性 migration：必须先让写入侧（evo sync、前端）停发 category/tags，再执行 DROP，避免旧版本写不存在的列。

### 4.7 `${PLUGIN_DIR}` 已是承重占位符（修正前置 spec 的过时表述）

前置 `remote-plugins-root-design.md` §1 写"仓库 15 个内置 plugin 的 SKILL.md 全部未使用 `${PLUGIN_DIR}`"，并据此把 §3.5 的远端 `${PLUGIN_DIR}` 渲染标为"当前无实际使用者"。**该表述已过时**：plotting plugin（2026-06-11 加入）的每个 skill 都在用 `${PLUGIN_DIR}/shared/...`：

- `plot-chart` / `plot-materials` / `plot-report`：`sys.path.insert(0, "${PLUGIN_DIR}/shared")` 后 `import mm_style`；
- 全部 4 个 skill：`Read ${PLUGIN_DIR}/shared/style-contract.md`；
- `plot-diagram`：`python3 ${PLUGIN_DIR}/shared/svg2png.py ...`、`${PLUGIN_DIR}/shared/svg_prelude.txt`。

影响：远端 `${PLUGIN_DIR}` 渲染（前置 spec §3.5）从"可选/无使用者"升为**本管线硬依赖**。NAS 上 `${PLUGIN_DIR}` 必须解析为 `/personal/.matmaster/plugins/<plugin>`，且该目录下 `shared/` 等辅助资源已随包同步到位（§4.1、§4.3）。实现计划阶段不得砍 §3.5。

`matmaster/skills/builtin_tags.yaml` 当前把 `matmaster/skills/` 的 11 个散装 skill 编进 3 类 / 6 组，消费者仅 `src/services/builtin_skills_sync.py`（同步时贴 category/tags）与 `scripts/migrate_to_plugins.py`。**evo 运行时一处不读**（`get_meta_info_context` 不消费 category/tags）。

决策：**删除 `builtin_tags.yaml`**，散装 skill 全部扁平，前端 skill 管理页不再按类分组。理由：按 evo 模型，运行时唯一分组维度是 plugin；这 11 个散装 skill 的弱主题分类不进 prompt，留着只是前端筛选维度，与"plugin 即唯一组"的目标不一致。

删除影响与连带改动：

- evo 运行时：零影响。
- `builtin_skills_sync.py`：`_load_tags_config` 与 category/tags 注入路径删除，散装 skill 以 `category=None, tags=None` 上传（`.get` 不崩）。
- `scripts/migrate_to_plugins.py`：清理其对 `builtin_tags.yaml` / tags 的依赖。
- 前端：散装 skill 列表变扁平（无分类筛选）。

可选（不在本 spec 强制）：若某个簇（如 characterization、system-tools）确需运行时分组，按 dual-track 单独把它做成 plugin —— 这是 plugin 化的常规路径，与本删除决策不冲突。

## 6. 错误处理

| 故障 | 行为 |
|---|---|
| plugin zip 无 `plugin.yaml` / 坏 YAML | tools-server `status=failed`，前端不同步，列表标错误 |
| bundle 无单一顶层目录 | 创建/同步拒绝（400 / `status=failed`）；身份恒取顶层目录名（D18） |
| `plugin.yaml` 含废弃的 `name` 字段 | 不阻断，lint 警告；evo 不读该字段，身份仍取目录名（D18） |
| plugin 无任何成员 `SKILL.md` | 创建时拒绝（400 / `status=failed`），强制至少一个成员 |
| NAS 写入部分失败 | 镜像 skill 同步器：该 plugin 保留旧 manifest sha，下轮重试 |
| 成员 `SKILL.md` 与 `plugin.yaml` 同目录（布局错误） | 不救护，evo 侧按现状行为处理（成员降级/被吞），前端文档提示正确布局 |
| 远端 plugins 根不存在 | evo `path_exists` 检查后跳过（前置 spec 现有语义） |

## 7. 已确认决策

| # | 决策 |
|---|---|
| D1 | 资产层以 evo 模型为真相源；plugin 是唯一运行时分组单元，散装 skill 扁平无组 |
| D2 | tools-server 用独立 plugin 表，不复用 skill 表加 `kind` |
| D3 | plugin 成员存 plugin 行 JSON `member_skills`，不建子表（无成员级查询/开关需求） |
| D4 | 归属唯一不变量：每个 skill 恰好一个归属（散装 或 唯一 plugin），无共用 |
| D5 | plugin 包复用现有 `artifact_api`，与 skill 一致 |
| D6 | builtin 同步不双写，按「remote-plugins-root → 前端/tools-server 通道 → evo 同步翻转」有序翻转 |
| D7 | 空 plugin（无成员）创建时拒绝 |
| D8 | plugin manifest 独立 `/plugins/.manifest.json`（`{plugins:{name:sha}}`），不与 skills 合并 |
| D9 | 删除 `builtin_tags.yaml`，散装 skill 全部扁平；清理 `builtin_skills_sync.py` 与 `migrate_to_plugins.py` 对它的依赖 |
| D10 | skill 表瘦身：删除 `category`、`tags` 两列及 by-tag toggle/filter 死链路（§4.6）；DROP 列前先停写入侧 |
| D11 | plugin 用两张表：`user_plugins` + `user_plugin_settings`；`enabled` 走 settings 表（镜像 skill）；`member_skills` 为 `[{name,description,dir}]`；成员完整内容经 plugin 单一 artifact 的 `/content?path=` 寻址，不为成员单独建 artifact |
| D12 | plugin bundle = 整个 plugin 目录树（含 `shared/` 等辅助目录）；`${PLUGIN_DIR}` 已被 plotting 使用，远端渲染（前置 spec §3.5）为硬依赖 |
| D13 | plugin 表**设 `category` 列**，来自 `plugin.yaml`，用于前端按大类分组 plugin 列表（词表：simulation/structure-modeling/analysis/reporting/workflow-system）。与被删的 skill.category 不同：plugin.category 始终有 plugin.yaml 真实来源，非死字段；evo 运行时仍不消费。**不按子目录推导**：目录派生只对仓库内 builtin 成立，未来外部注册插件无仓库目录，会源头分裂；plugin.yaml 字段是唯一能统一 builtin + 外部插件的来源（§4.2 字段说明） |
| D14 | 嵌套成员 skill 按「递归收所有 `SKILL.md` + 拍平」枚举，`member_skills[*].dir` 取各自最近目录相对路径；列表不携带层级，前端组内平铺（§4.2） |
| D15 | plugin 上传 zip 直传 + 预签名 URL 两通道都做，镜像 skill 现状 |
| D16 | **plugin 是唯一开关单元，不下钻成员 skill**：plugins 根 `.settings.json` 用 `{disabled_plugins:[plugin名]}`，evo cache 层并入 `read_disabled_plugins` 喂 `remove_plugin_members`（按 `skill.plugin.name` 过滤）。废弃"展开成员名写 disabled"折中，成员名撞名与禁用无关。需前置 spec 把 `read_disabled_plugins` 扩成"静态 config ∪ 远端 .settings.json"（§4.5） |
| D17 | user plugin 加 `UNIQUE(user_id, name, source)`，上传同名直接拒。理由：plugin 名驱动 NAS 目录 / `${PLUGIN_DIR}` / 禁用匹配，重名会撞车（builtin 行 user_id=NULL，靠"删旧全量重插"防重，不依赖该索引） |
| D18 | **plugin 身份 = 目录名，唯一来源**。`plugin.yaml` 只留 category/description、不含 name。目录名同时充当 `user_plugins.name` / NAS 目录名 / `disabled_plugins` 匹配键 / `${PLUGIN_DIR}` 末段。**evo 改为直接用目录名、不读 yaml name（去掉 `registry.py` 的 `raw.get("name") or` 分支，无 fallback）**，使唯一来源成为代码层硬保证——即便 yaml 仍含 name 也被无视，无漂移。tools-server 不再为正确性校验 name，仅 lint 警告；删 16 个 builtin plugin.yaml 的 `name:` 行 + parity 测试（§4.1、§4.2、§4.5） |

## 8. 范围外

- evo 运行时识别远端 plugins 根的实现（前置 spec 负责）。
- `category` 字段的运行时消费（evo 模型已定为死字段）。
- ~~远端 plugin 级独立禁用配置~~ → **已纳入范围**（D16）：plugins 根 `.settings.json` 的 `disabled_plugins`，按 plugin 名经 `remove_plugin_members` 生效，不下钻成员。
- service 层 `build_skill_registry` 的 plugin 禁用接入（前置 spec 范围外延续）。
- NAS 多根扫描的 SSH 往返合并优化。
- **外部用户注册插件、开放给全平台**（future）。这是「可见性/scope」维度（私有归属 `user_id` → 平台公开），与 `category`（主题分组）正交，将来会另加 visibility/scope 字段实现，不得与 category 混用。本 spec 的 `user_plugins` 已用 `source(builtin|user)` + `user_id`，为将来扩 scope 留位，但全平台发布流程不在本 spec。

## 9. 自检

- 以 evo 模型为真相源：plugin 是唯一运行时分组单元，散装 skill 扁平无组，category 不进 prompt（D1）。
- tools-server 用独立 plugin 表，不复用 skill 表加 `kind`；两表对两根、两类、两同步器（D2）。
- plugin 成员在 tools-server 仅为 JSON 派生展示，归属由 evo 远端按目录祖先重新推导（D3）。
- 归属唯一：成员只存在于其 plugin 目录，绝不并存于 `/skills` 散装根（D4）。
- builtin 同步不双写，有序翻转，停止把 plugin 压成 tag（D6）。
- `builtin_tags.yaml` 删除，连带清理其两个消费者（D9）。
- skill 表删除死字段 `category`/`tags` 及 by-tag 链路，DROP 列前先停写入侧（D10）。
- plugin 两张表，`enabled` 走 settings 表，`member_skills` 带 `dir`，成员内容经单一 artifact 寻址（D11）。
- plugin bundle 含 `shared/` 等整树；`${PLUGIN_DIR}` 远端渲染为硬依赖（D12）。
- plugin 表设 `category`（来自 plugin.yaml，前端分组用，非死字段，区别于被删的 skill.category）（D13）。
- 嵌套成员拍平枚举，`member_skills[*].dir` 取最近目录，列表无层级（D14）。
- 上传 zip + URL 两通道（D15）。
- plugin 级禁用按 plugin 名（`disabled_plugins`），不下钻成员，无撞名歧义；需前置 spec cache 层并集读取（D16）。
- user plugin `UNIQUE(user_id,name,source)`，同名上传拒（D17）。
- plugin 身份 = 目录名唯一来源（plugin.yaml 不含 name）；evo 去掉 `raw.get("name") or` 分支、直接用目录名，唯一来源成代码层硬保证，无 fallback、无漂移；tools-server 仅 lint 警告（D18）。
- 前端写文件遵守 §4.1 目录硬约束，否则 evo 不识别。
- 不在本 spec 重述 evo 运行时识别逻辑，只定义内容如何到达远端 plugins 根。
