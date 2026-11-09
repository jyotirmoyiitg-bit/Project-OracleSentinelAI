#requires -Version 5.1
<#
.SYNOPSIS
Prepares and launches LIVE Oracle monitoring with Docker PostgreSQL.
.EXAMPLE
.\test_real_phase2\Verify_Phase2_Real.ps1 -Interactive
.EXAMPLE
.\test_real_phase2\Verify_Phase2_Real.ps1 -TnsAlias YOUR_ALIAS -InstallDependencies -Interactive
.EXAMPLE
.\test_real_phase2\Verify_Phase2_Real.ps1 -CheckPrerequisites
.DESCRIPTION
Run from the project root OR this script's directory. Root .env supplies Oracle
credentials and SECRET_KEY; this folder's .env overrides live-mode/PostgreSQL
settings. Root .env is not modified and passwords are never copied or printed.

This is NOT the synthetic verifier. It starts Docker PostgreSQL, upgrades the
configured application schema, registers a selected real TNS alias if absent,
and polls the registered enabled fleet. Oracle probes are read-only, but target
registration/migrations write to PostgreSQL. Back up existing application data.

Interactive mode opens the portal and waits for Enter. Finally stops only the
backend/frontend PIDs created here. PostgreSQL container, targets and measurements
remain persistent. No databases, volumes or targets are deleted.
#>
[CmdletBinding()]
param(
    [string]$TnsAlias,
    [switch]$Interactive,
    [switch]$InstallDependencies,
    [switch]$CheckPrerequisites,
    [ValidateRange(1, 65535)]
    [int]$FrontendPort = 5173,
    [ValidateRange(30, 1800)]
    [int]$CollectionWaitSeconds = 180
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Runtime = Join-Path $PSScriptRoot 'real_runtime.py'
$Frontend = Join-Path $Root 'frontend'
$Backend = $null
$Dashboard = $null
$Logs = $null
$BaseUrl = 'http://127.0.0.1:8000'

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $Executable" }
}

function Assert-PortFree {
    param([int]$Port)
    $Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try { $Listener.Start() }
    catch { throw "Port $Port is occupied. Stop the existing service first; this script will not kill it." }
    finally { $Listener.Stop() }
}

function Stop-OwnedProcess {
    param([System.Diagnostics.Process]$Process)
    if ($null -ne $Process) {
        $Process.Refresh()
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id
            $Process.WaitForExit()
        }
    }
}

