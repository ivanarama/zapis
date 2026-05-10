# Zapis Build Script for Windows

$ErrorActionPreference = "Stop"

Write-Host "Building Zapis.exe..." -ForegroundColor Cyan

# Clean previous build
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "zapis.spec") { Remove-Item -Force "zapis.spec" }

Write-Host "Installing dependencies..." -ForegroundColor Yellow
pip install -r requirements.txt

# requirements.txt specifies gigaam from GitHub, but pip may skip reinstall
# if a PyPI version (without v3) is already in the venv. Force the GitHub build,
# and install its full dep tree (soundfile, etc).
Write-Host "Ensuring GigaAM v3 (GitHub build) is installed..." -ForegroundColor Yellow
pip install --force-reinstall git+https://github.com/salute-developers/GigaAM.git
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip install for gigaam failed. Build aborted." -ForegroundColor Red
    exit 1
}

# Sanity check: v3_ctc must be available.
python -c "import gigaam, sys; sys.exit(0 if 'v3_ctc' in getattr(gigaam, '_MODEL_NAMES', []) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: installed gigaam does not expose v3_ctc. Build aborted." -ForegroundColor Red
    exit 1
}

# PyInstaller spec
$specContent = @"
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('frontend', 'frontend'),
        ('settings.json', '.'),
    ],
    hiddenimports=[
        # GigaAM stack (v3 from GitHub depends on soundfile + onnxruntime)
        'gigaam',
        'gigaam.decoding',
        'gigaam.model',
        'gigaam.utils',
        'gigaam.preprocess',
        'gigaam.onnx_utils',
        'soundfile',
        'onnxruntime',
        'pyctcdecode',
        'pyctcdecode.constants',
        'pyctcdecode.language_model',
        'sentencepiece',
        'pygtrie',
        # faster-whisper stack
        'faster_whisper',
        'ctranslate2',
        'tokenizers',
        # LLM clients
        'openai',
        'anthropic',
        # backend submodules — на случай динамических импортов
        'backend.asr',
        'backend.asr.gigaam_engine',
        'backend.asr.whisper_engine',
        'backend.asr.factory',
        'backend.llm',
        'backend.llm.client',
        'backend.llm.prompts',
        'backend.config',
        'backend.schema',
        'backend.formats',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['test', 'tests', 'pytest', 'jupyter'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Zapis',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
"@

$specContent | Out-File -FilePath "zapis.spec" -Encoding UTF8

Write-Host "Running PyInstaller..." -ForegroundColor Yellow
pyinstaller zapis.spec --clean

Copy-Item "settings.json" -Destination "dist" -ErrorAction SilentlyContinue

Write-Host "`nBuild complete!" -ForegroundColor Green
Write-Host "Output: dist\Zapis.exe" -ForegroundColor Cyan
Write-Host "`nNote: GigaAM, KenLM and Whisper weights are downloaded to the HuggingFace cache" -ForegroundColor Yellow
Write-Host "      on first launch -- they are NOT bundled into the exe (and should not be)." -ForegroundColor Yellow
Write-Host "`nFor distribution, copy:" -ForegroundColor Yellow
Write-Host "  - dist\Zapis.exe" -ForegroundColor White
Write-Host "  - dist\settings.json" -ForegroundColor White
