# Zapis Build Script for Windows

$ErrorActionPreference = "Stop"

Write-Host "Building Zapis.exe..." -ForegroundColor Cyan

# Resolve Python: prefer the project venv (.venv), else python on PATH.
# Голый pip не на PATH, если venv не активирован, — поэтому зовём
# "<python> -m pip", который сам ставит в нужное окружение без активации.
if (Test-Path ".venv\Scripts\python.exe") {
    $pyExe = (Resolve-Path ".venv\Scripts\python.exe").Path
} else {
    $pyExe = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $pyExe) {
    Write-Host "ERROR: Python не найден. Создай venv:  python -m venv .venv" -ForegroundColor Red
    exit 1
}
Write-Host "Using Python: $pyExe" -ForegroundColor Cyan

# Clean previous build.
# ВАЖНО: НЕ удаляем dist/ целиком — там лежат пользовательские
# settings.json и transcripts/ (создаются рядом с .exe). PyInstaller
# с --noconfirm сам перезапишет dist/Zapis.exe.
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "zapis.spec") { Remove-Item -Force "zapis.spec" }
if (Test-Path "dist\Zapis.exe") { Remove-Item -Force "dist\Zapis.exe" }

Write-Host "Installing dependencies..." -ForegroundColor Yellow
& $pyExe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip install -r requirements.txt failed. Build aborted." -ForegroundColor Red
    exit 1
}

# pyctcdecode 0.5.0 pins numpy<2.0.0, which conflicts with gigaam's numpy==2.*.
# It actually runs fine on numpy 2.x, so install it without its (stale) deps;
# pygtrie (its only real dependency) is already pinned in requirements.txt.
Write-Host "Installing pyctcdecode (no-deps)..." -ForegroundColor Yellow
& $pyExe -m pip install --no-deps pyctcdecode==0.5.0
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip install for pyctcdecode failed. Build aborted." -ForegroundColor Red
    exit 1
}

# requirements.txt pins gigaam to a GitHub commit, so a fresh install already
# carries v3. But a dev venv may already hold a stale PyPI gigaam (v1/v2 only)
# that pip treats as satisfying the requirement -- detect that via the model
# registry and only then re-install from GitHub. This also keeps CI from
# cloning the repo twice (the clone is where a flaky github.com 500 would bite).
# Keep the commit below in sync with requirements.txt.
# v3_ctc lives in _MODEL_HASHES (dict, new GitHub gigaam) or _MODEL_NAMES (list, old PyPI).
& $pyExe -c "import gigaam, sys; reg = getattr(gigaam, '_MODEL_HASHES', None) or getattr(gigaam, '_MODEL_NAMES', ()); sys.exit(0 if 'v3_ctc' in reg else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installed gigaam lacks v3_ctc -- reinstalling from GitHub..." -ForegroundColor Yellow
    & $pyExe -m pip install --force-reinstall --no-deps "git+https://github.com/salute-developers/GigaAM.git@6e4b027c6fb554e09e8b9059b757a175295ab879"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: pip install for gigaam failed (transient github.com 500? just retry the build). Aborted." -ForegroundColor Red
        exit 1
    }
    & $pyExe -c "import gigaam, sys; reg = getattr(gigaam, '_MODEL_HASHES', None) or getattr(gigaam, '_MODEL_NAMES', ()); sys.exit(0 if 'v3_ctc' in reg else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: gigaam still does not expose v3_ctc after reinstall. Aborted." -ForegroundColor Red
        exit 1
    }
}
Write-Host "GigaAM v3_ctc present." -ForegroundColor Green

# kenlm: C++ extension, no Windows wheel on PyPI. Install from a pre-built
# wheel (downloaded from GitHub Actions artifact) if available, otherwise skip.
$kenlmWheels = Get-ChildItem -Path "wheels" -Filter "kenlm-*.whl" -ErrorAction SilentlyContinue
if ($kenlmWheels) {
    Write-Host "Installing kenlm from local wheel..." -ForegroundColor Yellow
    & $pyExe -m pip install $kenlmWheels[0].FullName
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: kenlm wheel install failed, continuing without it." -ForegroundColor Yellow
    }
} else {
    Write-Host "No kenlm wheel found in wheels/ -- building without LM support." -ForegroundColor Yellow
}

