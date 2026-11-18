#requires -Version 5.1
<#
.SYNOPSIS
Prepares and launches the live portal through the Phase 25 checks of MemoryDB, StorageDB, ParameterDB, and AlertDB.
.EXAMPLE
.\test_real_phase25\Verify_Phase25_Real.ps1 -Interactive
.EXAMPLE
.\test_real_phase25\Verify_Phase25_Real.ps1 -TnsAlias YOUR_ALIAS -InstallDependencies -Interactive
.EXAMPLE
.\test_real_phase25\Verify_Phase25_Real.ps1 -CheckPrerequisites
.DESCRIPTION
Run from the project root OR this script's directory. Root .env supplies Oracle
credentials, SECRET_KEY, and the local bootstrap account. This folder's .env
overrides live-mode settings. Root .env is not modified. Passwords and API keys
are never copied or printed.

This builds on the Phase 23 live harness: it starts Docker PostgreSQL, upgrades
the schema to the current revision (0012 or later; Phase 25 adds no migration),
signs in with the local bootstrap account, reuses or registers the selected real
TNS alias, and confirms the Oracle connection. It does not run the Phase 24
indexing step; use the Phase 24 launcher for that.

Phase 25 is then checked against the running API and the selected database.
MemoryDB must return the SGA pools, and each pool's used and free memory must
add up to its size. StorageDB must return every tablespace, with used and free
space adding up to the total. ParameterDB must return at least 100 parameters
sorted by name with a correct count of changed ones. AlertDB must return at
most 300 lines, oldest first, with a correct error count. A temporary operator
must be able to read all four sections, must not be shown a parameter value
that holds a file path or a network address, and must not be shown a file path
in the alert log. An unknown database must return 404. No answer may contain
SQL.

