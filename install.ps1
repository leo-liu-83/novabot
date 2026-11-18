param(
    [string]$Venv = "",
    [switch]$NoVenv,
    [string]$Python = "python",
    [string]$BinDir = "$env:USERPROFILE\.local\bin",
    [string]$CommandName = "novabot",
    [switch]$SkipDeps,
    [switch]$InitConfig,
    [switch]$NonInteractive,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$ProjectDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }

# 在脚本作用域记录哪些参数是用户显式给的；Main 里的 $PSBoundParameters 是它自己的
$PythonSet = $PSBoundParameters.ContainsKey("Python")
$VenvSet = $PSBoundParameters.ContainsKey("Venv") -or $NoVenv.IsPresent
$DepsSet = $SkipDeps.IsPresent
$NameSet = $PSBoundParameters.ContainsKey("CommandName")
$BinSet = $PSBoundParameters.ContainsKey("BinDir")
$ConfigSet = $InitConfig.IsPresent

function Write-Usage {
    Write-Host @"
Usage: install.bat [options]

Interactive by default. Run install.bat by double-clicking it, or from a terminal.

  -Venv PATH          Use or create a virtual environment
  -NoVenv             Do not use a virtual environment
  -Python PATH        Python executable (3.12+; default: python)
  -BinDir PATH        Where to install the launcher (default: $env:USERPROFILE\.local\bin)
  -CommandName NAME   Launcher command name (default: novabot)
  -SkipDeps           Skip pip install
  -InitConfig         Create %USERPROFILE%\.novabot\novabot.json if missing
                      (single user; interactive setup asks about multi-user and the model endpoint)
  -NonInteractive     Do not prompt; use defaults and the options above
  -Help               Show this help
"@
}

if ($Help) {
    Write-Usage
    exit 0
}

# ── 终端样式 ──────────────────────────────────────────────────────────────────
# 颜色只在交互式运行时加：-NonInteractive 或把输出重定向到日志时保持纯文本，
# 文件里不会混进颜色控制块。NO_COLOR 是跨工具的事实标准。
#
# 符号只用 ASCII：install.ps1 是 UTF-8 无 BOM，Windows PowerShell 5.1 会按系统
# ANSI 代码页读脚本，非 ASCII 字符在 GBK/CP437 控制台上会显示成问号。
$script:UiRule = "-" * 52
$script:UiStepMark = "*"
$script:UiOkMark = "[OK]"
$script:UiWarnMark = "[!]"
$script:UiProgressMark = "->"
$script:UiErrorMark = "[X]"

function Test-UiColor {
    return [bool]($script:Interactive -and -not $env:NO_COLOR)
}

# 统一的着色出口：不满足条件时就是普通 Write-Host，调用方不用判断
function Write-UiColored([string]$Text, [string]$Color) {
    if ($Color -and (Test-UiColor)) {
        Write-Host $Text -ForegroundColor $Color
    } else {
        Write-Host $Text
    }
}

function Write-UiRule {
    Write-UiColored $script:UiRule DarkGray
}

function Write-UiBanner {
    Write-UiRule
    Write-UiColored " NovaBot setup" Cyan
    Write-UiColored (" Project         " + $ProjectDir) DarkGray
    Write-UiRule
}

# 每一步一个标题，避免十几行输出糊成一片
function Write-UiStep([string]$Text) {
    Write-Host ""
    Write-UiColored ("$($script:UiStepMark) " + $Text) Cyan
}

function Write-UiOk([string]$Text) {
    Write-UiColored ("$($script:UiOkMark) " + $Text) Green
}

function Write-UiWarn([string]$Text) {
    Write-UiColored ("$($script:UiWarnMark) " + $Text) Yellow
}

function Write-UiError([string]$Text) {
    Write-UiColored ("$($script:UiErrorMark) " + $Text) Red
}

# 进度行：不强调步骤，只标记"正在做某事"
function Write-UiProgress([string]$Text) {
    Write-UiColored ("$($script:UiProgressMark) " + $Text) Cyan
}