# Write PyInstaller spec to file (avoid here-string encoding issues in PS 5.1)
$specLines = @(
    '# -*- mode: python ; coding: utf-8 -*-'
    ''
    'from PyInstaller.utils.hooks import collect_all'
    ''
    'block_cipher = None'
    ''
    '# Пакеты с данными/нативными или динамическими модулями, которым одних'
    '# hiddenimports мало: ruaccent (ударения) + стек transformers/tokenizers/'
    '# safetensors, и piper (внутри espeak-ng-data + нативный espeakbridge).'
    '# collect_all забирает их целиком. Увеличивает размер exe; сами веса моделей'
    '# НЕ бандлятся -- качаются в кэш рядом с приложением при первом запуске.'
    "_accent_pkgs = ('transformers', 'tokenizers', 'safetensors', 'ruaccent', 'piper')"
    '_accent_datas, _accent_bins, _accent_hidden = [], [], []'
    'for _p in _accent_pkgs:'
    '    _d, _b, _h = collect_all(_p)'
    '    _accent_datas += _d; _accent_bins += _b; _accent_hidden += _h'
    ''
    '# ruaccent ships a 161 MB koziev/.../ruword2tags.db. RuleEngine.load() opens'
    '# it (sqlite connect) but our process_all path never queries it, so drop it'
    '# and substitute an empty 0-byte SQLite stub (valid empty DB) -- keeps'
    '# RuleEngine.load() from failing while shedding 161 MB.'
    'import os as _os, tempfile as _tf'
    "_stub_dir = _os.path.join(_tf.gettempdir(), 'zapis_ruaccent_stub')"
    '_os.makedirs(_stub_dir, exist_ok=True)'
    "_stub_db = _os.path.join(_stub_dir, 'ruword2tags.db')"
    "open(_stub_db, 'wb').close()"
    "_accent_datas = [(_s, _dd) for (_s, _dd) in _accent_datas if _os.path.basename(_s).lower() != 'ruword2tags.db']"
    "_accent_datas.append((_stub_db, _os.path.join('ruaccent', 'koziev', 'rupostagger', 'database')))"
    ''
    'a = Analysis('
    "    ['main.py'],"
    '    pathex=[],'
    '    binaries=_accent_bins,'
    '    datas=['
    "        ('frontend', 'frontend'),"
    "        ('settings.json', '.'),"
    '    ] + _accent_datas,'
    '    hiddenimports=['
    '        # GigaAM stack'
    "        'gigaam',"
    "        'gigaam.decoding',"
    "        'gigaam.model',"
    "        'gigaam.utils',"
    "        'gigaam.preprocess',"
    "        'gigaam.onnx_utils',"
    "        'torchaudio',"
    "        'soundfile',"
    "        'onnxruntime',"
    "        'pyctcdecode',"
    "        'pyctcdecode.constants',"
    "        'pyctcdecode.language_model',"
    '        # kenlm: C++ extension, optional import inside pyctcdecode'
    "        'kenlm',"
    "        'sentencepiece',"
    "        'pygtrie',"
    '        # faster-whisper stack'
    "        'faster_whisper',"
    "        'ctranslate2',"
    "        'tokenizers',"
    '        # pyav -- audio decoder shared by both ASR engines'
    "        'av',"
    '        # LLM clients'
    "        'openai',"
    "        'anthropic',"
    '        # backend submodules'
    "        'backend.asr',"
    "        'backend.asr.gigaam_engine',"
    "        'backend.asr.whisper_engine',"
    "        'backend.asr.factory',"
    "        'backend.llm',"
    "        'backend.llm.client',"
    "        'backend.llm.prompts',"
    "        'backend.config',"
    "        'backend.schema',"
    "        'backend.formats',"
    '        # TTS (озвучивание) -- Silero грузится через torch.hub в рантайме'
    "        'backend.tts',"
    "        'backend.tts.engine',"
    "        'backend.tts.pipeline',"
    "        'backend.tts.reader',"
    "        'backend.tts.chapters',"
    "        'backend.tts.normalize',"
    "        'backend.tts.normalize_cache',"
    "        'backend.tts.chunker',"
    "        'backend.tts.assemble',"
    "        'backend.tts.export',"
    "        'backend.tts.spool',"
    "        'backend.tts.stress',"
    "        'razdel',"
    '        # Ударения: ruaccent + его ML-стек (данные забирает collect_all выше)'
    "        'ruaccent',"
    "        'pycrfsuite',"
    '        # Piper (движок «качество») + фабрика выбора движка'
    "        'backend.tts.factory',"
    "        'backend.tts.engine_piper',"
    '        # Облачные TTS-движки (Яндекс/Сбер) — импортируются лениво в factory'
    "        'backend.tts.engine_cloud_base',"
    "        'backend.tts.engine_yandex',"
    "        'backend.tts.engine_sber',"
    "        'backend.tts.errors',"
    "        'piper',"
    "        'piper.voice',"
    "        'piper.config',"
    "        'piper.download_voices',"
    "        'piper.phonemize_espeak',"
    "        'piper.espeakbridge',"
    "        'pathvalidate',"
    '        # num2words языковые модули импортируются динамически -- см. hooks/hook-num2words.py'
    "        'num2words',"
    '    ] + _accent_hidden,'
    "    hookspath=['hooks'],"
    '    hooksconfig={},'
    '    runtime_hooks=[],'
    '        # networkx -- нужен только torch._dynamo (torch.compile), в инференсе не используется.'
    '        # ВНИМАНИЕ: sympy ИСКЛЮЧАТЬ НЕЛЬЗЯ. gigaam.encoder импортирует'
    '        # torch.utils.checkpoint, который транзитивно тянет'
    '        # torch.fx.experimental.symbolic_shapes -> torch.utils._sympy.functions -> import sympy.'
    '        # Без sympy GigaAM падает при загрузке модели:'
    '        #   hydra.errors.InstantiationException: gigaam.encoder.ConformerEncoder'
    "    excludes=['test', 'tests', 'pytest', 'jupyter', 'tensorboard', 'networkx'],"
    '    win_no_prefer_redirects=False,'
    '    win_private_assemblies=False,'
    '    cipher=block_cipher,'
    '    noarchive=False,'
    ')'
    ''
    'pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)'
    ''
    'exe = EXE('
    '    pyz,'
    '    a.scripts,'
    '    a.binaries,'
    '    a.zipfiles,'
    '    a.datas,'
    '    [],'
    "    name='Zapis',"
    '    debug=False,'
    '    bootloader_ignore_signals=False,'
    '    strip=False,'
    '    upx=False,'
    '    upx_exclude=[],'
    '    runtime_tmpdir=None,'
    '    console=False,'
    '    disable_windowed_traceback=False,'
    '    argv_emulation=False,'
    '    target_arch=None,'
    '    codesign_identity=None,'
    '    entitlements_file=None,'
    ')'
)
$specLines | Out-File -FilePath "zapis.spec" -Encoding utf8

