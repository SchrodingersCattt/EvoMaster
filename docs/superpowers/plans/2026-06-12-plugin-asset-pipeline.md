# Plugin 资产管线 实现计划（前端 → tools-server → NAS → evo 运行时）

> **For agentic workers:** 按 spec `docs/superpowers/specs/2026-06-12-plugin-asset-pipeline-design.md`（D1–D18）逐 Task 实施。步骤用 `- [ ]` 勾选追踪。本计划跨三仓，按 spec §4.4 的 **D6 有序翻转** 编排 Phase；同一 Phase 内自底向上、每步保持绿。

**Goal:** 让 plugin 成为从前端上传到运行时识别的一等资产，镜像现有 skill 管线。终态：前端 `pages/plugins` → tools-server `user_plugins` CRUD（OSS+DB）→ 前端 `plugin-nas-sync.ts` 把 plugin 完整目录树写入 Bohrium NAS `/personal/.matmaster/plugins/<plugin>/` → evo 运行时按 plugin 分组、按 plugin 名禁用。同时收口 D9/D10：删 `builtin_tags.yaml`、瘦身 skill 表。

**Architecture（按 D6 有序翻转分 Phase）:**
- **Phase ①（已完成，无需实施）** evo remote-plugins-root 已落地：`agent_run_bohrium.py:135` 已把 `_BOHRIUM_REMOTE_USER_PLUGINS_ROOT` 配进 `remote_skill_roots`；`registry.py` 已有 `remove_plugin_members` / `read_disabled_plugins`；`skill_registry_cache.py` 已是 4 元组 key + 构建后过滤。**本计划不重做。**
- **Phase A（evo 契约增量，可独立先行）** D18 registry 去 yaml name 读取（身份恒取目录名）+ D16 cache 读远端 `/plugins/.settings.json` 的 `disabled_plugins` 并 union + parity 测试。纯 evo 内部、不依赖另两仓，先行降风险。
- **Phase B（tools-server）** `user_plugins` + `user_plugin_settings` 迁移、model、dao、service、API，全程镜像 `user_skill` 链路。
- **Phase C（前端）** `api/user-plugin.ts` + `services/plugin-nas-sync.ts` + plugin 管理 UI，镜像 skill 同步器但保留 plugin 完整目录树、按 plugin 名禁用。
- **Phase D（evo builtin 同步翻转）** `builtin_skills_sync.py` 的 plugin 轨从"压平发 `/skills`"翻转为"整包发 `/plugins/sync-builtin`"。**必须在 B/C 上线、`/plugins` 有数据后执行**（D6 不双写）。
- **Phase E（skill 表瘦身，D9/D10）** 删 `builtin_tags.yaml`、三仓删 `category`/`tags` 死字段与 by-tag 链路。**写入侧先停（Phase D 已不发 tags）再 DROP 列。**

执行顺序：**A → B → C → D → E**。A 可与 B/C 并行；D 依赖 B+C 上线且 evo 已认 `/plugins`（①已满足）；E 依赖 D（plugin 成员已走 `/plugins`，`/skills` 不再有 plugin tag 写入）。

**Tech Stack:**
- **matmaster-evo**：Python 3.13（`.venv`）、pydantic、PyYAML、pytest、httpx；pre-commit（black `--skip-string-normalization` / isort / flake8，line-length 88，单文件 ≤1000 行，doc 除外）。引号：`matmaster/`、`tests/` 双引号，`src/` 单引号。
- **matmaster-tools-server**：FastAPI、pydantic v2、SQL（MySQL/InnoDB）、OSS（oss2）、pytest。迁移放 `migrations/*.sql`。
- **scimaster-bohr-chat**：Vite + React + TypeScript、axios、JSZip、NAS 文件 API。

---

## 0. 执行须知（先读）

- **三仓工作树**：本计划跨 `matmaster-evo` / `matmaster-tools-server` / `scimaster-bohr-chat`。每个 Phase 标注所在仓库；切仓即切对应工作树，测试/格式化命令按各仓约定。
- **Phase 间有数据依赖，不可乱序**：D 在 B+C 上线后才翻转（否则 plugin 成员从 `/skills` 消失又无 `/plugins` 接住）；E 在 D 之后（先停 tags 写入再 DROP 列）。A 无外部依赖，最先做。
- **镜像优先**：B/C 的每个文件都有 skill 侧对照物（`user_skill_*` / `skill-nas-sync.ts`）。优先复制其结构再改差异点，不要另起炉灶。差异点已在各 Task 列明。
- **evo 测试约束**：沿用仓库习惯，新用例尽量加进现有测试文件（`tests/test_skill_registry.py`、`tests/matmaster/core/test_skill_registry_cache.py`）。
- **每个 commit message 末尾**附 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- **起点**：各仓执行前记录 `git rev-parse HEAD` 作为 `<Phase 起点 commit>`，收尾统计净代码量用。

### 0.1 计划级裁定（spec 未逐字明示，据代码事实裁定）

