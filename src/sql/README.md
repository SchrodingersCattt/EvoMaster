# 建库与迁移 SQL

- **新环境**：按顺序执行 `create_chat_tables.sql`、`create_bohrium_nodes_table.sql`。
- **Bohrium 作业状态**：执行 `create_bohrium_jobs_table.sql` 创建 `bohrium_jobs`（作业状态事实源，需 MySQL 8.0.16+）。删除 session 不级联删除本表（无 FK），retention 由独立策略处理。
- **已有库迁移**：按需执行 `migrate_add_*.sql`（若表已包含对应列可跳过）。
- **Bohrium Node 生命周期**：已有 `evo_bohrium_nodes` 的环境先执行只读 `preflight_bohrium_node_lifecycle.sql`；确认无重复 `node_id` 后，再执行 `migrate_add_bohrium_node_lifecycle.sql`，增加 nullable `node_id`、槽位状态和 invocation lease 表。迁移仍会用唯一索引显式拦截检查后新出现的重复值。
- **Node 上线前审计**：迁移完成、启用新 Worker/monitor 前执行只读的 `audit_bohrium_node_lifecycle.sql`，人工核对按 SKU 的状态分布、无 live lease 的历史 ready 节点、过期 lease 与过期 creating claim；审计脚本不 stop、delete 或修改 DB。
- **会话绑定目录**：`migrate_add_session_directory.sql` 为 `evo_chat_sessions` 增加 `session_directory`（对应 `GET/PUT …/session-directory`）。
- **会话标题（重命名）**：`migrate_add_session_title.sql` 为 `evo_chat_sessions` 增加 `session_title`（对应 `PUT …/title`）；NULL 时前端回退到 `first_user_message`。
- **会话软删除**：`migrate_add_session_soft_delete.sql` 为 `evo_chat_sessions` 增加 `deleted_at/deleted_by`；用户侧删除仅隐藏会话，保留历史事件。
