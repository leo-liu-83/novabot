# Model Configuration

This document covers model providers: config locations, fields, official APIs, aggregators, intranet gateways, dynamic discovery, headers and troubleshooting.

## 1. Config files

| File | Purpose |
|---|---|
| `config/config.json` | Built-in defaults shipped with the release |
| `~/.novabot/novabot.json` | User configuration |

Load order:

```text
built-in config/config.json
  → user ~/.novabot/novabot.json
  → later provider with the same code wins
```

A user provider with the same code replaces the entire built-in provider, including its `models` list.

Restart NovaBot after changing static configuration.

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

Request URL:

```text
base_url + /chat/completions
```

Do not include `/chat/completions` in `base_url`.

## 3. Provider fields

| Field | Required | Purpose |
|---|---:|---|
| `name` | No | Display name; provider group in the model picker |
| `base_url` | Yes | OpenAI-compatible API prefix |
| `api` | Yes | Only `openai-completions` |
| `key` | No | Sends `Authorization: Bearer <key>`; empty means no auth header |
| `api_key` | No | Alias for `key` |
| `host` | No | Legacy `Host` header for gateways |
| `headers` | No | Provider-level request headers |
| `models` | No | Static model list / allowlist |
| `dynamic_models` | No | Discover models from `/models` |
| `models_url` | No | Discovery URL; defaults to `base_url + /models` |

