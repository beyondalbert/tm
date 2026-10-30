# Release TM (The Machine) from Windows PowerShell.
#
# Bumps the version, runs the checks, builds the artifacts, commits and pushes,
# tags the release, creates the GitHub Release, and triggers the PyPI publish
# workflow. If the release-triggered workflow fails to start or publish, it
# falls back to a manual workflow_dispatch for target=pypi.
#
# Examples:
#   .\scripts\release.ps1
#   .\scripts\release.ps1 -Bump minor
#   .\scripts\release.ps1 -Version 1.0.0
#   .\scripts\release.ps1 -Message "feat(core): add x" -Paths src\tm\x.py tests\test_x.py
#   .\scripts\release.ps1 -SkipChecks
#
# Authentication (only needed for GitHub Release + workflow dispatch):
#   - GITHUB_TOKEN or GH_TOKEN, or
#   - the credential already stored for github.com (git credential fill).
#
# PyPI itself is published by GitHub Actions with Trusted Publishing; this
# script never handles a PyPI token.

[CmdletBinding()]
param(
    [ValidateSet("patch", "minor", "major")]
    [string] $Bump = "patch",
    [string] $Version,
    [string] $Message,
    [string[]] $Paths = @(),
    [string] $Branch = "main",
    [switch] $SkipChecks,
    [switch] $NoGitHubRelease,
    [switch] $Yes,
    [int] $TimeoutMinutes = 20
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Join-Path $PSScriptRoot "..")

function Step([string] $name) {
    Write-Host "`n[release] $name" -ForegroundColor Cyan
}

# Native tools write progress to stderr. With $ErrorActionPreference = "Stop"
# PowerShell can treat that as a terminating error, so wrap native calls.
function Invoke-Native([scriptblock] $block) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $block } finally { $ErrorActionPreference = $prev }
}

function Run([string] $name, [scriptblock] $block) {
    Step $name
    Invoke-Native $block
    if ($LASTEXITCODE -ne 0) {
        throw "$name failed (exit $LASTEXITCODE)"
    }
}

function Get-RepoInfo {
    $url = (git remote get-url origin).Trim()
    if ($url -match "github\.com[:/](?<owner>[^/]+)/(?<repo>[^/]+?)(?:\.git)?/?$") {
        return @{ Owner = $Matches.owner; Name = $Matches.repo }
    }
    throw "origin is not a GitHub remote: $url"
}

function Get-GitHubToken {
    if ($env:GITHUB_TOKEN) { return $env:GITHUB_TOKEN }
    if ($env:GH_TOKEN) { return $env:GH_TOKEN }
    $prev = $env:GIT_TERMINAL_PROMPT
    $env:GIT_TERMINAL_PROMPT = "0"
    try {
        $lines = @("protocol=https", "host=github.com", "") | & git credential fill 2>$null
    }
    finally {
        if ($null -eq $prev) {
            Remove-Item Env:GIT_TERMINAL_PROMPT -ErrorAction SilentlyContinue
        }
        else {
            $env:GIT_TERMINAL_PROMPT = $prev
        }
    }
    $password = @($lines) | Where-Object { $_ -like "password=*" } | Select-Object -First 1
    if ($password) { return $password.Substring("password=".Length) }
    return $null
}

function Invoke-GitHub([string] $method, [string] $path, $body = $null) {
    $headers = @{
        Accept                 = "application/vnd.github+json"
        Authorization          = "Bearer $script:GitHubToken"
        "X-GitHub-Api-Version" = "2022-11-28"
    }
    $uri = "https://api.github.com$path"
    try {
        if ($null -ne $body) {
            $json = $body | ConvertTo-Json -Depth 6 -Compress
            return Invoke-RestMethod -Method $method -Uri $uri -Headers $headers -Body $json -ContentType "application/json"
        }
        return Invoke-RestMethod -Method $method -Uri $uri -Headers $headers
    }
    catch {
        $detail = $_.ErrorDetails.Message
        if (-not $detail) { $detail = $_.Exception.Message }
        throw "GitHub API $method $path failed: $detail"
    }
}

