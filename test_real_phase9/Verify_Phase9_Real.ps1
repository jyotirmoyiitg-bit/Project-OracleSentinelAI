#requires -Version 5.1
<#
.SYNOPSIS
Prepares and launches the live portal through the Phase 9 assistant-policy checks.
.EXAMPLE
.\test_real_phase9\Verify_Phase9_Real.ps1 -Interactive
.EXAMPLE
.\test_real_phase9\Verify_Phase9_Real.ps1 -TnsAlias YOUR_ALIAS -InstallDependencies -Interactive
.EXAMPLE
.\test_real_phase9\Verify_Phase9_Real.ps1 -CheckPrerequisites
.DESCRIPTION
Run from the project root OR this script's directory. Root .env supplies Oracle
credentials, SECRET_KEY, and the local bootstrap account. This folder's .env
overrides live-mode settings. Root .env is not modified. Passwords and API keys
are never copied or printed.

This builds on the earlier live harnesses: it starts Docker PostgreSQL, upgrades
the schema through revision 0010, signs in with the local bootstrap account, reuses
or registers the selected real TNS alias, and confirms the Oracle connection.

The language model stays disabled for this run, so a concept question is not sent
to a hosted model. The script then checks the assistant policy: pasted SQL is
refused, a general Oracle question does not query the database, a company-policy
question stays on approved evidence, a change is not executed, and a tablespace
question uses an approved reading. Privileged execution stays disabled.
No databases, volumes, targets, or audit rows are deleted.
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
$CookieFile = $null
$BaseUrl = 'http://127.0.0.1:8000'
$Session = $null

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $Executable" }
}

