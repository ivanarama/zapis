"""Zapis — desktop entry point.

Starts FastAPI/uvicorn in a background thread, then opens a pywebview window.
"""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()

    import logging
    import os
    import socket
    import threading
    import traceback

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    def _get_app_dir():
        if getattr(sys, "frozen", False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    def _setup_logging(to_file: bool):
        handlers = []
        if to_file:
            log_path = os.path.join(_get_app_dir(), "zapis.log")
            handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.WARNING)
        handlers.append(console)
        logging.basicConfig(
            level=logging.DEBUG if to_file else logging.WARNING,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=handlers,
        )

    def _find_free_port(start: int = 8001) -> int:
        for port in range(start, start + 100):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", port))
                    return port
            except OSError:
                continue
        return start

    _server_state = {"error": None}

    def _run_server(port: int) -> None:
        try:
            import uvicorn
            from backend.main import app
            log.info("Starting uvicorn on port %d", port)

            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="info",
                workers=1,
            )
            server = uvicorn.Server(config)
            server.run()
        except Exception:
            _server_state["error"] = traceback.format_exc()
            log.exception("Server thread crashed")

    def _show_error(message: str):
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0, message, "Записная книжка — Ошибка", 0x10
                )
                return
            except Exception:
                pass
        elif sys.platform == "darwin":
            try:
                import subprocess
                subprocess.run([
                    "osascript", "-e",
                    f'display alert "Записная книжка — Ошибка" message {repr(message)}'
                ], check=False)
                return
            except Exception:
                pass
        print(message, file=sys.stderr)

    log = logging.getLogger("zapis")
    _setup_logging(to_file=True)
    log.info("Starting Zapis from %s", _get_app_dir())

    try:
        port = _find_free_port(8001)
        log.info("Using port %d", port)

        server_thread = threading.Thread(target=_run_server, args=(port,), daemon=True)
        server_thread.start()

        import time
        import httpx

        url = f"http://127.0.0.1:{port}"
        started = False
        for _ in range(40):
            if _server_state["error"]:
                break
            if not server_thread.is_alive():
                log.error("Server thread died unexpectedly")
                break
            try:
                httpx.get(f"{url}/api/health", timeout=1.0)
                started = True
                log.info("Server ready at %s", url)
                break
            except Exception:
                time.sleep(0.25)

        if not started:
            detail = _server_state["error"] or "Server thread exited without error info"
            log.error("Server did not start: %s", detail)
            _show_error(f"Не удалось запустить сервер.\n\n{detail}")
            sys.exit(1)

        import webview
        log.info("Opening window")
        webview.create_window(
            "Записная книжка",
            url=url,
            width=1000,
            height=700,
            min_size=(600, 400),
            easy_drag=False,
        )
        webview.start()
    except Exception:
        log.exception("Fatal error")
        _show_error(traceback.format_exc())
        sys.exit(1)

    sys.exit(0)