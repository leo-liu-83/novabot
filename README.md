# NovaBot

版本：`0.2.0` ｜ [变更日志](CHANGELOG.md)

## 1. 简介

NovaBot 是一个运行在本机的 AI agent 控制台。它把模型对话、文件与附件、技能、网络搜索、MCP 工具、长期记忆和权限审批放在同一个本地应用里，同时提供 TUI 和 Web 控制台。

- 所有执行都发生在本机，不上传用户数据到 NovaBot 自己的服务
- 支持单用户和多用户；管理员可以分配执行档位、搜索配额和 MCP 授权
- 模型接入走 OpenAI 兼容接口，可接厂商官方接口、聚合平台或内网模型
- 技能和工具默认受权限控制；高风险操作需要确认
- 项目：把一个已有目录登记为项目，会话围绕该目录工作，项目内的说明与笔记由该项目的会话共享
- 支持中文 / English 界面

详细说明见下方的手册索引。

## 2. 快速开始

要求：Python **3.12+**。

### Linux

```bash
cd /path/to/novabot
chmod +x install.sh
./install.sh
novabot
```

`install.sh` 会交互询问虚拟环境、依赖、启动命令名和安装目录，以及**是否启用多用户模式**和**模型接口**（OpenAI 兼容的接口地址、API Key、模型 code，可跳过）。回答会写入 `~/.novabot/novabot.json` 的最小配置，并在 `~/.local/bin/novabot` 生成启动器。

- 单用户：本机一个身份，TUI 直接使用（默认）。
- 多用户：账号注册/登录、数据按账号隔离；**Web 控制台必须多用户**，首次启动要先用本机 TUI 创建管理员。
- 跳过模型接口仍能装完，但无法开始对话；之后编辑 `~/.novabot/novabot.json` 的 `models` 与 `agent.model`，说明见 [模型配置](docs/model-config.md)。

不使用安装脚本时：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

手动安装时需要在 `~/.novabot/novabot.json` 配置一个 provider 的 `base_url` / `key`、至少一个模型，并让 `agent.model` 指向 `provider/model`。不要直接复制 `novabot.template.json`：模板里的 provider 带着空 key，容易被误认为已配置好。

### Windows

建议使用 **Windows Terminal** 和 Python 3.12+。在资源管理器双击 `install.bat`，或在终端执行：

```bat
install.bat
novabot
```

`install.bat` 会调用 PowerShell 版本，询问同样的环境与配置问题，生成 `%USERPROFILE%\.local\bin\novabot.cmd`，并尝试加入当前用户 PATH；新开终端后生效。

不使用安装脚本时：

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

配置文件位于 `%USERPROFILE%\.novabot\novabot.json`。非交互场景可以执行：

```bat
install.bat -InitConfig
```

它只在文件不存在时生成单用户最小配置，不会覆盖已有配置。

多用户模式首次启动时，必须先在 TUI 内创建管理员，再对外开放端口。

卸载默认只删除启动器：`./uninstall.sh`（Windows `uninstall.bat`）。需要清理数据或虚拟环境时用 `--purge` / `--remove-venv`（Windows `-Purge` / `-RemoveVenv`）。

## 3. 项目功能简表

| 功能 | 简要说明 |
|---|---|
| 对话与会话 | TUI / Web 共用会话、消息和 checkpoint；支持流式输出、停止、重问、上下文压缩 |
| 模型接入 | 多 provider、OpenAI 兼容接口、动态模型发现、思考开关、按会话切换模型 |
| 多用户与权限 | 匿名 / 普通用户 / 管理员；登录、注册、改密、注销、会话过户 |
| 项目（Project） | 登记已有目录作为工作根；项目会话共享 `AGENTS.md` 与项目笔记，文件读写限定在项目内 |
| 技能系统 | 系统 / 共享 / 私有三层；脚本技能先扫描再放行；未放行技能不加载 |
| 附件与多模态 | 文件粘贴、路径识别、图片视觉输入、PDF/Office 转文本、上传配额和草稿清理 |
| 网络搜索与抓取 | 多档搜索降级、每用户配额、网页抓取确认、域名 allowlist、SSRF 防护 |
| MCP | 系统层 / 用户层 server；逐工具 `allow/ask/deny`；延迟发现工具 |
| 人格与长期记忆 | `prompt.md`、`SOUL.md`、`USER.md` 和分类记忆文件 |
| 执行档位 | T0–T4；技能脚本、只读命令、逐条确认、免确认四类边界 |
| 界面 | TUI + Web 控制台；中英双语；亮色 / 暗色 / 跟随系统 |
| 数据与部署 | 本地 SQLite + 文件目录；安装脚本、配置模板、升级与备份说明 |

