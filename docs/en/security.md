# Security and Permissions

NovaBot separates identity, execution level, skill release, network access, MCP grants and confirmation gates. None of them replaces another.

## 1. Identity and ceilings

| Identity | Execution ceiling | Default level |
|---|---|---|
| Multi-user anonymous | T1 | T0 |
| Regular user, no grant | T1 | T0 |
| Regular user, granted T3 | T3 | T0 (must raise it) |
| Administrator | T4 (service ceiling applies) | T3 |
| Single-user owner | T4 | T1 |

- Administrators use `/exec-limit` to raise non-admin ceilings
- A ceiling limits the maximum; it does not raise the current level
- `/exec` applies only to the current session

## 2. Execution levels

| Level | Capability |
|---|---|
| T0 | No commands |
| T1 | Released skill `.py` scripts only |
| T2 | Single read-only commands inside the workspace |
| T3 | Any shell command, each confirmed |
| T4 | Any shell command, no confirmation |

Hard limits:

- Destructive deletes, fork bombs, disk writes, formatting, shutdown/reboot are blocked
- Commands are split at control operators; `ls && rm -rf /` is blocked
- T2 path arguments must stay inside the workspace
- No level can exceed the identity ceiling

## 3. Skill review

- System skills are read-only and always loaded
- Shared and private skills require review
- Script skills are not loaded until released
- Prompt-only skills load by default but can be rejected
- Release is bound to a content hash; changes require re-review
- Administrators can release dangerous skills; regular users only safe ones
- Reject is administrator/owner only

Scanning is advisory:

- It checks prompt injection, hidden characters, dangerous script patterns and credential paths
- Obfuscated payloads such as base64 are not visible to static analysis
- `No findings` does not mean safe

## 4. File and skill gates

- File tools and read-only commands block unreleased skill directories
- The guarantee is that unreleased skill bodies cannot be read by name
- `grep -r "x" .` can still walk the workspace; full isolation requires OS-level sandboxing, which this version does not provide

Project sessions add their own boundary (see [features](features.md#13-projects)):

- The working root is the project directory; reads and writes inside it are allowed directly, while paths outside it and sensitive files ask
- T4 inside a project narrows to "no confirmation inside the project": commands naming paths outside it still ask
- The project's `AGENTS.md` and `.novabot/notes/*.md` are **content authored by the project**, injected into the model context as instructions; review an untrusted project first
- "Always allow" on a confirmation card is remembered per path and only for the current session; a new session asks again

## 5. Web search and fetch

### Fetch permission

| Value | Behavior |
|---|---|
| `ask` | Confirm each URL; allowlisted domains skip |
| `allow` | All public URLs skip confirmation |
| `deny` | `webfetch` is removed |

The allowlist is a confirmation shortcut, not a hard allowlist. In `allow` mode all public addresses can be fetched.

### SSRF

Fetch blocks:

- localhost / loopback
- private networks
- link-local and cloud metadata
- reserved addresses

Every redirect is revalidated. DNS resolution happens at request time, leaving a DNS rebinding window.

### Prompt injection

Search results and fetched pages are wrapped as untrusted content; forged boundary markers are rewritten. Suspected injection is logged, not automatically blocked.

## 6. MCP

- System servers are bundled and enabled by default
- User servers are disabled until an administrator grants MCP
- Server-level mode: `ask` / `allow` / `deny`
- Individual tools can override the server mode
- `allow` is an explicit user decision; MCP read-only hints are not a security guarantee
- `ask` shows arguments and waits for confirmation
- Per-user server limits apply

## 7. Data and keys

### 7.1 Optional key encryption

Provider keys and self-hosted search keys can be stored encrypted (`enc:v1:sm4:...`):

- The master key is chosen at install time (paste an SM2 private key / SM4 key, or leave it
  empty to generate one and print it), then read from the database on every start;
- Values are encrypted with SM4-CBC and an SM3 integrity tag; restoring on another machine
  only needs the config file plus the master key;
- Plaintext values keep working (hand-edited configs are fine), and `status` reports how many
  are still plaintext;
- When a ciphertext cannot be decrypted, it is **never** sent to the provider — you get a
  "master key missing or mismatched" error instead.

```bash
python -m util.secret_cli init --config ~/.novabot/novabot.json --data-dir ~/.novabot/data
python -m util.secret_cli status --config ~/.novabot/novabot.json --data-dir ~/.novabot/data
```

**Boundaries**: it protects against copying/screenshotting/sharing the config or database
alone; it does **not** protect against a local process that can read
`~/.novabot/data/novabot.db` (the master key lives in the same file), against someone who can
edit the config (`base_url` can be repointed), or against backing up the whole `~/.novabot`
directory (the master key travels with it). Headers, MCP headers and login tokens are not
encrypted. Encryption does not replace file permissions: the config and database are chmod
`0600` on POSIX.

### 7.2 Other data

- MCP headers stay plaintext; login tokens live in `data/login.json` when `remember_login=true`
- Do not commit or share `~/.novabot/`
- Account deletion cleans sessions, attachments and related data; keep backups

## 8. Deployment recommendations

- Single-user API is unauthenticated by default; do not expose `--host 0.0.0.0` to the internet
- Create the first administrator in the local TUI before exposing the service
- Preserve the original `Host` header in reverse proxies and consider extra authentication
- Enable risky skills, MCP servers and shell tools only in trusted environments
- Periodically review `/exec-limit`, search quotas and MCP grants

## 9. Known risks

- All accounts run as the same OS user; there is no process isolation
- Skill scanning cannot detect every obfuscated payload
- `grep -r` can bypass the “read by name” skill gate
- DNS rebinding and TOCTOU windows remain
- Web/TUI IME behavior depends on the terminal
- The project is model-generated; audit before use