No new Oracle grant is needed: the four sections read views the monitoring
account already has. No setting is changed and no language model is used.
Privileged execution stays disabled. No databases, volumes, targets, or audit
rows are deleted. The temporary phase25-operator account is removed.
#>
[CmdletBinding()]
param(
    [string]$TnsAlias,
    [switch]$Interactive,
    [switch]$InstallDependencies,
    [switch]$CheckPrerequisites,
    [ValidateRange(1, 65535)]
    [int]$FrontendPort = 5173
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
    if (-not $Config.auth_required -or $Config.oidc_configured) {
        throw 'Phase 25 live mode requires sign-in and leaves organization sign-in unset.'
    }
    if ($Config.ai_provider -eq 'hosted' -or $Config.ai_fallback_provider -ne 'disabled' -or $Config.ai_hosted_approved) {
        throw 'Phase 25 live mode does not approve a hosted model or a fallback.'
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

    Write-Host "`n4. Applying application migrations to the current revision" -ForegroundColor Cyan
    Invoke-Checked $Python @($Runtime, 'migrate')
    $SchemaOutput = & $Python $Runtime schema
    if ($LASTEXITCODE -ne 0) { throw 'Phase 25 schema check failed. See the safe error above.' }
    $Schema = $SchemaOutput | ConvertFrom-Json
    if ([string]$Schema.alembic_head -lt '0012') { throw "Expected Alembic head 0012 or later, received $($Schema.alembic_head)." }
    if ([int]$Schema.privileged_rows -ne 0) { throw 'The privileged operation table is not empty.' }
    Write-Host "[PASS] Schema is at revision $($Schema.alembic_head) and the privileged table is empty" -ForegroundColor Green

    $Logs = Join-Path ([System.IO.Path]::GetTempPath()) ('OracleSentinel-RealPhase25-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Logs | Out-Null
    Write-Host "Local service logs: $Logs"
    $Backend = Start-Process -FilePath $Python -ArgumentList @(('"' + $Runtime + '"'), 'serve') `
        -WorkingDirectory $Root -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $Logs 'backend.stdout.log') `
        -RedirectStandardError (Join-Path $Logs 'backend.stderr.log')
    Wait-Http "$BaseUrl/health/ready" $Backend

    Write-Host "`n5. Signing in with the local bootstrap account" -ForegroundColor Cyan
    try {
        Invoke-WebRequest "$BaseUrl/api/v1/chat/threads/$([guid]::NewGuid().ToString())/messages" -UseBasicParsing -TimeoutSec 30 | Out-Null
        throw 'A chat thread was readable before sign-in.'
    } catch {
        if ($_.Exception.Message -eq 'A chat thread was readable before sign-in.') { throw }
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
    if ($SignedIn.role -notin @('admin', 'platform_admin')) {
        throw 'The bootstrap account is not an administrator.'
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
    $Enabled = 0
    $Page = 1
    do {
        $Fleet = Invoke-Api -Method Get -Path "/api/v1/databases?page=$Page&page_size=100"
        $Targets += @($Fleet.items | Where-Object { $_.tns_alias -ieq $TnsAlias })
        $Enabled += @($Fleet.items | Where-Object { $_.enabled }).Count
        $Page++
    } while (($Page - 1) * 100 -lt $Fleet.total)
    if ($Targets.Count -gt 1) { throw 'Multiple targets have this TNS alias. Resolve duplicates through /docs first.' }
    if ($Targets.Count -eq 0) {
        $Body = @{display_name="Local Oracle - $TnsAlias"; tns_alias=$TnsAlias; environment='dev'} | ConvertTo-Json
        $Target = Invoke-Api -Method Post -Path '/api/v1/databases' -Body $Body
        $Enabled++
    } else { $Target = $Targets[0] }
    if (-not $Target.enabled) { throw 'This target is disabled. Enable it explicitly through /docs before live testing.' }
    $Connection = Invoke-Api -Method Post -Path "/api/v1/databases/$($Target.id)/test-connection" -TimeoutSec 180
    if ($Connection.status -ne 'connected') {
        throw 'Oracle connection failed. Check TNS, listener, credentials and read-only grants; inspect /docs locally.'
    }
    Write-Host '[PASS] Real Oracle connection established' -ForegroundColor Green

    Write-Host "`n7. Reading MemoryDB, StorageDB, ParameterDB, and AlertDB from the selected database" -ForegroundColor Cyan
    $ChecksOutput = & $Python $Runtime checks --cookie-file $CookieFile --target-id $Target.id
    if ($LASTEXITCODE -ne 0) { throw 'Phase 25 checks failed. See the safe error above.' }
    Remove-Item -LiteralPath $CookieFile -Force
    $CookieFile = $null
    $Checked = $ChecksOutput | ConvertFrom-Json
    $Expected = [ordered]@{
        memory = 'read'
        storage = 'read'
        parameter_list = 'read'
        alert_log = 'read'
        access = 'every_role_reads_paths_admin_only'
    }
    foreach ($Name in $Expected.Keys) {
        if ([string]$Checked.$Name -ne $Expected[$Name]) {
            throw "Phase 25 check '$Name' reported '$($Checked.$Name)', expected '$($Expected[$Name])'."
        }
    }
    $Who = Invoke-Api -Method Get -Path '/api/v1/auth/session'
    if (-not $Who.authenticated) { throw 'The administrator session did not survive the checks.' }
    $Operators = Invoke-Api -Method Get -Path '/api/v1/admin/operators'
    if (@($Operators.items | Where-Object { $_.username -eq 'phase25-operator' }).Count -ne 0) {
        throw 'The temporary operator account was not removed.'
    }
    Write-Host ("[PASS] MemoryDB: {0} SGA pools, {1} shared pool areas, and used plus free equals each pool" -f $Checked.sga_pools, $Checked.shared_pool_areas) -ForegroundColor Green
    Write-Host ("[PASS] StorageDB: {0} tablespaces ({1} temporary), and used plus free equals each total" -f $Checked.tablespaces, $Checked.temporary_tablespaces) -ForegroundColor Green
    Write-Host ("[PASS] ParameterDB: {0} parameters, {1} changed from their default" -f $Checked.parameters, $Checked.parameters_changed) -ForegroundColor Green
    Write-Host ("[PASS] AlertDB: {0} lines oldest first, {1} errors, {2} warnings" -f $Checked.alert_lines, $Checked.alert_errors, $Checked.alert_warnings) -ForegroundColor Green
    Write-Host ("[PASS] An operator reads all four sections; {0} parameter values and {1} alert lines had a path held back" -f $Checked.parameters_held_back, $Checked.alert_paths_masked) -ForegroundColor Green
    Write-Host '[PASS] An unknown database returns 404 and no answer contains SQL' -ForegroundColor Green
    if ($Checked.version_known -ne 'yes') {
        Write-Warning 'The version banner was not returned. Check SELECT on V_$VERSION for the monitoring account. The parameters are still listed.'
    }
    if ([int]$Checked.alert_lines -eq 0) {
        Write-Warning 'The alert log returned no line for the last 7 days.'
    }

    Write-Host "`n8. Launching the portal" -ForegroundColor Cyan
    $Dashboard = Start-Process -FilePath $Node `
        -ArgumentList @(('"' + $Vite + '"'), '--host', '127.0.0.1', '--port', "$FrontendPort", '--strictPort') `
        -WorkingDirectory $Frontend -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $Logs 'frontend.stdout.log') `
        -RedirectStandardError (Join-Path $Logs 'frontend.stderr.log')
    $Portal = "http://127.0.0.1:$FrontendPort"
    Wait-Http $Portal $Dashboard
    $Ready = Invoke-RestMethod "$Portal/health/ready"
    if ($Ready.status -ne 'ready') { throw 'Portal proxy cannot reach the ready backend.' }
    Write-Host "`nPortal (MemoryDB):                    $Portal/#/memory/databases/$($Target.id)" -ForegroundColor Green
    Write-Host "Portal (StorageDB):                   $Portal/#/storage/databases/$($Target.id)"
    Write-Host "Portal (ParameterDB):                 $Portal/#/parameters/databases/$($Target.id)"
    Write-Host "Portal (AlertDB):                     $Portal/#/alerts/databases/$($Target.id)"
    Write-Host "API documentation:                    $BaseUrl/docs"
    Write-Host 'Open a database: the four sections are tabs beside GraphDB and WaitDB.'
    Write-Warning 'This is a local development app. Do not expose it publicly.'
    if ($Interactive) {
        Start-Process "$Portal/#/storage/databases/$($Target.id)"
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
