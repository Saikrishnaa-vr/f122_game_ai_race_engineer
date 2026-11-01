param(
    [Parameter(Mandatory = $true)][ValidateRange(1, 2147483647)][int]$PullRequest,
    [string]$Repository = 'Saikrishnaa-vr/f122_game_ai_race_engineer'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'GitHub CLI (gh) is required.'
}

function Get-GhJson {
    param([string]$Path)
    $output = & gh api $Path
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub API request failed: $Path"
    }
    return ConvertFrom-Json -InputObject ($output -join "`n")
}

$repositoryInfo = Get-GhJson "repos/$Repository"
if ($repositoryInfo.full_name -cne $Repository) {
    throw "Expected $Repository; found $($repositoryInfo.full_name)."
}
$actions = Get-GhJson "repos/$Repository/actions/permissions"
$workflow = Get-GhJson "repos/$Repository/actions/workflows/validate.yml"
$pr = Get-GhJson "repos/$Repository/pulls/$PullRequest"
if ($pr.state -ne 'open') {
    Write-Warning "PR #$PullRequest is $($pr.state); inspect a current open PR for trigger testing."
}

Write-Output "Repository: $Repository"
Write-Output "Default branch: $($repositoryInfo.default_branch)"
Write-Output "Actions: enabled=$($actions.enabled), allowed_actions=$($actions.allowed_actions)"
Write-Output "Validation workflow: state=$($workflow.state), path=$($workflow.path)"
Write-Output "PR #$PullRequest`: $($pr.head.ref) ($($pr.head.sha)) -> $($pr.base.ref)"
Write-Output "PR URL: $($pr.html_url)"

$rulesets = @(Get-GhJson "repos/$Repository/rulesets?includes_parents=false")
if ($rulesets.Count -eq 0) {
    Write-Output 'Repository rulesets: none'
} else {
    foreach ($rule in $rulesets) {
        Write-Output "Ruleset: id=$($rule.id), enforcement=$($rule.enforcement), name=$($rule.name)"
    }
}

foreach ($event in @('pull_request', 'push', 'workflow_dispatch')) {
    $response = Get-GhJson "repos/$Repository/actions/workflows/validate.yml/runs?event=$event&per_page=100"
    $related = @($response.workflow_runs | Where-Object {
        if ($event -eq 'pull_request') {
            $numbers = @($_.pull_requests | ForEach-Object { $_.number })
            ($numbers -contains $PullRequest) -or ($_.head_branch -eq $pr.head.ref)
        } else {
            $_.head_sha -eq $pr.head.sha
        }
    } | Select-Object -First 5)
    if ($related.Count -eq 0) {
        Write-Output "${event}: no run found for this PR/commit in the latest 100 runs"
    } else {
        foreach ($run in $related) {
            Write-Output "${event}: id=$($run.id), status=$($run.status), conclusion=$($run.conclusion), head=$($run.head_sha), url=$($run.html_url)"
        }
    }
}

$checked = @($pr.head.sha)
if ($pr.merge_commit_sha -and $pr.merge_commit_sha -ne $pr.head.sha) {
    $checked += $pr.merge_commit_sha
}
foreach ($sha in $checked) {
    $checks = Get-GhJson "repos/$Repository/commits/$sha/check-runs?per_page=100"
    $validationChecks = @($checks.check_runs | Where-Object { $_.name -eq 'validate' })
    if ($validationChecks.Count -eq 0) {
        Write-Output "Check validate on ${sha}: absent"
    } else {
        foreach ($check in $validationChecks) {
            Write-Output "Check validate on ${sha}: status=$($check.status), conclusion=$($check.conclusion), url=$($check.html_url)"
        }
    }
}

Write-Output 'Read-only inspection complete. A workflow_dispatch success is not proof that a PR-required check is satisfied.'
