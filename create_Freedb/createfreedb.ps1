# =====================================================================
# Oracle Database Free 23ai Docker Setup
# Host Port: 1629
# Container Port: 1521
# =====================================================================

$ContainerName = "oracle-free"
$VolumeName    = "oracle-free-data"
$DbPassword    = "MyStrongPassword123"
$HostPort      = "1629"

Write-Host "======================================="
Write-Host "Oracle Database Free Docker Installer"
Write-Host "======================================="

# ---------------------------------------------------------------------
# Verify Docker
# ---------------------------------------------------------------------

Write-Host "`nChecking Docker..."

docker version

if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker Desktop is not running."
    exit 1
}

# ---------------------------------------------------------------------
# Create Volume
# ---------------------------------------------------------------------

Write-Host "`nCreating volume if not exists..."

docker volume create $VolumeName

# ---------------------------------------------------------------------
# Pull Oracle Free Image
# ---------------------------------------------------------------------

Write-Host "`nPulling Oracle Database Free image..."

docker pull container-registry.oracle.com/database/free:latest

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Failed to pull image."
    Write-Host "You may need to:"
    Write-Host "1. Create an Oracle account"
    Write-Host "2. Accept the Oracle Free Database container licence"
    Write-Host "3. Login using:"
    Write-Host "   docker login container-registry.oracle.com"
    exit 1
}

# ---------------------------------------------------------------------
# Remove Existing Container
# ---------------------------------------------------------------------

Write-Host "`nRemoving existing container if present..."

docker stop $ContainerName 2>$null
docker rm $ContainerName 2>$null

# ---------------------------------------------------------------------
# Start New Container
# ---------------------------------------------------------------------

Write-Host "`nStarting Oracle Database..."

docker run -d `
    --name $ContainerName `
    -p ${HostPort}:1521 `
    -p 5500:5500 `
    -e ORACLE_PWD=$DbPassword `
    -v ${VolumeName}:/opt/oracle/oradata `
    container-registry.oracle.com/database/free:latest

# -------------------------