Write-Host "Running PyInstaller in a separate cmd window..." -ForegroundColor Yellow
Write-Host "Live log: Get-Content C:\Projects\Zapis\build.log -Tail 5 -Wait" -ForegroundColor Cyan
# PyInstaller sometimes catches "Aborted by user request" because the parent
# PowerShell session sends CTRL_BREAK on large output. Run in a separate cmd
# window so the child process gets its own console and process group.
# $pyExe уже определён выше (venv-aware).
$cmdLine = "`"$pyExe`" -u -m PyInstaller zapis.spec --clean --noconfirm > build.log 2>&1"
$proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c",$cmdLine -Wait -PassThru
if ($proc.ExitCode -ne 0) {
    Write-Host "ERROR: PyInstaller failed (exit $($proc.ExitCode))." -ForegroundColor Red
    Write-Host "--- last 40 lines of build.log ---" -ForegroundColor Red
    if (Test-Path build.log) { Get-Content build.log -Tail 40 }
    exit $proc.ExitCode
}

# Кладём дефолтный settings.json только при первом билде — не затираем
# пользовательский, если он уже есть.
if (-not (Test-Path "dist\settings.json")) {
    Copy-Item "settings.json" -Destination "dist" -ErrorAction SilentlyContinue
}

Write-Host "`nBuild complete!" -ForegroundColor Green
Write-Host "Output: dist\Zapis.exe" -ForegroundColor Cyan
Write-Host "`nNote: GigaAM, KenLM and Whisper weights are downloaded to the HuggingFace cache" -ForegroundColor Yellow
Write-Host "      on first launch -- they are NOT bundled into the exe (and should not be)." -ForegroundColor Yellow
Write-Host "`nFor distribution, copy:" -ForegroundColor Yellow
Write-Host "  - dist\Zapis.exe" -ForegroundColor White
Write-Host "  - dist\settings.json" -ForegroundColor White
