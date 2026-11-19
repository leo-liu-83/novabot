# Features

## 1. Chat and sessions

- Shared sessions and message history between TUI and Web
- Streaming output, stop generation, retry and tool-call display
- Rename and delete sessions; deletion cleans checkpoints and attachments
- Automatic context compaction near the window limit; manual `/compact`
- Compaction records persist and can be replayed
- Paginated history for long conversations
- A session may belong to a project, in which case its working root becomes the project directory; see [§13 Projects](#13-projects)

## 2. Models

- Multiple providers: vendor APIs, aggregators and intranet gateways
- OpenAI `/chat/completions` compatible protocol
- Provider- and model-level request headers, including custom auth headers
- `${session_id}` placeholders and automatic `x-opencode-session`
- Dynamic model discovery: omit `models` to discover; provide `models` as a static allowlist
- Thinking levels (off / low / medium / high) with endpoint-specific styles: `reasoning_effort`, `thinking`, `thinking_level`, `enable_thinking`
- Per-session model selection; new-session selection is used for the first request

See [Model configuration](model-config.md).

## 3. Multi-user

- Anonymous, user and administrator identities
- Registration, login, logout, password change and account deletion
- TUI token persistence; password change revokes other sessions
- Optional handover of the current anonymous session to a new account
- Administrators set execution ceilings and search quotas
- Administrators enable/disable MCP for accounts

## 4. Skills

| Layer | Source | Visibility |
|---|---|---|
| System | Bundled with the release | Read-only for everyone |
| Shared | Public workspace | Everyone |
| Private | User workspace | Owner or administrator |

Two skills ship with the release: `skill-creator` (create and improve skills) and `project-notes`
(record project knowledge in `AGENTS.md` and project notes, keeping project details out of global
memory). `project-notes` carries per-project-type reference documents (Python for now) that the
model reads on demand; adding a type means adding one document under `references/` and registering
it. Built-in skills are read-only; to change their behaviour, add a skill with the same name
to your own private layer (the later layer wins).

- Script skills are not loaded until scanned and released
- Prompt-only skills load by default but can be rejected
- Release is bound to a content hash; changes require re-review
- Unreleased skills cannot be read by name through file tools
- Administrators can release dangerous skills; users can release safe ones
- Scanning is advisory, not a security guarantee

## 5. Attachments and multimodal input

- TUI: paste paths or drag paths from a file manager
- Web: file picker, drag-and-drop and clipboard files
- Images can be sent to vision-capable models
- PDF / Office / EPUB / RTF are converted locally to Markdown
- Text is inlined; audio/video provide paths only
- Default user limits: 5 attachments per message, 10 MB per file
- Administrators and single-user owners are unlimited by default
- Unsent draft attachments are cleaned after a configurable TTL; sent attachments are kept

## 6. Web search and fetch

### Search

- Three tiers: user providers → global providers → built-in free fallback
- User providers do not consume NovaBot quota
- Global and free tiers are quota-limited per user
- Automatic fallback on failure or empty results
- Endpoint failures appear as visible notices once per session

### Fetch

- `webfetch` asks for confirmation by default
- In `ask` mode, allowlisted domains skip confirmation; other domains still require approval
- `allow` skips confirmation; `deny` removes the tool
- Blocks localhost, private, link-local and cloud metadata addresses
- Revalidates every redirect; applies size and character limits
- Wraps fetched content as untrusted external content

## 7. MCP

- Streamable HTTP MCP servers
- System-layer servers are bundled and enabled by default
- User-layer servers are disabled until an administrator grants MCP
- Delayed tool discovery: `mcp_list` then `mcp_call`
- Server-level mode plus per-tool `allow` / `ask` / `deny`
- Per-user server limit
- Tool descriptions have a context budget

## 8. Persona, prompts and memory

- Core prompt + personalization files + user instructions
- Logged-in users can maintain `SOUL.md` and `USER.md`
- `prompt.md` customizes behavior
- Long-term memory uses a `core.md` index plus categorized files
- `/remember` writes; `/memory` reads
- Anonymous identities have no persona or long-term memory

## 9. Execution levels

| Level | Capability |
|---|---|
| T0 | No commands |
| T1 | Only released skill `.py` scripts |
| T2 | Single read-only commands inside the workspace |
| T3 | Any shell command, confirmation for each |
| T4 | Any shell command, no confirmation |

Hard-blocked destructive commands are rejected at every level. See [Security](security.md).

## 10. Interfaces

- TUI: Textual; `Ctrl+Q` exit, `Ctrl+T` theme
- The TUI sets the terminal window title to `NovaBot · session title` (OSC 2), so several open
  terminals are easy to tell apart; it is cleared on exit and handed back to the terminal
- Web: browser console with responsive layout
- Chinese/English: Web top bar, TUI `/lang`
- Themes: light / dark / system (Web)
- Windows TUI includes an IME compatibility fix

## 11. Persistence

- Sessions, messages, accounts, reviews and quotas live in local SQLite
- Checkpoints allow conversations to continue after restart
- Session attachments, compacted history and large tool results are isolated per session
- If the database is unavailable, NovaBot falls back to memory mode and displays a clear “not persistent” warning

## 12. Admin reports

Administrators can open **Reports** for aggregate analytics:

- User activity: active users in range, DAU/WAU/MAU, new users, sessions, turns
- Model usage: calls, users, token share, and top models
- Skill usage: uses, users/sessions, script runs, share, and last-used time per skill
- Skill counting: one use per successful first-page read of a skill's `SKILL.md`, or one per
  `run_skill_script` run; other files in the skill directory do not add a second use, and
  `ls`/`grep` (seeing the name only) do not count
- MCP usage: calls, tool count, users/sessions, and share per server (successful calls only)
- Web search: calls and share per provider and tier (user-provided / global / built-in);
  queries are never recorded
- Token consumption: input/output trend, per-turn average, top users and models
- Time ranges: last 7 / 30 / 90 days; granularity: daily or weekly
- Aggregates only; message content is never returned
- Backfilled historical model rows make call counts approximate

## 13. Projects

A project ties sessions to a real directory. It does not copy or move anything: it **registers an existing directory**, and file access plus project knowledge revolve around it.

| Concept | Meaning |
|---|---|
| Project | Name + directory + description + recommended skills; active projects are unique per account by name and path, and names/paths can be reused after archiving |
| Membership | A session may belong to a project or not; sessions without one behave exactly as before |
| Working root | A project session works inside the project directory, with reads and writes confined to it |
| Project knowledge | The root `AGENTS.md` is injected every turn (truncated when long); `.novabot/notes/*.md` contributes only a file-name index, and the agent reads bodies on demand |
| Recommended skills | A soft priority only; it does not change skill visibility or the review gate |

Permissions and boundaries:

- Projects are a separately granted capability: available to the single-user owner by default; in multi-user mode an administrator grants it per account with `/project-limit`, and administrators always have it
- Reads and writes inside the project directory are allowed directly; paths outside it and sensitive files require confirmation
- Project sessions cap the shell at T3 by default; T4 must be raised explicitly with `/exec`, and its meaning narrows from "any command, no confirmation" to "no confirmation inside the project" — commands naming paths outside the project still ask
- "Always allow" on a confirmation card is remembered per path and applies to the current session only; moving or switching a session to another project clears those approvals so they cannot leak into it
- Archiving a project stops using it; the directory is not deleted and existing files are not moved
- **Project virtualenv**: shell commands in a project session use the project's own Python
  environment — an existing `.venv` / `venv` / `env` (with `pyvenv.cfg`) is used as is; without
  one, the first command that actually **runs tests/scripts or installs dependencies** creates
  `.venv` (empty projects and queries such as `python -V` do not; projects managed by
  uv/poetry/pdm with a lockfile are left to their own runner; `python -m venv .venv` also works
  explicitly). Editing files is unaffected;
  only `execute` injects `PATH` and `VIRTUAL_ENV`. **Pollution guard**: child processes inherit
  no other environment variables, `PATH` drops every venv/conda entry (including NovaBot's own),
  and `pip install` is refused when the project has no isolated environment — it never lands in
  NovaBot or the system Python

Entry points:

- Web: the projects page (`#/projects`) creates, renames and archives projects, edits or generates the project `AGENTS.md`, and lists the project's sessions
- TUI: `/project` works on the current directory — with no project it asks for a name right away (and adopts the current session when it has none); with a project it shows that project, Enter renames it and `Ctrl+D` deletes it (it first asks whether to remove only the record or also clean `.novabot` / `AGENTS.md`; sessions are always only detached); when `.novabot/notes` or `AGENTS.md` is missing the dialog offers "Fill in missing project files" and only fills the gaps; `/session` shows which project a session belongs to

Explicit non-goals: no project directory relocation (the web console creates the project subdirectory under the chosen root; the TUI still registers the current directory), no sharing between projects, no project-level long-term memory, and no process-level sandbox.

## 14. Subagents

The main agent can hand work to two built-in subagents. Each runs in its own context and returns
only the conclusion.

| Role | Can do | Use it when |
|---|---|---|
| `explore` | Read-only: `ls` / `read_file` / `glob` / `grep`, plus search, fetch and document conversion | You need to find code across files or gather facts first |
| `general` | Every main-agent tool except `task`; writes still follow the execution level | The plan is clear and code must change or commands must run |

- Dispatch rules live in the main prompt: small edits stay inline; explore first when the code is
  unknown, then hand the facts to `general`
- Subagents inherit the session's execution level, project path boundary, MCP/fetch permissions and
  write confirmation; they **cannot dispatch further subagents**
- Concurrency: at most 3 running globally; one `general` per session; at most 6 `task` calls per
  turn; a subagent active for more than 5 minutes is stopped; queueing longer than 60 seconds
  returns a tool error so the main model can serialise or do the work itself
- Models: each role can pin a model; an empty value follows the session model, and an unusable
  configuration falls back with a one-time notice
- Usage: subagent calls are recorded per role in `usage_events`, so reports separate main and
  subagent spend
- Non-goals: no background tasks that outlive a turn, no inner token stream in the UI, and no
  custom roles from the web console
