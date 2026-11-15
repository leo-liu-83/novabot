param(
    [string]$BinDir = "$env:USERPROFILE\.local\bin",
    [string]$CommandName = "novabot",
    [switch]$Purge,
    [switch]$RemoveVenv,
    [switch]$NonInteractive,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$ProjectDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }

function Write-Usage {
    Write-Host @"
Usage: uninstall.bat [options]

Removes the launcher only by default. Config, data and the virtual environment
are kept unless you ask for them explicitly.

  -BinDir PATH        Where the launcher lives (default: $env:USERPROFILE\.local\bin)
  -CommandName NAME   Launcher command name (default: novabot)
  -Purge              Remove %USERPROFILE%\.novabot after backing it up
  -RemoveVenv         Remove the project .venv (never an external environment)
  -NonInteractive     Do not prompt; run only the options above
  -Help               Show this help

Examples:
  uninstall.bat
  uninstall.bat -Purge
  uninstall.bat -CommandName novabot -BinDir $env:USERPROFILE\.local\bin
"@
}

if ($Help) {
    Write-Usage
    exit 0
}

function Ask-YesNo([string]$Prompt, [string]$Default) {
    if (-not $script:Interactive) { return $Default -eq "y" }
    $answer = Read-Host "$Prompt [$Default]"
    if ([string]::IsNullOrWhiteSpace($answer)) { $answer = $Default }
    return $answer -match "^(y|yes)$"
}

function Expand-Home([string]$Path) {
    if ($Path -eq "~") { return $env:USERPROFILE }
    if ($Path.StartsWith("~\") -or $Path.StartsWith("~/")) {
        return (Join-Path $env:USERPROFILE $Path.Substring(2))
    }
    return $Path
}

function Test-PathEntryMatch([string]$Entry, [string[]]$Targets) {
    foreach ($target in $Targets) {
        if ([string]::Equals($Entry, $target, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Main {
    if ($CommandName -notmatch "^[A-Za-z0-9._-]+$" -or $CommandName.StartsWith(".")) {
        throw "CommandName may only contain letters, numbers, dot, underscore or hyphen"
    }

    $script:Interactive = (-not $NonInteractive) -and (-not [Console]::IsInputRedirected)

    $configDir = Join-Path $env:USERPROFILE ".novabot"
    $venvDir = Join-Path $ProjectDir ".venv"
    $BinDir = Expand-Home $BinDir
    $launcher = Join-Path $BinDir "$CommandName.cmd"

    $removeLauncher = $true
    $deleted = @()
    $kept = @()

    if ($script:Interactive) {
        Write-Host "NovaBot uninstall"
        Write-Host ""
        Write-Host "Will delete:"
        Write-Host "  Launcher  $launcher"
        Write-Host "Will keep:"
        Write-Host "  Config/data  $configDir"
        Write-Host "  Virtualenv   $venvDir"
        Write-Host ""

        if (-not (Ask-YesNo "Remove launcher?" "y")) { $removeLauncher = $false }
        if (-not $Purge -and (Ask-YesNo "Also remove $configDir (backup first)?" "n")) {
            $Purge = $true
        }
        if (-not $RemoveVenv -and (Ask-YesNo "Also remove the project .venv?" "n")) {
            $RemoveVenv = $true
        }
        Write-Host ""
    }

    if ($removeLauncher) {
        if (Test-Path -LiteralPath $launcher) {
            Remove-Item -LiteralPath $launcher -Force
            Write-Host "Removed launcher: $launcher"
            $deleted += "Launcher $launcher"
        } else {
            Write-Host "Launcher not found, skipped: $launcher"
        }
    } else {
        Write-Host "Kept launcher: $launcher"
        $kept += "Launcher $launcher"
    }

    # 安装时写入的是解析后的绝对路径；这里也解析一次再比，避免大小写/相对路径差异
    $resolvedBin = $BinDir
    if (Test-Path -LiteralPath $BinDir) {
        $resolvedBin = (Resolve-Path -LiteralPath $BinDir).Path
    }
    $targets = @($BinDir, $resolvedBin)

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath) {
        $parts = @($userPath -split ";" | Where-Object { $_ })
        $keptParts = @($parts | Where-Object { -not (Test-PathEntryMatch $_ $targets) })
        if ($keptParts.Count -ne $parts.Count) {
            [Environment]::SetEnvironmentVariable("Path", ($keptParts -join ";"), "User")
            Write-Host "Removed from user PATH: $resolvedBin"
            $deleted += "PATH entry $resolvedBin"
        } else {
            Write-Host "User PATH does not contain the install directory, skipped"
        }
    } else {
        Write-Host "User PATH is empty, skipped"
    }

    if ($env:Path) {
        $currentParts = @($env:Path -split ";" | Where-Object { $_ })
        $keptCurrent = @($currentParts | Where-Object { -not (Test-PathEntryMatch $_ $targets) })
        if ($keptCurrent.Count -ne $currentParts.Count) {
            $env:Path = ($keptCurrent -join ";")
        }
    }

    if ($Purge) {
        if (Test-Path -LiteralPath $configDir) {
            $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
            $backup = "$configDir.bak-$stamp"
            if (Test-Path -LiteralPath $backup) {
                $backup = "$backup-$PID"
            }
            Move-Item -LiteralPath $configDir -Destination $backup
            Write-Host "Backed up and removed data directory: $configDir -> $backup"
            $deleted += "Data directory $configDir (backup $backup)"
        } else {
            Write-Host "Data directory not found, skipped: $configDir"
        }
    } else {
        Write-Host "Kept data directory: $configDir"
        $kept += "Data directory $configDir"
    }

    if ($RemoveVenv) {
        if (Test-Path -LiteralPath $venvDir) {
            Remove-Item -LiteralPath $venvDir -Recurse -Force
            Write-Host "Removed virtual environment: $venvDir"
            $deleted += "Virtualenv $venvDir"
        } else {
            Write-Host "Project virtualenv not found, skipped: $venvDir"
        }
    } else {
        Write-Host "Kept virtual environment: $venvDir"
        $kept += "Virtualenv $venvDir"
    }

    Write-Host ""
    Write-Host "Uninstall complete"
    if ($deleted.Count -gt 0) {
        Write-Host "Deleted:"
        foreach ($item in $deleted) { Write-Host "  $item" }
    } else {
        Write-Host "Nothing was deleted"
    }
    if ($kept.Count -gt 0) {
        Write-Host "Kept:"
        foreach ($item in $kept) { Write-Host "  $item" }
    }
    Write-Host ""
    Write-Host "Note: config and data are only removed with -Purge; external virtual environments are never touched."
}

try {
    Main
} catch {
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
