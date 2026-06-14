# 建库与迁移 SQL

- **新环境**：按顺序执行 `create_chat_tables.sql`、`create_bohrium_nodes_table.sql`。
- **Bohrium 作业状态**：执行 `create_bohrium_jobs_table.sql` 创建 `bohrium_jobs`（作业状态事实源，需 MySQL 8.0.16+）。删除 session 不级联删除本表（无 FK），retention 由独立策略处理。
- **已有库迁移**：按需执行 `migrate_add_*.sql`（若表已包含对应列可跳过）。
- **会话绑定目录**：`migrate_add_session_directory.sql` 为 `evo_chat_sessions` 增加 `session_directory`（对应 `GET/PUT …/session-directory`）。
- **会话标题（重命名）**：`migrate_add_session_title.sql` 为 `evo_chat_sessions` 增加 `session_title`（对应 `PUT …/title`）；NULL 时前端回退到 `first_user_message`。
- **会话软删除**：`migrate_add_session_soft_delete.sql` 为 `evo_chat_sessions` 增加 `deleted_at/deleted_by`；用户侧删除仅隐藏会话，保留历史事件。
