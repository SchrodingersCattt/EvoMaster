# 批量提交（任务组 + 多任务）

按以下顺序执行：

1. 只创建一个任务组并保存返回的 `groupId`。
2. 用 JSON 序列化器生成全部配置，本地逐个解析（`python3 -c "import json;json.load(open('<file>'))"`），并用 `--dry-run` 验证。
3. 串行执行真实 submit；每次成功后立即保存该任务的返回 ID。
4. 已成功的试投直接计入批次，不要再次提交同一任务；只有明确失败且修正原因后才重试。
5. 完成后查询任务组，确认实际任务数与计划一致，再生成汇总文件。

ID 使用规则见 SKILL.md「计算作业」一节的 ID 决策表：`job_group create` 返回的 `groupId` 用于 `job submit --job_group_id`；submit 返回的 `jobGroupId` 用于组查询，两者不同不代表建组失败，不要重复创建任务组。