| 事项 | 裁定 | 依据 |
|------|------|------|
| D18 evo 改法 | `_parse_plugin_info_from_content` 形参 `fallback_name` 重命名为 `dir_name`，`PluginInfo.name = dir_name`（删 `raw.get("name") or`） | 名实相符，明示"目录名即身份、无 fallback" |
| D16 远端禁用读取位置 | 在 `build_cached_skill_registry` 的 `build()` 内读远端 `disabled_plugins`，union 进喂给 `remove_plugin_members` 的集合 | 镜像现有 `_disabled_skill_names_from_remote_settings` 的懒读（SSH 往返放 build 内） |
| 远端 `disabled_plugins` 是否进 cache key | 不进 | cache 生命周期是 per-query/session（见 cache 文件 docstring），远端 per-user 差异天然隔离；与现有远端 skill 禁用同处理 |
| plugin bundle 解包取名 | tools-server 取 zip 顶层目录名为 `name`；多顶层目录/无顶层目录则拒（D18、D7 同款校验位置） | spec §4.2 错误表 |
| plugin 包 zip 打包单位 | 整个 plugin 根目录（`_zip_dir(plugin_dir)`），复用 `_zip_skill_dir` 的固定时间戳逻辑、改为接受任意根 | spec §4.4、D12 |
| `member_skills` 枚举 | 递归 `plugin_dir.rglob("SKILL.md")`，`dir` = 相对 plugin 根的最近目录；跳过 `_` 前缀链 | D14 |
| tools-server 是否复用 user_skill 的 materializer/bundle_parser | 复用 materializer（OSS 落地通用）；新增 plugin bundle parser（取目录名 + 枚举成员 + 读 plugin.yaml） | materializer 与资产类型无关；解析逻辑 plugin 特有 |

### 0.2 关键代码事实速查（执行者零上下文需要；行号为撰写时锚点，以符号名定位为准）

**matmaster-evo**
- `matmaster/skills/registry.py`：`PluginInfo`（88-93）、`_parse_plugin_info_from_content`（96-103，**D18 改这里**，行 100 `name=str(raw.get("name") or fallback_name)`）、`parse_plugin_info`（106-110）、`remove_plugin_members`（618）、`read_disabled_plugins`（273，读本地 `plugins.yaml` 的 `disabled_plugins`）。
- `matmaster/core/skill_registry_cache.py`：已是 4 元组 key；`build()`（82-113）已读远端 skill 禁用（89-93）并 `remove_plugin_members(disabled_plugins)`（99）。**D16 在 build() 内补远端 `disabled_plugins` union**。
- `matmaster/skills/settings.py`：`disabled_skill_names_from_remote_settings(session, root)` 读远端 `.settings.json` 的 `disabled`。**D16 新增 `disabled_plugins_from_remote_settings` 读 `disabled_plugins`**。
- `src/services/builtin_skills_sync.py`：`_zip_skill_dir`（112）、`_build_skill_item`（158，塞 category/tags）、`_scan_builtin_skills`（190，plugin 轨在 211-224 压平成 `tags=[plugin.name]`）、`_load_tags_config`（39）、`sync_builtin_skills_to_tools_server`（229，发 `/skills/sync-builtin`）。**Phase D 改 plugin 轨；Phase E 删 tags 相关**。
- `agent_run_bohrium.py:135`：`remote_skill_roots = [plugins, skills]`（已就绪）。

**matmaster-tools-server**
- `src/services/user_skill_service.py`：`create`（68，OSS 取包→parse name→materialize→artifact upsert→db insert）、`_row_to_out`（158）、`list_for_user`（181）、`delete`（194）、`toggle`（199，items/tag 二选一，enabled→batch_delete，否则 batch_upsert false）、`sync_builtin`（225，build_seq 防陈旧→materialize→`replace_all_builtin`）。
- `src/apis/user_skill_api.py`：`GET /skills`、`POST /skills/upload-url`、`POST /skills/upload-zip`、`POST /skills`、`DELETE /skills/{id}`、`PATCH /skills/toggle`；鉴权 `get_current_user` + `user.user_id == user_id` 校验。
- `src/models/user_skill.py`：`UserSkillOut`（68，含待删 category/tags）、`SyncBuiltinSkillItem`（178）、`SkillToggleRequest`（157，含待删 tag）、upload-url / upload-zip 请求响应模型。
- dao：`user_skill_db.py`（`insert/get_one/list_for_user_with_settings/delete_for_user/replace_all_builtin/get_builtin_build_seq`）、`user_skill_settings_db.py`（`batch_upsert/batch_delete`）。
- `migrations/add_user_skills.sql`、`migrations/add_skill_switch.sql`（settings 表）。
- `src/services/user_skill_artifact_materializer.py`（`materialize_user_skill_zip_to_oss`，资产无关，可复用）、`user_skill_bundle_parser.py`（`parse_display_name_from_skill_zip`，skill 特有，plugin 另写）。
- `src/services/oss_presign_service.py`：`USER_SKILL_KEY_PREFIX`、`presign_user_skill_zip_put`、`upload_user_skill_zip_bytes`、`safe_user_id_segment`。

