#requires -Version 5.1
<#
.SYNOPSIS
Verifies Phase 2 against an isolated, credential-free synthetic fleet.
.EXAMPLE
.\Verify_Phase2.ps1
.EXAMPLE
.\Verify_Phase2.ps1 -Interactive
.EXAMPLE
.\Verify_Phase2.ps1 -InstallDependencies -Interactive
.DESCRIPTION
Requires the repository .venv (Python 3.14+) and Node/npm. Tests and builds run
by default. -Interactive keeps the dashboard available until you press Enter.
No live Oracle/PostgreSQL checks are performed. The real .env is not loaded or
changed. Temporary services and the disposable database are cleaned up on exit.
#>
[CmdletBinding()]
param(
    [switch]$InstallDependencies,
    [switch]$Interactive,
    [switch]$SkipTests,
    [ValidateRange(1, 65535)]
    [int]$FrontendPort = 5173
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Frontend = Join-Path $Root 'frontend'
$Backend = $null
$Dashboard = $null
$Work = $null
$Succeeded = $false
$SavedEnvironment = @{}
$BaseUrl = 'http://127.0.0.1:8000'
# Disable dotenv loading before importing the application or running a Python CLI.
$Bootstrap = "import runpy,sys; from sentinel.config.settings import Settings; Settings.model_config['env_file']=None; module=sys.argv[1]; sys.argv=sys.argv[1:]; runpy.run_module(module,run_name='__main__')"
$ServerCode = "import uvicorn; from sentinel.config.settings import Settings; Settings.model_config['env_file']=None; from sentinel.main import create_app; uvicorn.run(create_app(Settings(_env_file=None)),host='127.0.0.1',port=8000)"

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Executable $($Arguments -join ' ')"
    }
}

function Invoke-PythonModule {
    param([string]$Module, [string[]]$Arguments = @())
    Invoke-Checked $Python (@('-c', $Bootstrap, $Module) + $Arguments)
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "Verification failed: $Message" }
    Write-Host "[PASS] $Message" -ForegroundColor Green
}

function Assert-PortFree {
    param([int]$Port)
    $Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try { $Listener.Start() }
    catch { throw "Port $Port is occupied. Stop its existing service before running verification." }
    finally { $Listener.Stop() }
}

function Wait-Http {
    param([string]$Url, [System.Diagnostics.Process]$Process)
    $Deadline = (Get-Date).AddSeconds(45)
    do {
        $Process.Refresh()
        if ($Process.HasExited) { throw "Service exited before readiness: $Url. See logs in $Work" }
        try {
            $Response = Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec 2
            if ($Response.StatusCode -eq 200) { return }
        }
        catch [System.Net.WebException] {
            # A refused connection is expected only while this owned service starts.
            Write-Verbose "Waiting for $Url : $($_.Exception.Message)"
        }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $Deadline)
    throw "Readiness timed out: $Url. See logs in $Work"
}

