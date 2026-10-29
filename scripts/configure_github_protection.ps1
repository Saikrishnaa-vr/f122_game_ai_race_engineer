param([switch]$Apply)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo = 'Saikrishnaa-vr/f122_game_ai_race_engineer'
$apiPath = "repos/$repo"

function Invoke-GhJson {
    param([string[]]$Arguments)
    $output = & gh @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub CLI failed ($LASTEXITCODE): gh $($Arguments -join ' ')"
    }
    return ConvertFrom-Json -InputObject ($output -join "`n")
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'GitHub CLI is required. Install it, then sign in with gh auth login -h github.com.'
}

$repository = Invoke-GhJson -Arguments @('api', $apiPath)
if ($repository.full_name -cne $repo -or -not $repository.permissions.admin) {
    throw "Sign in as an administrator of $repo with gh auth login -h github.com."
}
if ($repository.default_branch -ne 'main') {
    throw "Expected main as the default branch; found $($repository.default_branch)."
}

try {
    $existingResponse = Invoke-GhJson -Arguments @('api', "$apiPath/rulesets?includes_parents=false")
} catch {
    if ($repository.private) {
        throw "GitHub rulesets are unavailable for this private repository. GitHub Pro or public visibility is required. No rules were changed. $($_.Exception.Message)"
    }
    throw
}
$existing = @($existingResponse | Where-Object {
    $null -ne $_ -and $null -ne $_.PSObject.Properties['name']
})
if ($existing.Count -gt 0) {
    $names = ($existing | ForEach-Object { $_.name }) -join ', '
    throw "Repository rulesets already exist ($names). Review them before applying this bootstrap."
}

function Get-ValidationRuns {
    param([string]$Branch)
    $response = Invoke-GhJson -Arguments @('run', 'list', '--repo', $repo,
        '--workflow', 'validate.yml', '--branch', $Branch, '--limit', '1',
        '--json', 'databaseId,status,conclusion,headSha,url')
    return @($response | Where-Object {
        $null -ne $_ -and $null -ne $_.PSObject.Properties['status']
    })
}

foreach ($branch in @('main', 'develop')) {
    $branchInfo = Invoke-GhJson -Arguments @('api', "$apiPath/branches/$branch")
    $runs = @(Get-ValidationRuns -Branch $branch)
    if ($Apply -and ($runs.Count -eq 0 -or
        $runs[0].headSha -ne $branchInfo.commit.sha)) {
        $previousRunId = if ($runs.Count -eq 0) { 0 } else { $runs[0].databaseId }
        Write-Host "Starting Validation on $branch because no run covers its current head."
        & gh workflow run validate.yml --repo $repo --ref $branch
        if ($LASTEXITCODE -ne 0) {
            throw "Could not start Validation on $branch. Inspect the workflow in GitHub Actions."
        }
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            Start-Sleep -Seconds 3
            $runs = @(Get-ValidationRuns -Branch $branch)
            if ($runs.Count -eq 1 -and $runs[0].databaseId -ne $previousRunId -and
                $runs[0].headSha -eq $branchInfo.commit.sha) { break }
        }
    }
    if ($Apply -and $runs.Count -eq 1 -and
        $runs[0].headSha -eq $branchInfo.commit.sha -and
        $runs[0].status -ne 'completed') {
        Write-Host "Waiting for Validation on $branch ($($runs[0].url))."
        & gh run watch $runs[0].databaseId --repo $repo --compact --exit-status
        if ($LASTEXITCODE -ne 0) {
            throw "Validation failed on $branch. Inspect $($runs[0].url)."
        }
        $runs = @(Get-ValidationRuns -Branch $branch)
    }
    if ($runs.Count -ne 1 -or $runs[0].status -ne 'completed' -or
        $runs[0].conclusion -ne 'success' -or
        $runs[0].headSha -ne $branchInfo.commit.sha) {
        $runSummary = if ($runs.Count -eq 0) { 'no Validation run found' } else {
            "status=$($runs[0].status), conclusion=$($runs[0].conclusion), commit=$($runs[0].headSha), url=$($runs[0].url)"
        }
        throw "Cannot protect $branch yet: $runSummary; branch head=$($branchInfo.commit.sha). Check GitHub Actions."
    }
    $checks = Invoke-GhJson -Arguments @('api',
        "$apiPath/commits/$($runs[0].headSha)/check-runs")
    $matching = @($checks.check_runs | Where-Object {
        $_.name -eq 'validate' -and $_.conclusion -eq 'success'
    })
    if ($matching.Count -eq 0) {
        throw "The validate status check was not found on $branch. Inspect the workflow job name."
    }
    Write-Host "Validation passed on $branch at $($runs[0].headSha)."
}

