#requires -Version 5.1
<#
.SYNOPSIS
Prepares and launches LIVE Oracle compliance, forecasting, and non-compliance
notification testing.
.EXAMPLE
.\test_real_phase4\Verify_Phase4_Real.ps1 -Interactive
.EXAMPLE
.\test_real_phase4\Verify_Phase4_Real.ps1 -TnsAlias YOUR_ALIAS -InstallDependencies -Interactive
.EXAMPLE
.\test_real_phase4\Verify_Phase4_Real.ps1 -CheckPrerequisites
.DESCRIPTION
Run from the project root OR this script's directory. Root .env supplies Oracle
credentials and SECRET_KEY; this folder's .env overrides live-mode/PostgreSQL/alert
settings. Root .env is not modified and passwords are never copied or printed.

This builds on test_real_phase2/test_real_phase3: it starts Docker PostgreSQL, upgrades
the configured application schema (including the Phase 4 compliance/forecast tables),
reuses or registers the selected real TNS alias, and confirms the real Oracle connection
and collection probes. It then reads the REAL compliance evaluation and capacity forecast
produced from the live collected metrics, and exercises the Phase 4 behavior requested in
the specification -- "a non-compliant database raises a notification rather than a hard
stop" -- by calling the real notification pipeline with one synthetic, uniquely named
policy result (forcing a genuine non-compliant Oracle configuration, such as disabling
ARCHIVELOG, on demand is unsafe). This verifies: a non-compliant evaluation opens an
incident and a persistent portal notification without raising; repeating the same failure
does not duplicate either; the incident auto-resolves once the policy reports compliant;
and a later failure opens a fresh incident instead of reopening the resolved one. Oracle
probes remain read-only; schema/incident/notification writes go to PostgreSQL. Back up
existing application data.

Interactive mode opens the portal (database detail, with the Compliance/Forecast panels)
and waits for Enter. Finally stops only the backend/frontend PIDs created here. PostgreSQL
container, targets, incidents and notifications remain persistent. No databases, volumes,
targets or incidents are deleted.

Each invocation uses a separate synthetic policy id so reruns do not depend on leftover
state from a previous run. Real catalog policies (policies/compliance.yaml) and their
production incidents/cooldowns are never touched by the synthetic check. Test incidents
and notifications remain for audit.
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

function Invoke-SimulateNoncompliance {
    param([string]$TnsAlias, [string]$Result, [string]$RunId)
    $Output = & $Python $Runtime simulate-noncompliance '--alias' $TnsAlias '--result' $Result '--run-id' $RunId
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic non-compliance injection failed. See the safe error above.' }
    return ($Output | ConvertFrom-Json)
}

