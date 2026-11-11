#requires -Version 5.1
<#
.SYNOPSIS
Prepares and launches LIVE Oracle monitoring plus knowledge indexing, search, quarantine, and ACL checks.
.EXAMPLE
.\test_real_phase5\Verify_Phase5_Real.ps1 -Interactive
.EXAMPLE
.\test_real_phase5\Verify_Phase5_Real.ps1 -TnsAlias YOUR_ALIAS -InstallDependencies -Interactive
.EXAMPLE
.\test_real_phase5\Verify_Phase5_Real.ps1 -CheckPrerequisites
.DESCRIPTION
Run from the project root OR this script's directory. Root .env supplies Oracle
credentials and SECRET_KEY; this folder's .env overrides live-mode/PostgreSQL/knowledge
settings. Root .env is not modified and passwords are never copied or printed.

This builds on test_real_phase2/test_real_phase3/test_real_phase4: it starts Docker
PostgreSQL (the pgvector image), upgrades the schema through the Phase 5 knowledge
tables, reuses or registers the selected real TNS alias, and confirms the real Oracle
connection and collection probes while AI_ENABLED stays false.

It then writes one unique Markdown note and one corrupt PDF under
knowledge-intake/Knowledge/test-real-phase5, reindexes through the real API, and checks:
the note is searchable with a document/section citation and llm_used=false; the sample
runbook and ARCHIVELOG policy return citations; the corrupt PDF is moved to Quarantine;
PostgreSQL can run a pgvector distance and a full-text match; an operator cannot see
the note after its ACL is limited to dba, while dba and admin still can. The ACL is
restored to operator/dba/admin before the portal opens. Oracle probes remain read-only.
Schema and knowledge-index writes go to PostgreSQL. Back up existing application data.

The verification note and the quarantined PDF remain for audit. No databases, volumes,
targets, incidents, or existing knowledge documents are deleted.
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
$RestrictedDocumentId = $null
$AdminHeaders = @{ 'X-Sentinel-Role' = 'admin' }
$OperatorHeaders = @{ 'X-Sentinel-Role' = 'operator' }
$DbaHeaders = @{ 'X-Sentinel-Role' = 'dba' }

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

function Invoke-Knowledge {
    param(
        [string]$Method,
        [string]$Path,
        [hashtable]$Headers,
        [string]$Body,
        [int]$TimeoutSec = 60
    )
    $Params = @{ Uri = "$BaseUrl$Path"; Method = $Method; Headers = $Headers; TimeoutSec = $TimeoutSec }
    if ($Body) {
        $Params.ContentType = 'application/json'
        $Params.Body = $Body
    }
    return Invoke-RestMethod @Params
}

