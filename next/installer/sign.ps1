# Sign a file with the code signing certificate in $env:SIGN_PFX (base64 .pfx) / $env:SIGN_PASSWORD.
# Used by the GitHub build only when those secrets are set. Signed files get far fewer antivirus and SmartScreen warnings.
param([Parameter(Mandatory)][string]$File)
$ErrorActionPreference = "Stop"
$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $signtool) { throw "signtool.exe not found (Windows SDK)" }
$pfx = Join-Path $env:RUNNER_TEMP "sign.pfx"
[IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($env:SIGN_PFX))
try {
    & $signtool.FullName sign /f $pfx /p $env:SIGN_PASSWORD /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /d "VSD Pad" $File
    if ($LASTEXITCODE) { throw "signing $File failed" }
} finally { Remove-Item $pfx -Force -ErrorAction SilentlyContinue }
