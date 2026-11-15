# NovaBot

Version: `0.2.1` | [Changelog](CHANGELOG.md)

## 1. Introduction

NovaBot is a local AI-agent console. It combines model chat, files and attachments, skills, web search, MCP tools, long-term memory and permission approvals in one application, with both a terminal UI and a web console.

- Everything runs locally; NovaBot does not send your data to its own service
- Single-user and multi-user modes; administrators control execution ceilings, search quotas and MCP grants
- Models use OpenAI-compatible endpoints: vendor APIs, aggregators or intranet gateways
- Skills and tools are permission-gated; risky actions can require confirmation
- Projects: register an existing directory, work inside it, and share its notes across that project's sessions
- Chinese and English interfaces

See the manual index below for details.

## 2. Quick start

Requires Python **3.12+**.

### Linux

```bash
cd /path/to/novabot
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
| Projects | Register an existing directory as the working root; project sessions share `AGENTS.md` and project notes, with file access confined to the directory |
| Skills | System / shared / private layers; script skills are scanned before release; unreleased skills are not loaded |
| Attachments and multimodal | File paste, path detection, image vision input, PDF/Office conversion, upload limits and draft cleanup |
| Web search and fetch | Multi-tier fallback, per-user quotas, fetch confirmation, domain allowlist, SSRF protection |
| MCP | System / user servers; per-tool `allow/ask/deny`; delayed tool discovery |
| Persona and memory | `prompt.md`, `SOUL.md`, `USER.md` and categorized memory files |
| Execution levels | T0–T4: no commands, skill scripts, read-only commands, confirm each command, no confirmation |
| Interfaces | TUI and web console; Chinese/English; light, dark and system themes |
| Data and deployment | Local SQLite and files; installers, config template, upgrade and backup guidance |

### Projects

A project **registers an existing directory**; it does not copy or move anything — the code already there is used as is.

- A session may belong to a project. Once it does, the project directory is its working root: file access is confined to it, and paths outside the project or sensitive files require confirmation
- All sessions of a project share project knowledge: the root `AGENTS.md` and `.novabot/notes/`; global memory stays per-user and cross-project, holding summaries only
- A project can bind recommended skills; project sessions default to execution level T3 and can be raised to T4, which inside a project means "no confirmation"
- The web console offers a projects menu, per-project session lists and session transfer; the TUI can create a project from the current directory and rename projects
- Projects are a separately granted capability: enabled by default in single-user mode, granted per account by an administrator in multi-user mode
- Explicit non-goals: no automatic directory creation or relocation, no sharing between projects, no project-level long-term memory, and no process-level sandbox

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
- [Versioning and releases](docs/en/versioning.md)

## 5. Disclaimer

All code in this project was written through **vibe coding** by models. The project is provided "as is", without warranty of correctness, stability, security or fitness for any purpose.

The authors accept no liability for any direct or indirect loss caused by using this project, including data loss, credential or account leakage, service interruption, costs or legal responsibility. Audit the code, back up important data, and use it cautiously in production or sensitive environments.

## 6. Acknowledgements

The project is developed through vibe coding with [OpenCode](https://opencode.ai).

Models used include:

- Qwen3.8 Flash
- Space Bunny (MiniMax M3.1 Flash)
- DeepSeek 4.1 Flash
