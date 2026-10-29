param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CompanionArgs)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Virtual environment missing. See README setup." }
$companionTemp = Join-Path $project 'data\tmp'
New-Item -ItemType Directory -Force -Path $companionTemp | Out-Null
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
if (-not $CompanionArgs -or $CompanionArgs.Count -eq 0) { $CompanionArgs = @('--drive') }
Push-Location $project
try {
    $env:TEMP = $companionTemp
    $env:TMP = $companionTemp
    & $python -m app @CompanionArgs
    exit $LASTEXITCODE
}
finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    Pop-Location
}
