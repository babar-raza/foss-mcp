# Put the repo's pinned toolchain first on PATH for THIS shell only.
#
#   . .\scripts\toolchain\activate.ps1
#
# Nothing here edits the user or system PATH. Git's bash comes first on purpose:
# C:\Windows\System32 or WindowsApps can resolve `bash` to the WSL launcher, which
# breaks .githooks and scripts/ci_check.sh. See tests/test_githooks.py.
# Tools are described in scripts/toolchain/toolchain.lock.json.

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tools = Join-Path $repoRoot 'tools'

$pathDirs = @(
    (Join-Path $tools 'git\bin'),
    (Join-Path $tools 'git\cmd'),
    (Join-Path $tools 'python313'),
    (Join-Path $tools 'python313\Scripts'),
    (Join-Path $tools 'go\bin'),
    (Join-Path $tools 'cmake\bin'),
    (Join-Path $tools 'nodejs'),
    (Join-Path $tools 'maven\bin'),
    (Join-Path $tools 'rp-toolchains\ninja'),
    (Join-Path $tools 'rp-toolchains\npm\prefix'),
    (Join-Path $tools 'adoptium\jdk-21.0.11.10-hotspot\bin')
)

# Rust: the same RUSTUP_HOME and CARGO_HOME pair that tools/rp-toolchains/TOOLCHAIN_PATHS.txt
# documents (cargo 1.98.1). Never the user profile, so no ~/.cargo state is shared.
$env:RUSTUP_HOME = Join-Path $tools 'rp-toolchains\rustup\rustup-home'
$env:CARGO_HOME = Join-Path $tools 'rp-toolchains\rustup\cargo-home'
$pathDirs += (Join-Path $env:CARGO_HOME 'bin')

$env:JAVA_HOME = Join-Path $tools 'adoptium\jdk-21.0.11.10-hotspot'
$env:NPM_CONFIG_PREFIX = Join-Path $tools 'rp-toolchains\npm\prefix'
$env:NPM_CONFIG_CACHE = Join-Path $tools 'rp-toolchains\npm\cache'

# Docker Desktop is installed on C:. Its CLI is added here, not to the user PATH.
$dockerBin = 'C:\Users\babar\AppData\Local\Programs\DockerDesktop\resources\bin'
if (Test-Path $dockerBin) { $pathDirs += $dockerBin }

$existing = @($pathDirs | Where-Object { Test-Path $_ })
$env:Path = (($existing -join ';') + ';' + $env:Path)

Write-Host "foss-mcp toolchain active for this shell (repo: $repoRoot)"
foreach ($probe in @(
    @('git', '--version'), @('bash', '--version'), @('python', '--version'),
    @('go', 'version'), @('cmake', '--version'), @('node', '--version'),
    @('mvn', '-v'), @('javac', '-version'), @('cargo', '--version'), @('docker', '--version')
)) {
    $cmd = Get-Command $probe[0] -ErrorAction SilentlyContinue
    if ($cmd) {
        $line = (& $probe[0] $probe[1] 2>&1 | Select-Object -First 1)
        Write-Host ("  {0,-7} {1}" -f $probe[0], $line)
    } else {
        Write-Host ("  {0,-7} MISSING" -f $probe[0])
    }
}
