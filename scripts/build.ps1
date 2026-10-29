$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Virtual environment missing. See README setup." }
$buildTemp = Join-Path $project 'data\build-temp'
$pyinstallerConfig = Join-Path $project 'data\pyinstaller-config'
New-Item -ItemType Directory -Force -Path $buildTemp, $pyinstallerConfig | Out-Null
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
$previousPyinstallerConfig = $env:PYINSTALLER_CONFIG_DIR
Push-Location $project
try {
    $env:TEMP = $buildTemp
    $env:TMP = $buildTemp
    $env:PYINSTALLER_CONFIG_DIR = $pyinstallerConfig
    & $python -m PyInstaller --noconfirm --clean --onedir --name F1RaceEngineer --add-data 'app\dashboard.html;app' --add-data 'app\practice.html;app' --add-data 'app\replay_library.html;app' --add-data 'app\track_catalog.json;app' --add-data 'app\bahrain_game_route.json;app' --add-data 'app\TRACK_CATALOG_LICENSE.md;app' run.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
    Write-Host "Built dist\F1RaceEngineer\F1RaceEngineer.exe. Keep the whole folder on D: and validate it with --help before gameplay."
}
finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    $env:PYINSTALLER_CONFIG_DIR = $previousPyinstallerConfig
    Pop-Location
}
