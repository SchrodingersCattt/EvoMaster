-- 迁移前只读检查：结果非空时不要执行 uk_node_id 迁移，先人工核对 provider 实例。
SELECT node_id, COUNT(*) AS slot_count
FROM evo_bohrium_nodes
WHERE node_id IS NOT NULL
GROUP BY node_id
HAVING COUNT(*) > 1;
