"""Expose bundled Node and FFmpeg to local subprocesses."""
import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    tools = Path(sys._MEIPASS) / "portable-tools"
    os.environ["PATH"] = str(tools) + os.pathsep + os.environ.get("PATH", "")
