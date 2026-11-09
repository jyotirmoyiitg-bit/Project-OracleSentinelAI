#requires -Version 5.1
<#
.SYNOPSIS
Prepares and launches LIVE Oracle alert-log rules, incidents, and notification testing.
.EXAMPLE
.\test_real_phase3\Verify_Phase3_Real.ps1 -Interactive
.EXAMPLE
.\test_real_phase3\Verify_Phase3_Real.ps1 -TnsAlias YOUR_ALIAS -InstallDependencies -Interactive
.EXAMPLE
.\test_real_phase3\Verify_Phase3_Real.ps1 -CheckPrerequisites
.DESCRIPTION
Run from the project root OR this script's directory. Root .env supplies Oracle
credentials and SECRET_KEY; this folder's .env overrides live-mode/PostgreSQL/alert
settings. Root .env is not modified and passwords are never copied or printed.

This builds on test_real_phase2: it starts Docker PostgreSQL, upgrades the configured
application schema (including the Phase 3 rule/incident/notification tables), reuses
or registers the selected real TNS alias, and confirms the real Oracle connection and
collection probes exactly like Phase 2. It then exercises the Phase 3 pipeline by
injecting ONE synthetic ORA-00257 alert-log record (forcing a genuine Oracle archiver
failure on demand is unsafe) through the real rules engine using a uniquely named
copy of the configured YAML event rule, then through the incident lifecycle and
portal notifications, and optional SMTP notifier. Verifies a deduplicated incident
plus a persistent portal alert through the live REST API. Oracle probes remain read-only; schema/incident/
notification writes go to PostgreSQL. Back up existing application data.

Interactive mode opens the portal (Incident Center) and waits for Enter. Finally stops
only the backend/frontend PIDs created here. PostgreSQL container, targets, incidents
and notifications remain persistent. No databases, volumes, targets or incidents are
deleted.

