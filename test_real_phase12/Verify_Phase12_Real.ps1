#requires -Version 5.1
<#
.SYNOPSIS
Prepares and launches the live portal through the Phase 12 memory-structure checks.
.EXAMPLE
.\test_real_phase12\Verify_Phase12_Real.ps1 -Interactive
.EXAMPLE
.\test_real_phase12\Verify_Phase12_Real.ps1 -TnsAlias YOUR_ALIAS -InstallDependencies -Interactive
.EXAMPLE
.\test_real_phase12\Verify_Phase12_Real.ps1 -CheckPrerequisites
.DESCRIPTION
Run from the project root OR this script's directory. Root .env supplies Oracle
credentials, SECRET_KEY, and the local bootstrap account. This folder's .env
overrides live-mode settings. Root .env is not modified. Passwords and API keys
are never copied or printed.

This builds on the earlier live harnesses: it starts Docker PostgreSQL, upgrades
the schema through revision 0010, signs in with the local bootstrap account, reuses
or registers the selected real TNS alias, and confirms the Oracle connection.

GraphDB memory is then read for that database only. The check expects SGA, PGA,
and background processes, with sizes labeled in MB or GB. A named limitation may
stand in for one group, and the other groups stay in the response. An operator
receives 403. A missing database returns not-found. The response must not contain
SQL. The language model stays off. Privileged execution stays disabled.
No databases, volumes, targets, or audit rows are deleted. The temporary
phase12-operator account created for the denial check is removed.
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
    Write-Host "Provider: $($Config.ai_provider). AI enabled: $($Config.ai_enabled). Hosted approved: $($Config.ai_hosted_approved). Fallback: $($Config.ai_fallback_provider)."
    if (-not $Config.auth_required -or $Config.oidc_configured) {
        throw 'Phase 12 live mode requires sign-in and leaves organization sign-in unset.'
    }
    if ($Config.ai_enabled -or $Config.ai_provider -ne 'disabled' -or $Config.ai_fallback_provider -ne 'disabled' -or $Config.ai_hosted_approved) {
        throw 'Phase 12 live mode keeps the language model disabled.'
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
    if ($LASTEXITCODE -ne 0) { throw 'Phase 12 schema check failed. See the safe error above.' }
    $Schema = $SchemaOutput | ConvertFrom-Json
    if ($Schema.alembic_head -ne '0010') { throw "Expected Alembic head 0010, received $($Schema.alembic_head)." }
    if ([int]$Schema.privileged_rows -ne 0) { throw 'The privileged operation table is not empty.' }
    Write-Host '[PASS] Schema is at revision 0010 and the privileged table is empty' -ForegroundColor Green

    $Logs = Join-Path ([System.IO.Path]::GetTempPath()) ('OracleSentinel-RealPhase12-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Logs | Out-Null
    Write-Host "Local service logs: $Logs"
    $Backend = Start-Process -FilePath $Python -ArgumentList @(('"' + $Runtime + '"'), 'serve') `
        -WorkingDirectory $Root -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $Logs 'backend.stdout.log') `
        -RedirectStandardError (Join-Path $Logs 'backend.stderr.log')
    Wait-Http "$BaseUrl/health/ready" $Backend

    Write-Host "`n5. Signing in with the local bootstrap account" -ForegroundColor Cyan
    try {
        Invoke-WebRequest "$BaseUrl/api/v1/databases/$([guid]::NewGuid().ToString())/graph/memory" -UseBasicParsing -TimeoutSec 30 | Out-Null
        throw 'Memory was readable before sign-in.'
    } catch {
        if ($_.Exception.Message -eq 'Memory was readable before sign-in.') { throw }
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

    Write-Host "`n7. Checking the memory snapshot" -ForegroundColor Cyan
    $MemoryOutput = & $Python $Runtime memory --cookie-file $CookieFile --target-id $Target.id
    if ($LASTEXITCODE -ne 0) { throw 'Memory checks failed. See the safe error above.' }
    Remove-Item -LiteralPath $CookieFile -Force
    $CookieFile = $null
    $Checked = $MemoryOutput | ConvertFrom-Json
    if ($Checked.memory -ne 'snapshot' -or $Checked.operator -ne 'denied' -or $Checked.missing_database -ne 'not_found') {
        throw 'Memory checks did not report the expected boundaries.'
    }
    $Memory = Invoke-Api -Method Get -Path "/api/v1/databases/$($Target.id)/graph/memory" -TimeoutSec 180
    if ($Memory.snapshot -ne 'Current instance snapshot.') { throw 'The memory panel did not say this is the current snapshot.' }
    if ($null -eq $Memory.sga.items -or $null -eq $Memory.pga.items -or $null -eq $Memory.background_processes) {
        throw 'Memory did not return SGA, PGA, and background processes.'
    }
    foreach ($Item in @($Memory.sga.items) + @($Memory.pga.items)) {
        if ($Item.display -notmatch '^\d+(\.\d+)? (MB|GB)$') { throw 'A memory size was not labeled in MB or GB.' }
    }
    $Encoded = $Memory | ConvertTo-Json -Depth 6 -Compress
    if ($Encoded -match '(?i)from v\$') { throw 'The memory response included SQL.' }
    Write-Host ("[PASS] Memory snapshot: {0} SGA pools, {1} PGA values, {2} background sessions" -f @(
        @($Memory.sga.items).Count, @($Memory.pga.items).Count, @($Memory.background_processes).Count
    )) -ForegroundColor Green

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
    Write-Host "`nPortal (GraphDB for this database):  $Portal/#/graph/databases/$($Target.id)" -ForegroundColor Green
    Write-Host "Portal (GraphDB chooser):            $Portal/#/graph"
    Write-Host "API documentation:                   $BaseUrl/docs"
    Write-Host 'Sign in as the administrator. Memory is the current snapshot. The model is off for this run.'
    Write-Warning 'This is a local development app. Do not expose it publicly.'
    if ($Interactive) {
        Start-Process "$Portal/#/graph/databases/$($Target.id)"
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