**scimaster-bohr-chat**
- `src/services/skill-nas-sync.ts`：`SKILLS_BASE_PATH=/personal/.matmaster/skills`、`MANIFEST_PATH`、`SETTINGS_PATH`、`SkillSettings{disabled:string[]}`（67）、`writeSettings`（122）、主同步循环（下载 artifact zip→JSZip 解包→nasSaveFile→manifest diff，245-298）、`updateSkillSettings`（396）、`deleteSkillDir`。
- `src/api/user-skill.ts`：`UserSkillOut` 类型、`getUserSkillsList`、`toggle`、`create`、`presignUpload` 等。
- skill 同步触发点（登录后全量 / 每轮发送前增量）：搜 `skill-nas-sync` 的调用方对照接。

---

## Phase A — evo 契约增量（仓库：matmaster-evo）

> 纯 evo 内部改动，不依赖另两仓，最先做。落地 D18（身份恒取目录名、去 fallback）与 D16（远端按 plugin 名禁用）。

### Task A1: D18 — registry 去 yaml name 读取，身份恒取目录名

**Files:**
- Modify: `matmaster/skills/registry.py`（`_parse_plugin_info_from_content` / `parse_plugin_info`）
- Test: `tests/test_skill_registry.py`

- [ ] **A1.1 写失败测试**

`tests/test_skill_registry.py` 加（放在 plugin 相关用例附近）：

```python
def test_plugin_name_always_from_dir_ignoring_yaml_name() -> None:
    """D18：PluginInfo.name 恒取目录名，yaml 的 name 被忽略。"""
    from matmaster.skills.registry import _parse_plugin_info_from_content

    info = _parse_plugin_info_from_content(
        "name: yaml-name\ncategory: simulation\ndescription: Desc\n",
        dir_name="dir-name",
    )
    assert info.name == "dir-name"
    assert info.category == "simulation"
    assert info.description == "Desc"
```

- [ ] **A1.2 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_skill_registry.py::test_plugin_name_always_from_dir_ignoring_yaml_name -q`
Expected: FAIL（现状 `name == "yaml-name"`；且关键字参数名仍是 `fallback_name`，会 TypeError）。

- [ ] **A1.3 实现**

`matmaster/skills/registry.py`，`_parse_plugin_info_from_content`（行 96-103）改为：

```python
def _parse_plugin_info_from_content(content: str, *, dir_name: str) -> PluginInfo:
    raw = yaml.safe_load(content) or {}
    category = raw.get("category")
    return PluginInfo(
        name=dir_name,
        category=str(category).strip() if category else None,
        description=str(raw.get("description") or ""),
    )
```

`parse_plugin_info`（行 106-110）随之改关键字：

```python
def parse_plugin_info(manifest_path: Path) -> PluginInfo:
    return _parse_plugin_info_from_content(
        manifest_path.read_text(encoding="utf-8"),
        dir_name=manifest_path.parent.name,
    )