Push-Location $Root
try {
    Write-Host "`n1. Checking tools and local files" -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath $Python)) { throw 'Create the project .venv with Python 3.14+ first.' }
    foreach ($File in @(
        (Join-Path $Root '.env'), (Join-Path $PSScriptRoot '.env'), $Runtime,
        (Join-Path $Root 'policies\compliance.yaml'), (Join-Path $Root 'policies\forecasting.yaml')
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

    Write-Host "`n4. Applying application migrations (including Phase 4 compliance/forecast tables)" -ForegroundColor Cyan
    Invoke-Checked $Python @($Runtime, 'migrate')
    $Logs = Join-Path ([System.IO.Path]::GetTempPath()) ('OracleSentinel-RealPhase4-' + [guid]::NewGuid().ToString('N'))
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
    Write-Host 'Waiting for initial collection. Compliance/forecast depend on instance/configuration/fra_archive probes.'
    $Deadline = (Get-Date).AddSeconds($CollectionWaitSeconds)
    do {
        $Detail = Invoke-RestMethod "$BaseUrl/api/v1/databases/$($Target.id)"
        $Pending = @($Detail.poll_status | Where-Object { $_.status -in @('pending', 'running') })
        if ($Pending.Count -eq 0) { break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $Deadline)
    $Detail.poll_status | Format-Table probe_type,status,last_success_at,error_class -AutoSize
    Write-Host "Measured target health: $($Detail.last_health_status)"

    Write-Host "`n6. Verifying real compliance evaluation" -ForegroundColor Cyan
    $Compliance = Invoke-RestMethod "$BaseUrl/api/v1/databases/$($Target.id)/compliance"
    if ($Compliance.items.Count -eq 0) { throw 'No compliance policies were evaluated. Check policies/compliance.yaml and probe status above.' }
    $Compliance.items | Format-Table policy_id,result,severity,observed_at -AutoSize
    Write-Host "[PASS] $($Compliance.items.Count) policies evaluated (pass=$($Compliance.pass) fail=$($Compliance.fail) unknown=$($Compliance.unknown) not_applicable=$($Compliance.not_applicable) error=$($Compliance.error))" -ForegroundColor Green
    if ($Compliance.error -gt 0) { Write-Warning 'One or more policies evaluated to "error". Inspect evidence via /docs.' }

    Write-Host "`n7. Verifying real capacity forecast" -ForegroundColor Cyan
    $Forecast = Invoke-RestMethod "$BaseUrl/api/v1/databases/$($Target.id)/forecast"
    $Forecast.items | Format-Table resource,status,days_to_exhaustion,early_warning -AutoSize
    if ($Forecast.items.Count -eq 0) {
        Write-Host 'No forecastable tablespace/FRA resources were found yet.'
    } elseif (@($Forecast.items | Where-Object { $_.status -ne 'unknown' }).Count -eq 0) {
        Write-Host 'All forecasts are "unknown" -- expected on a freshly registered target (needs >=5 points spanning 24h). Rerun this script later, or after a longer collection period, to see a populated forecast.'
    } else {
        Write-Host '[PASS] At least one resource produced a populated growth forecast' -ForegroundColor Green
    }

    Write-Host "`n8. Verifying a non-compliant database raises a notification, not a hard stop" -ForegroundColor Cyan
    $VerificationRunId = [guid]::NewGuid().ToString()
    $First = Invoke-SimulateNoncompliance -TnsAlias $TnsAlias -Result 'fail' -RunId $VerificationRunId
    if ($First.incident_state -ne 'new') { throw 'Synthetic non-compliance did not open a new incident.' }
    Write-Host "[PASS] Non-compliant policy opened incident $($First.incident_id) without raising" -ForegroundColor Green
    $Second = Invoke-SimulateNoncompliance -TnsAlias $TnsAlias -Result 'fail' -RunId $VerificationRunId
    if ($Second.incident_id -ne $First.incident_id) { throw 'Repeated failure created a duplicate incident instead of deduplicating.' }
    Write-Host '[PASS] Repeated failure deduplicated onto the same incident' -ForegroundColor Green
    $Notifications = Invoke-RestMethod "$BaseUrl/api/v1/notifications?incident_id=$($First.incident_id)"
    $WebNotifications = @($Notifications | Where-Object { $_.channel -eq 'web' })
    if ($WebNotifications.Count -ne 1) { throw 'Expected exactly one persistent portal notification for the incident.' }
    Write-Host '[PASS] One persistent portal notification recorded (no duplicate on repeated failure)' -ForegroundColor Green
    $EmailNotifications = @($Notifications | Where-Object { $_.channel -eq 'smtp' })
    if ($EmailNotifications.Count -eq 0) {
        Write-Host 'No email attempt recorded. SMTP is disabled by default (SMTP_ENABLED=false); portal alerts remain enabled.'
    } else {
        $SentCount = @($EmailNotifications | Where-Object { $_.status -eq 'sent' }).Count
        if ($SentCount -gt 0) { Write-Host "[PASS] $SentCount notification(s) sent via SMTP" -ForegroundColor Green }
        else { Write-Warning 'SMTP was enabled but delivery failed. Inspect the recorded email error_class and relay settings; the portal alert remains available.' }
    }
    $Incident = Invoke-RestMethod "$BaseUrl/api/v1/incidents/$($First.incident_id)"
    Write-Host "Incident detail: severity=$($Incident.severity) state=$($Incident.state) message=$($Incident.message)"

    Write-Host "`n9. Verifying the incident auto-resolves once the policy reports compliant" -ForegroundColor Cyan
    $Passing = Invoke-SimulateNoncompliance -TnsAlias $TnsAlias -Result 'pass' -RunId $VerificationRunId
    if ($Passing.incident_state -ne 'resolved') { throw 'Incident did not auto-resolve when the policy reported compliant.' }
    Write-Host "[PASS] Incident $($Passing.incident_id) auto-resolved on compliant re-evaluation" -ForegroundColor Green

    Write-Host "`n10. Verifying a later failure opens a fresh incident rather than reopening" -ForegroundColor Cyan
    $Reopened = Invoke-SimulateNoncompliance -TnsAlias $TnsAlias -Result 'fail' -RunId $VerificationRunId
    if ($Reopened.incident_id -eq $First.incident_id) { throw 'A resolved incident was reopened instead of creating a fresh one.' }
    if ($Reopened.incident_state -ne 'new') { throw 'Fresh incident after resolution was not created in the "new" state.' }
    Write-Host "[PASS] Fresh incident $($Reopened.incident_id) opened after prior resolution (never reopens a resolved incident)" -ForegroundColor Green

    Write-Host "`n11. Launching the portal" -ForegroundColor Cyan
    $Dashboard = Start-Process -FilePath $Node `
        -ArgumentList @(('"' + $Vite + '"'), '--host', '127.0.0.1', '--port', "$FrontendPort", '--strictPort') `
        -WorkingDirectory $Frontend -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $Logs 'frontend.stdout.log') `
        -RedirectStandardError (Join-Path $Logs 'frontend.stderr.log')
    $Portal = "http://127.0.0.1:$FrontendPort"
    Wait-Http $Portal $Dashboard
    $Ready = Invoke-RestMethod "$Portal/health/ready"
    if ($Ready.status -ne 'ready') { throw 'Portal proxy cannot reach the ready backend.' }
    Write-Host "`nPortal (database detail, Compliance/Forecast panels): $Portal/#/databases/$($Target.id)" -ForegroundColor Green
    Write-Host "Portal (Incident Center, synthetic incident is resolved):  $Portal/#/incidents"
    Write-Host "API documentation: $BaseUrl/docs"
    Write-Host 'Open the notification bell for the portal alert, then inspect the Compliance and Forecast panels and the resolved/reopened synthetic incidents.'
    Write-Warning 'This is a local pre-authentication development app. Do not expose it publicly.'
    if ($Interactive) {
        Start-Process "$Portal/#/databases/$($Target.id)"
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
