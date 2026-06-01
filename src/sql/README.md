# 建库与迁移 SQL

- **新环境**：按顺序执行 `create_chat_tables.sql`、`create_bohrium_nodes_table.sql`、`create_billing_tables.sql`。
- **已有库迁移**：按需执行 `migrate_add_*.sql`（若表已包含对应列可跳过）。
- **LLM 金额计费 dry-run**：`create_billing_tables.sql` 创建模型价格目录 `evo_model_price_catalog` 与用量金额流水 `evo_llm_usage_ledger`；部署后设置 `LLM_BILLING_DRY_RUN_ENABLED=true` 开始采集。
- **会话绑定目录**：`migrate_add_session_directory.sql` 为 `evo_chat_sessions` 增加 `session_directory`（对应 `GET/PUT …/session-directory`）。
- **会话标题（重命名）**：`migrate_add_session_title.sql` 为 `evo_chat_sessions` 增加 `session_title`（对应 `PUT …/title`）；NULL 时前端回退到 `first_user_message`。