### 项目（Project）

项目是“登记一个已有目录”，不是复制或搬迁——目录里已有的代码原样使用。

- 会话可以归属某个项目；归属后工作根就是项目目录，文件读写限定在项目内，项目外路径与敏感文件需要确认
- 同一项目的多个会话共享项目知识：项目根 `AGENTS.md` 与 `.novabot/notes/`；全局记忆仍按用户跨项目共享，只放概括信息
- 项目可绑定推荐技能；项目会话的 shell 默认 T3，可按需提到 T4（T4 在项目内表示免确认）
- Web 端有项目菜单、项目内会话列表和会话转移；TUI 可以在当前目录创建项目、重命名项目
- 项目功能是独立授权：单用户默认可用，多用户由管理员为账号开启
- 明确的非目标：不自动创建/搬迁项目目录，不做项目间共享，不做项目级长期记忆，也不提供进程级沙箱

## 4. 详细手册索引

### 中文手册

| 文档 | 内容 |
|---|---|
| [快速开始与部署](docs/quickstart.md) | 安装脚本、虚拟环境、首次启动、升级与备份 |
| [架构与运行模型](docs/architecture.md) | 组件、数据目录、会话、项目与安全边界 |
| [功能说明](docs/features.md) | 对话、项目、模型、技能、附件、搜索、MCP、记忆、多用户 |
| [命令参考](docs/commands.md) | CLI、TUI 命令、Web 页面入口 |
| [配置参考](docs/configuration.md) | 配置文件、附件、执行档位、网络与 MCP 配置 |
| [模型配置](docs/model-config.md) | provider、模型、请求头、动态发现、聚合平台接入 |
| [HTTP API](docs/api.md) | 鉴权、会话、聊天 SSE、管理接口 |
| [安全与权限](docs/security.md) | 身份、执行档位、技能审查、SSRF、已知风险 |
| [排错与限制](docs/troubleshooting.md) | 常见错误、模型/网络问题、已知限制 |
| [版本与发布](docs/versioning.md) | 版本号规则、发布产物、变更日志口径 |

### English manual

| Document | Content |
|---|---|
| [README (English)](README.en.md) | Overview, quick start, features and index |
| [Quick start](docs/en/quickstart.md) | Install scripts, virtual environments, first run |
| [Architecture](docs/en/architecture.md) | Components, data layout, sessions, boundaries |
| [Features](docs/en/features.md) | Chat, models, skills, attachments, search, MCP, memory |
| [Commands](docs/en/commands.md) | CLI, TUI commands, Web pages |
| [Configuration](docs/en/configuration.md) | Config files, attachments, exec, web and MCP |
| [Model configuration](docs/en/model-config.md) | Providers, models, headers, discovery |
| [HTTP API](docs/en/api.md) | Auth, sessions, chat SSE, admin endpoints |
| [Security](docs/en/security.md) | Identity, exec levels, skill review, SSRF and risks |
| [Troubleshooting](docs/en/troubleshooting.md) | Common errors, model/network issues, limits |

## 5. 免责声明

本项目所有代码均通过 **vibe coding** 由模型编写。项目按“现状”提供，不保证正确性、稳定性、安全性或适用性。

使用本项目造成的任何直接或间接损失，包括但不限于数据丢失、账号或密钥泄露、服务中断、费用损失和法律责任，作者均不承担责任。请在使用前自行审计代码、备份重要数据，并在生产或敏感环境中谨慎评估。

## 6. 鸣谢

项目基于 [OpenCode](https://opencode.ai) 进行 vibe coding 开发。

使用的模型包括：

- Qwen3.8 Flash
- Space Bunny（MiniMax M3.1 Flash）
- DeepSeek V4.1 Flash