> A provider's `key` / `api_key` may be ciphertext (`enc:v1:sm4:...`), and so may any value
> under `headers`; the whole config is decrypted on load, and plaintext keeps working.
> Install scripts only encrypt `key` fields, so paste header ciphertext yourself (put the
> value in a `key` field temporarily, run `python -m util.secret_cli encrypt-config`, then copy
> the result); an undecryptable header makes that request fail, never sending the ciphertext. See
> [configuration §10.5](configuration.md#105-optional-key-encryption) for the master key.

## 4. Model fields

| Field | Required | Purpose |
|---|---:|---|
| `code` | Yes | Model ID sent to the endpoint and used in `provider/code` |
| `name` | No | Display name; defaults to `code` |
| `stream` | No | Streaming, default true |
| `enable_thinking` | No | Default thinking switch, default false |
| `thinking_style` | No | Endpoint-specific thinking parameters |
| `num_ctx` | No | Context window; falls back to `llm.max_input_tokens` |
| `max_output_tokens` | No | Real output limit; budget reserves 4096 when omitted |
| `cost` | No | `input` / `output` / `cache_read` / `cache_write` |
| `headers` | No | Model-level headers; override provider headers |

Reference format:

```text
provider/code
```

Model codes may contain `/`, such as `anthropic/claude-sonnet-4.6`:

```text
openrouter/anthropic/claude-sonnet-4.6
```

Only the first `/` separates provider from model.

## 5. Default and session models

```json
"agent": {
    "model": "deepseek/deepseek-flash",
    "max_steps": 50
}
```

- TUI: `/model` opens the picker; `/model provider/code` selects directly
- Web: model picker in the chat header
- New-session page: the selected model is used for session creation and the first request
- Existing sessions remember their model
- Without a session-level choice, `agent.model` is used

The default can also be changed without editing the file: in the Web console under
Admin → System → Model catalog, hovering a row reveals "Set as default →". Clicking it writes
that model to `agent.model` in `novabot.json` (exactly one default at a time) and takes effect
immediately, with every other key in the file left untouched.

## 6. Provider examples

### Official or pay-as-you-go

```json
"deepseek": {
    "name": "DeepSeek",
    "base_url": "https://api.deepseek.com",
    "api": "openai-completions",
    "key": "sk-...",
    "models": [
        { "code": "deepseek-flash", "name": "DeepSeek Flash", "num_ctx": 131072 }
    ]
}
```

### OpenRouter

```json
"openrouter": {
    "name": "OpenRouter",
    "base_url": "https://openrouter.ai/api/v1",
    "api": "openai-completions",
    "key": "sk-or-...",
    "headers": {
        "HTTP-Referer": "https://your.site",
        "X-OpenRouter-Title": "NovaBot"
    },
    "dynamic_models": true
}
```

### OpenCode Go

```json
"opencode-go": {
    "name": "OpenCode Go",
    "base_url": "https://opencode.ai/inference/go/openai/v1",
    "models_url": "https://opencode.ai/inference/go/v1/models",
    "dynamic_models": true,
    "api": "openai-completions",
    "key": "oc_sk_...",
    "headers": {
        "User-Agent": "NovaBot/1.0",
        "x-opencode-session": "${session_id}"
    }
}
```

OpenCode chat and models paths differ; set `models_url` explicitly. Console pay-as-you-go uses:

```text
base_url:   https://opencode.ai/inference/openai/v1
models_url: https://opencode.ai/inference/v1/models
```

### Intranet model with a custom auth header

```json
"intranet": {
    "name": "Intranet",
    "base_url": "http://10.0.0.8:8000/v1",
    "api": "openai-completions",
    "key": "",
    "headers": {
        "X-Api-Token": "intranet-token"
    },
    "models": [
        { "code": "qwen3-32b", "name": "Qwen3-32B", "num_ctx": 131072 }
    ]
}
```

With an empty `key`, NovaBot does not send the automatic Authorization header.

### Local llama.cpp / vLLM

```json
"local": {
    "name": "Local",
    "base_url": "http://127.0.0.1:8080/v1",
    "api": "openai-completions",
    "key": "",
    "models": [
        { "code": "qwen3-32b", "name": "Qwen3-32B", "num_ctx": 65536 }
    ]
}
```

## 7. Request headers

Priority, low to high:

1. Automatically generated `Content-Type` / `Accept` / `Authorization`
2. `Host` from the legacy `host` field
3. Provider `headers`
4. Model `headers`

### Session placeholders

```json
"headers": {
    "User-Agent": "NovaBot/1.0",
    "x-opencode-session": "${session_id}",
    "X-Conversation": "novabot-${session_id}"
}
```

- `${session_id}` is replaced with the current session ID
- Calls without a session ID skip headers containing the placeholder
- `x-opencode-session` is automatically filled with the session ID when configured
- Works at both provider and model level

## 8. Thinking levels and request styles

Most 2026 models no longer have a hard "thinking off" switch; they expose levels
(OpenAI `reasoning_effort`, Gemini 3 `thinking_level`, DeepSeek effort plus `thinking.type`).
NovaBot normalises them to **off / low / medium / high**, plus a session-level `auto`
(no override, follow the model default).

### 8.1 Per-model config

```json
{
  "code": "gpt-5.1",
  "name": "GPT-5.1",
  "thinking_style": "reasoning_effort",
  "thinking_default": "medium",
  "thinking_levels": ["off", "low", "medium", "high"],
  "thinking_off_value": "none"
}
```

| Field | Meaning |
|---|---|
| `thinking_style` | Request fields to send; `+` composes parts such as `thinking_type+reasoning_effort` |
| `thinking_default` | Default level; when omitted it is derived from the legacy `enable_thinking` (true=medium, false=off) |
| `thinking_levels` | Levels this model accepts; `/think` and the web picker only offer these |
| `thinking_off_value` | What `off` sends to endpoints without an off switch (effort defaults to `none`, `thinking_level` to `minimal`) |

The legacy `enable_thinking` still works and is equivalent to `thinking_default`
(true=medium, false=off).

### 8.2 Styles per endpoint

| Endpoint | `thinking_style` | What `off` sends |
|---|---|---|
| OpenAI GPT-5.x / o-series | `reasoning_effort` | `reasoning_effort: "none"` (older models: set `thinking_off_value: "minimal"`) |
| OpenCode gateway (opencode.ai) | `reasoning_effort` (auto-detected) | `reasoning_effort: "none"` |
| DeepSeek | `thinking_type` (add `+reasoning_effort` for levels) | `thinking: {"type": "disabled"}` |
| Gemini 3 | `thinking_level` | `thinking_level: "minimal"` |
| Qwen / DashScope | `enable_thinking` | `enable_thinking: false` |
| llama-server | `template_kwargs` (or `enable_thinking+template_kwargs`) | `chat_template_kwargs.enable_thinking: false` |
| Endpoints that reject unknown fields | `none` | nothing is sent |

Without `thinking_style`, the style is guessed from `base_url`: `api.openai.com` / `opencode.ai` →
`reasoning_effort`, `deepseek.com` → `thinking_type`, **loopback addresses** (`127.0.0.1` /
`localhost` / `[::1]` / `unix://`) → `enable_thinking+template_kwargs`, otherwise (including LAN
IPs and remote gateways) top-level `enable_thinking`. A wrong guess is silently ignored by the
server, so set `thinking_style` explicitly for gateways and LAN inference servers:

```json
"myvllm": {
  "base_url": "http://192.168.10.5:8000/v1",
  "dynamic_models": true,
  "thinking_style": "enable_thinking+template_kwargs"
}
```

This is a common trap: Qwen served by vLLM / SGLang / llama.cpp only understands
`chat_template_kwargs.enable_thinking`, but on `192.168.x.x` it is not detected as "local", so the
top-level parameter is silently ignored and the thinking toggle appears to do nothing.

### 8.3 Models that cannot turn thinking off

Gemini 3 Pro and Claude 5.x only have levels. Either drop `off` from `thinking_levels`, or
keep it with `thinking_off_value: "minimal"` meaning "lowest tier" rather than truly off.

Switch levels with `/think` in the TUI or the `think` chip in the web header. It only affects
later requests; `show_thinking=false` merely hides reasoning text.

## 9. Dynamic model discovery

| Config | Behavior |
|---|---|
| `dynamic_models: true`, no `models` | Fetch `/models` and expose all text models |
| `dynamic_models: true`, with `models` | Use only the listed models; do not request `/models` |

With a static list, write `name`, `num_ctx` and `max_output_tokens` yourself.

- Discovery cache: 5 minutes
- Failure retry: 60 seconds
- Failure does not block startup
- Discovery requests use provider `key` / `headers`
- `${session_id}` headers are skipped during discovery

Supported model-list shapes include OpenCode (`limit.context`, `limit.output`, `type=chat`) and OpenRouter (`context_length`, `top_provider.max_completion_tokens`). Non-text-output models are filtered.

## 10. Multiple providers

```json
"models": {
    "deepseek": { "...": "..." },
    "openrouter": { "...": "..." },
    "opencode-go": { "...": "..." },
    "intranet": { "...": "..." }
}
```

Reference models as:

```text
deepseek/deepseek-flash
openrouter/anthropic/claude-sonnet-4.6
opencode-go/glm-5.3
intranet/qwen3-32b
```

## 11. Troubleshooting

### 401 / 403

- Missing or wrong key
- Custom auth header required
- Custom `Authorization` overridden

### 404 model not found

- Wrong model code
- Discovery did not return it
- Static allowlist omits it

### 404 endpoint not found

- `base_url` includes `/chat/completions`
- Separate `models_url` required
- Endpoint is not OpenAI-compatible

### 400 request error

- Unsupported thinking parameter
- Aggregator forwards an unsupported parameter

### Model picker does not show a new model

- Static config changed without restart
- Dynamic cache not expired
- Static `models` allowlist does not include it

### Context compacts too early

- `num_ctx` too small
- Missing `num_ctx` and `llm.max_input_tokens` too small

## 12. Security

- Keys and headers are plaintext in `~/.novabot/novabot.json` by default; `enc:v1:sm4:...` ciphertext is also accepted
- Do not commit or share the file
- Treat intranet tokens like model keys

---

## 13. Subagent models

Subagents have two built-in roles, each of which can pin a model:

```json
"agent": {
    "subagents": {
        "explore": {"model": "dashscope/qwen-flash"},
        "general": {"model": ""}
    }
}
```

- `""` or a missing role follows the current session model (whatever `/model` selected)
- Role models do not inherit the session `/think` switch; they use the model's own `enable_thinking`
- Suggestion: give `explore` a cheap, fast, large-context model (it is read-only and called often);
  let `general` follow the session model or pin a comparable one
- A missing model or missing key falls back to the session model with a one-time notice; the chat
  itself never fails because of it
- The web console's System → Subagent models panel writes this to `novabot.json`
