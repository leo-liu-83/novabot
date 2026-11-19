# 变更日志

版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)，规则见 [版本与发布](docs/versioning.md)。
本文件只记录对外可见的变化；内部开发过程与提交历史不在其中。

## 0.3.1 — 2026-11-19

### 新增

- 配置文件里**任何**以 `enc:v1:sm4:` 开头的值都会在读取时解密，不再限定字段名：
  内网网关可以只用 `headers.Authorization` 的密文鉴权，不必再配一个用不上的 `api_key`
- 只有 `headers.Authorization`、没有 key 的 provider 视为已配置，Web 模型选择器可以选、
  子代理也可以指定

### 变更

- 加密范围不变：`init` / `encrypt-config` 仍只加密 `key` / `api_key`；header 密文需要手贴，
  文档补了获取方法
- `init --rotate` 会重加密配置文件与 `.bak` 里任意位置的密文；新增 `--skip-broken`，
  跳过恰好以 `enc:v1:sm4:` 开头的普通文案
- `status` 的密文条数包含 header 里的密文

### 修复

- 解不开的模型请求头直接报错，不再把密文当 token 发给服务端
- 解不开的 MCP 请求头会让该 server 被跳过，不再把密文发出去
- `init` 与 `--rotate` 改为先写文件、最后落库：写盘失败时旧主密钥仍在，已有密文不会变成解不开
- `status` 的明文计数不再把 `base_url`、模型名等非密钥字段算进去

## 0.3.0 — 2026-11-18

### 新增

- 内置子代理：`explore`（只读探索）与 `general`（执行），主 agent 通过 `task` 分派；
  子代理在独立上下文运行，只回传结论
- 子代理可单独配置模型（跟随会话或指定），Web 系统信息页提供「子代理模型」面板
- 子代理用量按角色记入 `usage_events`，报表可区分主 agent 与子代理
- 并发闸门：全局 3 / `general` 每会话 1 / 单轮 6 次 / 活跃段 5 分钟 / 排队 60 秒
- 分派原则用可数阈值（预计读 3 个以上文件、跨 2 个以上目录就先派 `explore`）：实测
  「需要读很多文件」这类模糊表述模型不会派，改成可数口径后同一句提问就会派
- 思考从布尔开关升级为档位：`off` / `low` / `medium` / `high`，会话里 `/think`（TUI）
  或头部 `think` 芯片（Web）切换，`auto` 表示跟随模型默认
- 新增请求写法 `reasoning_effort` 与 `thinking_level`（含 `none` = 不发任何思考字段），
  覆盖 OpenAI GPT-5.x、DeepSeek、Gemini 3 等只有档位的模型；DeepSeek 可组合
  `thinking_type+reasoning_effort`
- 模型配置新增 `thinking_default` / `thinking_levels` / `thinking_off_value`；
  `/think` 与 Web 只列出当前模型支持的档位
- 会话新增 `thinking_level` 列（老 `thinking` 布尔列继续双写，老客户端仍可用）
- 模型目录接口返回 `thinking_default` 与 `thinking_levels`

### 变更

- OpenAI 官方端点默认改发 `reasoning_effort`：再发 `enable_thinking` 会被 400 拒绝
- 老配置与老接口继续可用：`enable_thinking: true/false` 等价于 `medium` / `off`

### 修复

- 非流式响应回填 `usage`，非流式调用的用量不再恒为 0
- 思考写法拼错在装配期就报错，不再让端点静默忽略（"以为关了其实还在烧 token"）
- opencode.ai 网关按 `reasoning_effort` 识别（实测它忽略 `enable_thinking`，`none` 才真关掉推理）；
  动态发现出来的模型默认 `auto`（不发思考字段），不会被悄悄改成"关思考"
- 动态发现型 provider 支持在 provider 级配置 `thinking_style` / `thinking_default` /
  `thinking_levels` / `thinking_off_value`，网关下的每个模型都能选档位

## 0.2.2 — 2026-11-15

### 新增

- 会话模型选择器支持搜索（TUI 与 Web）：模型超过 8 个时显示搜索框，可按模型名、模型 id
  与 provider 过滤，忽略大小写及空格、连字符、点、斜杠等分隔符
- 无匹配结果时给出明确提示；没有命中的 provider 分组标题与空行自动隐藏

## 0.2.1 — 2026-11-15

### 变更

- `project-notes` 增加按项目类型的参考文档：Python 项目按既有工具（uv / poetry / pdm / conda / venv）
  决定环境与命令，并隔离 NovaBot 自身环境与目标项目依赖
- 项目会话里的 Python 环境、依赖安装、测试与交付检查有了统一口径；新增项目类型时按
  `references/<type>.md` 登记即可

## 0.2.0 — 2026-11-15

### 新增

- 内置技能 `project-notes`：把项目知识沉淀到项目根 `AGENTS.md` 与 `.novabot/notes/`，区分"规则"与"细节"，
  并阻止项目资料写进全局记忆

### 变更

- 长期记忆规则：属于某个目录或项目的术语、资料与约定改为沉淀到项目笔记，不再写入全局记忆

### 修复

- 只说"记到笔记"这类含糊请求时不再默认写进全局记忆，先确认写在哪里
- 账号未开通项目功能时，说明如何开通，而不是笼统提示"会话未归属项目"

## 0.1.1 — 2026-11-15

### 新增

- 版本与发布章程：单一版本真源、发布打 tag、CHANGELOG 校验

### 变更

- README 与手册补齐项目（Project）功能说明：简介、功能表、架构、命令、HTTP API、安全边界
- 安装示例目录名不再带版本号（`novabot`），减少每版必改的位置

### 修复

- Windows PowerShell 5.1 下安装脚本因 UTF-8 无 BOM 导致的大括号错配
- 安装脚本在 PowerShell 5.1 里截断 `-c` 参数，导致正常的 Python 被判成不可用
- 发布脚本读取陈旧的本地 `main`，可能发布旧内容

## 0.1.0 — 2026-11-15

### 新增

- 首个发布版：对话与会话、模型接入、多用户与权限、项目（Project）、技能、附件与多模态、
  网络搜索与抓取、MCP、人格与长期记忆、执行档位 T0–T4、TUI 与 Web 控制台、中英双语
