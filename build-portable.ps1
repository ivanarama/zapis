param([string]$OutputDir = "dist/Zapis-Windows")

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$targetPath = if ([IO.Path]::IsPathRooted($OutputDir)) { $OutputDir } else { Join-Path $projectRoot $OutputDir }
$target = [IO.Path]::GetFullPath($targetPath)
if (Test-Path -LiteralPath $target) {
    throw "Output already exists. Select a fresh -OutputDir to preserve settings and history."
}
$pyExe = Join-Path $projectRoot ".venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $pyExe)) {
    $pyExe = (Get-Command python -ErrorAction Stop).Source
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Install Node.js 22 or newer first."
}
$nodeVersion = (& node --version).TrimStart('v').Split('.')[0]
if ([int]$nodeVersion -lt 22) { throw "Node.js 22 or newer is required." }

Push-Location $projectRoot
try {
    & $pyExe -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Python dependencies failed." }
    & $pyExe -m pip install --no-deps pyctcdecode==0.5.0
    if ($LASTEXITCODE -ne 0) { throw "pyctcdecode installation failed." }
    $kenlmWheel = Get-ChildItem -LiteralPath wheels -Filter 'kenlm-*.whl' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($kenlmWheel) {
        & $pyExe -m pip install $kenlmWheel.FullName
        if ($LASTEXITCODE -ne 0) { Write-Warning "KenLM is optional; continuing without its language model." }
    }
    & npm.cmd ci --prefix video
    if ($LASTEXITCODE -ne 0) { throw "Video renderer dependencies failed." }
    & $pyExe tools/build_portable.py --output $target --work build/portable-windows
    if ($LASTEXITCODE -ne 0) { throw "Portable build failed." }
} finally {
    Pop-Location
}
