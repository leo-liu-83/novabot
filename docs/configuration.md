# 配置参考

## 1. 文件位置

| 文件 | 作用 |
|---|---|
| `config/config.json` | 内置默认配置，随发行版分发，一般不改 |
| `~/.novabot/novabot.json` | 用户配置；模型、网络、MCP、附件等 |
| `~/.novabot/ui.json` | TUI 主题与语言 |
| `~/.novabot/data/novabot.db` | 会话、消息、账号、技能审查、配额等 |
| `~/.novabot/data/login.json` | TUI 登录凭证（`remember_login=true`） |

Windows 路径以 `%USERPROFILE%\.novabot\` 为根。

用户配置会在内置配置之后追加；同 `code` 的 provider 由用户配置覆盖。

模板：仓库根目录的 `novabot.template.json`。

## 2. 最小配置

```json
{
    "models": {
        "deepseek": {
            "name": "DeepSeek",
            "base_url": "https://api.deepseek.com",
            "api": "openai-completions",
            "key": "sk-你的密钥",
            "models": [
                {
                    "code": "deepseek-flash",
                    "name": "DeepSeek Flash",
                    "num_ctx": 131072
                }
            ]
        }
    },
    "agent": {
        "model": "deepseek/deepseek-flash"
    }
}
```

具体模型字段、聚合平台、动态发现和请求头见 [模型配置](model-config.md)。

## 3. 顶层配置

| 字段 | 说明 |
|---|---|
| `models` | provider 与模型列表 |
| `llm` | 模型默认参数 |
| `agent` | 默认模型、最大工具轮数 |
| `exec` | 服务级执行档位封顶 |
| `web` | 搜索与抓取配置 |
| `mcp` | 系统层 MCP server |
| `attachments` | 附件数量、大小、草稿保留 |
| `multi_user` | 是否启用多用户 |
| `remember_login` | 是否跨启动保留 TUI 登录 |

## 4. 模型相关

### `llm`

| 字段 | 默认 | 说明 |
|---|---:|---|
| `max_input_tokens` | 131072 | 模型未配置 `num_ctx` 时的默认上下文窗口 |

### `agent`

| 字段 | 默认 | 说明 |
|---|---:|---|
| `model` | 内置默认 | 新会话默认模型，格式 `provider/model` |
| `max_steps` | 50 | 单次运行 LLM→工具→LLM 往返上限 |

### `models`

```json
"models": {
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api": "openai-completions",
        "key": "sk-or-...",
        "dynamic_models": true
    }
}
```

`models` 省略或为空时配合 `dynamic_models` 自动发现；写了 `models` 则只使用列出的模型。

完整字段见 [模型配置](model-config.md)。

## 5. 执行档位

```json
"exec": {
    "level": "T2"
}
```

`exec.level` 是**服务级封顶**，不是每个账号的默认档位。

| 值 | 含义 |
|---|---|
| T0 | 不执行任何命令 |
| T1 | 只运行已放行技能的 `.py` |
| T2 | 工作区只读命令 |
| T3 | 任意 shell，逐条确认 |
| T4 | 任意 shell，免确认 |

身份默认档位与天花板见 [安全与权限](security.md)。

## 6. 网络搜索与抓取

```json
"web": {
    "search": {
        "enabled": true,
        "providers": []
    },
    "fetch": {
        "permission": "ask",
        "url_allowlist": ["docs.python.org", "github.com"]
    }
}
```

| 字段 | 说明 |
|---|---|
| `web.search.enabled` | 是否启用内置搜索，默认 true |
| `web.search.providers` | 全局第三方搜索接口，列表顺序即优先级 |
| `web.fetch.permission` | `ask` / `allow` / `deny` |
| `web.fetch.url_allowlist` | `ask` 下免确认域名；按域名后缀匹配 |

`url_allowlist` 只影响“是否需要确认”，不是硬白名单；`allow` 下所有公网地址都可抓取。

免费搜索档可用环境变量增加额度：

```text
KEENABLE_API_KEY
FIRECRAWL_API_KEY
PARALLEL_API_KEY
EXA_API_KEY
```

## 7. MCP

```json
"mcp": {
    "servers": [
        {
            "name": "context7",
            "url": "https://mcp.context7.com/mcp",
            "enabled": true,
            "headers": {}
        }
    ],
    "max_user_servers": 3
}
```

`mcp` 段只控制系统层 server。用户层 server 通过 `/mcp` 添加，存在数据库里；写在用户配置里的 `mcp` 段不会生效。

## 8. 附件

```json
"attachments": {
    "max_per_message": 5,
    "max_file_mb": 10,
    "admin_unlimited": true,
    "draft_ttl_days": 7
}
```

| 字段 | 默认 | 说明 |
|---|---:|---|
| `max_per_message` | 5 | 普通用户单条消息附件数上限 |
| `max_file_mb` | 10 | 普通用户单文件大小上限 |
| `admin_unlimited` | true | 管理员 / 单用户机主是否不受限制 |
| `draft_ttl_days` | 7 | 未发送草稿附件保留天数 |

不设用户总存储配额。已发送附件不会被 TTL 清理。

## 9. 多用户与登录

```json
"multi_user": true,
"remember_login": true
```

- `multi_user=true` 时必须有管理员；首次启动在本机 TUI 创建
- `remember_login=false` 时 TUI 每次启动回到匿名，登录 token 有 12 小时有效期
- Web 和 TUI 共用账号与会话

## 10. 界面语言与主题

TUI 语言：

```bash
NOVABOT_LANG=en novabot
```

优先级：显式设置（`/lang`） > `NOVABOT_LANG` > `ui.json` > 中文。

Web 语言在顶栏切换；主题存在浏览器 `localStorage`，不影响 TUI。

## 11. 生效方式

- 静态配置修改后需要重启 NovaBot
- 动态模型列表缓存 5 分钟，失败 60 秒后重试
- 技能、MCP、搜索 provider 等页面操作即时生效
