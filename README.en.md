# NovaBot

Version: `0.1.0`

## 1. Introduction

NovaBot is a local AI-agent console. It combines model chat, files and attachments, skills, web search, MCP tools, long-term memory and permission approvals in one application, with both a terminal UI and a web console.

- Everything runs locally; NovaBot does not send your data to its own service
- Single-user and multi-user modes; administrators control execution ceilings, search quotas and MCP grants
- Models use OpenAI-compatible endpoints: vendor APIs, aggregators or intranet gateways
- Skills and tools are permission-gated; risky actions can require confirmation
- Chinese and English interfaces

See the manual index below for details.

## 2. Quick start

Requires Python **3.12+**.

### Linux

```bash
cd /path/to/novabot-0.1.0
chmod +x install.sh
./install.sh
novabot
```

`install.sh` interactively asks about the virtual environment, dependencies, command name and install directory, plus **multi-user mode** and the **model endpoint** (OpenAI-compatible base URL, API key, model code; all optional). Answers are written as a minimal `~/.novabot/novabot.json`, and the launcher goes to `~/.local/bin/novabot`.

- Single user: one local identity, the TUI works without login (default).
- Multi user: accounts/login with per-account data isolation; **the Web console requires multi-user**, and the first administrator must be created in the local TUI.
- Skipping the model endpoint still installs, but chats cannot start. Edit the `models` block and `agent.model` in `~/.novabot/novabot.json` later; see [Model configuration](docs/model-config.md).

Without the installer:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

For a manual install, configure one provider's `base_url` / `key`, at least one model, and point `agent.model` at `provider/model` in `~/.novabot/novabot.json`. Do not copy `novabot.template.json` directly: its providers ship with empty keys and look configured when they are not.

### Windows

Use **Windows Terminal** and Python 3.12+. Double-click `install.bat`, or run:

```bat
install.bat
novabot
```

`install.bat` calls the PowerShell installer, asks the same environment and setup questions, writes `%USERPROFILE%\.local\bin\novabot.cmd` and tries to add it to the user PATH. Open a new terminal after installation.

Without the installer:

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Config path: `%USERPROFILE%\.novabot\novabot.json`. For automation, run:

```bat
install.bat -InitConfig
```

It only creates a single-user minimal config when the file is missing, and never overwrites an existing file.

In multi-user mode, create the first administrator in the local TUI before exposing the port.

Uninstall removes the launcher only by default: `./uninstall.sh` (Windows `uninstall.bat`). Use `--purge` / `--remove-venv` (Windows `-Purge` / `-RemoveVenv`) to also remove data or the virtual environment.

## 3. Feature overview

| Feature | Summary |
|---|---|
| Chat and sessions | Shared sessions, messages and checkpoints; streaming, stop, retry, context compaction |
| Models | Multiple providers, OpenAI-compatible APIs, dynamic discovery, thinking switch, per-session selection |
| Multi-user | Anonymous / user / administrator; login, registration, password, account deletion, session handover |
| Skills | System / shared / private layers; script skills are scanned before release; unreleased skills are not loaded |
| Attachments and multimodal | File paste, path detection, image vision input, PDF/Office conversion, upload limits and draft cleanup |
| Web search and fetch | Multi-tier fallback, per-user quotas, fetch confirmation, domain allowlist, SSRF protection |
| MCP | System / user servers; per-tool `allow/ask/deny`; delayed tool discovery |
| Persona and memory | `prompt.md`, `SOUL.md`, `USER.md` and categorized memory files |
| Execution levels | T0–T4: no commands, skill scripts, read-only commands, confirm each command, no confirmation |
| Interfaces | TUI and web console; Chinese/English; light, dark and system themes |
| Data and deployment | Local SQLite and files; installers, config template, upgrade and backup guidance |

## 4. Manual index

### Chinese

- [Quick start and deployment](docs/quickstart.md)
- [Architecture](docs/architecture.md)
- [Features](docs/features.md)
- [Commands](docs/commands.md)
- [Configuration](docs/configuration.md)
- [Model configuration](docs/model-config.md)
- [HTTP API](docs/api.md)
- [Security and permissions](docs/security.md)
- [Troubleshooting](docs/troubleshooting.md)

### English

- [Quick start](docs/en/quickstart.md)
- [Architecture](docs/en/architecture.md)
- [Features](docs/en/features.md)
- [Commands](docs/en/commands.md)
- [Configuration](docs/en/configuration.md)
- [Model configuration](docs/en/model-config.md)
- [HTTP API](docs/en/api.md)
- [Security](docs/en/security.md)
- [Troubleshooting](docs/en/troubleshooting.md)

## 5. Disclaimer

All code in this project was written through **vibe coding** by models. The project is provided "as is", without warranty of correctness, stability, security or fitness for any purpose.

The authors accept no liability for any direct or indirect loss caused by using this project, including data loss, credential or account leakage, service interruption, costs or legal responsibility. Audit the code, back up important data, and use it cautiously in production or sensitive environments.

## 6. Acknowledgements

The project is developed through vibe coding with [OpenCode](https://opencode.ai).

Models used include:

- Qwen3.8 Flash
- Space Bunny (MiniMax M3.1 Flash)
- DeepSeek 4.1 Flash
