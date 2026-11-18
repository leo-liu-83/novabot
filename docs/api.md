# HTTP API

## 1. 基础

启动 Web：

```bash
novabot web --host 127.0.0.1 --port 9000
```

API 前缀：

```text
/api/v1
```

默认返回统一信封：

```json
{"code": 200, "message": "Success", "data": {}}
```

固定错误可能带稳定错误码：

```json
{"code": 401, "message": "...", "error": "auth.unauthorized"}
```

原始异常文本不带错误码；模型输出永远不翻译。

## 2. 鉴权

| 情况 | 行为 |
|---|---|
| `multi_user=false` | 不鉴权 |
| `multi_user=true`，不带 token | 401 |
| `multi_user=true`，token 无效/过期/吊销 | 401 |

请求头：

```http
Authorization: Bearer <token>
```

登录：

```bash
curl -X POST http://127.0.0.1:9000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "leo", "password": "你的密码"}'
```

响应里的 `data.token` 用于后续请求。

匿名身份只存在于 TUI；多用户模式下 API 每个请求都对应真实账号。

## 3. 认证接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/auth/status` | 多用户 / 初始化状态 |
| POST | `/auth/setup` | 创建首个管理员 |
| POST | `/auth/login` | 登录 |
| POST | `/auth/register` | 注册并登录 |
| GET | `/auth/me` | 当前身份、配额、附件限额 |
| POST | `/auth/logout` | 吊销当前 token |
| POST | `/auth/password` | 修改密码并轮换 token |
| DELETE | `/auth/account` | 注销账号 |
| PUT/DELETE | `/auth/avatar` | 设置 / 清除头像 |

## 4. 会话

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/sessions` | 会话列表 |
| POST | `/sessions` | 创建会话 |
| GET | `/sessions/{id}` | 会话元数据 |
| PATCH | `/sessions/{id}` | 重命名 |
| DELETE | `/sessions/{id}` | 删除会话 |
| GET | `/sessions/{id}/messages?limit=30&before_id=...` | keyset 分页读取历史 |
| PATCH | `/sessions/{id}/level` | 调整本会话执行档位 |
| PATCH | `/sessions/{id}/project` | 设置或取消会话的项目归属 |
| POST | `/sessions/{id}/attachments` | 上传附件 |
| DELETE | `/sessions/{id}/attachments/{file_id}` | 删除未发送附件 |
| GET | `/sessions/usage?days=7` | 近 N 天 token 用量 |
| GET | `/sessions/stats` | 账号会话与 token 汇总 |

`session_id` 由服务端签发；客户端自造或使用他人会话键会被拒绝。

## 5. 聊天

### 发送消息

```http
POST /api/v1/chat/completions
Content-Type: application/json
```

```json
{
    "query": "你好",
    "session_id": "可选；首次留空",
    "model": "provider/model",
    "thinking_level": "medium",
    "enable_thinking": true,
    "attachments": [
        {"id": "已上传附件 id", "name": "report.pdf"}
    ]
}
```

`thinking_level` 取 `off` / `low` / `medium` / `high`，不传表示跟随模型默认；
`enable_thinking` 是旧字段，等价于 `true=medium` / `false=off`。`show_thinking`
只控制是否回显思考内容，不改变请求档位。

返回 SSE 流式事件：

- `session_id`：服务端签发的会话键
- `answer`：正文增量
- `think`：思考增量
- `tool_call` / `tool_end`：工具调用
- `usage` / `context` / `progress`：token、上下文和速度
- `interrupt`：等待用户确认
- `warning` / `error`：诊断与错误

续聊时把首次返回的 `session_id` 原样带回。

### 恢复中断

```http
POST /api/v1/chat/resume
```

用于处理待确认工具，可带 `interrupt_id` 逐条处理。

## 6. 模型

```http
GET /api/v1/models
```

返回模型展示信息和 `configured` 状态，不返回 key、base_url 或请求头。另外带 `default_model`
与 `subagents`：

```json
{
  "default_model": "dashscope/qwen3.5-plus",
  "subagents": [
    {"role": "explore", "title": "探索", "description": "只读查资料、跨文件定位",
     "model": "", "fallback_model": "dashscope/qwen3.5-plus", "inherits": true}
  ]
}
```

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/models/default` | 设置 `agent.model`（管理员） |
| PUT | `/models/subagents/{role}` | 设置某个子代理角色的模型（管理员） |

