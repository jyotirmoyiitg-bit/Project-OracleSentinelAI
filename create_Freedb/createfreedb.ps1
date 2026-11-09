# =====================================================================
# Oracle Database Free 23ai Docker Setup
# Database Port : 1629
# Service Names : FREE, FREEPDB1
# =====================================================================

$ContainerName = "oracle-free"
$VolumeName    = "oracle-free-data"
$DbPassword    = "MyStrongPassword123"
$HostPort      = "1629"

Write-Host ""
Write-Host "======================================="
Write-Host "Oracle Database Free Setup"
Write-Host "======================================="
Write-Host ""

# ---------------------------------------------------------------------
# Verify Docker is running
# ---------------------------------------------------------------------

Write-Host "Checking Docker..."

docker version | Out-Null

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: Docker Desktop is not running."
    exit 1
}

Write-Host "Docker OK."
Write-Host ""

# ---------------------------------------------------------------------
# Create persistent volume
# ---------------------------------------------------------------------

Write-Host "Creating persistent volume..."

docker volume inspect $VolumeName *> $null

if ($LASTEXITCODE -ne 0) {
    docker volume create $VolumeName
    Write-Host "Volume created."
}
else {
    Write-Host "Volume already exists."
}

Write-Host ""

# ---------------------------------------------------------------------
# Pull latest image
# ---------------------------------------------------------------------

Write-Host "Pulling Oracle Free image..."

docker pull container-registry.oracle.com/database/free:latest

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Failed to pull image."
    Write-Host "You may need to login first:"
    Write-Host ""
    Write-Host "docker login container-registry.oracle.com"
    exit 1
}

Write-Host ""

# ---------------------------------------------------------------------
# Remove existing container
# ---------------------------------------------------------------------

Write-Host "Removing existing container if present..."

docker stop $ContainerName 2>$null | Out-Null
docker rm   $ContainerName 2>$null | Out-Null

Write-Host ""

# ---------------------------------------------------------------------
# Start Oracle
# ---------------------------------------------------------------------

Write-Host "Starting Oracle Database..."

docker run -d `
    --name $ContainerName `
    -p ${HostPort}:1521 `
    -e ORACLE_PWD=$DbPassword `
    -v ${VolumeName}:/opt/oracle/oradata `
    container-registry.oracle.com/database/free:latest

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Container failed to start."
    exit 1
}

Write-Host ""
Write-Host "Container started."
Write-Host ""

# ---------------------------------------------------------------------
# Show status
# ---------------------------------------------------------------------

Write-Host "Current Containers:"
docker ps

Write-Host ""
Write-Host "===================================================="
Write-Host "Oracle Database Creation In Progress"
Write-Host "===================================================="
Write-Host ""
Write-Host "Monitor startup:"
Write-Host ""
Write-Host "docker logs -f oracle-free"
Write-Host ""
Write-Host "Wait until you see:"
Write-Host ""
Write-Host "DATABASE IS READY TO USE!"
Write-Host ""

# ---------------------------------------------------------------------
# Connection Information
# ---------------------------------------------------------------------

Write-Host "===================================================="
Write-Host "Connection Information"
Write-Host "===================================================="
Write-Host ""

Write-Host "Host             : localhost"
Write-Host "Port             : 1629"
Write-Host "Password         : $DbPassword"
Write-Host ""

Write-Host "CDB Service      : FREE"
Write-Host "PDB Service      : FREEPDB1"
Write-Host ""

Write-Host "JDBC (PDB)"
Write-Host "jdbc:oracle:thin:@//localhost:1629/FREEPDB1"
Write-Host ""

Write-Host "JDBC (CDB)"
Write-Host "jdbc:oracle:thin:@//localhost:1629/FREE"
Write-Host ""

Write-Host "TNS Alias:"
Write-Host "FREE"
Write-Host "FREEPDB1"