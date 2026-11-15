# Commands

## 1. Launch commands

After installation, `novabot` is available in PATH:

```bash
novabot                              # TUI
novabot repl                         # REPL
novabot web --host 127.0.0.1 --port 9000
```

From source:

```bash
python main.py
python main.py repl
python main.py web --host 127.0.0.1 --port 9000
```

`main.py` supports:

```text
positional: tui | repl | web
--host       web listen address
--port       web listen port
```

## 2. TUI commands

Type `/` in the input box to open the command picker.

### Session and model

| Command | Description |
|---|---|
| `/new` | Start a new session |
| `/session` | Switch session |
| `/model` | Switch model |
| `/think` | Thinking mode: on / off / auto |
| `/compact` | Compact context manually |
| `/lang` | Switch Chinese / English |
| `/help` | Show all commands |
| `/exit` | Exit NovaBot |

### Memory

| Command | Description |
|---|---|
| `/memory` | Show the memory index |
| `/memory <file>` | Show a memory file |
| `/remember <content>` | Write to long-term memory |

Logged-in users only.

### Execution and security

| Command | Description |
|---|---|
| `/exec` | View/change the session execution level |
| `/exec-limit` | Administrator: set an account's ceiling |
| `/skill-review` | Review skills |
| `/web-limit` | Administrator: set search quotas |
| `/web-config` | Configure your search providers |
| `/mcp` | Manage MCP servers and tool permissions |

### Identity

| Command | Description |
|---|---|
| `/login` | Log in |
| `/register` | Register |
| `/logout` | Log out |
| `/passwd` | Change password |
| `/delete-account` | Delete the account |

### Projects

| Command | Description |
|---|---|
| `/project` | Create a project for the current directory, or rename an existing one (always the current directory) |
| `/project-limit` | Grant or revoke the projects capability per account (administrator) |

Neither command is listed when the capability is not enabled for the account.

Command visibility depends on identity, configuration and permissions.

## 3. TUI shortcuts

| Shortcut | Action |
|---|---|
| `Enter` | Send |
| `Ctrl+N` | New line |
| `Esc` | Stop generation / close popup |
| `Ctrl+T` | Switch theme |
| `Ctrl+Q` | Exit |

## 4. Web console

Open `http://127.0.0.1:9000/`.

Main pages:

| Page | Purpose |
|---|---|
| Chat | Chat, model selection, attachments, compaction, retry |
| Overview | Session, usage, skill review, MCP summary |
| Persona | SOUL.md, long-term memory, agent avatar |
| Web search | User/global providers, quotas |
| MCP | Servers, approval modes, per-tool permissions |
| Skills | List, scan, release, reject |
| Account | Password, avatar, statistics |
| Users & access | Admin: accounts, ceilings, quotas, MCP grants |
| Global search | Admin global providers |
| Projects | Project list and per-project sessions; create / rename / archive; edit or generate the project `AGENTS.md` |
| System | Diagnostics; the model catalog can switch the agent default model |

## 5. HTTP API

See [HTTP API](api.md).

## 6. Configuration

See [Configuration](configuration.md).