`PUT` 的 body 是 `{"model": "provider/model"}`；空串表示跟随会话模型。角色必须是
`explore` / `general`，否则 404 `model.role_not_found`；非空模型必须能在当前配置里用，否则
404 `model.not_found`。

响应里还有密钥加密状态（系统信息页用）：

```json
"secrets": {
  "enabled": true, "algo": "sm2", "fingerprint": "9f3c1a02",
  "plaintext": 0, "undecryptable": 0
}
```

`plaintext` 是配置文件与库里剩余的明文 key 条数；`undecryptable` 是带密文前缀却解不开的条数
（主密钥缺失或不匹配）。没有主密钥时 `enabled=false`。

## 7. 搜索 provider

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/PUT/PATCH/DELETE | `/web/providers[/{kind}]` | 用户自备 provider |
| GET/PUT/PATCH/DELETE | `/web/global-providers[/{kind}]` | 全局 provider，仅管理员 |
| GET | `/web/quota` | 当前账号配额 |

## 8. MCP

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/mcp/servers` | 列出系统层与用户层 server |
| PUT/PATCH/DELETE | `/mcp/servers/{name}` | 新增或更新 / 启停或切换模式 / 删除 |
| GET | `/mcp/servers/{name}/tools` | 工具列表与逐工具模式 |
| PUT/DELETE | `/mcp/servers/{name}/tools/{tool}` | 设置逐工具权限 / 恢复继承 |
| GET | `/mcp/admin/servers` | 管理员查看账号 MCP 配置 |

## 9. 技能

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/skills` | 当前身份可见技能 |
| POST | `/skills/{name}/scan` | 扫描 |
| POST | `/skills/{name}/release` | 放行 |
| POST | `/skills/{name}/reject` | 拒绝 |
| GET | `/skills/{name}/files` | 文件列表 |
| GET | `/skills/{name}/file` | 读取技能文件 |

私有技能需带 `layer=private` 和 `owner_uid` 以区分同名技能。

## 10. 管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/admin/users` | 用户列表 / 创建 |
| PATCH/DELETE | `/admin/users/{uid}` | 修改 / 删除 |
| GET | `/system` | 只读系统信息 |
| GET | `/admin/reports` | 用户活跃、模型用量与 token 统计 |

仅管理员或单用户机主可用。

报表查询参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `days` | 30 | 最近 N 天，含今天，范围 1–365 |
| `bucket` | `day` | 时间粒度，`day` / `week` |
| `limit` | 20 | Top 用户/模型条数，范围 1–100 |

返回 `range / summary / activity / tokens / models / users`，只包含账号、模型和 token 聚合，不返回消息正文。

## 11. 项目

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/projects` | 活动项目列表 |
| POST | `/projects` | 登记一个已有目录作为项目 |
| GET | `/projects/browse?path=...` | 浏览本机目录（选项目目录用） |
| GET | `/projects/{id}` | 项目详情 |
| PATCH | `/projects/{id}` | 改名 / 调整推荐技能 |
| DELETE | `/projects/{id}` | 归档项目（不删除目录） |
| GET | `/projects/{id}/agents` | 读取项目根 `AGENTS.md` |
| PUT | `/projects/{id}/agents` | 保存或手工创建 `AGENTS.md` |
| DELETE | `/projects/{id}/agents` | 删除 `AGENTS.md` |
| POST | `/projects/{id}/agents-draft` | 按项目现状生成草稿（不落盘） |
| POST | `/projects/{id}/agents` | 写入用户确认过的草稿 |
| POST | `/projects/{id}/agents-generate` | 让模型生成 `AGENTS.md` 草稿 |

需要项目功能授权；未开通时返回 `project.disabled`（403）。

## 12. 错误码

常见稳定码：

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

前端按 `error` 翻译固定提示；没有 `error` 的原始异常保持原文。
