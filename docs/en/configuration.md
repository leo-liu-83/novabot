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

### Subagent models

```json
"agent": {
    "model": "dashscope/qwen3.5-plus",
    "subagents": {
        "explore": {"model": ""},
        "general": {"model": "dashscope/qwen-flash"}
    }
}
```

- An empty string (or a missing role) follows the current session model; when only one role is
  given, the other keeps its built-in default
- Model ids use the same `provider/model` form as `agent.model`; the model code itself may contain `/`
- No need to edit the file by hand: the "Subagent models" panel in the web system page saves it to
  `novabot.json` immediately
- A broken subagent configuration never blocks the session: it falls back to the session model and
  shows a one-time notice

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

## 10.5 Optional key encryption

Provider `key` / `api_key` values and `web.search.providers[].key` may be stored encrypted:

```json
"models": {
  "deepseek": {
    "key": "enc:v1:sm4:3Jk... (written by the tool; do not edit by hand)"
  }
}
```

- The installer asks once for a master key (SM2 private key, 64 hex chars / SM4 key, 32 hex
  chars; empty generates one and prints it). **Keep that generated key**: losing it means
  re-entering every provider key
- To enable encryption on an existing install (idempotent; only plaintext is touched and an
  existing master key is never overwritten):

```bash
python -m util.secret_cli init --config ~/.novabot/novabot.json --data-dir ~/.novabot/data
python -m util.secret_cli status --config ~/.novabot/novabot.json --data-dir ~/.novabot/data
```

- Rotate with `init --rotate` (decrypts everything with the old key, then rewrites with the
  new one; aborts without writing if any value cannot be decrypted). If a plain note happens
  to start with `enc:v1:sm4:`, pass `--skip-broken` to leave it untouched
- Plaintext values keep working; the system page shows the encryption state, fingerprint and
  how many plaintext keys remain
- **Any** config value starting with `enc:v1:sm4:` is decrypted on load (including `headers`);
  an undecryptable request header makes that request fail instead of being sent
- The master key lives in the `app_secrets` table of `~/.novabot/data/novabot.db`; see
  [Security](security.md#71-optional-key-encryption) for the threat model

## 10.6 Web project directory allowlist

The web console may be reachable from the LAN, so registering a project there is limited to
directories under the user **workspace** by default (the TUI is a local tool and is not
restricted). To register existing repositories elsewhere, opt in explicitly:

```json
"projects": {
    "web_roots": ["~/work", "/srv/repos"]
}
```

- The web picker selects a **parent directory**: the project directory becomes
  `<parent>/<project name>` and is created when missing (so the web console can start an empty
  project directory; the TUI still registers the current directory)
- `~` is expanded; the workspace is always allowed and need not be repeated
- It only restricts browsing and creating projects from the web console; already-registered
  projects keep working
- The directory picker and the create endpoint share the same allowlist, so the boundary
  cannot be bypassed; escaping it returns `project.path_allowed_roots`
- Omitting the section is the same as `"web_roots": []` (workspace only)

## 11. Applying changes

- Static configuration changes require a restart
- Dynamic model lists cache for 5 minutes and retry after 60 seconds on failure
- Skill, MCP and search-provider actions in the UI take effect immediately