Each invocation creates a separate verification rule/incident so reruns do not depend
on production cooldowns or old resolved incidents. Production rule state and operator
overrides are not reset or tested by this isolated synthetic check. Test events, rule
rows, incidents and notifications remain for audit; the temporary YAML file is removed.
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
    [int]$CollectionWaitSeconds = 180,
    [ValidateSet('ORA-00257', 'ORA-01653', 'ORA-04031')]
    [string]$SyntheticCode = 'ORA-00257'
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
    foreach ($File in @(
        (Join-Path $Root '.env'), (Join-Path $PSScriptRoot '.env'), $Runtime,
        (Join-Path $Root 'rules\default.yaml')
    )) {
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

    Write-Host "`n4. Applying application migrations (including Phase 3 rule/incident tables)" -ForegroundColor Cyan
    Invoke-Checked $Python @($Runtime, 'migrate')
    $Logs = Join-Path ([System.IO.Path]::GetTempPath()) ('OracleSentinel-RealPhase3-' + [guid]::NewGuid().ToString('N'))
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

    Write-Host "`n6. Injecting one synthetic $SyntheticCode alert and evaluating real rules" -ForegroundColor Cyan
    # Seeds one synthetic DiagnosticEvent and runs a single real AlertEvaluationRunner
    # Each invocation uses a test-only rule ID, leaving production cooldown/history
    # intact. Keep the same run ID across retries to preserve event deduplication.
    $SimulateDeadline = (Get-Date).AddSeconds(60)
    $RunOutcome = $null
    $VerificationRunId = [guid]::NewGuid().ToString()
    do {
        $SimulateOutput = & $Python $Runtime simulate-alert '--alias' $TnsAlias '--code' $SyntheticCode '--run-id' $VerificationRunId
        if ($LASTEXITCODE -ne 0) { throw 'Synthetic alert injection failed. See the safe error above.' }
        $Simulation = $SimulateOutput | ConvertFrom-Json
        $RunOutcome = $Simulation.run_outcome
        if ($RunOutcome -eq 'leased') { Start-Sleep -Seconds 3 } else { break }
    } while ((Get-Date) -lt $SimulateDeadline)
    if ($RunOutcome -ne 'success') { throw "Alert evaluation did not complete (outcome: $RunOutcome). Inspect backend logs: $Logs" }
    Write-Host '[PASS] Synthetic alert processed through the real rules/incident pipeline' -ForegroundColor Green

    Write-Host "`n7. Verifying one deduplicated incident and one portal notification" -ForegroundColor Cyan
    if (-not $Simulation.incident_id) { throw 'Synthetic evaluation returned no verification incident ID.' }
    $Incident = Invoke-RestMethod "$BaseUrl/api/v1/incidents/$($Simulation.incident_id)"
    if ($Incident.rule_id -ne $Simulation.rule_id -or $Incident.target_id -ne $Target.id -or $Incident.state -ne 'new') {
        throw 'Verification incident does not match this run, target, or initial state.'
    }
    Write-Host "[PASS] One deduplicated incident: $($Incident.id) (severity=$($Incident.severity), state=$($Incident.state))" -ForegroundColor Green
    $Notifications = Invoke-RestMethod "$BaseUrl/api/v1/notifications?incident_id=$($Incident.id)"
    $WebNotifications = @($Notifications | Where-Object { $_.channel -eq 'web' })
    if ($WebNotifications.Count -ne 1) { throw 'Expected exactly one persistent portal notification for the incident.' }
    if ($WebNotifications[0].status -notin @('unread', 'read')) { throw 'Portal notification has an invalid read status.' }
    $PortalPage = 1
    $PortalMatches = @()
    do {
        $PortalFeed = Invoke-RestMethod "$BaseUrl/api/v1/portal-notifications?page=$PortalPage&page_size=100"
        $PortalMatches += @($PortalFeed.items | Where-Object { $_.id -eq $WebNotifications[0].id })
        $PortalPage++
    } while ($PortalMatches.Count -eq 0 -and ($PortalPage - 1) * 100 -lt $PortalFeed.total)
    if ($PortalMatches.Count -ne 1) {
        throw 'Portal notification is missing from the notification bell feed.'
    }
    Write-Host '[PASS] Portal alert persisted and available in the notification bell' -ForegroundColor Green
    $Notifications | Format-Table recipient,status,attempt_count,error_class,sent_at -AutoSize
    $EmailNotifications = @($Notifications | Where-Object { $_.channel -eq 'smtp' })
    $SentCount = @($EmailNotifications | Where-Object { $_.status -eq 'sent' }).Count
    if ($EmailNotifications.Count -eq 0) {
        Write-Host 'No email attempt recorded. SMTP is disabled by default (SMTP_ENABLED=false); portal alerts remain enabled.'
    } elseif ($SentCount -gt 0) {
        Write-Host "[PASS] $SentCount notification(s) sent via SMTP" -ForegroundColor Green
    } else {
        Write-Warning 'SMTP was enabled but delivery failed. Inspect the recorded email error_class and relay settings; the portal alert remains available.'
    }

    Write-Host "`n8. Exercising the incident lifecycle (acknowledge -> resolve)" -ForegroundColor Cyan
    $Acknowledged = Invoke-RestMethod "$BaseUrl/api/v1/incidents/$($Incident.id)/acknowledge" -Method Post -ContentType 'application/json' -Body (@{actor='test_real_phase3'; reason='Live verification run'} | ConvertTo-Json)
    if ($Acknowledged.state -ne 'acknowledged') { throw 'Incident did not transition to acknowledged.' }
    $Resolved = Invoke-RestMethod "$BaseUrl/api/v1/incidents/$($Incident.id)/resolve" -Method Post -ContentType 'application/json' -Body (@{actor='test_real_phase3'; reason='Synthetic verification complete'} | ConvertTo-Json)
    if ($Resolved.state -ne 'resolved') { throw 'Incident did not transition to resolved.' }
    Write-Host '[PASS] Incident state machine: new -> acknowledged -> resolved' -ForegroundColor Green

    Write-Host "`n9. Launching the portal" -ForegroundColor Cyan
    $Dashboard = Start-Process -FilePath $Node `
        -ArgumentList @(('"' + $Vite + '"'), '--host', '127.0.0.1', '--port', "$FrontendPort", '--strictPort') `
        -WorkingDirectory $Frontend -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $Logs 'frontend.stdout.log') `
        -RedirectStandardError (Join-Path $Logs 'frontend.stderr.log')
    $Portal = "http://127.0.0.1:$FrontendPort"
    Wait-Http $Portal $Dashboard
    $Ready = Invoke-RestMethod "$Portal/health/ready"
    if ($Ready.status -ne 'ready') { throw 'Portal proxy cannot reach the ready backend.' }
    Write-Host "`nPortal (Incident Center): $Portal/#/incidents" -ForegroundColor Green
    Write-Host "Portal (database detail):  $Portal/#/databases/$($Target.id)"
    Write-Host "API documentation: $BaseUrl/docs"
    Write-Host 'Open the notification bell for the portal alert, then inspect the resolved incident, audit trail and Logs/Events page.'
    Write-Warning 'This is a local pre-authentication development app. Do not expose it publicly.'
    if ($Interactive) {
        Start-Process "$Portal/#/incidents"
        Read-Host 'Press Enter when finished to stop the portal/backend (PostgreSQL, incidents and notifications stay)' | Out-Null
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
