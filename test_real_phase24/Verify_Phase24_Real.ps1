#requires -Version 5.1
<#
.SYNOPSIS
Indexes the knowledge folders once with the local embedding model, then launches the live portal through the Phase 24 copilot checks.
.EXAMPLE
.\test_real_phase24\Verify_Phase24_Real.ps1 -ReindexOnly
.EXAMPLE
.\test_real_phase24\Verify_Phase24_Real.ps1 -TnsAlias YOUR_ALIAS -Interactive
.EXAMPLE
.\test_real_phase24\Verify_Phase24_Real.ps1 -TnsAlias YOUR_ALIAS -RequireModel
.EXAMPLE
.\test_real_phase24\Verify_Phase24_Real.ps1 -CheckPrerequisites
.DESCRIPTION
Run from the project root OR this script's directory. Root .env supplies Oracle
credentials, SECRET_KEY, and the local bootstrap account. This folder's .env
overrides live-mode settings. Root .env is not modified. Passwords and API keys
are never copied or printed.

This builds on the Phase 23 live harness: it starts Docker PostgreSQL and
upgrades the schema to revision 0012, which widens the passage vectors to 768
numbers and marks every stored document for embedding again.

Indexing runs once, before the API starts. The embedding model nomic-embed-text
is pulled into Ollama when it is missing (about 270 MB, from the Ollama
library). The files under knowledge-intake\ora-19c-manual and
knowledge-intake\Policy are then embedded on this computer. A file is skipped
when its content and the embedding model have not changed, so a second run
takes seconds. A file whose content changed is indexed again, a removed file
leaves the search, and a stopped run continues with the next unfinished file.
The stored page, size, and parse limits are raised when they are too low for
the Oracle manuals, and the local embedding model is stored as the provider.
The first run of twelve manuals takes about fifteen minutes on a GPU.

-ReindexOnly stops after indexing. Use it after adding or replacing a manual.
The API does not need a restart: the next search uses the new passages.

The full run then signs in, reuses or registers the selected real TNS alias,
confirms the Oracle connection, and checks Phase 24 against the running API:
every indexed document carries the local model; a second indexing run embeds
nothing; a search by meaning finds a manual passage; a pasted statement is
refused and a question about a command is not; an explanation is not answered
with a database reading; a question about files, tablespaces, memory, errors,
and the alert log each reads the selected database; a memory question asked
after a snapshot uses that snapshot and says so, and the same question asked
for the value now reads live; an operator cannot change the embedding provider
or start an index run. Every changed setting is put back.

The supervisor needs the local language model stored in Admin. With the model
off the fixed patterns route the questions and the run still passes.
-RequireModel fails the run unless the supervisor routed the question.

