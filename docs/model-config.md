# NovaBot 模型配置说明

本文只讲模型接入：配置文件位置、字段含义、官方接口、聚合平台、内网模型、动态模型列表和常见排错。

---

## 1. 配置文件在哪

| 文件 | 作用 |
|---|---|
| `config/config.json` | 仓库内置默认配置，随版本走，一般不改 |
| `~/.novabot/novabot.json` | 用户配置，模型、密钥、默认模型都写这里 |

加载顺序：

```text
内置 config/config.json
  → 追加 ~/.novabot/novabot.json
  → 同 provider code 时，后加载的覆盖前面的
```

所以用户配置里写一个同 `code` 的 provider，会**整段替换**内置的同名 provider，包括它的 `models` 列表。

仓库根目录提供了模板 [`novabot.template.json`](../novabot.template.json)，复制到 `~/.novabot/novabot.json` 后填入 key 即可。模板同时展示了官方接口、OpenCode Go、OpenRouter 和内网自定义鉴权头，不需要的 provider 直接删掉。

改完静态配置需要重启 NovaBot 生效。

---

## 2. 最小可用配置

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

请求地址由代码自动拼接：

```text
base_url + /chat/completions
```

所以 `base_url` 不要带 `/chat/completions`，只写到版本前缀即可。

---

## 3. provider 字段

| 字段 | 必填 | 说明 |
|---|---:|---|
| `name` | 否 | 显示名，模型选择器里作为分组标题 |
| `base_url` | 是 | OpenAI 兼容接口前缀，自动拼 `/chat/completions` |
| `api` | 是 | 目前只支持 `openai-completions` |
| `key` | 否 | API 密钥；自动发 `Authorization: Bearer <key>`，留空则不发 |
| `api_key` | 否 | `key` 的别名 |
| `host` | 否 | 旧字段，生成 `Host` 请求头，内网代理场景可用 |
| `headers` | 否 | provider 级额外请求头，对该 provider 下所有模型生效 |
| `models` | 否 | 静态模型列表；写空或省略时可配合 `dynamic_models` 自动发现 |
| `dynamic_models` | 否 | `true` 时从 `/models` 动态发现模型 |
| `models_url` | 否 | 模型发现地址；留空按 `base_url + /models` |

---

## 4. 模型字段

`models` 是数组，每一项是一个模型：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `code` | 是 | 传给接口的模型名，也是 `provider/code` 的引用名 |
| `name` | 否 | 显示名，缺省用 `code` |
| `stream` | 否 | 是否流式，默认 `true` |
| `enable_thinking` | 否 | 是否默认深度思考，默认 `false` |
| `thinking_style` | 否 | 关闭思考的请求体写法，一般不用填，见 §8 |
| `num_ctx` | 否 | 上下文窗口；不填用 `llm.max_input_tokens` |
| `max_output_tokens` | 否 | 端点真实输出上限；不填按 4096 保守预留 |
| `cost` | 否 | 费用配置：`input` / `output` / `cache_read` / `cache_write` |
| `headers` | 否 | 模型级请求头，同名覆盖 provider 级 headers |

模型引用格式：

```text
provider/code
```

模型 `code` 本身可以带 `/`，例如 OpenRouter 的 `anthropic/claude-sonnet-4.6`，引用时写：

```text
openrouter/anthropic/claude-sonnet-4.6
```

代码只按第一个 `/` 切分 provider 和模型名，后面的 `/` 属于模型名。

---

## 5. 默认模型与会话模型

```json
"agent": {
    "model": "deepseek/deepseek-flash",
    "max_steps": 50
}
```

| 字段 | 说明 |
|---|---|
| `model` | 新会话的默认模型 |
| `max_steps` | 单次运行 LLM→工具→LLM 往返上限；到顶会摘工具并让模型收尾 |

实际行为：

- TUI：`/model` 选择模型，`/model provider/code` 直接指定
- Web：会话标题右侧的模型下拉框
- 新会话页先选模型再发送：首次创建会话和首个模型请求都会使用该模型
- 已有会话：每个会话记住自己的 `model`
- 没有会话级选择时，后端回落到 `agent.model`

改默认模型不用手改文件：Web 控制台的「管理 → 系统信息 → 模型目录」里，鼠标移到某一行会
出现「设为默认 →」，点击即把该模型写进 `novabot.json` 的 `agent.model`（同时只有一个默认），
新默认值当场生效、无需重启；配置里的其他键原样保留。

---

## 6. 常见接入方式

### 6.1 模型厂商官方接口 / plan

厂商提供 OpenAI 兼容接口时，直接配：

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

### 6.2 聚合平台：OpenRouter

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

不写 `models` 时会请求 `https://openrouter.ai/api/v1/models`，自动获取模型列表。

`HTTP-Referer` / `X-OpenRouter-Title` 是可选的流量归属头，不填也能用。

### 6.3 聚合平台：OpenCode Go

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

注意：

- OpenCode 的 chat 和 models 路径不同，必须显式写 `models_url`
- `x-opencode-session` 会自动替换成当前会话 ID
- 免费模型可能只对 OpenCode 客户端开放，第三方客户端建议用付费模型或 Go 套餐

OpenCode 按量付费 Console 的入口是：

```text
base_url:   https://opencode.ai/inference/openai/v1
models_url: https://opencode.ai/inference/v1/models
```

### 6.4 内网模型：token 放在自定义 header

有些内网网关不用 `Authorization: Bearer`，而是要求 `X-Api-Token` 之类的头：