```

再全仓搜其余调用点（远端扫描 `_load_remote_skills` 内也调 `_parse_plugin_info_from_content(..., fallback_name=plugin_dir.name)`），一并把关键字 `fallback_name=` 改为 `dir_name=`：

Run: `grep -rn "_parse_plugin_info_from_content\|fallback_name=plugin_dir" matmaster/skills/registry.py`
确保所有调用点都传 `dir_name=`。

- [ ] **A1.4 跑测试确认通过 + 回归**

Run: `.venv/bin/python -m pytest tests/test_skill_registry.py -q`
Expected: 全 PASS（含既有远端 plugin 归属用例——它们的 fixture 里 plugin.yaml 的 name 恰等于目录名，行为不变）。

- [ ] **A1.5 删 16 个 builtin plugin.yaml 的 `name:` 行**

让"目录名即身份"成为真约束。逐个编辑 `matmaster/plugins/<p>/plugin.yaml`，删掉 `name:` 行，保留 `category` / `description`。

Run 验证不剩 name 行：`grep -rn "^name:" matmaster/plugins/*/plugin.yaml`
Expected: 无输出。

再跑：`.venv/bin/python -m pytest tests/test_skill_registry.py -q` 与 `.venv/bin/python -c "from matmaster.skills.registry import parse_plugin_info; from pathlib import Path; print(parse_plugin_info(Path('matmaster/plugins/vasp/plugin.yaml')).name)"`
Expected: 打印 `vasp`。

- [ ] **A1.6 Commit**

```bash
git add matmaster/skills/registry.py matmaster/plugins tests/test_skill_registry.py
git commit -m "$(cat <<'EOF'
feat(skills): plugin 身份恒取目录名，去除 plugin.yaml name 读取（D18）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

### Task A2: D16 — cache 读远端 `disabled_plugins`，按 plugin 名禁用

**Files:**
- Modify: `matmaster/skills/settings.py`（新增 `disabled_plugins_from_remote_settings`）
- Modify: `matmaster/core/skill_registry_cache.py`（`build()` 内 union 远端 disabled_plugins）
- Test: `tests/matmaster/core/test_skill_registry_cache.py`

- [ ] **A2.1 写失败测试**

`tests/matmaster/core/test_skill_registry_cache.py` 末尾加（复用文件内既有 `FakeRemoteSkillSession`、`_skill_body`、`_write` 等辅助；fake 的 `exec_bash`/`read_file` 已能投喂远端文件，settings 读取走 `read_file`）：

```python
def test_remote_disabled_plugins_filters_members(tmp_path: Path) -> None:
    """D16：远端 /plugins/.settings.json 的 disabled_plugins 按 plugin 名过滤成员。"""
    plugins_root = "/personal/.matmaster/plugins"
    session = FakeRemoteSkillSession(
        plugins_root,
        {
            f"{plugins_root}/pack/plugin.yaml": "description: Pack\n",
            f"{plugins_root}/pack/skills/member/SKILL.md": _skill_body(
                "member-skill", "Member"
            ),
            f"{plugins_root}/.settings.json": (
                '{"disabled_plugins": ["pack"]}'
            ),
        },
    )
    skills_cfg = ExpSkillsConfig(
        enabled=True, skills_root=[], config_dir=str(tmp_path / "cfg")
    )

    registry = build_cached_skill_registry(
        skills_cfg=skills_cfg, session=session, skill_cache=SkillRegistryCache()
    )

    assert registry is not None
    assert registry.get_skill("member-skill") is None
```

> 注：若现有 `FakeRemoteSkillSession` 的 settings 读取路径或方法名与此不符，按文件内既有远端 skill 禁用测试的投喂方式对齐（关键是让 `disabled_plugins_from_remote_settings` 能读到 `.settings.json`）。

- [ ] **A2.2 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/matmaster/core/test_skill_registry_cache.py::test_remote_disabled_plugins_filters_members -q`
Expected: FAIL（member-skill 仍可见——当前只读本地 `plugins.yaml` 的 disabled_plugins）。

- [ ] **A2.3 实现 settings 读取**

`matmaster/skills/settings.py` 新增（镜像同文件的 `disabled_skill_names_from_remote_settings`，只改读取的字段名 `disabled` → `disabled_plugins`，返回 `set[str]`）：

```python
def disabled_plugins_from_remote_settings(session: Any, remote_root: str) -> set[str]:
    """读远端 <root>/.settings.json 的 disabled_plugins（plugin 名集合）。"""
    # 复制 disabled_skill_names_from_remote_settings 的读取/容错逻辑，
    # 仅把取出的字段从 "disabled" 换为 "disabled_plugins"。
```

- [ ] **A2.4 接入 cache build()**

`matmaster/core/skill_registry_cache.py`：import 加 `disabled_plugins_from_remote_settings`；`build()` 内、`registry = SkillRegistry(...)` 之后、`remove_plugin_members` 之前，把远端 disabled_plugins union 进去：

```python
        effective_disabled_plugins = set(disabled_plugins)
        if remote_roots and session is not None:
            for remote_root in remote_roots:
                effective_disabled_plugins.update(
                    disabled_plugins_from_remote_settings(session, remote_root)
                )
        removed_members = registry.remove_plugin_members(effective_disabled_plugins)
```

（替换原 `removed_members = registry.remove_plugin_members(disabled_plugins)` 一行。）

- [ ] **A2.5 跑测试确认通过 + 回归**

Run: `.venv/bin/python -m pytest tests/matmaster/core/test_skill_registry_cache.py tests/test_skill_registry.py -q`
Expected: 全 PASS。

- [ ] **A2.6 Commit**

```bash
git add matmaster/skills/settings.py matmaster/core/skill_registry_cache.py tests/matmaster/core/test_skill_registry_cache.py
git commit -m "$(cat <<'EOF'
feat(skills): cache 读远端 .settings.json 的 disabled_plugins 按 plugin 名禁用（D16）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

### Task A3: parity 测试 + Phase A 收尾

- [ ] **A3.1 parity 测试（evo 与 tools-server 取名一致性的 evo 侧锚点）**

在 `tests/test_skill_registry.py` 加一个断言"取名规则 = 目录名"的稳定锚点（tools-server 侧在 Phase B 加对称断言）：

```python
def test_plugin_name_equals_dir_for_all_builtin() -> None:
    """所有 builtin plugin 的 PluginInfo.name 等于其目录名（与 tools-server 取名口径一致）。"""
    from pathlib import Path

    from matmaster.skills.registry import parse_plugin_info

    for manifest in sorted(Path("matmaster/plugins").glob("*/plugin.yaml")):
        info = parse_plugin_info(manifest)
        assert info.name == manifest.parent.name
```

- [ ] **A3.2 全量测试 + 格式化**

```bash
.venv/bin/python -m pytest -q
.venv/bin/pre-commit run --files matmaster/skills/registry.py matmaster/skills/settings.py matmaster/core/skill_registry_cache.py tests/test_skill_registry.py tests/matmaster/core/test_skill_registry_cache.py
```

格式化若改写文件，重跑 pre-commit 至 no-op，再跑 pytest 确认绿，追加 `style:` 提交。

- [ ] **A3.3 Commit parity 测试**

```bash
git add tests/test_skill_registry.py
git commit -m "$(cat <<'EOF'
test(skills): builtin plugin 取名口径 = 目录名（D18 parity 锚点）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Phase B — tools-server plugin 实体（仓库：matmaster-tools-server）

