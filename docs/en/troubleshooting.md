# Troubleshooting

## 1. Installation

### `novabot: command not found`

Add the launcher directory to PATH:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

On Windows check `%USERPROFILE%\.local\bin` in the user PATH, or open a new terminal.

### Python version too old

Python 3.12+ is required. The installer scans for interpreters before asking any question and
only accepts 3.12+. When none qualifies it exits with code `2` (code `1` stays reserved for bad
arguments) and prints:

- interpreters it found that are too old, with their versions;
- usable 3.12+ interpreters on this machine, if any;
- the install command for your platform.

The scan covers `python3.13`/`python3.12`/`python3` on `PATH`, the active venv/Conda environment,
pyenv (`~/.pyenv/versions`), conda (`~/miniconda3/envs`, …) and the macOS Homebrew directories;
on Windows it also parses `py -0p`. So "3.12 is installed but `python3` points at an old system
build" is handled too.

The installer never installs Python itself (that needs elevation and network). Install it, re-run
the script, or point it at an interpreter:

```bash
./install.sh --python /usr/bin/python3.12
```

On Debian/Ubuntu the separate venv package is also required, otherwise creating the virtual
environment fails (the installer warns about this up front instead of letting an ensurepip error
take the blame):

```bash
sudo apt install python3.12-venv
```

If the interpreter passed to `--python` is unusable, the script fails instead of silently
switching to another one.

### Windows: `python` works in cmd but the installer cannot find it

Check the version (NovaBot needs 3.12+; 3.11 and below will not do) and the path:

```bat
python -V
where python
py -0p
```

If `python -V` is older than 3.12, install a newer one, or pass the interpreter explicitly:

```bat
install.bat -Python "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
```

If the version is fine but the installer still finds nothing, it is usually one of these two:

**1. PATH is a snapshot taken when the process started.** After editing the environment variables
a new cmd window sees `python`, but double-clicking `install.bat` lets Explorer start the process,
and Explorer may still be the process from before the change. Close every cmd window, restart
"Windows Explorer" from Task Manager if needed, and run it again.

**2. The app execution alias stub.** `%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe` exists but
prints nothing when executed — it is just an alias into the Store. Turn `python.exe` and
`python3.exe` off under Settings → Apps → Advanced app settings → App execution aliases, then
install a real build from python.org or `winget install -e --id Python.Python.3.12`.

The installer looks in three places: the registry
(`HKLM/HKCU\SOFTWARE\Python\PythonCore\<version>\InstallPath`), the usual install directories
(`%LOCALAPPDATA%\Programs\Python\Python*`, `C:\Program Files\Python*`, `C:\Python*`), and `py -0p`
plus PATH. So "installed but not on PATH" is covered too, and the error output lists paths that
were found but cannot be run, separating "not installed" from "installed but unusable".

### Dependency installation fails

- Check network access and your pip index
- Do not install into an externally managed system Python; use a virtual environment
- Upgrade pip if needed

## 2. Models

### 401 / 403

- Missing or wrong key
- Gateway requires a custom auth header
- Custom `Authorization` overridden by model-level headers

### 404 model not found

- Model code does not match the platform ID
- Dynamic discovery did not return it
- A static `models` allowlist omits it

### 404 endpoint not found

- `base_url` includes `/chat/completions`
- Platform needs a separate `models_url`
- Platform is not OpenAI-compatible

### 400 request error

- Model does not support `enable_thinking`
- The aggregator forwards an unsupported parameter upstream

### "Key cannot be decrypted: master key missing or mismatched"

- The config holds ciphertext (`enc:v1:sm4:...`) but `~/.novabot/data/novabot.db` has no master
  key — typically when only the config was copied. Restore the original master key and restart
- After changing the master key, re-encrypt with `init --rotate` instead of editing the DB
- If the master key is truly lost, re-enter each provider key:
  `python -m util.secret_cli init --force --config ~/.novabot/novabot.json --data-dir ~/.novabot/data`
  (it lists the entries it cannot decrypt)
- Check state with `python -m util.secret_cli status --config ~/.novabot/novabot.json --data-dir ~/.novabot/data`

### Thinking toggle does nothing (cannot turn off / on)

- The endpoint expects a different style: a common case is Qwen behind a LAN/remote vLLM that
  only understands `chat_template_kwargs.enable_thinking`, while the default only sends top-level
  `enable_thinking` → set `"thinking_style": "enable_thinking+template_kwargs"` on the model or
  provider
- Verify with `/think`; this boolean style only offers `off` / `medium`
- To see the actual fields, raise the log level to DEBUG; the "thinking request fields" line
  lists only the thinking keys (no message body or secrets)

### Context compacts too early

- `num_ctx` is too small
- The endpoint is larger but `num_ctx` is missing and `llm.max_input_tokens` is used

See [Model configuration](model-config.md).

## 3. Web search

A yellow notice such as:

```text
⚠ tavily failed (timeout), falling back to built-in firecrawl
```

means a provider failed and another tier was used:

- User providers and global providers are reported when they fail
- Built-in fallback only reports when the whole tier is unavailable
- Each endpoint is reported once per session

If all search fails, check network/proxy, keys, endpoints and IP-based quotas.

## 4. Attachments

### File too large

Default regular-user limit is 10 MB; administrators and single-user owners are unlimited by default.

```json
"attachments": {
    "max_per_message": 5,
    "max_file_mb": 10,
    "admin_unlimited": true,
    "draft_ttl_days": 7
}
```

### Too many attachments

Default limit is 5 per message.

### Unsent attachment disappeared

Unsent drafts older than `draft_ttl_days` are cleaned at startup. Attachments referenced by messages are kept.

### Scanned PDF cannot be read

Document conversion is local and does not use OCR or third-party services. Use a text PDF or Markdown.

### Model says it cannot see an image

The model must support vision. Text-only models return a readable error; audio/video provide paths only.

## 5. Interface

### Windows Chinese IME issues

- Use Windows Terminal, not legacy conhost
- Enable UTF-8:

```bat
chcp 65001
set PYTHONUTF8=1
```

- NovaBot applies a Textual IME compatibility patch
- If it still fails, use the web console

### The window title has a command line and paths appended

Ubuntu's default terminal, Ptyxis, has a "dynamic title": when the foreground process is not a
shell, it appends ` — <that process's full command line>` (see `ptyxis-tab.c`, `dup_title`), so
the title reads `NovaBot · hello — /path/python3.12 /path/main.py`.

- The `novabot` launcher written by the installer deliberately does **not** exec: the foreground
  process stays bash, which Ptyxis treats as a shell and leaves the title alone
- Running `python main.py tui` directly still shows the suffix; use `novabot` or
  `bash -c 'python main.py tui'` instead
- Ptyxis 50.1 has no toggle; newer versions expose "Disable Dynamic Title" in Preferences

### “Not persistent” warning

The database or checkpoint store failed to initialize; NovaBot falls back to memory mode. Chat still works, but history is lost after restart. Check `~/.novabot/data/` permissions and disk space.

### Cannot enter multi-user mode

Create the administrator in the local TUI first. Do not expose the port before that.

## 6. Known limitations

- All accounts share the same OS user; no process isolation
- Skill scanning is advisory and cannot detect all obfuscated payloads
- `grep -r` can walk the workspace
- SSRF has a DNS rebinding window
- Single-user API is unauthenticated; restrict the listener
- Anonymous workspace is shared
- `remember_login=false` cannot revoke the current login immediately; tokens expire after 12 hours
- Web and TUI store language/theme separately

See [Security](security.md) for details.