function Wait-WorkflowRun([string] $runId, [int] $timeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $run = Invoke-GitHub "GET" "/repos/$($repo.Owner)/$($repo.Name)/actions/runs/$runId"
        Write-Host "  run ${runId}: $($run.status) / $($run.conclusion)"
        if ($run.status -eq "completed") { return $run }
        Start-Sleep -Seconds 15
    }
    throw "workflow run $runId did not finish within $timeoutSeconds seconds"
}

function Start-PublishDispatch {
    $before = @(Invoke-GitHub "GET" "/repos/$($repo.Owner)/$($repo.Name)/actions/runs?event=workflow_dispatch&per_page=10").workflow_runs
    $beforeIds = @($before | ForEach-Object { $_.id })
    Invoke-GitHub "POST" "/repos/$($repo.Owner)/$($repo.Name)/actions/workflows/publish.yml/dispatches" @{
        ref    = $Branch
        inputs = @{ target = "pypi" }
    } | Out-Null
    Write-Host "  dispatched publish.yml (target=pypi)"

    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        $runs = @(Invoke-GitHub "GET" "/repos/$($repo.Owner)/$($repo.Name)/actions/runs?event=workflow_dispatch&per_page=10").workflow_runs
        $run = $runs | Where-Object { $beforeIds -notcontains $_.id } | Select-Object -First 1
        if ($run) { return (Wait-WorkflowRun $run.id ($TimeoutMinutes * 60)) }
    }
    throw "publish.yml was dispatched but no workflow run appeared"
}

function Wait-ForPyPI([string] $expectedVersion, [int] $timeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $info = Invoke-RestMethod -Uri "https://pypi.org/pypi/the-machine/json" -TimeoutSec 30
            if ($info.info.version -eq $expectedVersion) { return }
        }
        catch {
            # PyPI can be briefly unavailable; keep polling.
        }
        Start-Sleep -Seconds 5
    }
    throw "PyPI did not report the-machine $expectedVersion within $timeoutSeconds seconds"
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git is not on PATH"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is not on PATH: https://docs.astral.sh/uv/"
}

$repo = Get-RepoInfo
$currentBranch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($currentBranch -ne $Branch) {
    throw "current branch is '$currentBranch'; release from '$Branch' or pass -Branch"
}
if (Test-Path ".git\MERGE_HEAD") {
    throw "a merge is in progress"
}

# Optional feature commit. New files must be named explicitly with -Paths; only
# tracked changes are staged by default, so this never runs `git add -A`.
$status = @(git status --porcelain)
$trackedChanges = @($status | Where-Object { $_ -notmatch "^\?\?" })
if ($Message) {
    if ($Paths.Count -gt 0) {
        Run "staging release changes" { git add -- @Paths }
    }
    else {
        $untracked = @($status | Where-Object { $_ -match "^\?\?" })
        if ($untracked.Count -gt 0) {
            Write-Host "[release] untracked files are not staged:" -ForegroundColor Yellow
            $untracked | ForEach-Object { Write-Host "  $_" }
            Write-Host "[release] pass -Paths to include new files" -ForegroundColor Yellow
        }
        Run "staging tracked changes" { git add -u }
    }
    Run "committing release changes" { git commit -m $Message }
}
elseif ($trackedChanges.Count -gt 0) {
    Write-Host "[release] working tree has uncommitted tracked changes:" -ForegroundColor Yellow
    $trackedChanges | ForEach-Object { Write-Host "  $_" }
    throw "commit them first, or pass -Message (and -Paths for new files)"
}

$status = @(git status --porcelain)
if (@($status | Where-Object { $_ -notmatch "^\?\?" }).Count -gt 0) {
    throw "working tree still has tracked changes; refusing to release"
}

if (-not $SkipChecks) {
    Run "ruff" { uv run ruff check src tests }
    Run "mypy" { uv run mypy src tests }
    Run "tests" { uv run pytest -q }
}