$account = Invoke-GhJson -Arguments @('api', 'users/Saikrishnaa-vr')
$quality = @{
    name = 'Require PR and validation on main and develop'
    target = 'branch'
    enforcement = 'active'
    bypass_actors = @()
    conditions = @{ ref_name = @{
        include = @('refs/heads/main', 'refs/heads/develop')
        exclude = @()
    } }
    rules = @(
        @{ type = 'deletion' }
        @{ type = 'non_fast_forward' }
        @{ type = 'pull_request'; parameters = @{
            allowed_merge_methods = @('merge', 'squash', 'rebase')
            dismiss_stale_reviews_on_push = $false
            require_code_owner_review = $false
            require_last_push_approval = $false
            required_approving_review_count = 0
            required_review_thread_resolution = $true
        } }
        @{ type = 'required_status_checks'; parameters = @{
            required_status_checks = @(@{ context = 'validate' })
            strict_required_status_checks_policy = $true
        } }
    )
}
$ownerOnly = @{
    name = 'Only Saikrishnaa-vr may merge to main'
    target = 'branch'
    enforcement = 'active'
    bypass_actors = @(@{
        actor_id = [int]$account.id
        actor_type = 'User'
        bypass_mode = 'pull_request'
    })
    conditions = @{ ref_name = @{
        include = @('refs/heads/main')
        exclude = @()
    } }
    rules = @(@{ type = 'update'; parameters = @{
        update_allows_fetch_and_merge = $false
    } })
}

Write-Host 'Planned rulesets:'
$qualityJson = ConvertTo-Json -InputObject $quality -Depth 12
$ownerJson = ConvertTo-Json -InputObject $ownerOnly -Depth 12
Write-Host $qualityJson
Write-Host $ownerJson
if (-not $Apply) {
    Write-Host 'Dry run only. Run this script with -Apply after reviewing the rules above.'
    return
}

$outputDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'data\github-rules'
$null = New-Item -ItemType Directory -Force -Path $outputDir
$encoding = New-Object System.Text.UTF8Encoding($false)
$qualityPath = Join-Path $outputDir 'quality.json'
$ownerPath = Join-Path $outputDir 'owner-only-main.json'
[System.IO.File]::WriteAllText($qualityPath, $qualityJson, $encoding)
[System.IO.File]::WriteAllText($ownerPath, $ownerJson, $encoding)

$createdOwner = Invoke-GhJson -Arguments @('api', '-X', 'POST',
    "$apiPath/rulesets", '--input', $ownerPath)
Write-Host "Created owner-only main ruleset $($createdOwner.id)."
$createdQuality = Invoke-GhJson -Arguments @('api', '-X', 'POST',
    "$apiPath/rulesets", '--input', $qualityPath)
Write-Host "Created quality ruleset $($createdQuality.id)."

foreach ($id in @($createdOwner.id, $createdQuality.id)) {
    $rule = Invoke-GhJson -Arguments @('api', "$apiPath/rulesets/$id")
    if ($rule.enforcement -ne 'active') {
        throw "Ruleset $id was created but is not active. Inspect it in GitHub Settings."
    }
    Write-Host "Verified active ruleset: $($rule.name) ($id)."
}
Write-Host 'Branch rules are active. Check the Rulesets page and test a feature PR to develop.'
