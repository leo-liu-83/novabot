# 命令参考

## 1. 启动命令

安装脚本会在 PATH 中生成 `novabot`：

```bash
novabot                              # TUI
novabot repl                         # 终端 REPL
novabot web --host 127.0.0.1 --port 9000
```

也可以直接运行源码：

```bash
python main.py
python main.py repl
python main.py web --host 127.0.0.1 --port 9000
```

`main.py` 支持：

```text
positional: tui | repl | web
--host       Web 监听地址
--port       Web 监听端口
```

## 2. TUI 常用命令

在 TUI 输入框输入 `/` 会弹出命令选择。

### 会话与模型

| 命令 | 说明 |
|---|---|
| `/new` | 开启新会话 |
| `/session` | 切换会话 |
| `/model` | 切换模型 |
| `/think` | 思考档位：off / low / medium / high / auto（按当前模型支持的档位裁剪） |
| `/compact` | 手动压缩上下文 |
| `/lang` | 切换中文 / English |
| `/help` | 显示全部命令 |
| `/exit` | 退出 NovaBot |

### 记忆

| 命令 | 说明 |
|---|---|
| `/memory` | 查看记忆索引 |
| `/memory <文件名>` | 查看指定记忆文件 |
| `/remember <内容>` | 写入长期记忆 |

仅登录用户可见。

### 执行与安全

| 命令 | 说明 |
|---|---|
| `/exec` | 查看 / 调整本会话执行档位 |
| `/exec-limit` | 管理员为账号设置执行天花板 |
| `/skill-review` | 查看并审查技能 |
| `/web-limit` | 管理员设置搜索配额 |
| `/web-config` | 配置自己的搜索 provider |
| `/mcp` | 管理 MCP server 与工具权限 |

### 身份

| 命令 | 说明 |
|---|---|
| `/login` | 登录 |
| `/register` | 注册 |
| `/logout` | 退出登录 |
| `/passwd` | 修改密码 |
| `/delete-account` | 注销账号 |

### 项目

| 命令 | 说明 |
|---|---|
| `/project` | 在当前目录创建项目，或重命名已有项目（目录固定为当前目录） |
| `/project-limit` | 管理员为账号开通 / 收回项目功能 |

项目功能未开通时，这两条命令不会出现在命令列表里。

命令是否可见取决于身份、配置和权限；看不到通常表示当前身份没有该能力。

## 3. TUI 快捷键

| 快捷键 | 作用 |
|---|---|
| `Enter` | 发送消息 |
| `Ctrl+N` | 换行 |
| `Esc` | 停止生成 / 关闭弹窗 |
| `Ctrl+T` | 切换主题 |
| `Ctrl+Q` | 退出 |

## 4. Web 控制台

启动 Web 后打开 `http://127.0.0.1:9000/`。

主要页面：

| 页面 | 作用 |
|---|---|
| 会话 | 聊天、模型选择、附件、压缩、重问 |
| 概览 | 会话、用量、技能审查、MCP 摘要 |
| 人格 | SOUL.md、长期记忆、Agent 头像 |
| 网络搜索 | 用户 / 全局搜索 provider、配额 |
| MCP | server 管理、审批模式、逐工具权限 |
| 技能 | 技能列表、扫描、放行、拒绝 |
| 账号 | 密码、头像、数据统计 |
| 用户与授权 | 管理员管理账号、天花板、配额、MCP 授权 |
| 全局搜索 | 管理员维护全局 provider |
| 项目 | 项目列表与项目内会话；创建 / 改名 / 归档；编辑或生成项目 `AGENTS.md`；目录缺 `.novabot/notes` 或 `AGENTS.md` 时可一键补齐；TUI 的 `/project` 支持 `Ctrl+D` 删除（可选只删登记或连 `.novabot`、`AGENTS.md` 一起清） |
| 系统信息 | 诊断信息；模型目录里可切换 agent 默认模型 |

Web 与 TUI 共用账号、会话和消息。

## 5. HTTP API

鉴权、请求示例和接口列表见 [HTTP API](api.md)。

## 6. 配置文件

模型、附件、网络、MCP 等配置见 [配置参考](configuration.md)。
