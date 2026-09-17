# Release smoke test for TM (The Machine) on Windows (PowerShell).
#
# Builds the wheel and sdist, validates metadata, then installs the wheel into a
# throwaway virtual environment and runs `tm --version` to prove the packaged CLI
# actually works before you tag or upload. Mirrors the local checks in RELEASE.md.
#
#   .\scripts\release-smoke.ps1                 # build from the working tree
#   .\scripts\release-smoke.ps1 -FromPyPI       # install the released sdist/wheel from PyPI
#   .\scripts\release-smoke.ps1 -Version 0.1.6  # assert a specific version after install
#
# Exit code 0 means: build ok, metadata ok, clean install ok, CLI runs.

[CmdletBinding()]
param(
    [string] $Version,
    [switch] $FromPyPI,
    [switch] $KeepVenv,
    [string] $IndexUrl = "https://pypi.org/simple/"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Join-Path $PSScriptRoot "..")

if (Get-Command uv -ErrorAction SilentlyContinue) {
    $uv = "uv"
} else {
    Write-Error "Could not find 'uv' on PATH. Install uv: https://docs.astral.sh/uv/"
    exit 1
}

$venv = Join-Path $PWD ".venv-smoke"
$py = Join-Path $venv "Scripts\python.exe"
$tm = Join-Path $venv "Scripts\tm.exe"

function Step($name) { Write-Host "[smoke] $name" -ForegroundColor Cyan }

# Native tools (uv, tm) write progress to stderr. Under $ErrorActionPreference
# = "Stop" PowerShell would treat that as a terminating error, so wrap each call.
function Invoke-Native([scriptblock] $block) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $block } finally { $ErrorActionPreference = $prev }
}

try {
    if ($FromPyPI) {
        if (-not $Version) {
            Write-Error "-FromPyPI requires -Version (e.g. -Version 0.1.6)"
            exit 1
        }
        Step "creating isolated venv"
        if (Test-Path $venv) { Remove-Item -Recurse -Force $venv }
        Invoke-Native { & $uv venv $venv --python 3.12 }
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        Step "installing the-machine==$Version from $IndexUrl"
        Invoke-Native { & $uv pip install --python $py --index-url $IndexUrl "the-machine==$Version" }
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else {
        Step "building wheel and sdist"
        Invoke-Native { & $uv build }
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        Step "checking metadata (twine check)"
        $distFiles = Get-ChildItem dist\the_machine-* | ForEach-Object { $_.FullName }
        if (-not $distFiles) { Write-Error "no artifacts found under dist/"; exit 1 }
        Invoke-Native { & uvx twine check @distFiles }
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        $wheel = Get-ChildItem dist\the_machine-*.whl | Sort-Object LastWriteTime | Select-Object -Last 1
        if (-not $wheel) { Write-Error "no wheel found under dist/"; exit 1 }

        Step "creating isolated venv"
        if (Test-Path $venv) { Remove-Item -Recurse -Force $venv }
        Invoke-Native { & $uv venv $venv --python 3.12 }
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        Step "installing $($wheel.Name)"
        Invoke-Native { & $uv pip install --python $py $wheel.FullName }
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    Step "running tm --version"
    $reported = $null
    Invoke-Native { $script:reported = (& $tm --version).Trim() }
    Write-Host "[smoke] tm reports version $reported"
    if ($Version -and $reported -ne $Version) {
        Write-Error "version mismatch: expected $Version, got $reported"
        exit 1
    }

    Step "running tm --list-models"
    Invoke-Native { & $tm --list-models | Out-Null }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "[smoke] OK" -ForegroundColor Green
    exit 0
}
finally {
    if (-not $KeepVenv -and (Test-Path $venv)) {
        Remove-Item -Recurse -Force $venv -ErrorAction SilentlyContinue
    }
}
