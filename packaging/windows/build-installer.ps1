Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..\..")

python -m pip install -r packaging\requirements-build.txt
python -m PyInstaller packaging\pyinstaller\Estratto.spec --noconfirm --clean

if (-not (Get-Command iscc.exe -ErrorAction SilentlyContinue)) {
    throw "Inno Setup compiler (iscc.exe) not found in PATH."
}

iscc.exe packaging\windows\Estratto.iss
Write-Host "Built dist\windows-installer\Estratto-Setup.exe"