```json
"intranet": {
    "name": "内网模型",
    "base_url": "http://10.0.0.8:8000/v1",
    "api": "openai-completions",
    "key": "",
    "headers": {
        "X-Api-Token": "内网 token",
        "X-Tenant": "novabot"
    },
    "models": [
        { "code": "qwen3-32b", "name": "Qwen3-32B", "num_ctx": 131072 }
    ]
}
```

`key` 留空时不会发送自动生成的 `Authorization`，自定义鉴权头会原样发送。

### 6.5 本机 llama.cpp / vLLM

```json
"local": {
    "name": "本地模型",
    "base_url": "http://127.0.0.1:8080/v1",
    "api": "openai-completions",
    "key": "",
    "models": [
        { "code": "qwen3-32b", "name": "Qwen3-32B", "num_ctx": 65536 }
    ]
}
```

本机地址会被识别为本地端点，未显式传 `max_tokens` 时会按 `local_max_tokens` 兜底。

---

## 7. 请求头

### 7.1 层级与优先级

从低到高：

1. 自动生成的 `Content-Type` / `Accept` / `Authorization`
2. `host` 字段生成的 `Host`
3. provider 级 `headers`
4. 模型级 `headers`

模型级同名头会覆盖 provider 级，也能覆盖自动生成的 `Authorization`。

### 7.2 会话级动态值

```json
"headers": {
    "User-Agent": "NovaBot/1.0",
    "x-opencode-session": "${session_id}",
    "X-Conversation": "novabot-${session_id}"
}
```

规则：

- `${session_id}` 在模型请求前替换成当前会话 ID
- 拿不到会话 ID 的调用会跳过该请求头，不会把占位符原样发出去
- `x-opencode-session` 特殊处理：只要配置了这一项，运行时有会话 ID 就自动替换；值写成固定字符串也可以
- provider 级和模型级都支持

---

## 8. 思考开关写法

不同端点关闭思考的参数名不同，发错通常不会报错，只会被静默忽略。

| 端点 | 实际生效的写法 |
|---|---|
| DashScope / Qwen 等 | 顶层 `enable_thinking` |
| DeepSeek | `thinking: {"type": "enabled"/"disabled"}` |
| llama-server | 顶层 `enable_thinking` + `chat_template_kwargs.enable_thinking` |

默认按 `base_url` 自动识别。网关把 DeepSeek 转发到别的域名时，可以显式指定：

```json
{ "code": "deepseek-flash", "thinking_style": "thinking_type" }
```

可用值：

```text
enable_thinking
thinking_type
template_kwargs
enable_thinking+template_kwargs
enable_thinking+thinking_type
```

拼错会在装配模型时直接报错。

---

## 9. 动态模型列表

### 9.1 两种模式

| 配置 | 行为 |
|---|---|
| `dynamic_models: true` 且不写 `models` | 请求 `/models`，暴露返回的全部文本模型 |
| `dynamic_models: true` 且写了 `models` | **只使用列出的模型，完全不请求 `/models`** |

写了 `models` 后，`name`、`num_ctx`、`max_output_tokens` 都需要自己写全，不会再从平台补齐。

### 9.2 models_url

默认：

```text
base_url + /models
```

OpenRouter 和大多数平台可以直接省略。OpenCode 这类 chat 与 models 路径不同的平台需要显式配置。

### 9.3 缓存与失败

- 动态列表缓存 5 分钟
- 请求失败 60 秒后重试
- 失败不影响启动；静态 `models` 始终按配置使用
- 发现请求会使用 provider 的 `key` / `headers`
- `${session_id}` 这类动态头在发现阶段会跳过

### 9.4 支持的返回结构

兼容常见的 OpenAI `/models`：

```json
{"data": [{"id": "model-name", "name": "显示名"}]}
```

以及：

- OpenCode：`limit.context` / `limit.output` / `type=chat`
- OpenRouter：`context_length` / `top_provider.max_completion_tokens`
- 通用：`max_context_length` / `context_window` / `max_output_tokens`

非文本输出模型会被过滤。

---

## 10. 多平台并存

`models` 下可以同时配置多个 provider，模型选择器会按 provider 分组：

```json
"models": {
    "deepseek": { "...": "..." },
    "openrouter": { "...": "..." },
    "opencode-go": { "...": "..." },
    "intranet": { "...": "..." }
}
```

引用时写全：

```text
deepseek/deepseek-flash
openrouter/anthropic/claude-sonnet-4.6
opencode-go/glm-5.3
intranet/qwen3-32b
```

---

## 11. 排错

### 401 / 403

- `key` 没填或填错
- 网关要求自定义鉴权头，但只配了 `key`
- 自定义 `Authorization` 被模型级 headers 覆盖

### 404 模型不存在

- `code` 与平台模型 ID 不一致
- 动态发现没拿到该模型
- 写了 `models` 白名单但漏了它

### 404 接口不存在

- `base_url` 多写了 `/chat/completions`
- 平台需要单独的 `models_url`
- 平台不是 OpenAI 兼容接口

### 400 请求参数错误

- 模型不支持 `enable_thinking`，或需要不同的 `thinking_style`
- 聚合平台把未知参数转发给了上游

### 模型选择器里没有新模型

- 静态配置：改完没重启
- 动态配置：缓存还没过期，或 `/models` 请求失败
- 写了 `models` 白名单：不会自动发现，必须手动加

### 上下文很快被压缩

- `num_ctx` 填小了
- 端点实际窗口更大，但没配 `num_ctx`，使用了 `llm.max_input_tokens` 的默认值

---

## 12. 安全提醒

- `key` 和 `headers` 都是明文存在 `~/.novabot/novabot.json`
- 不要把该文件提交进仓库
- 不要把带密钥的配置贴到 issue、聊天记录或截图里
- 内网 token 和模型 key 一样，按敏感信息处理
