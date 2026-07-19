"""Zapis — desktop entry point.

Starts FastAPI/uvicorn in a background thread, then opens a pywebview window.
"""

import multiprocessing
import sys


class ExportApi:
    """Мост pywebview: методы, вызываемые из JS как ``window.pywebview.api.*``.

    Нужен для экспорта TXT/SRT/VTT. Браузерный механизм скачивания
    (``blob`` + программный клик по ``<a download>``) не работает во встроённом
    WebView pywebview — поэтому файл сохраняем через нативный системный диалог.
    """

    def save_as(self, filename: str, content: str) -> dict:
        import webview

        if not webview.windows:
            return {"ok": False, "error": "Окно недоступно"}
        win = webview.windows[0]
        try:
            result = win.create_file_dialog(webview.SAVE_DIALOG, save_filename=filename)
        except Exception as e:  # диалог выбросил исключение
            return {"ok": False, "error": str(e)}
        if not result:  # пользователь отменил
            return {"ok": False, "cancelled": True}
        # SAVE_DIALOG возвращает str, но на части бэкендов — кортеж/список.
        path = result if isinstance(result, str) else result[0]
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"ok": True, "path": path}
        except OSError as e:
            return {"ok": False, "error": str(e)}

if __name__ == "__main__":
    multiprocessing.freeze_support()

    import logging
    import os
    import socket
    import ssl
    import threading
    import traceback

    # macOS Python doesn't use system certificates — inject certifi bundle
    if sys.platform == "darwin":
        try:
            import certifi
            os.environ.setdefault("SSL_CERT_FILE", certifi.where())
            os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
            # urllib doesn't respect SSL_CERT_FILE — patch ssl context directly
            ssl._create_default_https_context = lambda: ssl.create_default_context(
                cafile=certifi.where()
            )
        except ImportError:
            pass

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
            js_api=ExportApi(),
        )
        webview.start()
    except Exception:
        log.exception("Fatal error")
        _show_error(traceback.format_exc())
        sys.exit(1)

    sys.exit(0)