# 两列摘要：调用方把标签补到 10 个字符宽（全是 ASCII，直接数空格）
function Write-UiKv([string]$Label, [string]$Value) {
    if (Test-UiColor) {
        Write-Host "  $Label " -NoNewline -ForegroundColor DarkGray
        Write-Host $Value
    } else {
        Write-Host "  $Label $Value"
    }
}

function Write-UiOption([string]$Number, [string]$Text) {
    if (Test-UiColor) {
        Write-Host "  $Number)" -NoNewline -ForegroundColor Cyan
        Write-Host " $Text"
    } else {
        Write-Host "  $Number) $Text"
    }
}

function Ask-Value([string]$Prompt, [string]$Default) {
    if (-not $script:Interactive) { return $Default }
    if (Test-UiColor) { Write-Host "? " -NoNewline -ForegroundColor Cyan } else { Write-Host "? " -NoNewline }
    # default 为空时不显示一对空方括号
    $label = if ($Default) { "$Prompt [$Default]" } else { $Prompt }
    $answer = Read-Host $label
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer
}

function Ask-YesNo([string]$Prompt, [string]$Default) {
    if (-not $script:Interactive) { return $Default -eq "y" }
    if (Test-UiColor) { Write-Host "? " -NoNewline -ForegroundColor Cyan } else { Write-Host "? " -NoNewline }
    $answer = Read-Host "$Prompt [y/n, default $Default]"
    if ([string]::IsNullOrWhiteSpace($answer)) { $answer = $Default }
    return $answer -match "^(y|yes)$"
}

# Secrets are not echoed; empty input just means "configure later".
function Ask-Secret([string]$Prompt) {
    if (-not $script:Interactive) { return "" }
    if (Test-UiColor) { Write-Host "? " -NoNewline -ForegroundColor Cyan } else { Write-Host "? " -NoNewline }
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    if (-not $secure -or $secure.Length -eq 0) { return "" }
    return [System.Net.NetworkCredential]::new("", $secure).Password
}

# Derive a provider code from the endpoint: api.deepseek.com -> deepseek, IP/localhost -> local.
# Reusing a built-in provider code is fine: user config overrides the built-in entry.
function Get-ProviderCode([string]$Url) {
    $hostName = ""
    try {
        $hostName = ([System.Uri]::new($Url)).Host.ToLowerInvariant()
    } catch {
        $hostName = (($Url -replace "^[A-Za-z][A-Za-z0-9+.-]*://", "") -replace "[/?#].*$", "")
        $hostName = ($hostName -split ":")[0].ToLowerInvariant()
    }
    if (-not $hostName -or $hostName -eq "localhost" -or $hostName -match "^\d+\.\d+\.\d+\.\d+$") {
        return "local"
    }
    $skip = @("api", "www", "openai", "com", "net", "org", "io", "ai", "cn", "co", "dev")
    foreach ($label in $hostName.Split(".")) {
        if ($skip -contains $label) { continue }
        $code = ($label -replace "[^a-z0-9_-]", "-").Trim("-")
        if ($code) { return $code }
    }
    return "provider"
}

# Encrypt the plaintext keys just written. Failures only warn: the config stays usable.
# The master key travels via an environment variable (never a command-line argument), and
# non-interactive installs never auto-generate one (CI logs would keep the private key).
function Protect-ConfigKeys {
    $dataDir = Join-Path $script:ConfigDir "data"
    # No key was written: do not create a master key that protects nothing
    if (-not $script:ProviderKey -and -not $env:NOVABOT_MASTER_KEY) { return }
    if (-not $script:Interactive -and -not $env:NOVABOT_MASTER_KEY) {
        Write-UiWarn "Key encryption not enabled (non-interactive installs never generate a master key)."
        Write-Host "  Run: python -m util.secret_cli init --config `"$($script:ConfigFile)`" --data-dir `"$dataDir`""
        return
    }

    $env:NOVABOT_MASTER_KEY = $script:MasterKey
    try {
        $out = & $Py -m util.secret_cli init --config $script:ConfigFile --data-dir $dataDir 2>&1
        $code = $LASTEXITCODE
    } finally {
        Remove-Item Env:NOVABOT_MASTER_KEY -ErrorAction SilentlyContinue
    }
    if ($code -ne 0) {
        Write-UiWarn "Key encryption failed (keys stay plaintext): $out"
        return
    }
    # The generated private key must be visible to the user
    $out | ForEach-Object { Write-Host $_ }
    $match = [regex]::Match(($out -join "`n"), "fingerprint ([0-9a-f]{8})")
    if ($match.Success) { $script:MasterFp = $match.Groups[1].Value }
}

