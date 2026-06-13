# 远端双根工作区设计：打通 /share 与 /personal

日期：2026-06-10
状态：已评审通过

## 背景与目标

agent 在远端（Bohrium SSH 节点）执行时，workspace root 目前被限制在 `/share` 下；远端节点上还挂载了 `/personal` 用于存放用户个人文件。本设计打通两个根：

1. 会话 workspace 可以选在 `/share` 或 `/personal` 任意一个根下。
2. 无论 workspace 在哪个根下，两个根对 agent 始终全量可读可写。
3. 不设任何额外防护，`/personal` 与 `/share` 完全对等（含现作为只读根暴露的 `/personal/.matmaster/skills`，全根可写后该只读限制被覆盖，属预期行为）。

仅针对远端 SSH 会话；local 与 docker 会话行为零变化。

## 关键决策记录

| 决策点 | 结论 |
|---|---|
| 访问模式 | /personal 可作为完整工作区，不只是引用源 |
| 双根关系 | 双根始终全开（可读可写），与 workspace 位置无关 |
| 防护策略 | 不设防，完全对等 |
| 实现机制 | 方案 A：双根作为可写 PathAccessRoot 注入（见下） |
| 模型可见性 | 不在 prompt 中显式说明双根，模型靠工具结果自然发现 |

### 备选方案（已否决）

- **RuntimeTopology 增加 writable_roots 一等字段**：与 path_access_roots 职责重叠，校验层与消费方均需适配，改动面大而收益仅是显式性。
- **校验层硬编码双前缀**：把 Bohrium 部署细节泄漏进 matmaster 通用校验层，破坏分层，且污染 local 会话。

## 现状：需要打通的硬编码点

| 位置 | 现状 |
|---|---|
| `src/services/session_directory_service.py:62` | 会话目录 API 校验，只认 `/share` |
| `src/dao/bohrium_jobs_table.py:33` `_require_workspace` | DAO 写入校验，只认 `/share` |
| `src/sql/create_bohrium_jobs_table.sql` `chk_workspace_share_path` | SQL CHECK 约束，只认 `/share` |
| `matmaster/core/path_access.py` `derive_path_access_roots` | 从不注入可写根 |
| `matmaster/tools/builtin/bohrium_tool/paths.py:9` `_REMOTE_SHARE_PREFIXES` | 已含双根，但为独立硬编码副本 |

关键既有机制：`matmaster/types/topology.py` 的 `PathAccessOperation` 已定义 `"write"` 操作类型，`PathAccessRoot.permissions` 为 frozenset；`structural_validation.py` 的 `_allowed_roots_for_operation()` 已按操作类型过滤根。核心校验机制天然支持多个可写根，只差注入。

## 设计

### 1. 单一权威常量

`matmaster/types/session.py` 新增：

```python
REMOTE_ACCESS_ROOTS: tuple[str, ...] = ("/share", "/personal")
```

位置理由：types 为最底层，core / tools / src 均可引用而不破坏依赖方向；该文件已承载 `workspace_path` 默认值 `"/share"` 这一同源事实。

收敛三份重复定义：

- `bohrium_tool/paths.py` 的 `_REMOTE_SHARE_PREFIXES` 改为从常量派生。
- `session_directory_service` 与 DAO 的硬编码改为引用常量。
- SQL 无法引用 Python 常量，靠脚本与常量定义处的注释互指维持同步。

### 2. 核心机制：双根可写注入

`derive_path_access_roots()`（`matmaster/core/path_access.py`）：当 `env.session_type == "ssh"` 时，把 `REMOTE_ACCESS_ROOTS` 中每个根以全权限注入：

```python
PathAccessRoot(root=root, kind="remote_root",
               permissions=frozenset({"read", "search", "write"}))
```

docker 与 local 会话不注入（双根是 Bohrium 远端挂载事实）。

不变量：

- `workspace_root` 语义不变：仍是会话当前工作目录与相对路径锚点，可位于任一根下。
- `structural_validation.py` 零修改：绝对路径写操作由 `_allowed_roots_for_operation()` 既有逻辑放行。
- 相对路径仍严格锚定 `workspace_root`，行为不变。
- 现有只读 skills 根（如 `/personal/.matmaster/skills`）与全开 `/personal` 重叠后冗余但无害（校验为 any() 匹配，去重按完整字符串），不动其派生逻辑。
- 去重边角：`derive_path_access_roots` 的 seen 集合以 workspace_root 初始化，workspace 恰为 `/share` 时注入被跳过，行为仍正确（workspace_root 在校验层无条件全权限）。

### 3. 外围放开

**会话目录 API 校验**（`src/services/session_directory_service.py`）

- 校验改为遍历 `REMOTE_ACCESS_ROOTS`：路径等于某根、或为某根加 `/` 的后代（沿用 `==` 或 `startswith(root + "/")` 的精确判断，防 `/personalx` 前缀混淆）。
- 函数改名：`normalize_remote_share_path` → `normalize_remote_workspace_path`。
- 错误码迁移：`directory_outside_share` → `directory_outside_roots`，错误信息列出允许的根。不留旧码；前端若依赖旧错误码需同步迁移（见迁移事项）。

**DAO 校验**（`src/dao/bohrium_jobs_table.py`）

- `_require_workspace()` 改为遍历常量校验，与 API 层判断逻辑同构。

**SQL 约束**

- 建表脚本约束改名并放开：

```sql
CONSTRAINT `chk_workspace_root_path` CHECK (
    `workspace` = '/share' OR `workspace` LIKE '/share/%'
    OR `workspace` = '/personal' OR `workspace` LIKE '/personal/%'
)
```

- 新增外部迁移脚本 `src/sql/migrate_bohrium_jobs_workspace_dual_root.sql`（DROP CHECK 旧约束 + ADD CONSTRAINT 新约束），手动执行，主代码不内联迁移逻辑。

**API 文档与示例文案**

- `src/apis/chat_api.py`、`src/models/chat.py` 中会话目录相关 example 与 description 更新为双根表述。纯文案。

### 4. 测试策略

- `test_session_directory_service.py`：`/personal`、`/personal/sub` 正例；`/personalx`、`/share2`、`/` 负例；错误码断言更新为 `directory_outside_roots`。
- path_access 测试：ssh 会话注入双根且权限含 write；local 与 docker 不注入；workspace_root 恰为 `/share` 时去重不破坏行为。
- structural_validation 集成断言：ssh 拓扑下写工具绝对路径落 `/personal/...` 得 allow；local 拓扑同路径 deny。
- DAO 测试：`/personal/...` workspace 插入通过，`/personalx` 拒绝。
- bohrium_tool paths 现有测试在常量收敛后应继续通过。

## 范围外（明确不做）

- 默认 workspace 不变：用户未指定目录时仍为 `/share`，`get_remote_session_workspace_root()` 及配置项 `remote_session_workspace_root` 不动。
- 不加任何 `/personal` 防护。
- 不在 system prompt / context 组装层添加双根说明（`SessionWorkspaceSource` 保持空载体）。
- 不动 skills 只读根派生逻辑。
- local / docker 会话行为零变化。

## 迁移事项（外部执行）

1. 数据库：手动执行 `src/sql/migrate_bohrium_jobs_workspace_dual_root.sql`。
2. 前端：若依赖错误码 `directory_outside_share`，同步更新为 `directory_outside_roots`。
