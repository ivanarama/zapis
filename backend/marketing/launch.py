"""Open the native video tab, reusing only this checkout's running server."""
import argparse
import json
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from ..config import get_settings
from .routes import service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    port = get_settings().app.port
    address = f"http://127.0.0.1:{port}"
    def info():
        with urllib.request.urlopen(address + "/api/video/status", timeout=2) as response:
            return json.load(response)
    try:
        running = info()
    except (OSError, urllib.error.URLError):
        running = None
    if running:
        if running.get("project") != service.status()["project"]:
            raise SystemExit(f"Порт {port} занят другой копией проекта. Измените app.port в settings.json.")
        if not args.no_browser:
            webbrowser.open(address + "/#video")
        print("Zapis уже запущен: " + address + "/#video")
        return
    def open_when_ready():
        for _ in range(60):
            try:
                if info().get("project") == service.status()["project"]:
                    webbrowser.open(address + "/#video")
                    return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(.5)
    if not args.no_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