> 全程镜像 `user_skill` 链路（迁移→model→dao→service→API）。差异点：独立两表、`member_skills` JSON、name=目录名、plugin 级 toggle、整包 zip。命令按该仓 pytest/迁移约定。

### Task B1: 迁移 — user_plugins + user_plugin_settings

**Files:**
- New: `migrations/add_user_plugins.sql`
- New: `migrations/add_user_plugin_settings.sql`

- [ ] **B1.1 写 `migrations/add_user_plugins.sql`**（直接照搬 spec §4.2 表 1 DDL，含 `UNIQUE(user_id,name,source)`）：

```sql
CREATE TABLE IF NOT EXISTS user_plugins (
  id              VARCHAR(64)  NOT NULL PRIMARY KEY,
  source          VARCHAR(16)  NOT NULL DEFAULT 'user' COMMENT 'builtin / user',
  user_id         VARCHAR(128) NULL COMMENT 'builtin 为 NULL',
  name            VARCHAR(256) NOT NULL COMMENT 'plugin 名 = bundle 顶层目录名（非 plugin.yaml，D18）',
  description     VARCHAR(1024) NULL COMMENT '来自 plugin.yaml，进 evo [Plugin:] 行',
  category        VARCHAR(64)  NULL COMMENT '来自 plugin.yaml，前端按大类分组',
  member_skills   JSON         NULL COMMENT '派生展示 [{name,description,dir}]',
  status          VARCHAR(32)  NOT NULL DEFAULT 'uploading' COMMENT 'uploading/ready/failed',
  bundle_object_key VARCHAR(1024) NULL,
  content_sha256  CHAR(64)     NULL,
  byte_size       BIGINT UNSIGNED NULL,
  file_count      INT UNSIGNED NULL,
  artifact_id     VARCHAR(64)  NULL,
  extra           JSON         NULL,
  created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_user_plugins_user     (user_id),
  KEY idx_user_plugins_source   (source),
  KEY idx_user_plugins_status   (status),
  KEY idx_user_plugins_category (category),
  KEY idx_user_plugins_created  (user_id, created_at),
  UNIQUE KEY uq_user_plugins_name (user_id, name, source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户 / 内置 Plugin';
```

- [ ] **B1.2 写 `migrations/add_user_plugin_settings.sql`**（照搬 spec §4.2 表 2，`source` 默认 `'user'`）。

- [ ] **B1.3 在本地/测试库执行迁移**，确认两表建成（按该仓迁移执行脚本/流程）。

### Task B2: model — `src/models/user_plugin.py`

镜像 `user_skill.py`，差异：去 `tags`；`UserPluginOut` 加 `member_skills: list[dict] | None`、保留 `category`、`description`；toggle 仅按 plugin 名（无 tag 分支）。

- [ ] **B2.1** 新建 `src/models/user_plugin.py`，定义：
  - `CreateUserPluginRequest`（同 `CreateUserSkillRequest`：`bundle_object_key` + sha/size/status/extra）。
  - `MemberSkillItem{ name:str; description:str=''; dir:str }`。
  - `UserPluginOut{ id, source, user_id, name, description, category, member_skills:list[MemberSkillItem]|None, status, enabled, artifact_id, bundle_object_key, content_sha256, byte_size, file_count, extra, created_at, updated_at }`（**无 tags/tools**）。
  - `PluginToggleItem{ plugin_name:str; source:str='builtin' }`、`PluginToggleRequest{ items:list[PluginToggleItem]; enabled:bool }`（**无 tag 字段**，D16 plugin 级）。
  - upload-url / upload-zip 请求响应（直接镜像 skill 的 4 个模型，改名 Plugin）。
  - `SyncBuiltinPluginItem{ name, description='', category, member_skills:list[MemberSkillItem]|None, artifact_id, bundle_object_key, content_sha256, byte_size, file_count }`（**无 tags**）、`SyncBuiltinPluginsRequest{ version, build_seq, plugins:list[...] }`、响应 `{deleted,inserted}`。

### Task B3: dao — `src/dao/user_plugin_db.py` + `user_plugin_settings_db.py`

- [ ] **B3.1** 新建 `user_plugin_db.py`，镜像 `user_skill_db.py` 的方法（`insert / get_one / list_for_user_with_settings / delete_for_user / replace_all_builtin / get_builtin_build_seq`），差异：
  - 列集去 `tags/tools`，加 `category / member_skills`（`member_skills` 入库 `json.dumps`、读出 `json.loads`）。
  - `insert` 受 `UNIQUE(user_id,name,source)` 约束：重复 name 由 DB 报错；service 层捕获转 400（B5.1）。
  - `list_for_user_with_settings`：LEFT JOIN `user_plugin_settings`（按 `user_id,name,source`）带出 `enabled`（无记录视为 True）。去掉 skill 版的 `tag` 过滤参数。
