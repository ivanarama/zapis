# Zapis Build Script for Windows

$ErrorActionPreference = "Stop"

Write-Host "Building Zapis.exe..." -ForegroundColor Cyan

# Clean previous build
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "zapis.spec") { Remove-Item -Force "zapis.spec" }

Write-Host "Installing dependencies..." -ForegroundColor Yellow
pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip install -r requirements.txt failed. Build aborted." -ForegroundColor Red
    exit 1
}

# pyctcdecode 0.5.0 pins numpy<2.0.0, which conflicts with gigaam's numpy==2.*.
# It actually runs fine on numpy 2.x, so install it without its (stale) deps;
# pygtrie (its only real dependency) is already pinned in requirements.txt.
Write-Host "Installing pyctcdecode (no-deps)..." -ForegroundColor Yellow
pip install --no-deps pyctcdecode==0.5.0
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip install for pyctcdecode failed. Build aborted." -ForegroundColor Red
    exit 1
}

# requirements.txt pins gigaam to a GitHub commit, so a fresh install already
# carries v3. But a dev venv may already hold a stale PyPI gigaam (v1/v2 only)
# that pip treats as satisfying the requirement — detect that via the model
# registry and only then re-install from GitHub. This also keeps CI from
# cloning the repo twice (the clone is where a flaky github.com 500 would bite).
# Keep the commit below in sync with requirements.txt.
# v3_ctc lives in _MODEL_HASHES (dict, new GitHub gigaam) or _MODEL_NAMES (list, old PyPI).
python -c "import gigaam, sys; reg = getattr(gigaam, '_MODEL_HASHES', None) or getattr(gigaam, '_MODEL_NAMES', ()); sys.exit(0 if 'v3_ctc' in reg else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installed gigaam lacks v3_ctc -- reinstalling from GitHub..." -ForegroundColor Yellow
    pip install --force-reinstall --no-deps "git+https://github.com/salute-developers/GigaAM.git@6e4b027c6fb554e09e8b9059b757a175295ab879"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: pip install for gigaam failed (transient github.com 500? just retry the build). Aborted." -ForegroundColor Red
        exit 1
    }
    python -c "import gigaam, sys; reg = getattr(gigaam, '_MODEL_HASHES', None) or getattr(gigaam, '_MODEL_NAMES', ()); sys.exit(0 if 'v3_ctc' in reg else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: gigaam still does not expose v3_ctc after reinstall. Aborted." -ForegroundColor Red
        exit 1
    }
}
Write-Host "GigaAM v3_ctc present." -ForegroundColor Green

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
        'torchaudio',
        'soundfile',
        'onnxruntime',
        'pyctcdecode',
        'pyctcdecode.constants',
        'pyctcdecode.language_model',
        # kenlm: C++ extension, optional import inside pyctcdecode. Only present
        # in CI builds (compiled there) — harmless as a hidden import otherwise.
        'kenlm',
        'sentencepiece',
        'pygtrie',
        # faster-whisper stack
        'faster_whisper',
        'ctranslate2',
        'tokenizers',
        # pyav -- audio decoder shared by both ASR engines (replaces ffmpeg subprocess)
        'av',
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
    hookspath=['hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['test', 'tests', 'pytest', 'jupyter', 'tensorboard'],
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
    upx=False,
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

Write-Host "Running PyInstaller in a separate cmd window..." -ForegroundColor Yellow
Write-Host "Live log: Get-Content C:\Projects\Zapis\build.log -Tail 5 -Wait" -ForegroundColor Cyan
# PyInstaller на Windows иногда ловит "Aborted by user request" из-за того, что
# Windows Terminal/PowerShell родительской сессии шлёт CTRL_BREAK при больших
# объёмах вывода. Запускаем в отдельном окне cmd через Start-Process БЕЗ
# -NoNewWindow -- у дочернего процесса своя консоль и свой process group,
# сигналы родительского PowerShell туда не утекают.
$pyExe = (Get-Command python).Source
$cmdLine = "`"$pyExe`" -u -m PyInstaller zapis.spec --clean --noconfirm > build.log 2>&1"
$proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c",$cmdLine -Wait -PassThru
if ($proc.ExitCode -ne 0) {
    Write-Host "ERROR: PyInstaller failed (exit $($proc.ExitCode))." -ForegroundColor Red
    Write-Host "--- last 40 lines of build.log ---" -ForegroundColor Red
    if (Test-Path build.log) { Get-Content build.log -Tail 40 }
    exit $proc.ExitCode
}

Copy-Item "settings.json" -Destination "dist" -ErrorAction SilentlyContinue

Write-Host "`nBuild complete!" -ForegroundColor Green
Write-Host "Output: dist\Zapis.exe" -ForegroundColor Cyan
Write-Host "`nNote: GigaAM, KenLM and Whisper weights are downloaded to the HuggingFace cache" -ForegroundColor Yellow
Write-Host "      on first launch -- they are NOT bundled into the exe (and should not be)." -ForegroundColor Yellow
Write-Host "`nFor distribution, copy:" -ForegroundColor Yellow
Write-Host "  - dist\Zapis.exe" -ForegroundColor White
Write-Host "  - dist\settings.json" -ForegroundColor White
