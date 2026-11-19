# 排错与限制

## 1. 安装问题

### `novabot: command not found`

启动器目录不在 PATH：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Windows 检查 `%USERPROFILE%\.local\bin` 是否在用户 PATH，或重新打开终端。

### Python 版本不够

需要 3.12+：

```bash
python3 --version
```

安装脚本会在任何提问之前先扫描本机解释器，只接受 3.12+。找不到时以退出码 `2` 结束
（`1` 留给参数错误），并列出三样东西：

- 已检测到但版本不足的解释器及版本号；
- 本机可用的 3.12+ 解释器（如果有）；
- 对应平台的安装命令。

扫描范围包括 `PATH` 上的 `python3.13`/`python3.12`/`python3`、当前激活的 venv/Conda、pyenv
（`~/.pyenv/versions`）、conda（`~/miniconda3/envs` 等）与 macOS Homebrew 目录；Windows 额外
解析 `py -0p`。所以「装了 3.12、但 `python3` 指向系统的旧版本」这种情况也会被找出来。

脚本不会自动安装 Python（需要提权与联网）。装好后重跑脚本，或用 `--python` 指定：

```bash
./install.sh --python /usr/bin/python3.12
```

Debian/Ubuntu 还需要独立的 venv 包，否则创建虚拟环境会失败（脚本会提前提示，而不是让
ensurepip 的报错背锅）：

```bash
sudo apt install python3.12-venv
```

`--python` 指定的解释器不可用时脚本直接报错退出，不会悄悄换成别的解释器。

### Windows：cmd 里能运行 `python`，安装脚本却说找不到

先确认版本（NovaBot 需要 3.12+，3.11 及以下都不行）和路径：

```bat
python -V
where python
py -0p
```

`python -V` 低于 3.12 就装新版本，或用参数指定解释器：

```bat
install.bat -Python "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
```

版本够却仍报找不到，通常是这两件事之一：

**1. PATH 是进程启动时的快照。** 改完环境变量后新开的 cmd 能用，但双击 `install.bat` 时是由
资源管理器拉起的进程，而资源管理器可能还是改 PATH 之前的那个。关掉所有 cmd 窗口，必要时在
任务管理器里重启「Windows 资源管理器」，再运行一次。

**2. 应用执行别名占位程序。** `%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe` 这个文件存在，
但执行后什么都不输出——它只是通往应用商店的别名。到「设置 → 应用 → 高级应用设置 → 应用
执行别名」把 `python.exe`、`python3.exe` 关掉，再用 `winget install -e --id Python.Python.3.12`
或 python.org 安装包装一份。

脚本查三个来源：注册表 `HKLM/HKCU\SOFTWARE\Python\PythonCore\<版本>\InstallPath`、常见安装目录
（`%LOCALAPPDATA%\Programs\Python\Python*`、`C:\Program Files\Python*`、`C:\Python*`）、以及
`py -0p` 与 PATH。所以「装了但没进 PATH」的情形也能找到；报错时还会把「找到了但跑不起来」
的路径单独列出来，用来区分「没装」和「装了但用不了」。

### 依赖安装失败

- 检查网络和 pip 源
- 不要用系统 Python 直接装（PEP 668）；用虚拟环境
- Windows 某些包需要较新的 pip：

```bat
python -m pip install --upgrade pip
```

## 2. 模型问题

### 401 / 403

- key 未填或填错
- 网关要求自定义 header，但只配了 `key`
- 自定义 `Authorization` 被模型级 headers 覆盖

### 404 模型不存在

- `models[].code` 与平台模型 ID 不一致
- 动态发现没拿到该模型
- 写了 `models` 白名单但漏了它

### 404 接口不存在

- `base_url` 多写了 `/chat/completions`
- 平台需要单独的 `models_url`
- 平台不是 OpenAI 兼容接口

### 400 请求参数错误

- 模型不支持 `enable_thinking`，或需要不同的 `thinking_style`
- 聚合平台把未知参数转发给了上游

### 提示「密钥无法解密：主密钥缺失或与配置不匹配」

- 配置里是密文（`enc:v1:sm4:...`），但本机库 `~/.novabot/data/novabot.db` 里没有主密钥：
  重装/换机器时只拷贝了配置。补回原主密钥后重启即可
- 换过主密钥：用 `init --rotate` 重加密，不要直接改库
- 主密钥确认丢了：只能重新填写各 provider 的 key ——
  `python -m util.secret_cli init --force --config ~/.novabot/novabot.json --data-dir ~/.novabot/data`
  会列出哪些条目解不开