function Restore-VerificationAcl {
    if ([string]::IsNullOrWhiteSpace($RestrictedDocumentId)) { return }
    if ($null -eq $Backend) { return }
    $Backend.Refresh()
    if ($Backend.HasExited) { return }
    $Body = '{"roles":["operator","dba","admin"],"principals":[]}'
    Invoke-Knowledge -Method Post -Path "/api/v1/knowledge/documents/$RestrictedDocumentId/acl" `
        -Headers $AdminHeaders -Body $Body | Out-Null
    $script:RestrictedDocumentId = $null
    Write-Host '[PASS] Verification document ACL restored to operator, dba, and admin' -ForegroundColor Green
}

Push-Location $Root
try {
    Write-Host "`n1. Checking tools and local files" -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath $Python)) { throw 'Create the project .venv with Python 3.14+ first.' }
    foreach ($File in @(
        (Join-Path $Root '.env'), (Join-Path $PSScriptRoot '.env'), $Runtime,
        (Join-Path $Root 'knowledge-intake\Knowledge\internal-runbooks\ora-00257.md'),
        (Join-Path $Root 'knowledge-intake\Policy\archivelog-standard.md')
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
    $Config = $PreflightOutput | ConvertFrom-Json
    $Aliases = @($Config.aliases)
    Write-Host ('Available TNS aliases: ' + ($Aliases -join ', '))
    Write-Host "Embeddings: $($Config.embedding_provider). AI enabled: $($Config.ai_enabled). Knowledge scheduler: $($Config.knowledge_enabled)."
    if ($Config.embedding_provider -ne 'local' -or $Config.ai_enabled -or $Config.knowledge_enabled) {
        throw 'Phase 5 live mode requires local embeddings, AI disabled, and the knowledge scheduler off.'
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
    # Empty env-file stops Compose from parsing unrelated project .env credentials.
    # The existing compose service defines sentinel and its persistent volume.
    Invoke-Checked $Docker @('compose', '--env-file', 'NUL', '-f', 'docker-compose.yml', 'up', '-d', '--wait', '--wait-timeout', '120', 'postgres')
    Invoke-Checked $Docker @('exec', 'sentinel-postgres', 'pg_isready', '-U', 'postgres', '-d', 'sentinel')

    Write-Host "`n4. Applying application migrations (including Phase 5 knowledge/pgvector tables)" -ForegroundColor Cyan
    Invoke-Checked $Python @($Runtime, 'migrate')
    $Logs = Join-Path ([System.IO.Path]::GetTempPath()) ('OracleSentinel-RealPhase5-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Logs | Out-Null
    Write-Host "Local service logs: $Logs"
    $Backend = Start-Process -FilePath $Python -ArgumentList @(('"' + $Runtime + '"'), 'serve') `
        -WorkingDirectory $Root -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $Logs 'backend.stdout.log') `
        -RedirectStandardError (Join-Path $Logs 'backend.stderr.log')
    Wait-Http "$BaseUrl/health/ready" $Backend

    Write-Host "`n5. Registering and testing the real Oracle target" -ForegroundColor Cyan
    # Reuse an existing target with the same alias; never erase existing targets.
    # Collection continues with the language model disabled. Knowledge search is separate.
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
    Write-Host '[PASS] Real Oracle connection established while AI is disabled' -ForegroundColor Green
    Write-Host 'Waiting for a collection that starts during this run. Existing stale rows do not count.'
    $CollectionStarted = [DateTimeOffset]::UtcNow
    $Deadline = (Get-Date).AddSeconds($CollectionWaitSeconds)
    do {
        $Detail = Invoke-RestMethod "$BaseUrl/api/v1/databases/$($Target.id)"
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
        Write-Warning 'No probe completed after this launcher started. Knowledge checks will continue. Inspect the backend log if collection stays stale.'
    }

    Write-Host "`n6. Planting one verification note and one corrupt PDF" -ForegroundColor Cyan
    $RunId = [guid]::NewGuid().ToString('N')
    $Token = "phase5live$RunId"
    $FixtureDir = Join-Path $Root 'knowledge-intake\Knowledge\test-real-phase5'
    New-Item -ItemType Directory -Force -Path $FixtureDir | Out-Null
    $NoteRelative = "Knowledge/test-real-phase5/$Token.md"
    $CorruptRelative = "Knowledge/test-real-phase5/$Token-corrupt.pdf"
    $NotePath = Join-Path $FixtureDir "$Token.md"
    $CorruptPath = Join-Path $FixtureDir "$Token-corrupt.pdf"
    $Note = @"
# Phase 5 live verification $Token

$Token is the unique token for this live knowledge check.
This note is reference text. It does not change a database and it is not executed.
"@
    $Utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($NotePath, $Note, $Utf8)
    [System.IO.File]::WriteAllBytes($CorruptPath, [System.Text.Encoding]::ASCII.GetBytes('not-a-real-pdf'))
    Write-Host "Verification note: $NoteRelative"

    Write-Host "`n7. Reindexing Knowledge and Policy on PostgreSQL" -ForegroundColor Cyan
    $Report = Invoke-Knowledge -Method Post -Path '/api/v1/knowledge/reindex' -Headers $AdminHeaders -Body '{}' -TimeoutSec 300
    $ReindexErrors = @($Report.errors | Where-Object { $_ })
    Write-Host "Reindex: indexed=$($Report.indexed) updated=$($Report.updated) unchanged=$($Report.unchanged) quarantined=$($Report.quarantined) deleted=$($Report.deleted) errors=$($ReindexErrors.Count)"
    $OwnErrors = @($ReindexErrors | Where-Object { $_ -like "*$Token.md*" })
    if ($OwnErrors.Count -gt 0) { throw "Verification note failed to index: $($OwnErrors -join '; ')" }
    if ($Report.quarantined -lt 1) { throw 'Reindex did not quarantine the planted corrupt PDF.' }
    if (-not (Test-Path -LiteralPath $NotePath)) { throw 'Verification note is missing after reindex.' }
    if (Test-Path -LiteralPath $CorruptPath) { throw 'Corrupt PDF was left in the Knowledge folder.' }
    $QuarantineLog = Join-Path $Root 'knowledge-intake\Quarantine\quarantine.log'
    $QuarantineHit = @(Select-String -LiteralPath $QuarantineLog -SimpleMatch $CorruptRelative)
    if ($QuarantineHit.Count -lt 1) { throw 'Quarantine log has no entry for the planted corrupt PDF.' }
    $QuarantineRecord = $QuarantineHit[-1].Line | ConvertFrom-Json
    if ($QuarantineRecord.reason -ne 'corrupt') { throw "Corrupt PDF was quarantined as '$($QuarantineRecord.reason)' instead of corrupt." }
    Write-Host "[PASS] Corrupt PDF quarantined as $($QuarantineRecord.destination)" -ForegroundColor Green

    Write-Host "`n8. Verifying cited search without a language model" -ForegroundColor Cyan
    $Listed = Invoke-Knowledge -Method Get -Path "/api/v1/knowledge/documents?page=1&page_size=100&q=$Token" -Headers $OperatorHeaders
    $Notes = @($Listed.items | Where-Object { $_.relative_path -eq $NoteRelative -and $_.ingestion_status -eq 'indexed' })
    if ($Notes.Count -ne 1) { throw 'Operator cannot see exactly one indexed verification note.' }
    if ($Notes[0].embedding_provider -ne 'local') { throw 'Verification note was not embedded with the local provider.' }
    $VerificationDocumentId = $Notes[0].id
    Write-Host "[PASS] Indexed $($Notes[0].file_name) version $($Notes[0].version) with local embeddings" -ForegroundColor Green

    $LiveSearch = Invoke-Knowledge -Method Post -Path '/api/v1/knowledge/search' -Headers $OperatorHeaders `
        -Body (@{ query = $Token; limit = 8; rerank = $true } | ConvertTo-Json)
    if ($LiveSearch.llm_used) { throw 'Knowledge search reported that a language model was used.' }
    if ($LiveSearch.retrieval -ne 'hybrid') { throw 'Knowledge search did not report hybrid retrieval.' }
    $LiveHits = @($LiveSearch.items | Where-Object { $_.citation.relative_path -eq $NoteRelative })
    if ($LiveHits.Count -lt 1) { throw 'Search did not cite the verification note.' }
    if ([string]::IsNullOrWhiteSpace($LiveHits[0].citation.heading)) { throw 'Verification citation has no section heading.' }
    if ($LiveHits[0].excerpt -notlike "*$Token*") { throw 'Cited excerpt does not contain the verification token.' }
    Write-Host "[PASS] Citation: $($LiveHits[0].citation.relative_path) version $($LiveHits[0].citation.version) · $($LiveHits[0].citation.heading)" -ForegroundColor Green

    $RunbookSearch = Invoke-Knowledge -Method Post -Path '/api/v1/knowledge/search' -Headers $OperatorHeaders `
        -Body (@{ query = 'ORA-00257 fast recovery area'; limit = 8; category = 'Knowledge'; rerank = $true } | ConvertTo-Json)
    $RunbookHits = @($RunbookSearch.items | Where-Object { $_.citation.relative_path -eq 'Knowledge/internal-runbooks/ora-00257.md' })
    if ($RunbookHits.Count -lt 1) { throw 'Search did not cite Knowledge/internal-runbooks/ora-00257.md.' }
    Write-Host "[PASS] Runbook citation: $($RunbookHits[0].citation.relative_path) · $($RunbookHits[0].citation.heading)" -ForegroundColor Green

    $PolicySearch = Invoke-Knowledge -Method Post -Path '/api/v1/knowledge/search' -Headers $OperatorHeaders `
        -Body (@{ query = 'ARCHIVELOG standard'; limit = 8; category = 'Policy'; rerank = $true } | ConvertTo-Json)
    $PolicyHits = @($PolicySearch.items | Where-Object { $_.citation.relative_path -eq 'Policy/archivelog-standard.md' -and $_.authoritative_policy })
    if ($PolicyHits.Count -lt 1) { throw 'Policy search did not cite Policy/archivelog-standard.md as authoritative.' }
    Write-Host "[PASS] Policy citation: $($PolicyHits[0].citation.relative_path) · $($PolicyHits[0].citation.heading)" -ForegroundColor Green

    Write-Host "`n9. Verifying pgvector and PostgreSQL full-text search" -ForegroundColor Cyan
    $VectorOutput = & $Python $Runtime 'check-vector' '--query' $Token
    if ($LASTEXITCODE -ne 0) { throw 'Live pgvector check failed. See the safe error above.' }
    $Vector = $VectorOutput | ConvertFrom-Json
    $IndexNames = @($Vector.indexes)
    if (-not $Vector.vector_extension) { throw 'PostgreSQL does not have the vector extension installed.' }
    if ($Vector.embedded_chunks -lt 1) { throw 'No embedded chunks are stored in PostgreSQL.' }
    if ($null -eq $Vector.self_distance -or [math]::Abs([double]$Vector.self_distance) -gt 0.000001) {
        throw 'pgvector cosine distance of a stored embedding against itself was not zero.'
    }
    if ($Vector.keyword_hits -lt 1) { throw 'PostgreSQL full-text search did not match the verification token.' }
    foreach ($IndexName in @('ix_document_chunks_embedding_cosine', 'ix_document_chunks_content_tsv')) {
        if ($IndexNames -notcontains $IndexName) { throw "Missing PostgreSQL index $IndexName." }
    }
    Write-Host "[PASS] pgvector extension, cosine index, full-text index, and $($Vector.embedded_chunks) embedded chunks" -ForegroundColor Green

    Write-Host "`n10. Verifying ACL hides the note from an operator" -ForegroundColor Cyan
    $RestrictedDocumentId = $VerificationDocumentId
    Invoke-Knowledge -Method Post -Path "/api/v1/knowledge/documents/$RestrictedDocumentId/acl" `
        -Headers $AdminHeaders -Body '{"roles":["dba"],"principals":[]}' | Out-Null
    $HiddenList = Invoke-Knowledge -Method Get -Path "/api/v1/knowledge/documents?page=1&page_size=100&q=$Token" -Headers $OperatorHeaders
    if (@($HiddenList.items).Count -ne 0) { throw 'Operator can still list the dba-only verification note.' }
    $HiddenSearch = Invoke-Knowledge -Method Post -Path '/api/v1/knowledge/search' -Headers $OperatorHeaders `
        -Body (@{ query = $Token; limit = 8; rerank = $true } | ConvertTo-Json)
    if (@($HiddenSearch.items | Where-Object { $_.citation.relative_path -eq $NoteRelative }).Count -ne 0) {
        throw 'Operator search still returns the dba-only verification note.'
    }
    $DbaSearch = Invoke-Knowledge -Method Post -Path '/api/v1/knowledge/search' -Headers $DbaHeaders `
        -Body (@{ query = $Token; limit = 8; rerank = $true } | ConvertTo-Json)
    if (@($DbaSearch.items | Where-Object { $_.citation.relative_path -eq $NoteRelative }).Count -lt 1) {
        throw 'DBA search cannot see the dba-only verification note.'
    }
    $AdminList = Invoke-Knowledge -Method Get -Path "/api/v1/knowledge/documents?page=1&page_size=100&q=$Token" -Headers $AdminHeaders
    if (@($AdminList.items | Where-Object { $_.id -eq $RestrictedDocumentId }).Count -ne 1) {
        throw 'Admin can no longer see the verification note, so its ACL could not be repaired.'
    }
    Write-Host '[PASS] Operator cannot see the restricted note; dba and admin can' -ForegroundColor Green
    Restore-VerificationAcl

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
    Write-Host "`nPortal (Knowledge search): $Portal/#/knowledge" -ForegroundColor Green
    Write-Host "Portal (database detail):  $Portal/#/databases/$($Target.id)"
    Write-Host "API documentation: $BaseUrl/docs"
    Write-Host "Search the portal for $Token. The page does not call a language model."
    Write-Host 'The verification note and quarantined PDF remain. PostgreSQL, targets, and the knowledge index stay.'
    Write-Warning 'This is a local pre-authentication development app. Do not expose it publicly. Reindex sends X-Sentinel-Role: admin until Phase 7 adds sign-in.'
    if ($Interactive) {
        Start-Process "$Portal/#/knowledge"
        Read-Host 'Press Enter when finished to stop the portal/backend (PostgreSQL, documents, and quarantine files stay)' | Out-Null
    } else {
        Write-Host 'Use -Interactive to keep the portal open; smoke-check services will now stop.'
    }
}
finally {
    try { Restore-VerificationAcl }
    catch { Write-Warning 'Could not restore the verification document ACL. Repair it as admin before the next run.' }
    try { Stop-OwnedProcess $Dashboard }
    finally {
        try { Stop-OwnedProcess $Backend }
        finally { Pop-Location }
    }
}