- [ ] **B3.2** 新建 `user_plugin_settings_db.py`，镜像 `user_skill_settings_db.py` 的 `batch_upsert(user_id, items, enabled)` / `batch_delete(user_id, items)`，键 `(user_id, plugin_name, source)`。

### Task B4: plugin bundle parser — `src/services/user_plugin_bundle_parser.py`

- [ ] **B4.1** 新建解析器（plugin 特有，不复用 skill 的 display-name parser）：输入 zip bytes，输出 `(name, description, category, member_skills)`。
  - `name` = zip 顶层唯一目录名；**无单一顶层目录则 raise**（D18/D7）。
  - 读 `<top>/plugin.yaml`：缺失/坏 YAML → raise；取 `category` / `description`（**不读 name**）。若 yaml 含 `name` 且 ≠ 目录名 → 仅 log 警告（D18）。
  - `member_skills`：遍历 zip 内 `<top>/**/SKILL.md`，跳过 `_` 前缀目录链；每个成员 `dir` = 相对 `<top>` 的最近目录（如 `skills/plot-chart`），`name` 取 SKILL.md frontmatter `name`（缺失 fallback 目录名），`description` 取 frontmatter。无任何成员 → raise（D7）。
  - 定义 `PluginBundleParseError`。

### Task B5: service — `src/services/user_plugin_service.py`

镜像 `user_skill_service.py`，差异点如下。

- [ ] **B5.1 `create`**：流程同 skill（OSS 取包→parse→materialize→artifact upsert→db insert→读回 `UserPluginOut`），但：
  - 用 B4 的 parser 取 `name/description/category/member_skills`；parser raise → 400。
  - artifact `biz_type='user_plugin'`。
  - `db.insert` 因 `UNIQUE` 冲突（重名）→ 捕获并返回 400「同名 plugin 已存在」（D17）。
- [ ] **B5.2 `list_for_user`**：去 `tag` 参；`_row_to_out` 映射含 `category/member_skills`、无 `tags/tools`。
- [ ] **B5.3 `delete`**：同 skill（仅删库记录）。
- [ ] **B5.4 `toggle`**：**仅 items 分支**（按 plugin_name，无 tag 分支）；`enabled` → `batch_delete`，否则 `batch_upsert(enabled=False)`。
- [ ] **B5.5 `sync_builtin`**：镜像 skill 的 build_seq 防陈旧 + `replace_all_builtin`；每个 plugin item 用整包 zip materialize（`user_id='__builtin__'`，`biz_type='user_plugin'`），写 `category/member_skills`、**不写 tags**。

### Task B6: API — `src/apis/user_plugin_api.py` + 注册路由

- [ ] **B6.1** 新建 `user_plugin_api.py`，镜像 `user_skill_api.py` 全部端点，路径 `skills`→`plugins`：
  - `GET /plugins`（去 `tag` 参）、`POST /plugins/upload-url`、`POST /plugins/upload-zip`、`POST /plugins`、`DELETE /plugins/{plugin_id}`、`PATCH /plugins/toggle`（按 plugin 名）。
  - 鉴权同 skill（`get_current_user` + `user.user_id == user_id`）。
  - upload 复用 OSS presign service；建议给 plugin 单独的 key 前缀（`USER_PLUGIN_KEY_PREFIX`）或复用 skill 前缀——按该仓 OSS 约定择一，spec 未强制。
- [ ] **B6.2** `POST /plugins/sync-builtin`（镜像 `skill_admin_api.py` 的 sync-builtin），给 evo Phase D 用。
- [ ] **B6.3** 在主路由注册 `user_plugin_api.router`（对照 user_skill 路由注册处）。

### Task B7: tests + parity + Phase B 收尾

- [ ] **B7.1** 镜像 `tests/test_user_skill_service.py` / `test_user_skill_api.py` 写 plugin 版用例，覆盖：create（成功 / 无成员拒 / 重名拒 / 无单一顶层目录拒）、list 带 enabled、toggle（plugin 名 enabled/disable）、sync_builtin（build_seq 防陈旧、replace_all）、`member_skills` 含 `dir`。
- [ ] **B7.2 parity 锚点**：加一个断言"plugin name = zip 顶层目录名"的测试，与 evo A3.1 对称。
- [ ] **B7.3** 跑该仓测试全绿；按仓内规范格式化。
- [ ] **B7.4** Commit（可按 model/dao/service/api 分多次提交，message 末尾附 Co-Authored-By）。

---

## Phase C — 前端 plugin API + NAS 同步器 + UI（仓库：scimaster-bohr-chat）

> 镜像 `skill-nas-sync.ts` / `user-skill.ts`，差异：目标根 `/plugins/`、保留 plugin 完整目录树、manifest 形状 `{plugins:{name:sha}}`、`.settings.json` 用 `{disabled_plugins:[plugin名]}`。

### Task C1: API 客户端 — `src/api/user-plugin.ts`

