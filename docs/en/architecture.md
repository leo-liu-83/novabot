# Architecture and Runtime Model

This document describes the user-facing architecture: components, data layout, sessions and security boundaries.

## 1. Overview

```text
┌─────────────────────────────────────────────┐
│  TUI (Textual)      Web console / HTTP API   │
│        │                    │               │
│        └──────┬─────────────┘               │
│               ▼                             │
│        session and identity services        │
│               │                             │
│               ▼                             │
│        agent runtime + tools                │
│  models / skills / attachments / search / MCP│
│               │                             │
│               ▼                             │
│     SQLite + local filesystem               │
└─────────────────────────────────────────────┘
```

Key properties:

- **Local first**: sessions, accounts, skills, memory and configuration stay on this machine
- **TUI and web share data**: the same sessions, messages, checkpoints and accounts
- **Server-issued session IDs**: the ID is used for messages, checkpoints and attachment directories
- **Layered permissions**: identity, execution level, skill release, network access and MCP grants are independent boundaries

## 2. Components

| Component | Purpose |
|---|---|
| TUI | Terminal interface for local operation and first-run setup |
| Web console | Browser interface sharing the same backend sessions |
| HTTP API | Auth, sessions, chat SSE, model catalog, skills, MCP, admin |
| Agent runtime | Model requests, tool calls, context compaction and event stream |
| Model layer | OpenAI-compatible providers, multiple endpoints and dynamic model lists |
| Skills | System / shared / private layers with scanning, release and content hashes |
| Files and attachments | Workspace files, session attachments, document conversion and images |
| Web access | Search provider fallback, page fetch and SSRF protection |
| MCP | External tools with server- and tool-level permissions |
| Storage | SQLite plus session and user directories |

## 3. Data directory

Default `~/.novabot/` (`%USERPROFILE%\.novabot\` on Windows):

```text
~/.novabot/
├── novabot.json              # user config
├── ui.json                   # TUI theme and language
├── data/
│   ├── novabot.db            # sessions, messages, accounts, reviews, quotas
│   └── login.json            # TUI login token when remember_login is enabled
├── sessions/
│   └── <thread_key>/         # attachments, compacted history, large tool results
├── users/
│   └── <uid>/workspace/      # user workspace, skills, prompts, memory
├── workspace/                # anonymous/public workspace
└── prompt.md                 # optional shared instructions
```

`config/config.json` in the release is the built-in default configuration. User overrides go to `~/.novabot/novabot.json`.

## 4. Identity and sessions

### Identities

- **Anonymous**: no login, shared public workspace
- **User**: separate workspace, private skills, persona and memory
- **Administrator**: manages accounts, execution ceilings, search quotas, global providers and MCP grants

Multi-user mode requires exactly one administrator, created in the local TUI.

### Sessions

- Session IDs are issued by the server
- TUI and Web use the same `sessions` / `messages` tables
- Message history is paginated; the server restores context from checkpoints and budget
- Deleting a session waits for active turns, then cleans messages, checkpoints and attachments

## 5. Security boundaries

| Boundary | Purpose |
|---|---|
| Identity ceiling | Maximum execution level for an account |
| Execution levels T0–T4 | Whether commands run and whether each command needs confirmation |
| Skill review | Script skills are scanned before release; unreleased skills are not loaded |
| Network policy | Fetch confirmation, domain allowlist and SSRF protection |
| MCP grants | Admin decides whether to enable MCP; users choose servers/tools |
| Confirmation gates | File writes, deletes, some MCP calls and web fetches can require approval |

These boundaries do not replace each other. MCP tools are independent of execution levels, and a clean skill scan does not mean the skill is safe.

## 6. Running

TUI:

```bash
novabot
```

Web:

```bash
novabot web --host 127.0.0.1 --port 9000
```

The default host is `0.0.0.0`. Restrict it before exposing the service.

REPL:

```bash
novabot repl
```

## 7. Extension points

- Models: add providers/models in `novabot.json`, or enable dynamic discovery
- Skills: system, shared or private directories
- Search: user-provided or global providers
- MCP: system config or user-layer `/mcp`
- Persona/memory: `SOUL.md`, `USER.md`, `prompt.md`, `memory/`

See the configuration and feature manuals for details.
