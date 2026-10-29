"""Build a fresh portable Windows GUI/CLI folder without deleting user data."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, default=Path("build/portable"))
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Build the Windows bundle on Windows")
    root = Path(__file__).resolve().parents[1]
    output, work = args.output.resolve(), args.work.resolve()
    if output.exists():
        parser.error("Output already exists. Choose a fresh folder to preserve settings and history.")
    work.mkdir(parents=True, exist_ok=True)
    node = shutil.which("node")
    compositor = root / "video/node_modules/@remotion/compositor-win32-x64-msvc"
    if not node or not (compositor / "ffmpeg.exe").is_file():
        parser.error("Install Node.js and run npm ci in video/ before building")
    for package in ["pyinstaller", "pywebview", "gigaam", "faster-whisper", "ruaccent", "piper-tts"]:
        importlib.metadata.version(package)

    # Only tracked application assets and installed renderer dependencies are shipped.
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "frontend", "video", "docs/video"], cwd=root
    ).decode().split("\0")
    datas = []
    for name in tracked:
        if not name or not (root / name).is_file():
            continue
        path = Path(name)
        if name.startswith("docs/video/") and name != "docs/video/recording.html":
            continue
        if name.startswith("video/") and path.parts[1] not in {
            "src", "scripts", "examples", "campaigns", "public", "package.json", "package-lock.json", "campaign.schema.json"
        }:
            continue
        datas.append((str(root / path), path.parent.as_posix()))
    datas += [(str(root / "video/node_modules"), "video/node_modules"),
              (node, "portable-tools"), (str(compositor / "ffmpeg.exe"), "portable-tools")]
    spec = f'''from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata
packages = ('transformers', 'tokenizers', 'safetensors', 'ruaccent', 'piper')
datas = {datas!r}
binaries, hidden = [], ['main'] + collect_submodules('backend')
for package in packages:
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hidden += h
for package in ('fastapi', 'uvicorn', 'pydantic', 'gigaam', 'piper-tts', 'edge-tts', 'pywebview'):
    datas += copy_metadata(package)
a = Analysis([{str(root / 'tools/windows_entry.py')!r}], pathex=[{str(root)!r}],
    binaries=binaries, datas=datas, hiddenimports=hidden,
    hookspath=[{str(root / 'hooks')!r}],
    runtime_hooks=[{str(root / 'hooks/runtime-portable.py')!r}],
    excludes=['tests', 'pytest', 'jupyter', 'tensorboard', 'networkx'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Zapis',
    console=True, hide_console='hide-early', debug=False, strip=False, upx=False)
bundle = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name={output.name!r})
'''
    spec_path = work / "portable.spec"
    spec_path.write_text(spec, encoding="utf-8")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", str(spec_path),
        "--distpath", str(output.parent), "--workpath", str(work / "pyinstaller"), "--noconfirm",
    ], cwd=root, check=True)
    finalize_bundle(root, output, node, compositor)


def finalize_bundle(root: Path, output: Path, node: str, compositor: Path):
    sys.path.insert(0, str(root))
    from backend.schema import Settings
    from PyInstaller.utils.hooks import copy_metadata

    for package in ("torch", "soundfile"):
        for source, destination in copy_metadata(package):
            shutil.copytree(source, output / "_internal" / destination, dirs_exist_ok=True)

    (output / "settings.json").write_text(
        json.dumps(Settings().model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    shutil.copy2(root / "LICENSE", output / "LICENSE")
    licenses = output / "third-party-licenses"
    licenses.mkdir(exist_ok=True)
    for name, source in [("node-LICENSE", Path(node).parent / "LICENSE"),
                         ("python-LICENSE.txt", Path(sys.base_prefix) / "LICENSE.txt")]:
        if source.is_file():
            shutil.copy2(source, licenses / name)
    for source in compositor.glob("*LICENSE*"):
        shutil.copy2(source, licenses / ("remotion-compositor-" + source.name))
    (output / "BUILD.json").write_text(json.dumps({
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip(),
        "python": sys.version.split()[0], "node": subprocess.check_output([node, "--version"]).decode().strip(),
        "models_included": False, "default_settings": True,
        "launch_gui": "Zapis.exe", "launch_cli": "Zapis.exe video doctor",
    }, indent=2), encoding="utf-8")
    print("Portable bundle:", output)


if __name__ == "__main__":
    main()
