# Quick Start and Deployment

This guide covers the shortest path from source code to a running NovaBot, plus first-run setup, upgrades and backups.

## 1. Requirements

- Python **3.12+**
- Linux: bash and network access for dependencies
- Windows: Windows 10/11, PowerShell, Windows Terminal recommended
- At least one OpenAI-compatible model endpoint

> The release package does not include `tests/`.

## 2. Linux

### Interactive installer

```bash
cd /path/to/novabot-0.1.0
chmod +x install.sh
./install.sh
```

The installer asks about:

- Python executable
- Virtual environment: project `.venv`, current virtualenv/Conda environment, an existing path, or no venv
- Whether to install dependencies
- Command name (default `novabot`)
- Launcher directory (default `~/.local/bin`)
- Whether to create `~/.novabot/novabot.json` from the template

Then run:

```bash
novabot
novabot web --host 127.0.0.1 --port 9000
novabot repl
```

If `~/.local/bin` is not in `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### Manual install

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

### Non-interactive install

```bash
./install.sh --non-interactive
./install.sh --venv ~/venvs/novabot
./install.sh --no-venv
./install.sh --python /usr/bin/python3.12
./install.sh --bin-dir /usr/local/bin
./install.sh --skip-deps
./install.sh --init-config
```

## 3. Windows

### install.bat

Double-click `install.bat`, or run:

```bat
install.bat
```

Do not double-click `install.ps1` unless your execution policy allows it; `install.bat` uses `-ExecutionPolicy Bypass`.

Then:

```bat
novabot
novabot web --host 127.0.0.1 --port 9000
novabot repl
```

Launcher:

```text
%USERPROFILE%\.local\bin\novabot.cmd
```

The installer tries to add that directory to the user PATH. Open a new terminal if `novabot` is not found.

### Manual install

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

### Options

```bat
install.bat -Venv C:\venvs\novabot
install.bat -NoVenv
install.bat -Python C:\Python312\python.exe
install.bat -BinDir C:\tools\novabot\bin
install.bat -InitConfig
install.bat -NonInteractive
```

> Use Windows Terminal for the TUI. NovaBot applies a compatibility fix for known Windows IME issues.

## 4. First configuration

Config paths:

```text
Linux:   ~/.novabot/novabot.json
Windows: %USERPROFILE%\.novabot\novabot.json
```

Start from the template:

```bash
mkdir -p ~/.novabot
cp novabot.template.json ~/.novabot/novabot.json
```

On Windows run `install.bat -InitConfig`, or copy the template manually.

At minimum:

- provider `base_url`, `api`, `key`
- at least one model
- `agent.model` set to `provider/model`

See [Model configuration](model-config.md).

## 5. First launch

Single user:

```bash
novabot
```

Useful commands:

```text
/model     choose a model
/help      list commands
/lang      switch Chinese/English
```

Multi-user:

1. Set `"multi_user": true` in `novabot.json`
2. Restart NovaBot
3. Create the first administrator in the local TUI
4. Register other accounts via `/register` or the web console
5. Administrators use `/exec-limit` for execution ceilings and `/web-limit` for search quotas

Do not expose the web port before creating the administrator.

## 6. Upgrade and backup

Rerun the installer after updating the source:

```bash
./install.sh
```

Back up before upgrading:

```bash
cp -a ~/.novabot ~/.novabot.backup-$(date +%Y%m%d)
```

Windows:

```bat
xcopy /E /I /H "%USERPROFILE%\.novabot" "%USERPROFILE%\.novabot.backup"
```

## 7. Uninstall

Remove:

- `~/.local/bin/novabot` or `%USERPROFILE%\.local\bin\novabot.cmd`
- the project virtual environment
- optionally `~/.novabot/`

Deleting `~/.novabot/` removes sessions, accounts, skills, memory and model configuration. Back it up first.

## 8. Data and backup

See [Architecture](architecture.md) and [Troubleshooting](troubleshooting.md).
