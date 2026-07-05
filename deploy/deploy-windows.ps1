<#
.SYNOPSIS
  Deploy resume-reviewer as a persistent Windows Service.

.DESCRIPTION
  - Installs Python 3.12 via winget (skipped if already installed).
  - Clones the repo into C:\services\resume-reviewer (or pulls if exists).
  - Creates venv + installs requirements.
  - Downloads NSSM (Non-Sucking Service Manager).
  - Registers the bot as a Windows Service with auto-restart on crash.
  - Starts the service.

.NOTES
  Run as Administrator:
    Set-ExecutionPolicy Bypass -Scope Process -Force
    .\deploy-windows.ps1

  After first run, edit C:\services\resume-reviewer\.env to add DISCORD_BOT_TOKEN,
  REVIEW_CHANNEL_ID, etc., then:
    Restart-Service ResumeReviewer
#>

$ErrorActionPreference = 'Stop'

$InstallDir = 'C:\services\resume-reviewer'
$RepoUrl = 'https://github.com/andrianthan/resume-reviewer.git'
$NssmDir = 'C:\tools\nssm'
$NssmExe = Join-Path $NssmDir 'win64\nssm.exe'
$ServiceName = 'ResumeReviewer'
$PythonExe = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) { $PythonExe = (Get-Command python3.exe -ErrorAction SilentlyContinue).Source }

function Write-Step($msg) { Write-Host "[deploy] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[ok]     $msg" -ForegroundColor Green }
function Write-Err($msg)  { Write-Host "[error]  $msg" -ForegroundColor Red }

# Sanity: admin
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Err "Must run as Administrator."
    exit 1
}

# 1. Python
if (-not $PythonExe) {
    Write-Step "Installing Python 3.12 via winget..."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Err "winget not available. Install 'App Installer' from MS Store or grab python.org installer manually."
        exit 1
    }
    winget install --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path', 'User')
    $PythonExe = (Get-Command python.exe).Source
}
Write-Ok "Python: $PythonExe"

# 2. Clone repo
if (-not (Test-Path $InstallDir)) {
    Write-Step "Cloning repo to $InstallDir..."
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    git clone $RepoUrl $InstallDir
} else {
    Write-Step "Repo exists, pulling latest..."
    Push-Location $InstallDir
    git pull
    Pop-Location
}
Write-Ok "Repo at $InstallDir"

# 3. Venv
$VenvPython = Join-Path $InstallDir '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    Write-Step "Creating venv..."
    & $PythonExe -m venv (Join-Path $InstallDir '.venv')
}
Write-Ok "Venv: $VenvPython"

# 4. Install deps
Write-Step "Installing requirements..."
& $VenvPython -m pip install --upgrade pip | Out-Null
& $VenvPython -m pip install -r (Join-Path $InstallDir 'requirements.txt')
Write-Ok "Deps installed"

# 5. NSSM
if (-not (Test-Path $NssmExe)) {
    Write-Step "Downloading NSSM..."
    $NssmZip = Join-Path $env:TEMP 'nssm.zip'
    Invoke-WebRequest -Uri 'https://nssm.cc/release/nssm-2.24.zip' -OutFile $NssmZip -UseBasicParsing
    Expand-Archive -Path $NssmZip -DestinationPath $NssmDir -Force
}
Write-Ok "NSSM: $NssmExe"

# 6. .env (only if missing)
$EnvPath = Join-Path $InstallDir '.env'
if (-not (Test-Path $EnvPath)) {
    Copy-Item (Join-Path $InstallDir '.env.example') $EnvPath
    Write-Step "Created .env — edit with your tokens before service start."
    Write-Step "  notepad $EnvPath"
}

# 7. Register service
Write-Step "Registering Windows Service '$ServiceName'..."
& $NssmExe install $ServiceName $VenvPython '-m src.bot' | Out-Null
& $NssmExe set $ServiceName AppDirectory $InstallDir | Out-Null
& $NssmExe set $ServiceName AppStdout (Join-Path $InstallDir 'logs\service.out.log') | Out-Null
& $NssmExe set $ServiceName AppStderr (Join-Path $InstallDir 'logs\service.err.log') | Out-Null
& $NssmExe set $ServiceName AppRotateFiles 1 | Out-Null
& $NssmExe set $ServiceName AppRotateBytes 10485760 | Out-Null  # 10MB
& $NssmExe set $ServiceName Start SERVICE_AUTO_START | Out-Null
& $NssmExe set $ServiceName AppRestartDelay 5000 | Out-Null
& $NssmExe set $ServiceName AppExitCodes Default 1 | Out-Null

# Pass env vars to service (read from .env file)
$envVars = @{}
Get-Content $EnvPath | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
        $key = $Matches[1].Trim()
        $val = $Matches[2].Trim()
        if ($val) { $envVars[$key] = $val }
    }
}
foreach ($k in $envVars.Keys) {
    & $NssmExe set $ServiceName AppEnvironmentExtra "${k}=${envVars[$k]}" | Out-Null
}

New-Item -ItemType Directory -Path (Join-Path $InstallDir 'logs') -Force | Out-Null

# 8. Start
Write-Step "Starting service..."
& $NssmExe start $ServiceName
Start-Sleep -Seconds 3
$svc = Get-Service $ServiceName
Write-Ok "Service '$ServiceName' status: $($svc.Status)"

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Edit $EnvPath with your DISCORD_BOT_TOKEN + REVIEW_CHANNEL_ID"
Write-Host "  2. Restart-Service $ServiceName"
Write-Host "  3. Tail logs: Get-Content (Join-Path $InstallDir 'logs\service.out.log') -Wait"
Write-Host "  4. Uninstall: & $NssmExe stop $ServiceName ; & $NssmExe remove $ServiceName confirm"