- 查看当前状态：`python -m util.secret_cli status --config ~/.novabot/novabot.json --data-dir ~/.novabot/data`

### 思考开关不生效（关不掉 / 打不开）

- 端点只认另一种写法：常见的是局域网/远程 vLLM 上的 Qwen，只认
  `chat_template_kwargs.enable_thinking`，而默认只发顶层 `enable_thinking`
  → 在模型或 provider 上显式配置 `"thinking_style": "enable_thinking+template_kwargs"`
- 显式配置后可用 `/think` 验证；这类布尔写法只有 `off` / `medium` 两档
- 想看实际发出的字段：把日志级别调到 DEBUG，日志里会打印「思考请求字段」（只有思考相关的键，不含正文与密钥）

### 上下文很快被压缩

- `num_ctx` 填小了
- 实际窗口更大，但没配 `num_ctx`，使用了 `llm.max_input_tokens`

详见 [模型配置](model-config.md)。

## 3. 网络搜索

聊天页出现黄色提示：

```text
⚠ 你的 tavily 连接失败（连接超时），已改用 NovaBot 内置 firecrawl
```

这是运行时降级提示，不是致命错误：

- 自备接口失败会明确提示
- 内置免费链内部降级不提示，整档不可用才提示
- 同一端点每会话只提示一次

如果所有搜索都不可用：

- 检查网络、代理和防火墙
- 检查 `/web-config` 中的 key / endpoint
- 免费档按出口 IP 限流，多用户共用一台机器时额度共享

## 4. 附件

### 文件过大

默认普通用户单文件 10MB；管理员/单用户机主默认不受限。配置：

```json
"attachments": {
    "max_per_message": 5,
    "max_file_mb": 10,
    "admin_unlimited": true,
    "draft_ttl_days": 7
}
```

### 一条消息附件太多

默认最多 5 个；管理员不受限。

### 未发送附件消失

未发送草稿超过 `draft_ttl_days` 会在启动时清理。已进入消息历史的附件不会删。

### 扫描件 PDF 读不了

文档转换是纯本地解析，不做 OCR，也不会把文档发给第三方。扫描件请转成文本型 PDF 或 Markdown。

### 图片发出去模型说看不到

模型需要支持视觉输入。纯文本模型会返回“不支持”的提示；音频/视频只提供路径。

## 5. 界面

### TUI 中文输入法异常

- 使用 Windows Terminal，不要用旧 conhost
- 设置 UTF-8：

```bat
chcp 65001
set PYTHONUTF8=1
```

- NovaBot 会尝试为 Textual 的 Windows IME 问题安装兼容补丁
- 仍异常时可使用 Web 控制台

### 窗口标题后面跟了一串路径和文件名

Ubuntu 默认终端 Ptyxis 的“动态标题”：前台进程不是 shell 时，它会在窗口标题后面追加
` — <该进程的完整命令行>`（见 Ptyxis 源码 `ptyxis-tab.c` 的 `dup_title`），所以会看到
`NovaBot · 你好 — /path/python3.12 /path/main.py`。

- 安装器生成的 `novabot` 启动器**故意不 exec**：前台留在 bash 上，Ptyxis 判定为 shell
  就不再追加，标题只剩 `NovaBot · 会话名`
- 直接 `python main.py tui` 仍会出现这一段；换成 `novabot` 或 `bash -c 'python main.py tui'` 启动即可
- Ptyxis 50.1 没有开关；更新版本在“首选项 → 关闭动态标题”（Disable Dynamic Title）里可以全局关掉

### 提示“不持久化”

数据库或 checkpoint 初始化失败，NovaBot 降级为内存模式：

- 当前仍可聊天
- 重启后历史不会保留
- 检查 `~/.novabot/data/` 权限和磁盘空间

### 多用户模式进不去

必须有管理员：

1. 在本机 TUI 启动
2. 完成管理员创建
3. 再使用 `/register` 或 Web 注册其他账号

在管理员创建前不要把端口暴露到公网。

## 6. 已知限制

- 同机所有账号在 OS 层是同一个用户，没有进程隔离
- 技能扫描是咨询性的，无法识别所有混淆载荷
- `grep -r` 可以遍历工作区，技能门不能阻止递归扫描
- SSRF 存在 DNS rebinding 时间窗
- 单用户模式 API 不鉴权；暴露公网前必须收紧监听地址
- 匿名工作区是公共的
- `remember_login=false` 时无法立刻吊销当次登录，token 有 12 小时上限
- Web 和 TUI 的语言/主题偏好分开存储

更多安全边界见 [安全与权限](security.md)。
