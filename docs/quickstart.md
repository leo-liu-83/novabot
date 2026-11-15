# 快速开始与部署

本文覆盖从拿到源码到启动 NovaBot 的最短路径，以及首次配置、升级和备份。

## 1. 环境要求

- Python **3.12+**
- Linux：bash、网络访问依赖安装
- Windows：Windows 10/11、PowerShell、建议使用 Windows Terminal
- 至少一个 OpenAI 兼容模型接口（厂商官方、聚合平台或内网模型）

> 本发布包不包含 `tests/`；开发测试请使用源码仓库。

## 2. Linux

### 2.1 推荐：交互式安装脚本

```bash
cd /path/to/novabot
chmod +x install.sh
./install.sh
```

脚本会询问：

- Python 解释器
- 使用项目 `.venv`、当前已激活的 virtualenv / Conda 环境、已有路径，或不用虚拟环境
- 是否安装依赖
- 启动命令名（默认 `novabot`）
- 启动器安装目录（默认 `~/.local/bin`）
- 是否启用多用户模式（默认单用户；Web 控制台必须多用户）
- 模型接口：OpenAI 兼容的接口地址、API Key 和模型 code（都可跳过）

最后会确认一次。回答会生成一份**最小配置** `~/.novabot/novabot.json`：只写多用户开关、可选的 provider 和 `agent.model`，其余默认值来自内置 `config/config.json`。模型 code 留空时会打开 `/models` 自动发现，首次启动在 TUI 里用 `/model` 选择。

如果配置文件已存在，脚本会先问是否重新生成（默认否）；覆盖前把原文件备份为 `novabot.json.bak`。不重新生成就完全不动该文件。

安装完成后：

```bash
novabot
novabot web --host 127.0.0.1 --port 9000
novabot repl
```

如果 `~/.local/bin` 不在 `PATH`：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### 2.2 手动安装

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

### 2.3 非交互安装

自动化或 CI 可用：

```bash
./install.sh --non-interactive
```

也可显式指定：

```bash
./install.sh --venv ~/venvs/novabot
./install.sh --no-venv
./install.sh --python /usr/bin/python3.12
./install.sh --bin-dir /usr/local/bin
./install.sh --skip-deps
./install.sh --init-config
```

`--non-interactive` 不修改已有配置；加 `--init-config` 时，只在 `~/.novabot/novabot.json` 不存在的情况下生成单用户、未配置模型接口的最小配置（`{"multi_user": false}`）。因此自动化安装后仍需编辑配置或手动写入 provider，才能开始对话。

## 3. Windows

### 3.1 推荐：install.bat

双击 `install.bat`，或在终端执行：

```bat
install.bat
```

不要直接双击 `install.ps1`，除非已经调整过 PowerShell 执行策略；`install.bat` 会自动使用 `-ExecutionPolicy Bypass`。

问答内容与 Linux 相同：Python 环境、依赖、命令名、启动器目录、多用户模式、模型接口。配置写入 `%USERPROFILE%\.novabot\novabot.json`；若已存在，会先问是否重新生成并备份为 `novabot.json.bak`。

安装完成后：

```bat
novabot
novabot web --host 127.0.0.1 --port 9000
novabot repl
```

启动器位于：

```text
%USERPROFILE%\.local\bin\novabot.cmd
```

安装脚本会尝试把它加入当前用户 PATH；如果命令暂时找不到，新开一个终端。

### 3.2 手动安装

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

### 3.3 Windows 参数

```bat
install.bat -Venv C:\venvs\novabot
install.bat -NoVenv
install.bat -Python C:\Python312\python.exe
install.bat -BinDir C:\tools\novabot\bin
install.bat -InitConfig
install.bat -NonInteractive
```

`-InitConfig` 的含义同样是「配置不存在时生成单用户最小配置」，不会覆盖已有文件，也不再复制模板。

> TUI 建议在 Windows Terminal 中运行。NovaBot 会为已知的 Windows 中文输入法问题安装兼容补丁；如果仍有候选框异常，可优先尝试 Windows Terminal，或使用 Web 控制台。

## 4. 首次配置

### 4.1 配置文件

```text
Linux:   ~/.novabot/novabot.json
Windows: %USERPROFILE%\.novabot\novabot.json
```

安装脚本交互运行时会把答案直接写进这个文件。生成的是**最小配置**，只包含：

```json
{
  "multi_user": false,
  "models": {
    "deepseek": {
      "name": "deepseek",
      "base_url": "https://api.deepseek.com",
      "api": "openai-completions",
      "key": "sk-...",
      "models": [{"code": "deepseek-flash", "name": "deepseek-flash", "stream": true}]
    }
  },
  "agent": {"model": "deepseek/deepseek-flash"}
}
```

其余配置（上下文上限、附件、`agent.max_steps` 等）来自内置 `config/config.json`，不需要抄一份完整模板。内置配置不附带样例 provider，可用模型全部来自用户配置。

想手动配置时，至少需要：

- 一个可用 provider 的 `base_url`、`api`、`key`
- 至少一个模型
- `agent.model` 指向 `provider/model`

也可以只写 `"dynamic_models": true` 让 NovaBot 从端点的 `/models` 自动发现模型，再在首次启动时用 `/model` 选择。

不建议直接复制 `novabot.template.json`：里面的 provider 带着空 key，看起来像已经配置好，实际第一条消息就会失败。模板仅作手工参考。

模型配置细节见 [模型配置](model-config.md)。

### 4.2 首次启动

单用户模式：

```bash
novabot
```

进入 TUI 后：

- `/model` 选择模型
- `/help` 查看命令
- `/lang` 切换中文 / English

多用户模式：

1. 在 `novabot.json` 设置 `"multi_user": true`（安装时选择多用户会自动写入）
2. 重启 NovaBot
3. **先在本机 TUI 内创建管理员**（不可跳过）
4. 再通过 `/register` 或 Web 注册其他账号
5. 管理员用 `/exec-limit` 分配执行天花板，用 `/web-limit` 分配搜索配额

在创建管理员之前，不要把 Web 端口暴露到公网。

## 5. 升级

源码目录更新后，重新运行安装脚本即可：

```bash
./install.sh
```

脚本会复用已有虚拟环境并更新依赖，同时重写启动器。

升级前建议备份：

```bash
cp -a ~/.novabot ~/.novabot.backup-$(date +%Y%m%d)
```

Windows：

```bat
xcopy /E /I /H "%USERPROFILE%\.novabot" "%USERPROFILE%\.novabot.backup"
```

## 6. 卸载

默认只删除启动器，配置、数据和虚拟环境都会保留：

```bash
./uninstall.sh
```

```bat
uninstall.bat
```

需要一并清理时显式开启：

```bash
./uninstall.sh --purge              # 删除 ~/.novabot，先备份为 ~/.novabot.bak-<时间戳>
./uninstall.sh --remove-venv        # 删除项目内 .venv
./uninstall.sh --name novabot --bin-dir ~/.local/bin   # 自定义安装位置时
```

Windows 对应 `-Purge`、`-RemoveVenv`、`-CommandName`、`-BinDir`。`uninstall.sh --help` / `uninstall.bat -Help` 查看全部参数。

`--purge` 会带走会话、账号、技能、记忆和模型配置，建议确认备份已生成后再清理备份。安装时指定的外部虚拟环境不会被删除，需要用户自行处理。

## 7. 数据与备份

数据目录和备份策略见：

- [架构与运行模型](architecture.md)
- [排错与限制](troubleshooting.md)