- [ ] **C1.1** 新建 `user-plugin.ts`，镜像 `user-skill.ts`：
  - 类型 `UserPluginOut`（含 `name/description/category/member_skills:{name,description,dir}[]/status/enabled/artifact_id/content_sha256/...`，**无 tags**）。
  - 函数：`getUserPluginsList(userId)`、`togglePlugins(userId, items, enabled)`（按 plugin_name）、`createPlugin`、`presignPluginUpload` / `uploadPluginZip`、`deletePlugin`。全部打到 tools-server `/users/{uid}/plugins*`。

### Task C2: NAS 同步器 — `src/services/plugin-nas-sync.ts`

- [ ] **C2.1** 新建，镜像 `skill-nas-sync.ts`，差异点逐条落实：
  - `PLUGINS_BASE_PATH = '/personal/.matmaster/plugins'`；`MANIFEST_PATH`/`SETTINGS_PATH` 指向该根。
  - `PluginSettings { disabled_plugins: string[] }`（**不是 skill 的 `disabled`**）。
  - 主循环：`getUserPluginsList` → 仅取 `status==='ready'` → 按 `content_sha256` 与 manifest 增量 diff → 下载 plugin artifact zip → **JSZip 解包保持完整目录树**写入 `<PLUGINS_BASE_PATH>/<name>/...`（`plugin.yaml` 在根、成员在 `skills/...`、`shared/` 等原样；**不铺平、不只挑 plugin.yaml+skills**）。
  - manifest 形状 `{plugins: {<name>: sha}}`；删除 manifest 中已不存在的 plugin 目录（镜像 skill 的 deleteSkillDir）。
  - **plugin 级禁用（D16）**：`disabledPlugins = plugins.filter(p => !p.enabled).map(p => p.name)`；写 `.settings.json` `{disabled_plugins: disabledPlugins}`。**不展开成员名。**
  - `updatePluginSettings(userId)`：开关变更后只重写 `.settings.json`（镜像 `updateSkillSettings`）。
- [ ] **C2.2** NAS 目录名用 `UserPluginOut.name`（= tools-server 的目录名口径，D18），保证与 evo `plugin.name` / `disabled_plugins` 匹配。

### Task C3: 触发接入 + UI

- [ ] **C3.1** 把 `plugin-nas-sync` 接到与 `skill-nas-sync` 并列的触发点（登录后全量 / 每轮发送前增量）；两同步器互不阻塞。
- [ ] **C3.2** UI：plugin 管理页（或现有 skills 页加 plugin 分区）。按 `category` 分组列 plugin；每个 plugin 展开显示 `member_skills`（点成员可 `GET /artifacts/{artifact_id}/content?path={dir}/SKILL.md` 拉内容）；**plugin 整体开关**（无成员级开关）。上传走 C1 的 upload + create。

### Task C4: Phase C 收尾

- [ ] **C4.1** 本地联调：上传一个 plugin → tools-server `status=ready` → 登录触发同步 → NAS `/plugins/<name>/` 出现完整目录树 + `.manifest.json` + 关掉后 `.settings.json` 出现 `disabled_plugins:[<name>]`。
- [ ] **C4.2** Commit（按仓内 lint/类型检查通过后）。

---

## Phase D — evo builtin 同步翻转到 /plugins（仓库：matmaster-evo）

> **前置硬依赖**：Phase B 的 `/plugins/sync-builtin` 已上线、Phase C 的同步器已能把 `/plugins` 铺到 NAS、且 evo 已认 `/plugins`（①已满足）。D6 不双写：本 Phase 一刀切，翻转后 plugin 成员只走 `/plugins`。

### Task D1: builtin_skills_sync 拆出 plugin 轨

**Files:**
- Modify: `src/services/builtin_skills_sync.py`
- Test: 现有 sync 测试文件（搜 `builtin_skills_sync` 的测试）

- [ ] **D1.1** `_zip_skill_dir` 泛化为可打任意根（或新增 `_zip_plugin_dir(plugin_dir)` 复用同一固定时间戳 + `_ZIP_EXCLUDE` 逻辑，按整个 plugin 根 `rglob("*")` 打包，含 `plugin.yaml`/`skills/`/`shared/`）。
- [ ] **D1.2** `_scan_builtin_skills` 的 plugin 轨（行 211-224）**停止**把成员压成 `tags=[plugin.name]` 的散装 skill。改为产出 plugin 整包条目：每个 `matmaster/plugins/<p>/` → `{name: 目录名, description, category, member_skills:[{name,description,dir}], zip_bytes, content_sha256, byte_size, file_count}`（`member_skills` 按 D14 递归枚举）。扁平轨（`matmaster/skills/`）保持发 `/skills` 不变。
- [ ] **D1.3** 新增 `sync_builtin_plugins_to_tools_server()`（镜像 `sync_builtin_skills_to_tools_server`）：整包 zip 走 `POST /users/__builtin__/plugins/upload-zip` 拿 object_key → 组 payload → `POST /api/v1/plugins/sync-builtin`（带 version/build_seq/plugins）。
- [ ] **D1.4** 启动时调用点：在调用 skill sync 处并列调用 plugin sync（两者独立，互不阻塞）。
- [ ] **D1.5** 测试：plugin 轨产出整包条目（不再有 `tags=[plugin.name]` 的散装项发往 `/skills`）；扁平轨不变。
- [ ] **D1.6** 跑 evo 全量测试 + 格式化 + Commit。

