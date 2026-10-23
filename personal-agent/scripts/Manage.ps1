param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('Start','Stop','Restart','Status','OpenBrowser','InstallStartup','RemoveStartup','Doctor')]
    [string]$Action
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskDeploymentFile = Join-Path $taskRoot 'deployment.json'
if (-not (Test-Path -LiteralPath $taskDeploymentFile)) { throw 'Run scripts/Setup.ps1 -Install first.' }
$taskDeployment = Get-Content -LiteralPath $taskDeploymentFile -Raw | ConvertFrom-Json
$env:HERMES_HOME = $taskDeployment.home
$env:HERMES_NONINTERACTIVE = '1'
$env:PYTHONUTF8 = '1'
$env:HERMES_GATEWAY_INSTALL_START_ON_LOGIN = '0'
$env:HERMES_GATEWAY_INSTALL_START_NOW = '0'
Push-Location $taskRoot
try {
    switch ($Action) {
        'Start' { & uv run python scripts/runtime.py --script scripts/start_gateway.py }
        'Stop' { & uv run python scripts/runtime.py -- gateway stop }
        'Restart' {
            & uv run python scripts/runtime.py -- gateway stop
            if ($LASTEXITCODE -ne 0) { throw 'Gateway stop failed' }
            & uv run python scripts/runtime.py --script scripts/start_gateway.py
        }
        'Status' { & uv run python scripts/runtime.py -- gateway status }
        'InstallStartup' {
            $env:HERMES_GATEWAY_INSTALL_START_ON_LOGIN = '1'
            & uv run python scripts/runtime.py -- gateway install
        }
        'RemoveStartup' { & uv run python scripts/runtime.py -- gateway uninstall }
        'Doctor' { & uv run python scripts/runtime.py -- doctor }
        'OpenBrowser' { & uv run python scripts/runtime.py --script scripts/browser.py -- --show }
    }
    if ($LASTEXITCODE -ne 0) { throw "Mika $Action failed (exit $LASTEXITCODE). Inspect the private runtime logs." }
} finally { Pop-Location }
