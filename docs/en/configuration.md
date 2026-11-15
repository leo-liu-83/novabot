# Configuration

## 1. Files

| File | Purpose |
|---|---|
| `config/config.json` | Built-in defaults shipped with the release |
| `~/.novabot/novabot.json` | User configuration: models, web, MCP, attachments |
| `~/.novabot/ui.json` | TUI theme and language |
| `~/.novabot/data/novabot.db` | Sessions, messages, accounts, reviews, quotas |
| `~/.novabot/data/login.json` | TUI login token when `remember_login=true` |

On Windows the root is `%USERPROFILE%\.novabot\`.

User providers with the same `code` override built-in providers.

Template: `novabot.template.json`.

## 2. Minimal config

```json
{
    "models": {
        "deepseek": {
            "name": "DeepSeek",
            "base_url": "https://api.deepseek.com",
            "api": "openai-completions",
            "key": "sk-your-key",
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

See [Model configuration](model-config.md) for provider/model fields, aggregators, dynamic discovery and headers.

## 3. Top-level sections

| Field | Purpose |
|---|---|
| `models` | Providers and models |
| `llm` | LLM defaults |
| `agent` | Default model and tool-step limit |
| `exec` | Service-level execution ceiling |
| `web` | Search and fetch |
| `mcp` | System-layer MCP servers |
| `attachments` | Attachment count, size and draft retention |
| `multi_user` | Enable multi-user mode |
| `remember_login` | Persist TUI login across restarts |

## 4. Models

### `llm`

| Field | Default | Purpose |
|---|---:|---|
| `max_input_tokens` | 131072 | Fallback context window when a model has no `num_ctx` |

### `agent`

| Field | Default | Purpose |
|---|---:|---|
| `model` | built-in | Default model for new sessions, `provider/model` |
| `max_steps` | 50 | LLM→tool→LLM round-trip limit |

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

Omit `models` with `dynamic_models` to discover; a non-empty `models` is a static allowlist.

See [Model configuration](model-config.md).

## 5. Execution

```json
"exec": {
    "level": "T2"
}
```

`exec.level` is a service-level ceiling, not every account's default.

| Level | Meaning |
|---|---|
| T0 | No commands |
| T1 | Released skill `.py` scripts only |
| T2 | Read-only commands inside the workspace |
| T3 | Any shell command, each confirmed |
| T4 | Any shell command, no confirmation |

See [Security](security.md) for identity defaults and ceilings.

## 6. Web search and fetch

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

| Field | Purpose |
|---|---|
| `web.search.enabled` | Enable built-in search, default true |
| `web.search.providers` | Global providers; order is priority |
| `web.fetch.permission` | `ask` / `allow` / `deny` |
| `web.fetch.url_allowlist` | Domains that skip confirmation in `ask` mode |

The allowlist is a confirmation shortcut, not a hard allowlist.

Optional environment variables for free-tier quota:

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

This section only controls system-layer servers. User-layer servers are added through `/mcp` and stored in the database; the `mcp` section in user config does not apply to them.

## 8. Attachments

```json
"attachments": {
    "max_per_message": 5,
    "max_file_mb": 10,
    "admin_unlimited": true,
    "draft_ttl_days": 7
}
```

| Field | Default | Purpose |
|---|---:|---|
| `max_per_message` | 5 | Attachments per message for regular users |
| `max_file_mb` | 10 | Per-file size limit for regular users |
| `admin_unlimited` | true | Administrators and single-user owners ignore both limits |
| `draft_ttl_days` | 7 | Retention days for unsent draft attachments |

No total user storage quota. Sent attachments are not removed by the TTL cleanup.

## 9. Multi-user and login

```json
"multi_user": true,
"remember_login": true
```

- `multi_user=true` requires an administrator created in the local TUI
- `remember_login=false` returns the TUI to anonymous on restart; issued tokens have a 12-hour lifetime
- Web and TUI share accounts and sessions

## 10. Language and theme

TUI language:

```bash
NOVABOT_LANG=en novabot
```

Priority: explicit `/lang` > `NOVABOT_LANG` > `ui.json` > Chinese.

Web language is switched in the top bar; theme is stored in browser `localStorage`.

## 11. Applying changes

- Static configuration changes require a restart
- Dynamic model lists cache for 5 minutes and retry after 60 seconds on failure
- Skill, MCP and search-provider actions in the UI take effect immediately
