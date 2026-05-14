#!/usr/bin/env bash
# Zapis Build Script for Linux / macOS
set -euo pipefail

echo "Building Zapis..."

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

# Pick a compatible Python (torch 2.5.1 needs ≤3.12; prefer pyenv 3.12, then default)
PICK_PYTHON="$(command -v "$HOME/.pyenv/versions/3.12.9/bin/python3.12" 2>/dev/null \
    || command -v python3)"

# Create/activate virtual environment
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment with $PICK_PYTHON..."
    "$PICK_PYTHON" -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

PYTHON="$(command -v python)"
PIP="$(command -v pip)"

# Clean previous build
rm -rf dist build zapis.spec

echo "Installing dependencies..."
"$PIP" install -r requirements.txt

# pyctcdecode 0.5.0 pins numpy<2.0.0 which conflicts with gigaam's numpy==2.*.
# It runs fine on numpy 2.x, so install without its (stale) deps.
echo "Installing pyctcdecode (no-deps)..."
"$PIP" install --no-deps pyctcdecode==0.5.0

# requirements.txt pins gigaam to a GitHub commit, so a fresh install already
# carries v3. But a dev venv may hold a stale PyPI gigaam (v1/v2 only).
# Keep the commit below in sync with requirements.txt.
if "$PYTHON" -c "import gigaam, sys; reg = getattr(gigaam, '_MODEL_HASHES', None) or getattr(gigaam, '_MODEL_NAMES', ()); sys.exit(0 if 'v3_ctc' in reg else 1)"; then
    echo "GigaAM v3_ctc present."
else
    echo "Installed gigaam lacks v3_ctc -- reinstalling from GitHub..."
    "$PIP" install --force-reinstall --no-deps "git+https://github.com/salute-developers/GigaAM.git@6e4b027c6fb554e09e8b9059b757a175295ab879"
    if ! "$PYTHON" -c "import gigaam, sys; reg = getattr(gigaam, '_MODEL_HASHES', None) or getattr(gigaam, '_MODEL_NAMES', ()); sys.exit(0 if 'v3_ctc' in reg else 1)"; then
        echo "ERROR: gigaam still does not expose v3_ctc after reinstall. Aborted." >&2
        exit 1
    fi
    echo "GigaAM v3_ctc present."
fi

# Detect platform for PyInstaller config
OS_NAME="$(uname -s)"

if [ "$OS_NAME" = "Darwin" ]; then
    # macOS: build an .app bundle
    cat > zapis.spec << 'SPEC'
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
        'gigaam', 'gigaam.decoding', 'gigaam.model', 'gigaam.utils',
        'gigaam.preprocess', 'gigaam.onnx_utils',
        'torchaudio', 'soundfile', 'onnxruntime',
        'pyctcdecode', 'pyctcdecode.constants', 'pyctcdecode.language_model',
        'kenlm', 'sentencepiece', 'pygtrie',
        'faster_whisper', 'ctranslate2', 'tokenizers',
        'av',
        'openai', 'anthropic',
        'backend.asr', 'backend.asr.gigaam_engine',
        'backend.asr.whisper_engine', 'backend.asr.factory',
        'backend.llm', 'backend.llm.client', 'backend.llm.prompts',
        'backend.config', 'backend.schema', 'backend.formats',
    ],
    hookspath=['hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['test', 'tests', 'pytest', 'jupyter', 'tensorboard'],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Zapis',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='Zapis',
)

app = BUNDLE(
    coll,
    name='Zapis.app',
    icon=None,
    bundle_identifier='com.zapis.app',
    info_plist={
        'CFBundleName': 'Zapis',
        'CFBundleDisplayName': 'Записная книжка',
        'CFBundleShortVersionString': '1.0',
        'NSHighResolutionCapable': True,
    },
)
SPEC
else
    # Linux: single-file executable
    cat > zapis.spec << 'SPEC'
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
        'gigaam', 'gigaam.decoding', 'gigaam.model', 'gigaam.utils',
        'gigaam.preprocess', 'gigaam.onnx_utils',
        'torchaudio', 'soundfile', 'onnxruntime',
        'pyctcdecode', 'pyctcdecode.constants', 'pyctcdecode.language_model',
        'kenlm', 'sentencepiece', 'pygtrie',
        'faster_whisper', 'ctranslate2', 'tokenizers',
        'av',
        'openai', 'anthropic',
        'backend.asr', 'backend.asr.gigaam_engine',
        'backend.asr.whisper_engine', 'backend.asr.factory',
        'backend.llm', 'backend.llm.client', 'backend.llm.prompts',
        'backend.config', 'backend.schema', 'backend.formats',
    ],
    hookspath=['hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['test', 'tests', 'pytest', 'jupyter', 'tensorboard'],
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
)
SPEC
fi

echo "Running PyInstaller..."
"$PYTHON" -u -m PyInstaller zapis.spec --clean --noconfirm

cp settings.json dist/ 2>/dev/null || true

if [ "$OS_NAME" = "Darwin" ]; then
    echo ""
    echo "Build complete!"
    echo "Output: dist/Zapis.app"
    echo ""
    echo "For distribution, copy:"
    echo "  - dist/Zapis.app"
    echo "  - dist/settings.json"
else
    echo ""
    echo "Build complete!"
    echo "Output: dist/Zapis"
    echo ""
    echo "For distribution, copy:"
    echo "  - dist/Zapis"
    echo "  - dist/settings.json"
fi

echo ""
echo "Note: GigaAM, KenLM and Whisper weights are downloaded to the HuggingFace cache"
echo "      on first launch -- they are NOT bundled into the binary (and should not be)."