# Write a minimal user config: only the differences; everything else comes from the
# built-in config/config.json. UTF-8 without BOM, because the app reads it as UTF-8.
function Write-Config([string]$Path) {
    $data = [ordered]@{ multi_user = [bool]$script:MultiUser }
    if ($script:ProviderBaseUrl) {
        $entry = [ordered]@{
            name = $script:ProviderCode
            base_url = $script:ProviderBaseUrl
            api = "openai-completions"
            key = $script:ProviderKey
        }
        if ($script:ProviderModel) {
            $entry["models"] = @([ordered]@{
                code = $script:ProviderModel
                name = $script:ProviderModel
                stream = $true
            })
            $data["agent"] = [ordered]@{ model = "$($script:ProviderCode)/$($script:ProviderModel)" }
        } else {
            # No model code: let /models discover them, pick one with /model on first run
            $entry["dynamic_models"] = $true
        }
        $models = [ordered]@{}
        $models[$script:ProviderCode] = $entry
        $data["models"] = $models
    }

    $json = ConvertTo-Json -InputObject $data -Depth 8
    $tmp = "$Path.tmp"
    [System.IO.File]::WriteAllText($tmp, $json + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Expand-Home([string]$Path) {
    if ($Path -eq "~") { return $env:USERPROFILE }
    if ($Path.StartsWith("~\") -or $Path.StartsWith("~/")) {
        return (Join-Path $env:USERPROFILE $Path.Substring(2))
    }
    return $Path
}

function Get-EnvironmentPython([string]$Dir) {
    foreach ($relative in @("Scripts\python.exe", "python.exe", "bin\python.exe")) {
        $candidate = Join-Path $Dir $relative
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

function Resolve-Python([string]$Command) {
    $found = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $found) { return $null }
    if ($found.Source) { return $found.Source }
    if (Test-Path -LiteralPath $Command) { return $Command }
    return $null
}

# ── Python 探测与选择 ─────────────────────────────────────────────────────────
# 这一步在任何提问之前完成：解释器不合格就立刻停下，而不是让用户答完五六个
# 问题、甚至在磁盘上留下一个建了一半的 .venv 之后才报错。
#
# 只探测、不自动安装：winget/Store 需要联网和用户确认，安装脚本不该悄悄改系统。

function Get-PythonVersion([string]$Path) {
    if (-not $Path) { return $null }
    # 这段 Python 代码里不能出现双引号：Windows PowerShell 5.1 把参数交给原生程序时
    # 只加外层引号、不转义内层引号，`print("%d.%d.%d" % ...)` 会被截断成
    # `import sys; print(` → 语法错误 → 输出为空 → 明明能跑的 python 被判成
    # "cannot run"。用 sys.version.split()[0] 就一个引号都不需要。
    $text = $null
    try {
        $out = & $Path -c 'import sys; sys.stdout.write(sys.version.split()[0])' 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { $text = [string]($out | Select-Object -First 1) }
    } catch {
        $text = $null
    }
    if (-not $text -or $text -notmatch '^\d+\.\d+') {
        # 兜底：有的包装器/安全软件会拦 -c，--version 的文本里同样有版本号
        try {
            $raw = (& $Path --version 2>&1 | Out-String)
            $match = [regex]::Match($raw, 'Python\s+(\d+\.\d+(?:\.\d+)?)')
            if ($match.Success) { $text = $match.Groups[1].Value }
        } catch {
            $text = $null
        }
    }
    if (-not $text -or $text -notmatch '^\d+\.\d+') { return $null }
    return $text.Trim()
}

function Test-PythonFloor([string]$Path) {
    if (-not $Path) { return $false }
    try {
        & $Path -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)' *> $null
    } catch {
        return $false
    }
    return ($LASTEXITCODE -eq 0)
}

function Get-PythonCandidates {
    $raw = New-Object System.Collections.ArrayList

    if ($PythonSet) { [void]$raw.Add($Python) }
    if ($env:VIRTUAL_ENV) { [void]$raw.Add((Join-Path $env:VIRTUAL_ENV "Scripts\python.exe")) }
    if ($env:CONDA_PREFIX) { [void]$raw.Add((Join-Path $env:CONDA_PREFIX "python.exe")) }

    # Python launcher 的已安装列表最权威：py -0p 列出机器上所有注册过的解释器
    if (Get-Command "py" -ErrorAction SilentlyContinue) {
        try {
            foreach ($line in (& py -0p 2>$null)) {
                $match = [regex]::Match([string]$line, '([A-Za-z]:\\.*python\.exe)')
                if ($match.Success) { [void]$raw.Add($match.Groups[1].Value.Trim()) }
            }
        } catch {
            # 没有 launcher 或输出格式变了就跳过，后面的来源仍然可用
        }
    }

    foreach ($name in @("python3.14", "python3.13", "python3.12", "python3", "python")) {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        if ($found -and $found.Source) { [void]$raw.Add($found.Source) }
    }

    # 注册表比 PATH 可靠：python.org 安装器把每个版本登记在
    # HKLM/HKCU\SOFTWARE\Python\PythonCore\<版本>\InstallPath，装到哪个目录都查得到。
    # 用户刚改完环境变量但终端/资源管理器还是旧进程时，PATH 里根本没有 python，
    # 这时只有注册表和目录扫描能捞到。
    foreach ($root in @(
        "HKLM:\SOFTWARE\Python\PythonCore",
        "HKCU:\SOFTWARE\Python\PythonCore",
        "HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore",
        "HKCU:\SOFTWARE\WOW6432Node\Python\PythonCore"
    )) {
        foreach ($version in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
            $installPath = $null
            try { $installPath = $version.GetValue("") } catch { $installPath = $null }
            if (-not $installPath) {
                $installPath = (Get-ItemProperty -LiteralPath (Join-Path $version.PSPath "InstallPath") -ErrorAction SilentlyContinue)."(default)"
            }
            if ($installPath) { [void]$raw.Add((Join-Path $installPath "python.exe")) }
        }
    }

    # 装了但没进 PATH 的也捡进来：用户常常不知道机器上已经有 3.12。
    # 用 Python* 而不是 Python31*：3.14 的目录叫 Python314，写死 31 会漏掉。
    foreach ($pattern in @(
        "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe",
        "$env:ProgramFiles\Python*\python.exe",
        "${env:ProgramFiles(x86)}\Python*\python.exe",
        "C:\Python*\python.exe",
        "$env:USERPROFILE\miniconda3\envs\*\python.exe",
        "$env:USERPROFILE\anaconda3\envs\*\python.exe",
        "$env:USERPROFILE\miniforge3\envs\*\python.exe"
    )) {
        foreach ($item in @(Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue)) {
            [void]$raw.Add($item.FullName)
        }
    }
    return $raw
}

function Show-NoPython([string]$Reason, $Rejected, $Available, $Unusable) {
    Write-Host ""
    Write-UiError $Reason
    Write-UiColored "  NovaBot requires Python 3.12 or newer; the current environment does not qualify." DarkGray
    Write-Host ""
    if (($Rejected.Count + $Available.Count + $Unusable.Count) -eq 0) {
        Write-Host "No Python interpreter was found."
        Write-Host "If 'python' works in cmd, this process may still have the old PATH:"
        Write-Host "open a NEW terminal (or restart Explorer) and run install.bat again."
    } else {
        if ($Rejected.Count -gt 0) {
            Write-Host "Found but too old:"
            foreach ($item in $Rejected) { Write-Host ("  {0,-12} {1}" -f $item.Version, $item.Path) }
        }
        if ($Unusable.Count -gt 0) {
            Write-Host "Found but cannot run:"
            foreach ($item in $Unusable) { Write-Host ("             {0}" -f $item.Path) }
            Write-Host "  If one of these is Microsoft Store's python.exe alias, turn it off in"
            Write-Host "  Settings > Apps > Advanced app settings > App execution aliases,"
            Write-Host "  or install Python from python.org / winget (see below)."
        }
        if ($Available.Count -gt 0) {
            Write-Host "Usable 3.12+ interpreters on this machine:"
            foreach ($item in $Available) { Write-Host ("  {0,-12} {1}" -f $item.Version, $item.Path) }
        }
    }
    Write-Host ""
    Write-UiColored "Install Python 3.12 first:" Cyan
    Write-Host "  winget install -e --id Python.Python.3.12"
    Write-Host "  # or download from https://www.python.org/downloads/"
    Write-Host ""
    Write-UiColored "Then re-run this script, or point it at the interpreter:" Cyan
    Write-Host "  install.bat -Python C:\Path\To\python.exe"
}

function Select-Python {
    $candidates = New-Object System.Collections.ArrayList
    # 存在但跑不起来的候选：最常见的是应用商店的"应用执行别名"占位程序，
    # 它在 PATH 上、Test-Path 也是真，但执行后什么都不输出。静默丢掉会让人以为
    # 机器上根本没装 Python，所以单独记下来在报错里摊开。
    $unusable = New-Object System.Collections.ArrayList
    $seen = @{}
    foreach ($item in (Get-PythonCandidates)) {
        $raw = [string]$item
        if (-not $raw) { continue }
        $path = $null
        if (Test-Path -LiteralPath $raw) {
            $path = (Resolve-Path -LiteralPath $raw).Path
        } else {
            $cmd = Get-Command $raw -ErrorAction SilentlyContinue
            if ($cmd -and $cmd.Source) { $path = $cmd.Source }
        }
        if (-not $path) { continue }
        $key = $path.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $version = Get-PythonVersion $path
        if (-not $version) {
            $seen[$key] = $true
            [void]$unusable.Add([pscustomobject]@{ Path = $path })
            continue
        }
        $seen[$key] = $true
        [void]$candidates.Add([pscustomobject]@{
            Path = $path
            Version = $version
            Ok = (Test-PythonFloor $path)
        })
    }

    $ok = @($candidates | Where-Object { $_.Ok })
    $rejected = @($candidates | Where-Object { -not $_.Ok })

    if ($PythonSet) {
        # 显式指定的必须真的可用：静默换成另一个解释器，用户会以为用的是指定的那个
        $resolved = Resolve-Python (Expand-Home $Python)
        $version = Get-PythonVersion $resolved
        if (-not $version -or -not (Test-PythonFloor $resolved)) {
            Show-NoPython "The interpreter given with -Python is not usable: $Python" $rejected $ok $unusable
            exit 2
        }
        $full = $resolved
        try { $full = (Resolve-Path -LiteralPath $resolved).Path } catch { }
        return [pscustomobject]@{ Path = $full; Version = $version; Ok = $true }
    }
    if ($ok.Count -eq 0) {
        Show-NoPython "No Python 3.12+ interpreter found" $rejected $ok $unusable
        exit 2
    }
    if ($ok.Count -eq 1 -or -not $script:Interactive) {
        return $ok[0]
    }

    Write-UiStep "Select Python interpreter (3.12+)"
    for ($i = 0; $i -lt $ok.Count; $i++) {
        Write-UiOption ([string]($i + 1)) ("{0,-9} {1}" -f $ok[$i].Version, $ok[$i].Path)
    }
    Write-UiOption ([string]($ok.Count + 1)) "Enter another path"
    $pick = Ask-Value "Choice" "1"
    if ($pick -match '^\d+$') {
        $number = [int]$pick
        if ($number -ge 1 -and $number -le $ok.Count) { return $ok[$number - 1] }
        if ($number -eq ($ok.Count + 1)) {
            $manual = Expand-Home (Ask-Value "Python executable" $Python)
            $resolved = Resolve-Python $manual
            $version = Get-PythonVersion $resolved
            if (-not $version -or -not (Test-PythonFloor $resolved)) {
                Show-NoPython "The interpreter is not usable: $manual" $rejected $ok $unusable
                exit 2
            }
            return [pscustomobject]@{ Path = $resolved; Version = $version; Ok = $true }
        }
    }
    throw "Invalid choice: $pick"
}

function Main {
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir "main.py"))) {
        throw "main.py not found under $ProjectDir"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir "requirements.txt"))) {
        throw "requirements.txt not found under $ProjectDir"
    }

    $script:Interactive = (-not $NonInteractive) -and (-not [Console]::IsInputRedirected)

    if ($script:Interactive) { Write-UiBanner }

    # 先探明解释器再问任何问题：这一段的失败必须早于所有交互和磁盘写入
    $selectedPython = Select-Python
    $Python = $selectedPython.Path
    $PythonVersion = $selectedPython.Version

    # venv 模块缺失时 `-m venv` 的报错会指向 ensurepip，与真实原因相隔一层
    & $Python -c "import venv" *> $null
    $venvModuleOk = ($LASTEXITCODE -eq 0)

    # 应用固定读取 ~/.novabot/novabot.json（不认其他数据目录变量）
    $script:ConfigDir = Join-Path $env:USERPROFILE ".novabot"
    $script:ConfigFile = Join-Path $script:ConfigDir "novabot.json"
    $script:WriteConfig = $false
    $script:MultiUser = $false
    $script:ProviderBaseUrl = ""
    $script:ProviderKey = ""
    $script:MasterKey = ""
    $script:MasterFp = ""
    $script:ProviderModel = ""
    $script:ProviderCode = ""

    $activeEnv = $null
    $activeKind = $null
    if ($env:VIRTUAL_ENV -and (Get-EnvironmentPython $env:VIRTUAL_ENV)) {
        $activeEnv = $env:VIRTUAL_ENV
        $activeKind = "virtualenv"
    } elseif ($env:CONDA_PREFIX -and $env:CONDA_DEFAULT_ENV -ne "base" -and (Get-EnvironmentPython $env:CONDA_PREFIX)) {
        $activeEnv = $env:CONDA_PREFIX
        $activeKind = "Conda"
    }

    $useVenv = -not $NoVenv
    if (-not $VenvSet) {
        if ($script:Interactive) {
            if (-not $venvModuleOk) {
                Write-UiWarn "$Python has no venv module, so no virtual environment can be created."
                Write-Host "    Install the full Python distribution, or choose option 4."
            }
            Write-UiStep "Select Python environment"
            Write-UiOption 1 "Create/use project .venv"
            if ($activeEnv) { Write-UiOption 2 "Use current $activeKind environment  $activeEnv" }
            Write-UiOption 3 "Use an existing virtual environment path"
            Write-UiOption 4 "Do not use a virtual environment"
            $choice = Ask-Value "Choice" "1"
            switch ($choice) {
                "1" { $Venv = Join-Path $ProjectDir ".venv"; $useVenv = $true }
                "2" {
                    if (-not $activeEnv) { throw "No active virtual environment detected" }
                    $Venv = $activeEnv
                    $useVenv = $true
                }
                "3" { $Venv = Ask-Value "Virtual environment path" (Join-Path $env:USERPROFILE "venvs\novabot"); $useVenv = $true }
                "4" { $useVenv = $false }
                default { throw "Invalid choice: $choice" }
            }
        } elseif ($activeEnv) {
            $Venv = $activeEnv
        } else {
            $Venv = Join-Path $ProjectDir ".venv"
        }
    } elseif ($NoVenv) {
        $useVenv = $false
    }

    if ($script:Interactive) {
        # 三个问题全被命令行参数固定时不留空标题
        if ((-not $DepsSet) -or (-not $NameSet) -or (-not $BinSet)) {
            Write-UiStep "Install options"
        }
        if (-not $DepsSet) {
            if (-not (Ask-YesNo "Install Python dependencies?" "y")) { $SkipDeps = $true }
        }
        if (-not $NameSet) {
            $CommandName = Ask-Value "Launcher command name" $CommandName
        }
        if (-not $BinSet) {
            $BinDir = Ask-Value "Launcher directory" $BinDir
        }

        Write-UiStep "Config file"
        Write-UiKv "Path      " $script:ConfigFile
        # 配置不存在就直接进入问答；已存在先问是否覆盖（覆盖前会备份）
        if ($ConfigSet) {
            if (-not (Test-Path -LiteralPath $script:ConfigFile)) { $script:WriteConfig = $true }
        } elseif (-not (Test-Path -LiteralPath $script:ConfigFile)) {
            $script:WriteConfig = $true
        } elseif (Ask-YesNo "Config exists. Regenerate it (old file saved as novabot.json.bak)?" "n") {
            $script:WriteConfig = $true
        }

        if ($script:WriteConfig) {
            Write-UiStep "Run mode"
            Write-UiKv "Single    " "one local identity, the TUI works without login (default)"
            Write-UiKv "Multi     " "accounts/login, data isolated per account; the Web console requires it"
            Write-Host ""
            if (Ask-YesNo "Enable multi-user mode?" "n") { $script:MultiUser = $true }

            Write-Host ""
            if (Ask-YesNo "Configure a model endpoint (OpenAI compatible) now?" "y") {
                $script:ProviderBaseUrl = Ask-Value "Endpoint (e.g. https://api.deepseek.com; empty to skip)" ""
                if ($script:ProviderBaseUrl) {
                    $script:ProviderKey = Ask-Secret "API Key (not echoed; may be empty)"
                    if ($script:ProviderKey) {
                        $script:MasterKey = Ask-Secret "Master key (SM2 private key 64 hex chars / SM4 key 32 hex chars; empty = generate)"
                    }
                    $script:ProviderModel = Ask-Value "Model code (e.g. deepseek-flash; empty = pick with /model on first run)" ""
                    $script:ProviderCode = Get-ProviderCode $script:ProviderBaseUrl
                }
            }
        }

        Write-UiStep "Install summary"
        Write-UiKv "Python    " "$Python ($PythonVersion)"
        if ($useVenv) { Write-UiKv "Virtualenv" $Venv } else { Write-UiKv "Virtualenv" "none" }
        Write-UiKv "Deps      " $(if ($SkipDeps) { "skip" } else { "install" })
        Write-UiKv "Command   " (Join-Path $BinDir "$CommandName.cmd")
        if ($script:WriteConfig) {
            $modeLabel = if ($script:MultiUser) { "multi user" } else { "single user" }
            Write-UiKv "Config    " "write $($script:ConfigFile) ($modeLabel)"
            if ($script:ProviderBaseUrl) {
                $modelLabel = if ($script:ProviderModel) { "/$($script:ProviderModel)" } else { "" }
                Write-UiKv "Endpoint  " "$($script:ProviderBaseUrl) ($($script:ProviderCode)$modelLabel)"
            } else {
                Write-UiKv "Endpoint  " "not configured"
            }
        } else {
            Write-UiKv "Config    " "keep existing $($script:ConfigFile)"
        }
        if (-not (Ask-YesNo "Start installation?" "y")) { return }
    } elseif ($ConfigSet -and -not (Test-Path -LiteralPath $script:ConfigFile)) {
        $script:WriteConfig = $true
    }

    if ($CommandName -notmatch "^[A-Za-z0-9._-]+$" -or $CommandName.StartsWith(".")) {
        throw "Command name may only contain letters, numbers, dot, underscore or hyphen"
    }

    $Venv = Expand-Home $Venv
    $BinDir = Expand-Home $BinDir
    $Python = Expand-Home $Python

    $basePython = $Python
    if ($useVenv -and -not $venvModuleOk) {
        throw "$Python has no venv module; install the full Python distribution, or use -NoVenv"
    }
    if ($useVenv) {
        if (-not (Test-Path -LiteralPath $Venv)) {
            Write-UiProgress "Creating virtual environment $Venv"
            & $basePython -m venv $Venv
            if ($LASTEXITCODE -ne 0) { throw "Failed to create virtual environment" }
        }
        $Py = Get-EnvironmentPython $Venv
        if (-not $Py) { throw "$Venv is not a usable Python virtual environment" }
        $Venv = (Resolve-Path -LiteralPath $Venv).Path
    } else {
        $Py = $basePython
    }

    & $Py -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)"
    if ($LASTEXITCODE -ne 0) { throw "$Py is older than 3.12 and cannot run NovaBot" }

    & $Py -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-UiProgress "pip is missing, running ensurepip"
        & $Py -m ensurepip --upgrade | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "pip is not available" }
    }

    if (-not $SkipDeps) {
        Write-UiProgress "Installing dependencies from $(Join-Path $ProjectDir 'requirements.txt')"
        & $Py -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip" }
        & $Py -m pip install -r (Join-Path $ProjectDir "requirements.txt")
        if ($LASTEXITCODE -ne 0) { throw "Failed to install dependencies" }
    } else {
        Write-UiProgress "Skipping dependencies (-SkipDeps)"
    }

    if (-not (Test-Path -LiteralPath $BinDir)) {
        New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    }
    $BinDir = (Resolve-Path -LiteralPath $BinDir).Path
    $mainPy = Join-Path $ProjectDir "main.py"
    $launcher = Join-Path $BinDir "$CommandName.cmd"
    $content = "@echo off`r`n`"$Py`" `"$mainPy`" %*`r`n"
    [System.IO.File]::WriteAllText($launcher, $content, [System.Text.Encoding]::Default)

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @()
    if ($userPath) { $parts = @($userPath -split ";" | Where-Object { $_ }) }
    if ($parts -notcontains $BinDir) {
        [Environment]::SetEnvironmentVariable("Path", (($parts + $BinDir) -join ";"), "User")
    }
    $env:Path = "$BinDir;$env:Path"

    if ($script:WriteConfig) {
        New-Item -ItemType Directory -Force -Path $script:ConfigDir | Out-Null
        if (Test-Path -LiteralPath $script:ConfigFile) {
            Copy-Item -LiteralPath $script:ConfigFile -Destination "$($script:ConfigFile).bak" -Force
            Write-UiProgress "Backed up existing config to $($script:ConfigFile).bak"
        }
        Write-Config $script:ConfigFile
        Write-UiProgress "Wrote config $($script:ConfigFile)"
        Protect-ConfigKeys
    }

    Write-Host ""
    Write-UiOk "Install complete"
    Write-UiKv "Command   " $launcher
    Write-UiKv "Project   " $ProjectDir
    Write-UiKv "Python    " "$Py ($PythonVersion)"
    if ($useVenv) { Write-UiKv "Virtualenv" $Venv }
    if ($script:WriteConfig) {
        $modeLabel = if ($script:MultiUser) { "multi user" } else { "single user" }
        Write-UiKv "Config    " "$($script:ConfigFile) ($modeLabel)"
        if ($script:MasterFp) {
            Write-UiKv "Master key" "encryption enabled (fingerprint $($script:MasterFp))"
        }
        if (-not $script:ProviderBaseUrl) {
            Write-UiWarn "No model endpoint configured: edit the models block and agent.model in $($script:ConfigFile),"
            Write-Host "         otherwise chats cannot start. See docs/model-config.md"
        } elseif (-not $script:ProviderModel) {
            Write-UiKv "Model     " "pick one with /model in the TUI on first run (/models discovery is enabled)"
        } else {
            Write-UiKv "Model     " "$($script:ProviderCode)/$($script:ProviderModel)"
            if (-not $script:ProviderKey) {
                Write-UiWarn "API key is empty: fill in models.$($script:ProviderCode).key in $($script:ConfigFile)"
            }
        }
        if ($script:MultiUser) {
            Write-UiKv "Multi user" "create the admin in the local TUI first, then open the Web console"
        }
    } elseif (Test-Path -LiteralPath $script:ConfigFile) {
        Write-UiKv "Config    " "$($script:ConfigFile) (existing content kept, untouched)"
        Write-Host "          Edit it to change multi-user mode or the model endpoint; see docs/model-config.md"
    } else {
        Write-UiKv "Config    " "not created (use -InitConfig, or run interactively)"
    }
    Write-Host ""
    Write-UiColored "Try:" Cyan
    Write-Host "  $CommandName"
    Write-Host "  $CommandName web --host 127.0.0.1 --port 9000"
    Write-Host ""
    Write-Host "Open a new terminal if '$CommandName' is not found yet."
}

try {
    Main
} catch {
    Write-UiError $_.Exception.Message
    exit 1
}
