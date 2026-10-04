# Build the verify image that the container check runner uses (redesign D2).
#
#   . .\scripts\toolchain\activate.ps1
#   .\scripts\toolchain\build-verify-image.ps1
#
# The image is built from Dockerfile.ingestion, which carries the pinned toolchain and the
# hash-locked Python dependencies (TC-174). It is tagged foss-mcp-verify:local, the name that
# ops/containerrun.py VERIFY_IMAGE expects. This script records the image id it produced, so a
# receipt's runner choice can be traced to a concrete image.

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repoRoot

docker build -f Dockerfile.ingestion -t foss-mcp-verify:local .
if ($LASTEXITCODE -ne 0) { throw "docker build failed with exit code $LASTEXITCODE" }

$id = (docker image inspect foss-mcp-verify:local --format '{{.Id}}').Trim()
$record = Join-Path $repoRoot 'scripts\toolchain\verify-image.id'
Set-Content -Path $record -Value $id -Encoding ascii
Write-Host "foss-mcp-verify:local -> $id (recorded in scripts/toolchain/verify-image.id)"
