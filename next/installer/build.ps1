# Build the VSD Pad installer (VSDPad-Setup-<version>.exe) on Windows.
#
#   cd C:\code\vsdpad\next
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1
#
# Needs Python 3.10+ and Inno Setup 6 (https://jrsoftware.org/isdl.php, or: winget install JRSoftware.InnoSetup).
# GitHub builds the same installer on every push (Actions tab), so you normally don't need to run this.
$ErrorActionPreference = "Stop"
$next = Split-Path -Parent $PSScriptRoot
Set-Location $next
$py = if ($env:PYTHON) { $env:PYTHON } else { "python" }

# a private build folder, so this never touches the Python you run VSD Pad with
if (-not (Test-Path build\venv)) { & $py -m venv build\venv }
$vpy = "build\venv\Scripts\python.exe"
& $vpy -m pip install --quiet --upgrade pip
& $vpy -m pip install --quiet -r installer\requirements.txt
if ($LASTEXITCODE) { throw "pip install failed" }

& $vpy tests\run_all.py
if ($LASTEXITCODE) { throw "tests failed: not building" }

& $vpy -m PyInstaller --noconfirm --clean --distpath build\dist --workpath build\work installer\vsdpad.spec
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$version = (& $vpy -c "import vsdpad; print(vsdpad.__version__)").Trim()
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 isn't installed (winget install JRSoftware.InnoSetup)" }
& $iscc /Qp "/DAppVersion=$version" "/DSourceDir=$next\build\dist\VSDPad" "/DOutputDir=$next\build" installer\vsdpad.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Write-Host "`nDone: $next\build\VSDPad-Setup-$version.exe"