The snapshot and the chat thread from this check stay. Privileged execution
stays disabled. No databases, volumes, targets, documents, or audit rows are
deleted. The temporary phase24-operator account is removed.
#>
[CmdletBinding()]
param(
    [string]$TnsAlias,
    [switch]$Interactive,
    [switch]$InstallDependencies,
    [switch]$CheckPrerequisites,
    [switch]$RequireModel,
    [switch]$ReindexOnly,
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
    $Ollama = (Get-Command ollama.exe -ErrorAction SilentlyContinue).Source
    if (-not $Ollama) { throw 'Ollama is required for the embedding model. Install it from https://ollama.com and run again.' }
    if (-not $ReindexOnly) {
        Stop-PortListener 8000
        Stop-PortListener $FrontendPort
    }
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
    Write-Host "Embedding model: $($Config.embedding_model) on $($Config.embedding_local_url). Knowledge folders: $($Config.knowledge_intake_path)."
    if (-not $Config.auth_required -or $Config.oidc_configured) {
        throw 'Phase 24 live mode requires sign-in and leaves organization sign-in unset.'
    }
    if ($Config.ai_provider -eq 'hosted' -or $Config.ai_fallback_provider -ne 'disabled' -or $Config.ai_hosted_approved) {
        throw 'Phase 24 live mode does not approve a hosted model or a fallback.'
    }
    if (-not $ReindexOnly) {
        if ([string]::IsNullOrWhiteSpace($TnsAlias)) { $TnsAlias = Read-Host 'Enter the TNS alias to register/test' }
        $Matched = @($Aliases | Where-Object { $_ -ieq $TnsAlias })
        if ($Matched.Count -ne 1) { throw 'Choose exactly one alias listed by the configured tnsnames.ora.' }
        $TnsAlias = $Matched[0]
    }

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
    if ($LASTEXITCODE -ne 0) { throw 'Phase 24 schema check failed. See the safe error above.' }
    $Schema = $SchemaOutput | ConvertFrom-Json
    if ([string]$Schema.alembic_head -lt '0012') { throw "Expected Alembic head 0012 or later, received $($Schema.alembic_head)." }
    if ([int]$Schema.privileged_rows -ne 0) { throw 'The privileged operation table is not empty.' }
    Write-Host "[PASS] Schema is at revision $($Schema.alembic_head) and the privileged table is empty" -ForegroundColor Green

    Write-Host "`n5. Indexing the knowledge folders with the local embedding model (one time; unchanged files are skipped)" -ForegroundColor Cyan
    $Installed = & $Ollama list
    if ($LASTEXITCODE -ne 0) { throw 'Ollama is not answering. Start Ollama and run again.' }
    if (-not ($Installed | Select-String -SimpleMatch "$($Config.embedding_model)")) {
        Write-Host "Pulling the embedding model $($Config.embedding_model) from the Ollama library (about 270 MB)"
        Invoke-Checked $Ollama @('pull', "$($Config.embedding_model)")
    }
    $IndexOutput = & $Python $Runtime index
    if ($LASTEXITCODE -ne 0) { throw 'Indexing failed. See the safe error above.' }
    $IndexOutput | Where-Object { $_ -notmatch '^\{' } | ForEach-Object { Write-Host $_ }
    $Indexed = ($IndexOutput | Where-Object { $_ -match '^\{' } | Select-Object -Last 1) | ConvertFrom-Json
    $Embedded = [int]$Indexed.indexed + [int]$Indexed.updated + [int]$Indexed.reembedded
    Write-Host ("[PASS] Indexing finished in {0} s: {1} embedded, {2} unchanged and skipped, {3} removed" -f $Indexed.seconds, $Embedded, $Indexed.unchanged, $Indexed.removed) -ForegroundColor Green
    if (@($Indexed.settings_stored).Count -gt 0) {
        Write-Host ('Stored in Admin for this installation: ' + (@($Indexed.settings_stored) -join ', '))
    }
    if ([int]$Indexed.failed -gt 0) { Write-Warning "$($Indexed.failed) files were not indexed. They are listed above and will be tried again on the next run." }
    if ([int]$Indexed.quarantined -gt 0) { Write-Warning "$($Indexed.quarantined) files were moved to knowledge-intake\Quarantine. The reason is in quarantine.log there." }
    if ($ReindexOnly) {
        Write-Host 'Indexing only was requested. The API was not started. A running API uses the new passages on its next search.'
        return
    }

    $Logs = Join-Path ([System.IO.Path]::GetTempPath()) ('OracleSentinel-RealPhase24-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Logs | Out-Null
    Write-Host "Local service logs: $Logs"
    $Backend = Start-Process -FilePath $Python -ArgumentList @(('"' + $Runtime + '"'), 'serve') `
        -WorkingDirectory $Root -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $Logs 'backend.stdout.log') `
        -RedirectStandardError (Join-Path $Logs 'backend.stderr.log')
    Wait-Http "$BaseUrl/health/ready" $Backend

    Write-Host "`n6. Signing in with the local bootstrap account" -ForegroundColor Cyan
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

    Write-Host "`n7. Registering and testing the real Oracle target" -ForegroundColor Cyan
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

    Write-Host "`n8. Checking the index, the search, the supervisor, and stored-snapshot answers" -ForegroundColor Cyan
    $CheckArguments = @($Runtime, 'checks', '--cookie-file', $CookieFile, '--target-id', $Target.id)
    if ($RequireModel) { $CheckArguments += '--require-model' }
    $ChecksOutput = & $Python @CheckArguments
    if ($LASTEXITCODE -ne 0) { throw 'Phase 24 checks failed. See the safe error above.' }
    Remove-Item -LiteralPath $CookieFile -Force
    $CookieFile = $null
    $Checked = $ChecksOutput | ConvertFrom-Json
    $Expected = [ordered]@{
        second_run = 'nothing_to_do'
        pasted_sql = 'refused'
        access = 'admin_only'
        restored = 'yes'
    }
    foreach ($Name in $Expected.Keys) {
        if ([string]$Checked.$Name -ne $Expected[$Name]) {
            throw "Phase 24 check '$Name' reported '$($Checked.$Name)', expected '$($Expected[$Name])'."
        }
    }
    $Who = Invoke-Api -Method Get -Path '/api/v1/auth/session'
    if (-not $Who.authenticated) { throw 'The administrator session did not survive the checks.' }
    $Operators = Invoke-Api -Method Get -Path '/api/v1/admin/operators'
    if (@($Operators.items | Where-Object { $_.username -eq 'phase24-operator' }).Count -ne 0) {
        throw 'The temporary operator account was not removed.'
    }
    Write-Host ("[PASS] {0} documents are indexed with the local embedding model, {1} of them manuals" -f $Checked.documents_indexed, $Checked.manuals_indexed) -ForegroundColor Green
    Write-Host ("[PASS] A second indexing run embedded nothing: {0} files unchanged and skipped" -f $Checked.second_run_unchanged) -ForegroundColor Green
    if ($Checked.search -eq 'found') {
        Write-Host '[PASS] A search by meaning found a passage in the manuals' -ForegroundColor Green
    } else {
        Write-Warning 'No manual is indexed, so the search by meaning was not checked. Put the Oracle manuals in knowledge-intake\ora-19c-manual and run with -ReindexOnly.'
    }
    Write-Host '[PASS] A pasted statement is refused, and a question about a command is answered' -ForegroundColor Green
    Write-Host ("[PASS] {0} of {1} database topics (files, tablespaces, memory, errors, alert log) read the selected database" -f $Checked.database_topics_answered, $Checked.database_topics_asked) -ForegroundColor Green
    switch ([string]$Checked.stored_snapshot) {
        'used' { Write-Host '[PASS] A memory question used the snapshot that had just been stored and said so; asked for the value now, it read live' -ForegroundColor Green }
        default { Write-Warning 'The memory question did not read memory, so the stored-snapshot answer was not checked.' }
    }
    Write-Host '[PASS] Only an administrator can change the embedding provider or start an index run, and the model must stay on this computer' -ForegroundColor Green
    Write-Host '[PASS] Every changed setting was put back' -ForegroundColor Green
    if ($Checked.routed_by -eq 'supervisor') {
        Write-Host '[PASS] The supervisor routed the questions, and an explanation was not answered with a database reading' -ForegroundColor Green
    } else {
        Write-Host 'The language model is off, so the fixed patterns routed the questions. Turn the local model on in Admin and run with -RequireModel to check the supervisor.'
    }
    if ([int]$Checked.database_topics_answered -lt [int]$Checked.database_topics_asked) {
        Write-Warning 'A topic was answered with a different reading than the expected one. Open the copilot and check the Functions list under that answer.'
    }

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
    Write-Host "`nPortal (copilot, this database):      $Portal/#/copilot/databases/$($Target.id)" -ForegroundColor Green
    Write-Host "Portal (knowledge):                   $Portal/#/knowledge"
    Write-Host "API documentation:                    $BaseUrl/docs"
    Write-Host 'Open the copilot on the database. The page shows who answers and six sample questions; each answer names the specialist that wrote it.'
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