---

## Phase E — 删 builtin_tags.yaml + 三仓 skill 表瘦身（D9/D10）

> **前置**：Phase D 已让 plugin 成员走 `/plugins`，`/skills` 不再收到 plugin 的 tags。本 Phase 先停所有 category/tags 写入侧，最后才 DROP 列（D10 破坏性迁移顺序）。

### Task E1: evo — 删 builtin_tags.yaml 依赖（仓库：matmaster-evo）

- [ ] **E1.1** `src/services/builtin_skills_sync.py`：删 `_load_tags_config`、`_TAGS_FILE`；`_build_skill_item` 去掉 category/tags 注入（散装 skill 以 `category=None, tags=None` 上传，`.get` 不崩）；`sync_builtin_skills_to_tools_server` 的 item 不再塞 category/tags。
- [ ] **E1.2** 删 `matmaster/skills/builtin_tags.yaml`；清理 `scripts/migrate_to_plugins.py` 对它/对 tags 的依赖。
- [ ] **E1.3** 跑测试 + 格式化 + Commit。

### Task E2: tools-server — 停写 category/tags + 删 by-tag 链路（仓库：matmaster-tools-server）

> 必须在 E3 DROP 列之前合入并上线（写入侧先停）。

- [ ] **E2.1** `models/user_skill.py`：删 `UserSkillOut.category/tags`、`SyncBuiltinSkillItem.category/tags`、`SkillToggleRequest.tag`、`SkillToggleItem` 不变。
- [ ] **E2.2** `services/user_skill_service.py`：`_row_to_out` 去 category/tags；`sync_builtin` 的 rows 去 category/tags；`toggle` 删 by-tag 分支（仅留 items）；`list_for_user` 删 `tag` 参。
- [ ] **E2.3** `apis/user_skill_api.py`：`GET /skills` 删 `tag` query；`PATCH /skills/toggle` 删 tag 校验分支。
- [ ] **E2.4** dao `user_skill_db.py`：`list_for_user_with_settings` 去 `tag` 过滤。
- [ ] **E2.5** 跑该仓测试（改相关用例）+ Commit。

### Task E3: tools-server — DROP 列迁移（仓库：matmaster-tools-server）

> **仅在 E1+E2 全部上线、确认无写入方再发 category/tags 后执行。**

- [ ] **E3.1** 新建 `migrations/drop_user_skills_category_tags.sql`：`ALTER TABLE user_skills DROP COLUMN category, DROP COLUMN tags;` + `DROP INDEX idx_user_skills_category`。
- [ ] **E3.2** 执行迁移；冒烟 list/toggle/sync-builtin 仍正常。
- [ ] **E3.3** Commit。

### Task E4: 前端 — 去 category/tags UI（仓库：scimaster-bohr-chat）

- [ ] **E4.1** `api/user-skill.ts` `UserSkillOut` 去 category/tags；skills 页去掉"按分类筛选 / 按 tag 切换"UI，散装 skill 列表扁平。
- [ ] **E4.2** 联调 + Commit。

---

## 完成定义

- **Phase A**：`PluginInfo.name` 恒取目录名（A1 测试钉死，16 个 builtin plugin.yaml 已无 `name:` 行）；远端 `/plugins/.settings.json` 的 `disabled_plugins` 按 plugin 名过滤成员（A2 测试钉死）；parity 锚点绿；evo 全量绿。
- **Phase B**：`user_plugins` + `user_plugin_settings` 两表就位；CRUD/upload/toggle/sync-builtin 全链路镜像 skill 且通过；`member_skills` 含 `dir`；重名/无成员/无单一顶层目录均被拒（D7/D17/D18）；parity 锚点绿。
- **Phase C**：上传 plugin 经 tools-server 落地后，前端同步器把**完整目录树**写到 `/personal/.matmaster/plugins/<name>/`，manifest 形状 `{plugins:{name:sha}}`，关 plugin 写 `disabled_plugins:[name]`（不展开成员）。
- **Phase D**：builtin plugin 以整包发 `/plugins/sync-builtin`，不再把成员压成 tag 发 `/skills`；扁平轨不变；不双写。
- **Phase E**：`builtin_tags.yaml` 删除且无残留引用；三仓 skill 表 category/tags 写入侧先停、再 DROP 列；by-tag toggle/filter 链路删除；前端散装 skill 列表扁平。
- **端到端**：远端 agent 运行时，用户上传的 plugin 以 `[Plugin: <目录名>]` 分组出现在系统提示词，plugin 级开关生效，`${PLUGIN_DIR}/shared/...` 在远端正确解析（依赖 §4.7，①已就绪）。
- **跨仓上线顺序**：A 任意；B、C 先于 D；E2/E1 先于 E3（DROP）。任何顺序倒置都可能造成成员短暂消失或写不存在的列。
