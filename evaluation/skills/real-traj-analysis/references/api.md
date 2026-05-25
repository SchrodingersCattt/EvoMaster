# Admin API Reference

## Endpoints

| Environment | Base URL |
|-------------|----------|
| test | `https://matmaster-evo.test.bohrium.com` |
| prod | `https://matmaster-evo.bohrium.com` |

**Auth:** `X-User-Id` header. Must be in tools-server `allowlist.admin`.

User ID 从对应环境的 `.env.*` 文件中获取 `BOHRIUM_USER_ID` 字段：
- test: `.env.test`
- prod: `.env.prod`

## List Sessions

```
GET /api/v1/admin/chat/sessions
  ?sort_by=created_at|last_event_at|event_count
  &order=asc|desc
  &since=ISO8601
  &until=ISO8601
  &min_events=N
  &user_id=xxx (optional filter)
  &limit=N&offset=N
```

## Get Session Events

```
GET /api/v1/admin/chat/sessions/{session_id}/events
  ?after_event_id=N (incremental pull)
  &include_spawn=true|false
  &limit=N
```

Response includes `max_event_id` for tracking analysis state.

## Example: Pull Sessions

```bash
curl -s "$BASE/api/v1/admin/chat/sessions?sort_by=created_at&order=asc&min_events=10&limit=10" \
  -H "X-User-Id: $ADMIN_UID"
```
