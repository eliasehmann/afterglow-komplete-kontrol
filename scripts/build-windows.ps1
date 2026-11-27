<#
.SYNOPSIS
  Build dist\KontrolLEDs.exe: one file, no Python needed on the target PC.

.EXAMPLE
  .\scripts\build-windows.ps1            # runs the tests first
  .\scripts\build-windows.ps1 -SkipTests
#>
param([switch]$SkipTests)

# "Continue": PyInstaller logs to stderr, which Windows PowerShell would otherwise treat as
# an error when output is redirected. Failures are caught through exit codes below.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path .venv)) { python -m venv .venv }
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --quiet --disable-pip-version-check -e ".[app,build,dev]"

if (-not $SkipTests) {
    & $py -m pytest -q
    if ($LASTEXITCODE) { throw "Tests failed - not building." }
}

New-Item -ItemType Directory -Force build | Out-Null
& $py -c "from kkleds.icon import save_ico; save_ico('build/icon.ico')"

& $py -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name KontrolLEDs `
    --icon build\icon.ico `
    --paths src `
    --collect-data kkleds `
    --hidden-import mido.backends.rtmidi `
    --hidden-import pystray._win32 `
    packaging\kontrol_leds.py
if ($LASTEXITCODE) { throw "PyInstaller failed." }

Get-Item dist\KontrolLEDs.exe | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