function Test-DockerReady {
    # Windows PowerShell 5.1 can treat native stderr as an ErrorRecord even when
    # redirected. Inspect Docker's exit code during this expected readiness probe.
    $ErrorActionPreference = 'Continue'
    & $Docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Wait-Http {
    param([string]$Url, [System.Diagnostics.Process]$Process)
    $Deadline = (Get-Date).AddSeconds(60)
    do {
        $Process.Refresh()
        if ($Process.HasExited) { throw "Service exited before readiness. Inspect local logs: $Logs" }
        try {
            $Response = Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec 2
            if ($Response.StatusCode -eq 200) { return }
        } catch [System.Net.WebException] {
            Write-Verbose "Waiting for $Url"
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $Deadline)
    throw "Readiness timed out for $Url. Inspect local logs: $Logs"
}

Push-Location $Root
try {
    Write-Host "`n1. Checking tools and local files" -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath $Python)) { throw 'Create the project .venv with Python 3.14+ first.' }
    foreach ($File in @((Join-Path $Root '.env'), (Join-Path $PSScriptRoot '.env'), $Runtime)) {
        if (-not (Test-Path -LiteralPath $File)) { throw "Missing required file: $File" }
    }
    $Node = (Get-Command node.exe -ErrorAction Stop).Source
    $Npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    $Docker = (Get-Command docker.exe -ErrorAction Stop).Source
    Invoke-Checked $Python @('-c', 'import sys; assert sys.version_info >= (3,14)')
    Invoke-Checked $Node @('--version')
    Invoke-Checked $Docker @('compose', 'version')
    if ($FrontendPort -eq 8000) { throw 'FrontendPort must differ from backend port 8000.' }
    if ($CheckPrerequisites) {
        Write-Host 'Prerequisites passed. No .env contents loaded, services started, migrations applied or Oracle connections made.'
        return
    }
    Assert-PortFree 8000
    Assert-PortFree $FrontendPort
    if ($InstallDependencies) {
        Invoke-Checked $Python @('-m', 'pip', 'install', '-e', '.\backend[dev]')
        Push-Location $Frontend
        try { Invoke-Checked $Npm @('ci') } finally { Pop-Location }
    }
    $Vite = Join-Path $Frontend 'node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $Vite)) { throw 'Run again with -InstallDependencies to prepare dependencies.' }

    Write-Host "`n2. Loading live configuration and selecting a TNS alias" -ForegroundColor Cyan
    # Python's dotenv/settings loader correctly handles quoting/encrypted values.
    # Never dot-source .env or interpret its contents as PowerShell commands.
    $PreflightOutput = & $Python $Runtime preflight
    if ($LASTEXITCODE -ne 0) { throw 'Live configuration preflight failed. See the safe error above.' }
    $Aliases = @(($PreflightOutput | ConvertFrom-Json).aliases)
    Write-Host ('Available TNS aliases: ' + ($Aliases -join ', '))
    if ([string]::IsNullOrWhiteSpace($TnsAlias)) { $TnsAlias = Read-Host 'Enter the TNS alias to register/test' }
    $Matched = @($Aliases | Where-Object { $_ -ieq $TnsAlias })
    if ($Matched.Count -ne 1) { throw 'Choose exactly one alias listed by the configured tnsnames.ora.' }
    $TnsAlias = $Matched[0]

    Write-Host "`n3. Starting Docker PostgreSQL" -ForegroundColor Cyan
    if (-not (Test-DockerReady)) { Invoke-Checked $Docker @('desktop', 'start') }
    $Deadline = (Get-Date).AddSeconds(120)
    do {
        $DockerReady = Test-DockerReady
        if ($DockerReady) { break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $Deadline)
    if (-not $DockerReady) { throw 'Docker engine is not ready. Start Docker Desktop with Linux containers.' }
    # Empty env-file stops Compose from parsing unrelated project .env credentials.
    # The existing compose service defines sentinel and its persistent volume.
    Invoke-Checked $Docker @('compose', '--env-file', 'NUL', '-f', 'docker-compose.yml', 'up', '-d', '--wait', '--wait-timeout', '120', 'postgres')
    Invoke-Checked $Docker @('exec', 'sentinel-postgres', 'pg_isready', '-U', 'postgres', '-d', 'sentinel')

    Write-Host "`n4. Applying application migrations (no automatic baseline stamping)" -ForegroundColor Cyan
    Invoke-Checked $Python @($Runtime, 'migrate')
    $Logs = Join-Path ([System.IO.Path]::GetTempPath()) ('OracleSentinel-Real-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Logs | Out-Null
    Write-Host "Local service logs: $Logs"
    $Backend = Start-Process -FilePath $Python -ArgumentList @(('"' + $Runtime + '"'), 'serve') `
        -WorkingDirectory $Root -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $Logs 'backend.stdout.log') `
        -RedirectStandardError (Join-Path $Logs 'backend.stderr.log')
    Wait-Http "$BaseUrl/health/ready" $Backend

    Write-Host "`n5. Registering and testing the real Oracle target" -ForegroundColor Cyan
    # Reuse an existing target with the same alias; never erase existing targets.
    $Targets = @()
    $Page = 1
    do {
        $Fleet = Invoke-RestMethod "$BaseUrl/api/v1/databases?page=$Page&page_size=100"
        $Targets += @($Fleet.items | Where-Object { $_.tns_alias -ieq $TnsAlias })
        $Page++
    } while (($Page - 1) * 100 -lt $Fleet.total)
    if ($Targets.Count -gt 1) { throw 'Multiple targets have this TNS alias. Resolve duplicates through /docs first.' }
    if ($Targets.Count -eq 0) {
        $Body = @{display_name="Local Oracle - $TnsAlias"; tns_alias=$TnsAlias; environment='dev'} | ConvertTo-Json
        $Target = Invoke-RestMethod "$BaseUrl/api/v1/databases" -Method Post -ContentType 'application/json' -Body $Body
    } else { $Target = $Targets[0] }
    if (-not $Target.enabled) { throw 'This target is disabled. Enable it explicitly through /docs before live testing.' }
    $Connection = Invoke-RestMethod "$BaseUrl/api/v1/databases/$($Target.id)/test-connection" -Method Post -TimeoutSec 180
    if ($Connection.status -ne 'connected') {
        throw 'Oracle connection failed. Check TNS, listener, credentials and read-only grants; inspect /docs locally.'
    }
    Write-Host '[PASS] Real Oracle connection established' -ForegroundColor Green
    Write-Host 'Waiting for initial collection. Status may remain unknown when grants are missing.'
    $Deadline = (Get-Date).AddSeconds($CollectionWaitSeconds)
    do {
        $Detail = Invoke-RestMethod "$BaseUrl/api/v1/databases/$($Target.id)"
        $Pending = @($Detail.poll_status | Where-Object { $_.status -in @('pending', 'running') })
        if ($Pending.Count -eq 0) { break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $Deadline)
    $Detail.poll_status | Format-Table probe_type,status,last_success_at,error_class -AutoSize
    Write-Host "Measured target health: $($Detail.last_health_status)"
    $Failures = @($Detail.poll_status | Where-Object { $_.status -ne 'success' })
    if ($Failures.Count -gt 0) {
        Write-Warning 'Some probes did not succeed. Live validation is INCOMPLETE; inspect Probe activity and Oracle grants.'
    } else {
        Write-Host '[PASS] All six real collection probes succeeded' -ForegroundColor Green
    }

    Write-Host "`n6. Launching the portal" -ForegroundColor Cyan
    $Dashboard = Start-Process -FilePath $Node `
        -ArgumentList @(('"' + $Vite + '"'), '--host', '127.0.0.1', '--port', "$FrontendPort", '--strictPort') `
        -WorkingDirectory $Frontend -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $Logs 'frontend.stdout.log') `
        -RedirectStandardError (Join-Path $Logs 'frontend.stderr.log')
    $Portal = "http://127.0.0.1:$FrontendPort"
    Wait-Http $Portal $Dashboard
    $Ready = Invoke-RestMethod "$Portal/health/ready"
    if ($Ready.status -ne 'ready') { throw 'Portal proxy cannot reach the ready backend.' }
    Write-Host "`nPortal: $Portal/#/databases/$($Target.id)" -ForegroundColor Green
    Write-Host "API documentation: $BaseUrl/docs"
    Write-Host 'Check real timestamps, tablespaces, FRA, archive mode and timeline charts.'
    Write-Warning 'This is a local pre-authentication development app. Do not expose it publicly.'
    if ($Interactive) {
        Start-Process "$Portal/#/databases/$($Target.id)"
        Read-Host 'Press Enter when finished to stop the portal/backend (PostgreSQL and data stay)' | Out-Null
    } else {
        Write-Host 'Use -Interactive to keep the portal open; smoke-check services will now stop.'
    }
}
finally {
    try { Stop-OwnedProcess $Dashboard }
    finally {
        try { Stop-OwnedProcess $Backend }
        finally { Pop-Location }
    }
}
