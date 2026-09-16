# Launch The Machine (TM) from source on Windows (PowerShell).
#
#   .\run.ps1                 # Textual TUI
#   .\run.ps1 --no-tui        # console REPL
#   .\run.ps1 "prompt"        # one-shot agent run
#   .\run.ps1 --login deepseek
#   .\run.ps1 --help
#
# Runs via `uv run` against the editable install, so source edits apply
# immediately and the global `tm` install is left untouched.

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $AppArgs
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (Get-Command uv -ErrorAction SilentlyContinue) {
    $uvExe = "uv"
    $uvPrefix = @()
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $uvExe = "python"
    $uvPrefix = @("-m", "uv")
} else {
    Write-Error "Could not find 'uv' or 'python' on PATH. Install uv: https://docs.astral.sh/uv/"
    exit 1
}

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot ".venv"))) {
    Write-Host "[tm] First run: installing dependencies..." -ForegroundColor Cyan
    & $uvExe @($uvPrefix + @("sync", "--all-extras"))
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $uvExe @($uvPrefix + @("run", "tm") + $AppArgs)
exit $LASTEXITCODE
