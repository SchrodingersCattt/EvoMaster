-- test 环境一次性 Bohrium Node 运行态对账。
--
-- 前置条件：
-- 1. 已部署包含 Bohrium invocation lease 与 node recycler 的 API/Worker/Monitor；
-- 2. 以下 10 个 node_id 来自 2026-07-14 的 provider 运行态审计；
-- 3. 在 test DMS、低流量时段执行；不要在其他环境执行；
-- 4. 先执行只读预检查。只有 target_count=10、ready_count=10、
--    leased_count=0 时，才执行 UPDATE。
--
-- 状态处理：
-- - provider status=-1 的 4 条：ready -> paused，不再调用 provider stop；
-- - provider status=2 的 6 条：ready -> stopping，由集群内 Monitor 在无 live
--   lease 时调用 provider stop，成功后 stopping -> paused；
-- - PROVIDER_LIST_MISSING 的 19 条不在本脚本中，保持 ready 等待后续审计。

-- 0. 环境确认：人工确认当前连接确实是 test 数据库。
SELECT DATABASE() AS current_database, @@hostname AS database_host;

-- 1. 明细预检查。每个目标都必须存在、仍为 ready，且 total_lease_count=0。
SELECT expected.node_id,
       expected.provider_status,
       expected.target_state,
       nodes.id AS node_slot_id,
       nodes.user_id,
       nodes.org_id,
       nodes.project_id,
       nodes.sku_id,
       nodes.state AS current_state,
       COALESCE(leases.total_lease_count, 0) AS total_lease_count,
       COALESCE(leases.live_lease_count, 0) AS live_lease_count,
       nodes.last_used_at,
       nodes.updated_at
FROM (
    SELECT 20079820 AS node_id, -1 AS provider_status, 'paused' AS target_state
    UNION ALL SELECT 20079823, -1, 'paused'
    UNION ALL SELECT 20079799, -1, 'paused'
    UNION ALL SELECT 20079880, -1, 'paused'
    UNION ALL SELECT 20079564, 2, 'stopping'
    UNION ALL SELECT 20079631, 2, 'stopping'
    UNION ALL SELECT 20079706, 2, 'stopping'
    UNION ALL SELECT 20079796, 2, 'stopping'
    UNION ALL SELECT 20079819, 2, 'stopping'
    UNION ALL SELECT 20079841, 2, 'stopping'
) AS expected
LEFT JOIN evo_bohrium_nodes AS nodes
  ON nodes.node_id = expected.node_id
LEFT JOIN (
    SELECT node_slot_id,
           COUNT(*) AS total_lease_count,
           SUM(lease_expires_at > NOW()) AS live_lease_count
    FROM bohrium_node_leases
    GROUP BY node_slot_id
) AS leases
  ON leases.node_slot_id = nodes.id
ORDER BY expected.provider_status, expected.node_id;

-- 2. 汇总预检查。执行 UPDATE 前必须返回：10 / 10 / 0。
SELECT COUNT(*) AS target_count,
       SUM(nodes.state = 'ready') AS ready_count,
       SUM(
           EXISTS (
               SELECT 1
               FROM bohrium_node_leases AS leases
               WHERE leases.node_slot_id = nodes.id
           )
       ) AS leased_count
FROM evo_bohrium_nodes AS nodes
WHERE nodes.node_id IN (
    20079820, 20079823, 20079799, 20079880,
    20079564, 20079631, 20079706, 20079796, 20079819, 20079841
);

-- 3. 一次性原子更新。
-- eligibility_guard 强制 10 条全部仍为 ready 且没有任何 lease；任一目标不满足时，
-- 整条 UPDATE 更新 0 行，不会只迁移部分槽位。
UPDATE evo_bohrium_nodes AS nodes
JOIN (
    SELECT COUNT(*) AS eligible_count
    FROM evo_bohrium_nodes AS candidates
    WHERE candidates.node_id IN (
        20079820, 20079823, 20079799, 20079880,
        20079564, 20079631, 20079706, 20079796, 20079819, 20079841
    )
      AND candidates.state = 'ready'
      AND NOT EXISTS (
          SELECT 1
          FROM bohrium_node_leases AS leases
          WHERE leases.node_slot_id = candidates.id
      )
) AS eligibility_guard
  ON eligibility_guard.eligible_count = 10
SET nodes.state = CASE
        WHEN nodes.node_id IN (20079820, 20079823, 20079799, 20079880)
            THEN 'paused'
        ELSE 'stopping'
    END,
    nodes.creating_invocation_id = NULL,
    nodes.creating_lease_token = NULL,
    nodes.creating_lease_expires_at = NULL,
    nodes.last_used_at = CASE
        WHEN nodes.node_id IN (
            20079564, 20079631, 20079706, 20079796, 20079819, 20079841
        ) THEN NOW()
        ELSE nodes.last_used_at
    END,
    nodes.last_error = NULL,
    nodes.updated_at = NOW()
WHERE nodes.node_id IN (
    20079820, 20079823, 20079799, 20079880,
    20079564, 20079631, 20079706, 20079796, 20079819, 20079841
)
  AND nodes.state = 'ready'
  AND NOT EXISTS (
      SELECT 1
      FROM bohrium_node_leases AS leases
      WHERE leases.node_slot_id = nodes.id
  );

-- 必须紧接 UPDATE 执行。首次执行预期 updated_count=10；如果为 0，说明保护条件
-- 未满足或脚本已经执行过，禁止手工放宽 WHERE，重新执行预检查。
SELECT ROW_COUNT() AS updated_count;

-- 4. UPDATE 后立即核对：4 条应为 paused，6 条应为 stopping。
SELECT nodes.node_id,
       CASE
           WHEN nodes.node_id IN (20079820, 20079823, 20079799, 20079880)
               THEN 'paused'
           ELSE 'stopping'
       END AS expected_state,
       nodes.state AS current_state,
       nodes.last_error,
       nodes.updated_at
FROM evo_bohrium_nodes AS nodes
WHERE nodes.node_id IN (
    20079820, 20079823, 20079799, 20079880,
    20079564, 20079631, 20079706, 20079796, 20079819, 20079841
)
ORDER BY nodes.node_id;

-- 5. 等待至少 2~3 分钟后再次查询。Monitor 默认 10 秒扫描一次，stopping 槽位
-- 默认需老化 120 秒才会重试 stop。6 条 stopping 最终应变成 paused；如果 provider
-- 已经找不到 Node，对应 DB 槽位可能被删除，这也是允许的终态。
SELECT expected.node_id,
       expected.immediate_state,
       CASE
           WHEN nodes.id IS NULL THEN 'DB_ROW_REMOVED'
           ELSE nodes.state
       END AS current_state,
       nodes.last_error,
       nodes.updated_at
FROM (
    SELECT 20079820 AS node_id, 'paused' AS immediate_state
    UNION ALL SELECT 20079823, 'paused'
    UNION ALL SELECT 20079799, 'paused'
    UNION ALL SELECT 20079880, 'paused'
    UNION ALL SELECT 20079564, 'stopping'
    UNION ALL SELECT 20079631, 'stopping'
    UNION ALL SELECT 20079706, 'stopping'
    UNION ALL SELECT 20079796, 'stopping'
    UNION ALL SELECT 20079819, 'stopping'
    UNION ALL SELECT 20079841, 'stopping'
) AS expected
LEFT JOIN evo_bohrium_nodes AS nodes
  ON nodes.node_id = expected.node_id
ORDER BY expected.node_id;
