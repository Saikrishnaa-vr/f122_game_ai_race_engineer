param([switch]$Apply)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo = 'Saikrishnaa-vr/f122_game_ai_race_engineer'
$archiveName = 'f122_game_ai_race_engineer_private_archive_20261029'
$archive = "Saikrishnaa-vr/$archiveName"
$root = Split-Path -Parent $PSScriptRoot
$gitArgs = @('-c', 'safe.directory=D:/games/F1 22/f1-ai-race-engineer', '-C', $root)
$oldHead = 'f9dcfdf74dc4b74831a6f7d93156e9bf9df6268e'
$cleanHead = '143fe9d6a40a0221fe649da4284b7a3cdcbc32e9'
$noreply = '218748147+Saikrishnaa-vr@users.noreply.github.com'

function Invoke-Git {
    param([string[]]$Arguments)
    $output = & git @gitArgs @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Git failed: git $($Arguments -join ' ')"
    }
    return $output
}

function Invoke-Gh {
    param([string[]]$Arguments)
    $output = & gh @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub CLI failed: gh $($Arguments -join ' ')"
    }
    return $output
}

function Get-GhJson {
    param([string[]]$Arguments)
    $output = Invoke-Gh -Arguments $Arguments
    return ConvertFrom-Json -InputObject ($output -join "`n")
}

foreach ($command in @('git', 'gh')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "$command is required in PATH."
    }
}
if (@(Invoke-Git -Arguments @('status', '--porcelain')).Count -gt 0) {
    throw 'Commit or discard local changes before publishing.'
}
foreach ($branch in @('main', 'develop')) {
    $head = (Invoke-Git -Arguments @('rev-parse', "refs/heads/$branch") | Select-Object -First 1)
    if ($head -ne $cleanHead) {
        throw "$branch must point at the reviewed clean baseline $cleanHead; found $head."
    }
}
$emails = @(Invoke-Git -Arguments @('log', 'main', 'develop',
    'feature/github-branch-protection', '--format=%ae|%ce') | Sort-Object -Unique)
if ($emails.Count -ne 1 -or $emails[0] -ne "$noreply|$noreply") {
    throw 'Branch history contains an unexpected commit email. Refusing to publish.'
}

Write-Host "Plan: retain $repo as a private archive named $archive."
Write-Host "Plan: create a fresh public $repo from clean noreply history."
Write-Host 'Plan: push main/develop, require PR and Validation, then open a feature PR to develop.'
Write-Host 'The new public repository will expose the two committed raw race captures.'
if (-not $Apply) {
    Write-Host 'Dry run only. Run with -Apply to publish.'
    return
}

$original = Get-GhJson -Arguments @('api', "repos/$repo")
if ($original.full_name -cne $repo -or -not $original.private -or
    -not $original.permissions.admin) {
    throw 'Expected the original private repository and owner admin access.'
}
$remoteMain = Get-GhJson -Arguments @('api', "repos/$repo/branches/main")
$remoteDevelop = Get-GhJson -Arguments @('api', "repos/$repo/branches/develop")
if ($remoteMain.commit.sha -ne $oldHead -or $remoteDevelop.commit.sha -ne $oldHead) {
    throw 'Remote branches changed since the public-history review. No repository was renamed.'
}

Invoke-Gh -Arguments @('repo', 'rename', $archiveName, '-R', $repo, '--yes') | Out-Null
$archived = Get-GhJson -Arguments @('api', "repos/$archive")
if ($archived.id -ne $original.id -or -not $archived.private) {
    throw 'The private archive was not verified. Stop and inspect GitHub.'
}
Write-Host "Original repository preserved privately at $archive."

Invoke-Gh -Arguments @('repo', 'create', $repo, '--public', '--description',
    'F1 22 race engineer and telemetry dashboard') | Out-Null
$published = Get-GhJson -Arguments @('api', "repos/$repo")
if ($published.id -eq $original.id -or $published.private -or
    $published.full_name -cne $repo) {
    throw 'The fresh public repository was not verified. Stop and inspect GitHub.'
}

Invoke-Git -Arguments @('remote', 'set-url', 'origin',
    "https://github.com/$repo.git") | Out-Null

Invoke-Git -Arguments @('push', '-u', 'origin', 'main', 'develop') | Out-Null
foreach ($branch in @('main', 'develop')) {
    $remote = Get-GhJson -Arguments @('api', "repos/$repo/branches/$branch")
    if ($remote.commit.sha -ne $cleanHead) {
        throw "$branch did not land at the clean baseline. Stop and inspect GitHub."
    }
}
if ($published.default_branch -ne 'main') {
    Invoke-Gh -Arguments @('repo', 'edit', $repo, '--default-branch', 'main') | Out-Null
}

& (Join-Path $PSScriptRoot 'configure_github_protection.ps1') -Apply
if (-not $?) {
    throw 'Branch rules did not finish. Stop and inspect GitHub.'
}
Invoke-Git -Arguments @('push', '-u', 'origin',
    'feature/github-branch-protection') | Out-Null
Invoke-Gh -Arguments @('pr', 'create', '-R', $repo,
    '--base', 'develop', '--head', 'feature/github-branch-protection',
    '--title', 'Set up owner-controlled branch rules',
    '--body', 'Adds the ruleset setup script and documents the public GitHub Free branch policy. Main remains at the original product baseline; this PR targets develop only.') | Out-Host
Write-Host 'Public repository, branch rules, and feature PR created. Main was not merged.'
