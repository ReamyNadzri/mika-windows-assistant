param(
    [string]$HermesHome = (Join-Path $env:LOCALAPPDATA 'HermesPersonal'),
    [string]$EnvFile,
    [switch]$Install
)
$ErrorActionPreference = 'Stop'
$taskProject = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$taskSource = Join-Path $taskProject 'personal-agent'
if (-not $EnvFile) { $EnvFile = Join-Path $taskProject '.env' }
$EnvFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($EnvFile)
$HermesHome = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($HermesHome)
# Check user-supplied settings before downloading or writing runtime files.
Push-Location $taskSource
try {
    & uv run python scripts/configure.py --home $HermesHome --env-file $EnvFile --validate-only
    if ($LASTEXITCODE -ne 0) { throw 'Private environment settings are invalid' }
} finally {
    Pop-Location
}
if ($Install) {
    New-Item -ItemType Directory -Path $HermesHome -Force | Out-Null
    $taskInstaller = Join-Path $HermesHome 'install-v0.22.2.ps1'
    Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/NousResearch/hermes-agent/v0.22.2/scripts/install.ps1' -OutFile $taskInstaller
    & $taskInstaller -Branch v0.22.2 -Commit d923a5eb487a17c0a68801bba9ef0f56c81040a8 -HermesHome $HermesHome -InstallDir (Join-Path $HermesHome 'hermes-agent') -NonInteractive
    if ($LASTEXITCODE -ne 0) { throw 'Pinned official installer failed' }
}
Push-Location $taskSource
try {
    & uv run python scripts/configure.py --home $HermesHome --env-file $EnvFile
    if ($LASTEXITCODE -ne 0) { throw 'Local configuration failed' }
    $taskDeployment = Get-Content -LiteralPath (Join-Path $taskSource 'deployment.json') -Raw | ConvertFrom-Json
    # Only this Windows user, SYSTEM, and administrators can read runtime secrets.
    $taskIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $taskDeployment.home /inheritance:r /grant:r "${taskIdentity}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict the private runtime directory ACL' }
    & uv run python scripts/runtime.py --script scripts/prepare_dependencies.py
    if ($LASTEXITCODE -ne 0) { throw 'Managed dependency preparation failed' }
    & uv run python scripts/runtime.py -- config check *> (Join-Path $taskDeployment.home 'logs\config-check.log')
    if ($LASTEXITCODE -ne 0) { throw 'Hermes configuration validation failed' }
    Write-Output 'Configuration validated. See the private logs/config-check.log for optional settings.'
} finally {
    Pop-Location
}
