-- 只读 dry-run：在启用 Node recycler 前审计既有槽位，不修改或删除数据。

-- 1. 迁移后的唯一性复核；迁移前检查请先执行 preflight_bohrium_node_lifecycle.sql。
SELECT node_id, COUNT(*) AS slot_count
FROM evo_bohrium_nodes
WHERE node_id IS NOT NULL
GROUP BY node_id
HAVING COUNT(*) > 1;

-- 2. 按 SKU/状态盘点数量与最早、最近使用时间，用于核对运行中和 Paused 费用。
SELECT sku_id, state, COUNT(*) AS slot_count,
       MIN(last_used_at) AS oldest_last_used_at,
       MAX(last_used_at) AS newest_last_used_at
FROM evo_bohrium_nodes
GROUP BY sku_id, state
ORDER BY sku_id, state;

-- 3. ready 但没有 live lease 的历史节点。启用 recycler 前只出报告，不批量 stop/delete。
SELECT n.id, n.user_id, n.org_id, n.project_id, n.sku_id, n.node_id,
       n.state, n.last_used_at, n.updated_at
FROM evo_bohrium_nodes AS n
LEFT JOIN bohrium_node_leases AS l
  ON l.node_slot_id = n.id AND l.lease_expires_at > NOW()
WHERE n.state = 'ready' AND n.node_id IS NOT NULL AND l.id IS NULL
ORDER BY n.last_used_at ASC;

-- 4. 过期 invocation lease；应用 recycler 会用 invocation_id + token + deadline 再做 CAS。
SELECT id, node_slot_id, session_id, invocation_id, lease_expires_at
FROM bohrium_node_leases
WHERE lease_expires_at <= NOW()
ORDER BY lease_expires_at ASC;

-- 5. 过期 create/restart claim；有 node_id 的槽位将 stop→paused，空占位会被删除。
SELECT id, user_id, org_id, project_id, sku_id, node_id,
       creating_invocation_id, creating_lease_expires_at, last_error
FROM evo_bohrium_nodes
WHERE state = 'creating' AND creating_lease_expires_at <= NOW()
ORDER BY creating_lease_expires_at ASC;
