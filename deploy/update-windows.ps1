<#
.SYNOPSIS
  Update and restart the existing ResumeReviewer Windows service.

.DESCRIPTION
  Pulls the latest resume-reviewer code into C:\services\resume-reviewer,
  refreshes Python dependencies, restarts the NSSM Windows service, and prints
  the recent service log tail. Run this after pushing application changes.

.NOTES
  Run in an elevated PowerShell prompt on the Windows bot host:
    Set-ExecutionPolicy Bypass -Scope Process -Force
    C:\services\resume-reviewer\deploy\update-windows.ps1
#>

$ErrorActionPreference = 'Stop'

$InstallDir = 'C:\services\resume-reviewer'
$ServiceName = 'ResumeReviewer'
$VenvPython = Join-Path $InstallDir '.venv\Scripts\python.exe'
$OutLog = Join-Path $InstallDir 'logs\service.out.log'
$ErrLog = Join-Path $InstallDir 'logs\service.err.log'

function Write-Step($msg) { Write-Host "[update] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[ok]     $msg" -ForegroundColor Green }
function Write-Err($msg)  { Write-Host "[error]  $msg" -ForegroundColor Red }

if (-not (Test-Path $InstallDir)) {
    Write-Err "$InstallDir does not exist. Run deploy\deploy-windows.ps1 first."
    exit 1
}
if (-not (Test-Path $VenvPython)) {
    Write-Err "$VenvPython does not exist. Run deploy\deploy-windows.ps1 first."
    exit 1
}

Write-Step "Stopping $ServiceName if it is running..."
$svc = Get-Service $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -ne 'Stopped') {
    Stop-Service $ServiceName
    $svc.WaitForStatus('Stopped', '00:00:30')
}
Write-Ok "$ServiceName stopped"

Write-Step "Pulling latest code..."
Push-Location $InstallDir
git pull --ff-only
$commit = git rev-parse --short HEAD
Pop-Location
Write-Ok "Repo at $commit"

Write-Step "Installing requirements..."
& $VenvPython -m pip install -r (Join-Path $InstallDir 'requirements.txt')
Write-Ok "Deps current"

Write-Step "Starting $ServiceName..."
Start-Service $ServiceName
Start-Sleep -Seconds 3
$svc = Get-Service $ServiceName
Write-Ok "$ServiceName status: $($svc.Status)"

Write-Host ""
Write-Host "Recent stdout:" -ForegroundColor Yellow
if (Test-Path $OutLog) {
    Get-Content $OutLog -Tail 40
} else {
    Write-Host "(no stdout log yet)"
}

Write-Host ""
Write-Host "Recent stderr:" -ForegroundColor Yellow
if (Test-Path $ErrLog) {
    Get-Content $ErrLog -Tail 40
} else {
    Write-Host "(no stderr log yet)"
}

Write-Host ""
Write-Host "To watch logs live:" -ForegroundColor Yellow
Write-Host "  Get-Content $OutLog -Wait"
