param([string]$Python = "py")
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        & $Python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create a Windows Python virtual environment.' }
    }
    $buildPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    & $buildPython -m pip install -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw 'Build dependency installation failed.' }
    & $buildPython -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    & $buildPython -m PyInstaller --noconfirm --clean --onefile --windowed --name WSLMediaDownloader launcher.pyw
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
    Copy-Item -LiteralPath .env.example, README.md, LICENSE -Destination dist
    Write-Output "Built: $PSScriptRoot\dist\WSLMediaDownloader.exe"
} finally {
    Pop-Location
}