function Stop-PortListener {
    param([int]$Port)
    $Owners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($ProcessId in $Owners) {
        if ($ProcessId -and $ProcessId -ne $PID) {
            Write-Host "Stopping process $ProcessId on port $Port"
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    $Deadline = (Get-Date).AddSeconds(15)
    do {
        $Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        try {
            $Listener.Start()
            return
        } catch {
            Start-Sleep -Milliseconds 300
        } finally {
            if ($Listener.Server.IsBound) { $Listener.Stop() }
        }
    } while ((Get-Date) -lt $Deadline)
    throw "Port $Port is still occupied after stopping the previous listener."
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

function Get-StatusCode {
    param($ErrorRecord)
    $Response = $ErrorRecord.Exception.Response
    if ($null -eq $Response -and $null -ne $ErrorRecord.Exception.InnerException) {
        $Response = $ErrorRecord.Exception.InnerException.Response
    }
    if ($null -eq $Response) { return 0 }
    return [int]$Response.StatusCode
}

function Invoke-Api {
    param(
        [string]$Method,
        [string]$Path,
        [string]$Body,
        [int]$TimeoutSec = 60
    )
    $Params = @{
        Uri = "$BaseUrl$Path"
        Method = $Method
        WebSession = $Session
        TimeoutSec = $TimeoutSec
    }
    if ($Body) {
        $Params.ContentType = 'application/json'
        $Params.Body = $Body
    }
    return Invoke-RestMethod @Params
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
    Stop-PortListener 8000
    Stop-PortListener $FrontendPort
    if ($InstallDependencies) {
        Invoke-Checked $Python @('-m', 'pip', 'install', '-e', '.\backend[dev]')
        Push-Location $Frontend
        try { Invoke-Checked $Npm @('ci') } finally { Pop-Location }
    }
    $Vite = Join-Path $Frontend 'node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $Vite)) { throw 'Run again with -InstallDependencies to prepare dependencies.' }

    Write-Host "`n2. Loading live configuration and selecting a TNS alias" -ForegroundColor Cyan
    $PreflightOutput = & $Python $Runtime preflight
    if ($LASTEXITCODE -ne 0) { throw 'Live configuration preflight failed. See the safe error above.' }
    $Config = $PreflightOutput | ConvertFrom-Json
    $Aliases = @($Config.aliases)
    Write-Host ('Available TNS aliases: ' + ($Aliases -join ', '))
    Write-Host "Sign-in required: $($Config.auth_required). OIDC configured: $($Config.oidc_configured)."
    Write-Host "Provider: $($Config.ai_provider). AI enabled: $($Config.ai_enabled). Hosted approved: $($Config.ai_hosted_approved). Fallback: $($Config.ai_fallback_provider)."
    if (-not $Config.auth_required -or $Config.oidc_configured) {
        throw 'Phase 9 live mode requires sign-in and leaves organization sign-in unset.'
    }
    if ($Config.ai_enabled -or $Config.ai_provider -ne 'disabled' -or $Config.ai_fallback_provider -ne 'disabled' -or $Config.ai_hosted_approved) {
        throw 'Phase 9 live mode keeps the language model disabled.'
    }
    if ([string]::IsNullOrWhiteSpace($TnsAlias)) { $TnsAlias = Read-Host 'Enter the TNS alias to register/test' }
    $Matched = @($Aliases | Where-Object { $_ -ieq $TnsAlias })
    if ($Matched.Count -ne 1) { throw 'Choose exactly one alias listed by the configured tnsnames.ora.' }
    $TnsAlias = $Matched[0]

    Write-Host "`n3. Starting Docker PostgreSQL (pgvector)" -ForegroundColor Cyan
    if (-not (Test-DockerReady)) { Invoke-Checked $Docker @('desktop', 'start') }
    $Deadline = (Get-Date).AddSeconds(120)
    do {
        $DockerReady = Test-DockerReady
        if ($DockerReady) { break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $Deadline)
    if (-not $DockerReady) { throw 'Docker engine is not ready. Start Docker Desktop with Linux containers.' }
    Invoke-Checked $Docker @('compose', '--env-file', 'NUL', '-f', 'docker-compose.yml', 'up', '-d', '--wait', '--wait-timeout', '120', 'postgres')
    Invoke-Checked $Docker @('exec', 'sentinel-postgres', 'pg_isready', '-U', 'postgres', '-d', 'sentinel')

    Write-Host "`n4. Applying application migrations through revision 0010" -ForegroundColor Cyan
    Invoke-Checked $Python @($Runtime, 'migrate')
    $SchemaOutput = & $Python $Runtime schema
    if ($LASTEXITCODE -ne 0) { throw 'Phase 9 schema check failed. See the safe error above.' }
    $Schema = $SchemaOutput | ConvertFrom-Json
    if ($Schema.alembic_head -ne '0010') { throw "Expected Alembic head 0010, received $($Schema.alembic_head)." }
    if ([int]$Schema.privileged_rows -ne 0) { throw 'The privileged operation table is not empty.' }
    Write-Host '[PASS] Schema is at revision 0010 and the privileged table is empty' -ForegroundColor Green

    $Logs = Join-Path ([System.IO.Path]::GetTempPath()) ('OracleSentinel-RealPhase9-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Logs | Out-Null
    Write-Host "Local service logs: $Logs"
    $Backend = Start-Process -FilePath $Python -ArgumentList @(('"' + $Runtime + '"'), 'serve') `
        -WorkingDirectory $Root -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $Logs 'backend.stdout.log') `
        -RedirectStandardError (Join-Path $Logs 'backend.stderr.log')
    Wait-Http "$BaseUrl/health/ready" $Backend

    Write-Host "`n5. Signing in with the local bootstrap account" -ForegroundColor Cyan
    try {
        Invoke-WebRequest "$BaseUrl/api/v1/operations/proposals" -UseBasicParsing -TimeoutSec 30 | Out-Null
        throw 'Proposals were readable before sign-in.'
    } catch {
        if ($_.Exception.Message -eq 'Proposals were readable before sign-in.') { throw }
        $Code = Get-StatusCode $_
        if ($Code -ne 401) { throw "Expected HTTP 401 before sign-in, received $Code." }
    }
    $CookieFile = Join-Path $Logs 'session.cookie'
    $SessionOutput = & $Python $Runtime session --cookie-file $CookieFile
    if ($LASTEXITCODE -ne 0) { throw 'Local sign-in failed. See the safe error above.' }
    $SignedIn = $SessionOutput | ConvertFrom-Json
    if ($SignedIn.method -ne 'local' -or [string]::IsNullOrWhiteSpace($SignedIn.principal)) {
        throw 'Local sign-in did not return a local principal.'
    }
    $CookieValue = [System.IO.File]::ReadAllText($CookieFile).Trim()
    if ([string]::IsNullOrWhiteSpace($CookieValue)) { throw 'The session cookie was empty.' }
    $Session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $Cookie = New-Object System.Net.Cookie('sentinel_session', $CookieValue, '/', '127.0.0.1')
    $Session.Cookies.Add($Cookie)
    $CookieValue = $null
    $Who = Invoke-Api -Method Get -Path '/api/v1/auth/session'
    if (-not $Who.authenticated -or $Who.principal -ne $SignedIn.principal) {
        throw 'The session cookie was not accepted.'
    }
    Write-Host "[PASS] Signed in as $($Who.principal) ($($Who.role))" -ForegroundColor Green

    Write-Host "`n6. Registering and testing the real Oracle target" -ForegroundColor Cyan
    $Targets = @()
    $Page = 1
    do {
        $Fleet = Invoke-Api -Method Get -Path "/api/v1/databases?page=$Page&page_size=100"
        $Targets += @($Fleet.items | Where-Object { $_.tns_alias -ieq $TnsAlias })
        $Page++
    } while (($Page - 1) * 100 -lt $Fleet.total)
    if ($Targets.Count -gt 1) { throw 'Multiple targets have this TNS alias. Resolve duplicates through /docs first.' }
    if ($Targets.Count -eq 0) {
        $Body = @{display_name="Local Oracle - $TnsAlias"; tns_alias=$TnsAlias; environment='dev'} | ConvertTo-Json
        $Target = Invoke-Api -Method Post -Path '/api/v1/databases' -Body $Body
    } else { $Target = $Targets[0] }
    if (-not $Target.enabled) { throw 'This target is disabled. Enable it explicitly through /docs before live testing.' }
    $Connection = Invoke-Api -Method Post -Path "/api/v1/databases/$($Target.id)/test-connection" -TimeoutSec 180
    if ($Connection.status -ne 'connected') {
        throw 'Oracle connection failed. Check TNS, listener, credentials and read-only grants; inspect /docs locally.'
    }
    Write-Host '[PASS] Real Oracle connection established' -ForegroundColor Green
    Write-Host 'Waiting for a collection that starts during this run. Existing stale rows do not count.'
    $CollectionStarted = [DateTimeOffset]::UtcNow
    $Deadline = (Get-Date).AddSeconds($CollectionWaitSeconds)
    do {
        $Detail = Invoke-Api -Method Get -Path "/api/v1/databases/$($Target.id)"
        $Pending = @($Detail.poll_status | Where-Object { $_.status -in @('pending', 'running') })
        $Fresh = @($Detail.poll_status | Where-Object {
            $Parsed = [DateTimeOffset]::MinValue
            $_.last_success_at -and [DateTimeOffset]::TryParse([string]$_.last_success_at, [ref]$Parsed) -and $Parsed -gt $CollectionStarted
        })
        if ($Fresh.Count -gt 0 -and $Pending.Count -eq 0) { break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $Deadline)
    $Detail.poll_status | Format-Table probe_type,status,last_success_at,error_class -AutoSize
    Write-Host "Measured target health: $($Detail.last_health_status)"
    if ($Fresh.Count -eq 0) {
        Write-Warning 'No probe completed after this launcher started. The portal will still open. Inspect the backend log if collection stays stale.'
    }

    Write-Host "`n7. Checking proposals, refused execution, and the audit list" -ForegroundColor Cyan
    $Proposals = Invoke-Api -Method Get -Path '/api/v1/operations/proposals'
    if ($null -eq $Proposals.items) { throw 'The proposal list did not return items.' }
    $UnusedId = [guid]::NewGuid().ToString()
    try {
        Invoke-WebRequest "$BaseUrl/api/v1/operations/proposals/$UnusedId/execute" -Method Post -WebSession $Session -UseBasicParsing -TimeoutSec 30 | Out-Null
        throw 'Proposal execution was accepted.'
    } catch {
        if ($_.Exception.Message -eq 'Proposal execution was accepted.') { throw }
        $Code = Get-StatusCode $_
        if ($Code -ne 403) { throw "Expected HTTP 403 for proposal execution, received $Code." }
    }
    $Audit = Invoke-Api -Method Get -Path '/api/v1/audit/events'
    if ($null -eq $Audit.items) { throw 'The audit list did not return items.' }
    $Platform = Invoke-Api -Method Get -Path '/api/v1/admin/platform'
    if ($Platform.execution_allowed) { throw 'The platform reports that proposal execution is allowed.' }
    $PlatformText = $Platform | ConvertTo-Json -Depth 6 -Compress
    if ($PlatformText -match 'sk-|password|BEGIN PRIVATE') { throw 'The platform summary contains a secret-shaped value.' }
    $Metrics = Invoke-WebRequest "$BaseUrl/metrics" -UseBasicParsing -TimeoutSec 30
    if ($Metrics.StatusCode -ne 200) { throw 'The metrics endpoint did not answer.' }
    Write-Host '[PASS] Proposals are readable, execution is refused, and the audit list is open to this admin' -ForegroundColor Green
    $PrivilegedId = [guid]::NewGuid().ToString()
    foreach ($Attempt in @(
        @{ Method = 'Get'; Path = '/api/v1/operations/privileged?enabled=true' },
        @{ Method = 'Post'; Path = "/api/v1/operations/privileged/$PrivilegedId/execute" }
    )) {
        try {
            Invoke-WebRequest "$BaseUrl$($Attempt.Path)" -Method $Attempt.Method -WebSession $Session -UseBasicParsing -TimeoutSec 30 | Out-Null
            throw "Privileged endpoint accepted a request: $($Attempt.Path)"
        } catch {
            if ($_.Exception.Message -like 'Privileged endpoint accepted*') { throw }
            $Code = Get-StatusCode $_
            if ($Code -ne 501) { throw "Expected HTTP 501 for $($Attempt.Path), received $Code." }
        }
    }
    if ($Platform.privileged_execution_enabled) { throw 'The platform reports that privileged execution is enabled.' }
    Write-Host '[PASS] Privileged operations return 501 and stay disabled' -ForegroundColor Green

    Write-Host "`n8. Checking the assistant policy with the model off" -ForegroundColor Cyan
    $PolicyOutput = & $Python $Runtime policy --cookie-file $CookieFile --target-id $Target.id
    if ($LASTEXITCODE -ne 0) { throw 'Assistant-policy checks failed. See the safe error above.' }
    Remove-Item -LiteralPath $CookieFile -Force
    $CookieFile = $null
    $Policy = $PolicyOutput | ConvertFrom-Json
    if ($Policy.pasted_sql -ne 'refused' -or $Policy.change -ne 'not_executed' -or $Policy.tablespace -ne 'approved_reading') {
        throw 'Assistant-policy checks did not report the expected boundaries.'
    }
    Write-Host '[PASS] SQL is refused, guidance does not query Oracle, a change is not executed, and tablespace uses a reading' -ForegroundColor Green

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
    Write-Host "`nPortal (Copilot for this database):  $Portal/#/copilot/databases/$($Target.id)" -ForegroundColor Green
    Write-Host "Portal (Audit):                      $Portal/#/audit"
    Write-Host "Portal (Admin):                      $Portal/#/admin"
    Write-Host "Portal (Proposals):                  $Portal/#/operations"
    Write-Host "API documentation:                   $BaseUrl/docs"
    Write-Host 'Sign in with the local account. The model is off for this run. SQL and database changes are not executed.'
    Write-Warning 'This is a local development app. Do not expose it publicly.'
    if ($Interactive) {
        Start-Process "$Portal/#/copilot/databases/$($Target.id)"
        Read-Host 'Press Enter when finished to stop the portal/backend (PostgreSQL and existing rows stay)' | Out-Null
    } else {
        Write-Host 'Use -Interactive to keep the portal open; smoke-check services will now stop.'
    }
}
finally {
    if ($CookieFile -and (Test-Path -LiteralPath $CookieFile)) {
        Remove-Item -LiteralPath $CookieFile -Force -ErrorAction SilentlyContinue
    }
    try { Stop-OwnedProcess $Dashboard }
    finally {
        try { Stop-OwnedProcess $Backend }
        finally { Pop-Location }
    }
}
