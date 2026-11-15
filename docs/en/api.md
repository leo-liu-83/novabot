# HTTP API

## 1. Basics

Start the web server:

```bash
novabot web --host 127.0.0.1 --port 9000
```

API prefix:

```text
/api/v1
```

Default response envelope:

```json
{"code": 200, "message": "Success", "data": {}}
```

Fixed errors may include a stable code:

```json
{"code": 401, "message": "...", "error": "auth.unauthorized"}
```

Raw exception text has no code; model output is never translated.

## 2. Authentication

| Case | Behavior |
|---|---|
| `multi_user=false` | No authentication |
| `multi_user=true`, no token | 401 |
| `multi_user=true`, invalid/expired/revoked token | 401 |

Header:

```http
Authorization: Bearer <token>
```

Login:

```bash
curl -X POST http://127.0.0.1:9000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "leo", "password": "your-password"}'
```

Use `data.token` for subsequent requests.

Anonymous identity exists only in the TUI. In multi-user mode every API request maps to a real account.

## 3. Auth endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/auth/status` | Multi-user / setup status |
| POST | `/auth/setup` | Create the first administrator |
| POST | `/auth/login` | Log in |
| POST | `/auth/register` | Register and log in |
| GET | `/auth/me` | Current identity, quota, attachment limits |
| POST | `/auth/logout` | Revoke the current token |
| POST | `/auth/password` | Change password and rotate token |
| DELETE | `/auth/account` | Delete account |
| PUT/DELETE | `/auth/avatar` | Set / clear avatar |

## 4. Sessions

| Method | Path | Purpose |
|---|---|---|
| GET | `/sessions` | List sessions |
| POST | `/sessions` | Create a session |
| GET | `/sessions/{id}` | Session metadata |
| PATCH | `/sessions/{id}` | Rename |
| DELETE | `/sessions/{id}` | Delete session |
| GET | `/sessions/{id}/messages?limit=30&before_id=...` | Paginated history |
| PATCH | `/sessions/{id}/level` | Change session execution level |
| POST | `/sessions/{id}/attachments` | Upload an attachment |
| DELETE | `/sessions/{id}/attachments/{file_id}` | Delete an unsent attachment |
| GET | `/sessions/usage?days=7` | Token usage for N days |
| GET | `/sessions/stats` | Session and token totals |

Session IDs are server-issued; client-created IDs or other users' IDs are rejected.

## 5. Chat

```http
POST /api/v1/chat/completions
Content-Type: application/json
```

```json
{
    "query": "hello",
    "session_id": "optional; omit for a new session",
    "model": "provider/model",
    "enable_thinking": true,
    "attachments": [
        {"id": "uploaded-id", "name": "report.pdf"}
    ]
}
```

SSE events include:

- `session_id`
- `answer`
- `think`
- `tool_call` / `tool_end`
- `usage` / `context` / `progress`
- `interrupt`
- `warning` / `error`

Return the `session_id` on the next request to continue.

Resume an interrupted turn:

```http
POST /api/v1/chat/resume
```

## 6. Models

```http
GET /api/v1/models
```

Returns display metadata and `configured`; never returns keys, `base_url` or headers.

## 7. Search providers

| Method | Path | Purpose |
|---|---|---|
| GET/PUT/PATCH/DELETE | `/web/providers[/{kind}]` | User providers |
| GET/PUT/PATCH/DELETE | `/web/global-providers[/{kind}]` | Global providers, admin only |
| GET | `/web/quota` | Current user's quota |

## 8. MCP

| Method | Path | Purpose |
|---|---|---|
| GET | `/mcp/servers` | List system and user servers |
| PUT/PATCH/DELETE | `/mcp/servers/{name}` | Add/update, enable/mode, delete |
| GET | `/mcp/servers/{name}/tools` | Tools and per-tool modes |
| PUT/DELETE | `/mcp/servers/{name}/tools/{tool}` | Set override / inherit server mode |
| GET | `/mcp/admin/servers` | Admin view of account MCP config |

## 9. Skills

| Method | Path | Purpose |
|---|---|---|
| GET | `/skills` | Visible skills |
| POST | `/skills/{name}/scan` | Scan |
| POST | `/skills/{name}/release` | Release |
| POST | `/skills/{name}/reject` | Reject |
| GET | `/skills/{name}/files` | File list |
| GET | `/skills/{name}/file` | Read a skill file |

Private skills require `layer=private` and `owner_uid` to distinguish same-name skills.

## 10. Administration

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/admin/users` | List/create users |
| PATCH/DELETE | `/admin/users/{uid}` | Update/delete |
| GET | `/system` | Read-only system information |
| GET | `/admin/reports` | User activity, model usage, and token statistics |

Administrator or single-user owner only.

Report query parameters:

| Parameter | Default | Description |
|---|---|---|
| `days` | 30 | Last N days including today, 1–365 |
| `bucket` | `day` | Time granularity, `day` / `week` |
| `limit` | 20 | Top users/models, 1–100 |

Returns `range / summary / activity / tokens / models / users`; aggregates only, never message content.

## 11. Error codes

Common stable codes:

```text
auth.unauthorized
auth.password_wrong
session.not_found
session.not_owned
attachment.too_large
attachment.too_many
attachment.type_denied
mcp.*
```

The frontend translates fixed messages by `error`; raw exceptions without a code stay as-is.