if ($Version) {
    Run "setting version $Version" { uv version $Version }
}
else {
    Run "bumping version ($Bump)" { uv version --bump $Bump }
}

$match = Select-String -Path "pyproject.toml" -Pattern '^version = "([^"]+)"' | Select-Object -First 1
if (-not $match) { throw "could not read version from pyproject.toml" }
$newVersion = $match.Matches[0].Groups[1].Value
$tag = "v$newVersion"

if (-not $Yes) {
    $answer = Read-Host "Release $newVersion to PyPI and push $tag? [y/N]"
    if ($answer -notmatch "^(y|yes)$") { throw "cancelled" }
}

# Build before committing the bump, so a broken release never gets tagged.
if (Test-Path "dist") {
    Get-ChildItem "dist\the_machine-*" -ErrorAction SilentlyContinue | Remove-Item -Force
}
Run "building wheel and sdist" { uv build }
$distFiles = @(Get-ChildItem "dist\the_machine-$newVersion*" | ForEach-Object { $_.FullName })
if ($distFiles.Count -eq 0) { throw "no artifacts found for $newVersion under dist/" }
Run "checking metadata" { uvx --from "twine>=7" twine check @distFiles }

Run "committing version bump" { git add pyproject.toml uv.lock; git commit -m "chore(release): $newVersion" }
Run "pushing $Branch" { git push origin $Branch }

$existingTag = git tag --list $tag
if ($existingTag) {
    throw "tag $tag already exists locally; delete it or release a new version"
}
Run "tagging $tag" { git tag $tag }
Run "pushing $tag" { git push origin $tag }

$script:GitHubToken = Get-GitHubToken
if (-not $script:GitHubToken) {
    Write-Host "[release] no GitHub token available; commit, tag, and push are done." -ForegroundColor Yellow
    Write-Host "Set GITHUB_TOKEN, or open the release workflow manually:" -ForegroundColor Yellow
    Write-Host "  https://github.com/$($repo.Owner)/$($repo.Name)/actions/workflows/publish.yml" -ForegroundColor Yellow
    exit 0
}

$publishRun = $null
if (-not $NoGitHubRelease) {
    Step "creating GitHub Release $tag"
    $release = Invoke-GitHub "POST" "/repos/$($repo.Owner)/$($repo.Name)/releases" @{
        tag_name               = $tag
        name                   = $tag
        draft                  = $false
        prerelease             = $false
        generate_release_notes = $true
    }
    Write-Host "  $($release.html_url)"

    # The published release triggers the pypi job. If GitHub never starts it
    # (startup_failure) or the job fails, fall back to workflow_dispatch.
    $headSha = (git rev-parse HEAD).Trim()
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline -and -not $publishRun) {
        Start-Sleep -Seconds 5
        $runs = @(Invoke-GitHub "GET" "/repos/$($repo.Owner)/$($repo.Name)/actions/runs?event=release&per_page=10").workflow_runs
        $publishRun = $runs | Where-Object { $_.head_sha -eq $headSha } | Select-Object -First 1
    }
    if ($publishRun) {
        $publishRun = Wait-WorkflowRun $publishRun.id ($TimeoutMinutes * 60)
    }
}

if (-not $publishRun -or $publishRun.conclusion -ne "success") {
    if ($publishRun) {
        Write-Host "[release] release-triggered publish ended as '$($publishRun.conclusion)': $($publishRun.html_url)" -ForegroundColor Yellow
    }
    $publishRun = Start-PublishDispatch
}

if ($publishRun.conclusion -ne "success") {
    throw "PyPI publish failed: $($publishRun.html_url)"
}

Step "verifying PyPI"
Wait-ForPyPI $newVersion 90
Write-Host "  PyPI reports the-machine $newVersion"

Write-Host "`n[release] done: $newVersion" -ForegroundColor Green
Write-Host "  tag:     $tag"
if ($release) { Write-Host "  release: $($release.html_url)" }
Write-Host "  publish: $($publishRun.html_url)"
Write-Host "  verify:  pip install -U the-machine==$newVersion"