function Start-Backend {
    param([string]$Label)
    $Process = Start-Process -FilePath $Python -ArgumentList @('-c', ('"' + $ServerCode + '"')) `
        -WorkingDirectory $Root -PassThru -NoNewWindow `
        -RedirectStandardOutput (Join-Path $Work "$Label.stdout.log") `
        -RedirectStandardError (Join-Path $Work "$Label.stderr.log")
    try { Wait-Http "$BaseUrl/health/ready" $Process }
    catch {
        Stop-OwnedProcess $Process
        throw
    }
    return $Process
}

function Stop-OwnedProcess {
    param([System.Diagnostics.Process]$Process)
    if ($null -ne $Process) {
        $Process.Refresh()
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id -ErrorAction Stop
            $Process.WaitForExit()
        }
    }
}

function Get-DatabaseState {
    $Code = "import json,os,sqlite3; c=sqlite3.connect(os.environ['PHASE2_VERIFY_DB']); print(json.dumps({'poll_runs':c.execute('select count(*) from poll_runs').fetchone()[0],'checkpoints':c.execute('select target_id,collector_type,lease_version,cursor from collector_checkpoints order by target_id,collector_type').fetchall()})); c.close()"
    $Output = & $Python -c $Code
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the isolated validation database' }
    return ($Output | ConvertFrom-Json)
}

Push-Location $Root
try {
    if (-not (Test-Path -LiteralPath $Python)) { throw "Missing $Python. Create the project virtual environment first." }
    $Node = (Get-Command node.exe -ErrorAction Stop).Source
    $Npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    Invoke-Checked $Python @('-c', 'import sys; assert sys.version_info >= (3,14), "Python 3.14+ required"')
    Invoke-Checked $Node @('--version')
    Assert-PortFree 8000
    if ($FrontendPort -eq 8000) { throw 'FrontendPort must differ from backend port 8000' }
    Assert-PortFree $FrontendPort

    $Work = Join-Path ([System.IO.Path]::GetTempPath()) ('OracleSentinel-Phase2-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Work | Out-Null
    $DatabaseFile = Join-Path $Work 'verification.sqlite'
    $Overrides = @{
        DATABASE_URL = ('sqlite:///' + $DatabaseFile.Replace('\', '/'))
        PHASE2_VERIFY_DB = $DatabaseFile
        SYNTHETIC_MODE = 'true'
        SCHEDULER_ENABLED = 'false'
        AI_ENABLED = 'false'
        ORACLE_PASSWORD = ''
        ORACLE_PASSWORD_SECRET_REF = ''
        TNS_ADMIN = ''
        POLL_INTERVALS = '{"connectivity":60,"instance":300,"storage":300,"fra_archive":180,"resources":180,"configuration":1800}'
        STALE_AFTER_SECONDS = '{"connectivity":180,"instance":900,"storage":900,"fra_archive":540,"resources":540,"configuration":5400}'
    }
    foreach ($Key in $Overrides.Keys) {
        $SavedEnvironment[$Key] = [Environment]::GetEnvironmentVariable($Key, 'Process')
        [Environment]::SetEnvironmentVariable($Key, $Overrides[$Key], 'Process')
    }

    if ($InstallDependencies) {
        Invoke-Checked $Python @('-m', 'pip', 'install', '-e', '.\backend[dev]')
        Push-Location $Frontend
        try { Invoke-Checked $Npm @('ci') } finally { Pop-Location }
    }
    if (-not (Test-Path (Join-Path $Frontend 'node_modules\vite\bin\vite.js'))) {
        throw 'Frontend dependencies are missing. Run again with -InstallDependencies.'
    }

    Write-Host "`n1. Compiling, testing and building" -ForegroundColor Cyan
    Invoke-PythonModule 'compileall' @('-q', 'backend\src', 'backend\alembic')
    if (-not $SkipTests) {
        Invoke-PythonModule 'pytest' @('-c', 'backend\pyproject.toml', 'backend\tests', '-q')
    } else {
        Write-Warning 'Automated test suites were skipped; this is not full verification.'
    }
    Push-Location $Frontend
    try {
        if (-not $SkipTests) { Invoke-Checked $Npm @('test') }
        Invoke-Checked $Npm @('run', 'build')
    } finally { Pop-Location }

    Write-Host "`n2. Migrating and seeding an isolated synthetic database" -ForegroundColor Cyan
    # Shorten connectivity timing only after tests that verify standard defaults.
    $env:POLL_INTERVALS = '{"connectivity":1,"instance":300,"storage":300,"fra_archive":180,"resources":180,"configuration":1800}'
    $env:STALE_AFTER_SECONDS = '{"connectivity":10,"instance":900,"storage":900,"fra_archive":540,"resources":540,"configuration":5400}'
    Invoke-PythonModule 'alembic' @('-c', 'backend\alembic.ini', 'upgrade', 'head')
    Invoke-PythonModule 'sentinel.collectors.demo' @('--database-url', $env:DATABASE_URL)
    $Backend = Start-Backend 'api-checks'

    Write-Host "`n3. Verifying health, fleet, details, history and API bounds" -ForegroundColor Cyan
    Assert-True ((Invoke-RestMethod "$BaseUrl/health/live").status -eq 'alive') 'Backend liveness'
    $Fleet = Invoke-RestMethod "$BaseUrl/api/v1/databases"
    Assert-True ($Fleet.total -eq 4) 'Four synthetic targets registered'
    foreach ($Expected in @(
        @('Demo healthy', 'healthy'), @('Demo critical', 'critical'),
        @('Demo offline', 'offline'), @('Demo missing permissions', 'unknown')
    )) {
        $Target = @($Fleet.items | Where-Object display_name -eq $Expected[0])
        Assert-True ($Target.Count -eq 1 -and $Target[0].last_health_status -eq $Expected[1]) "$($Expected[0]): $($Expected[1])"
    }
    $Critical = $Fleet.items | Where-Object display_name -eq 'Demo critical'
    $Healthy = $Fleet.items | Where-Object display_name -eq 'Demo healthy'
    $Detail = Invoke-RestMethod "$BaseUrl/api/v1/databases/$($Critical.id)"
    Assert-True ($Detail.tablespaces[0].used_pct -eq 97) 'Critical tablespace usage is 97%'
    Assert-True ($Detail.tablespaces[0].autoextend -eq $true) 'Tablespace autoextend is available'
    Assert-True ($Detail.fra.used_pct -eq 28.7) 'FRA measurements are available'
    Assert-True ($Detail.archive.log_mode -eq 'ARCHIVELOG') 'Archive mode is available'
    Assert-True ($Detail.compliance.unknown -eq 7) 'Unevaluated compliance remains unknown'
    Assert-True ($null -eq $Detail.critical_incident_count) 'Later-phase incident count remains unavailable'
    Assert-True ((Invoke-RestMethod "$BaseUrl/api/v1/databases?health_status=critical").total -eq 1) 'Health filter'
    Assert-True (@((Invoke-RestMethod "$BaseUrl/api/v1/databases?page_size=1").items).Count -eq 1) 'Fleet pagination'
    Assert-True ((Invoke-RestMethod "$BaseUrl/api/v1/databases?environment=absent").total -eq 0) 'Environment filter'
    $Metrics = Invoke-RestMethod "$BaseUrl/api/v1/databases/$($Critical.id)/metrics?metric_name=storage.tablespace.used_pct"
    Assert-True (@($Metrics).Count -gt 0 -and $Metrics[0].value -eq 97) 'Historical metric values'
    Assert-True (-not [string]::IsNullOrWhiteSpace($Metrics[0].source) -and $null -ne $Metrics[0].id) 'Metric source and evidence ID'
    $ParsedTimestamp = [datetimeoffset]::Parse($Metrics[0].timestamp)
    Assert-True ($ParsedTimestamp.Offset -eq [timespan]::Zero) 'Metric timestamps are UTC'
    $Observations = Invoke-RestMethod "$BaseUrl/api/v1/databases/$($Critical.id)/observations?limit=2"
    Assert-True (@($Observations).Count -le 2) 'Observations respect row limit'
    $Rejected = $false
    try { Invoke-RestMethod "$BaseUrl/api/v1/databases/$($Critical.id)/metrics?limit=5001" | Out-Null }
    catch [System.Net.WebException] {
        if ($null -ne $_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 422) { $Rejected = $true }
        else { throw }
    }
    Assert-True $Rejected 'Oversized metric requests rejected with HTTP 422'

    $Before = Get-DatabaseState
    1..5 | ForEach-Object {
        Invoke-RestMethod "$BaseUrl/api/v1/databases" | Out-Null
        Invoke-RestMethod "$BaseUrl/api/v1/databases/$($Critical.id)/metrics" | Out-Null
    }
    $After = Get-DatabaseState
    Assert-True ($Before.poll_runs -eq $After.poll_runs) 'Rapid HTTP refresh does not initiate collection'

    Write-Host "`n4. Checking restart persistence and stale telemetry" -ForegroundColor Cyan
    Stop-OwnedProcess $Backend
    $Backend = $null
    $Backend = Start-Backend 'restart-check'
    $Restarted = Get-DatabaseState
    Assert-True (($After.checkpoints | ConvertTo-Json -Depth 10 -Compress) -eq `
        ($Restarted.checkpoints | ConvertTo-Json -Depth 10 -Compress)) 'Checkpoint versions/cursors survive restart'
    Start-Sleep -Seconds 11
    $Stale = Invoke-RestMethod "$BaseUrl/api/v1/databases/$($Healthy.id)"
    Assert-True ($Stale.last_health_status -eq 'unknown') 'Expired connectivity evidence changes healthy to unknown'
    Assert-True (@($Stale.latest_observations | Where-Object {
        $_.metric_name -eq 'connectivity.available' -and $_.quality -eq 'stale'
    }).Count -eq 1) 'Expired observation is explicitly marked stale'

    Write-Host "`n5. Checking scheduled collection and dashboard connectivity" -ForegroundColor Cyan
    Stop-OwnedProcess $Backend
    $Backend = $null
    $env:SCHEDULER_ENABLED = 'true'
    $Backend = Start-Backend 'scheduler-check'
    $Deadline = (Get-Date).AddSeconds(30)
    do {
        $Recovered = Invoke-RestMethod "$BaseUrl/api/v1/databases/$($Healthy.id)"
        if ($Recovered.last_health_status -eq 'healthy') { break }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $Deadline)
    Assert-True ($Recovered.last_health_status -eq 'healthy') 'Scheduler restores fresh healthy telemetry'
    $Scheduled = Get-DatabaseState
    Assert-True ($Scheduled.poll_runs -gt $Restarted.poll_runs) 'Scheduled probes produce persistent poll runs'

    # Launch Node directly so cleanup owns the actual server PID, not an npm wrapper.
    $Vite = Join-Path $Frontend 'node_modules\vite\bin\vite.js'
    $Dashboard = Start-Process -FilePath $Node `
        -ArgumentList @(('"' + $Vite + '"'), '--host', '127.0.0.1', '--port', "$FrontendPort", '--strictPort') `
        -WorkingDirectory $Frontend -PassThru -NoNewWindow `
        -RedirectStandardOutput (Join-Path $Work 'dashboard.stdout.log') `
        -RedirectStandardError (Join-Path $Work 'dashboard.stderr.log')
    $DashboardUrl = "http://127.0.0.1:$FrontendPort"
    Wait-Http $DashboardUrl $Dashboard
    Assert-True ((Invoke-RestMethod "$DashboardUrl/health/ready").status -eq 'ready') 'Dashboard proxy reaches backend'
    Assert-True ((Invoke-RestMethod "$DashboardUrl/api/v1/databases").total -eq 4) 'Dashboard proxy serves fleet data'

    Write-Host "`nAutomated synthetic checks PASSED." -ForegroundColor Green
    Write-Warning 'Live PostgreSQL locking/recovery and real Oracle permissions/queries still require deployment acceptance.'
    if ($Interactive) {
        Write-Host "`nManual checklist at $DashboardUrl/#/:"
        Write-Host ' - Check four labelled health states, environment/status filters and refresh.'
        Write-Host ' - Open Demo critical: version, role/PDB, tablespaces, FRA and archive details.'
        Write-Host ' - Change timeline metric/time range; inspect source, timestamps and quality.'
        Write-Host ' - Resize the browser; verify no-data/error states and readable status labels.'
        Start-Process "$DashboardUrl/#/"
        Read-Host 'Press Enter after completing manual checks to stop services and clean up' | Out-Null
    } else {
        Write-Host 'For visual verification, rerun with -Interactive.'
    }
    $Succeeded = $true
}
finally {
    try {
        Stop-OwnedProcess $Dashboard
        Stop-OwnedProcess $Backend
    }
    finally {
        foreach ($Key in $SavedEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($Key, $SavedEnvironment[$Key], 'Process')
        }
        if ($null -ne $Work) {
            if ($Succeeded) {
                # Only this invocation's explicitly created directory is removed.
                Remove-Item -LiteralPath $Work -Recurse -Force
            } else {
                Write-Warning "Verification did not finish. Isolated database and logs retained at: $Work"
            }
        }
        Pop-Location
    }
}
