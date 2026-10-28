from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..config import get_settings


class CliError(Exception):
    def __init__(self, code: str, message: str, exit_code: int = 3, **details):
        super().__init__(message)
        self.code, self.exit_code, self.details = code, exit_code, details


class Client:
    def __init__(self, service, *, no_start=False, progress=True):
        self.service = service
        self.address = f"http://127.0.0.1:{get_settings().app.port}"
        self.no_start = no_start
        self.progress = progress

    def call(self, path: str, payload=None, *, timeout=60):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.address + "/api/video/" + path, data=data,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:
                detail = json.load(error).get("detail", str(error))
            except (ValueError, OSError):
                detail = str(error)
            raise CliError("busy" if error.code == 409 else "api_error", str(detail),
                           4 if error.code == 409 else 3, http_status=error.code) from error
        except (OSError, ValueError) as error:
            raise CliError("connection", "Нет ответа от Zapis: " + str(error)) from error

    def _probe(self):
        try:
            with urllib.request.urlopen(self.address + "/api/video/status", timeout=2) as response:
                status = json.load(response)
        except urllib.error.HTTPError as error:
            raise CliError("port_in_use", "Порт Zapis занят другим HTTP-сервисом.", http_status=error.code) from error
        except (ConnectionError, urllib.error.URLError, TimeoutError):
            return None
        except ValueError as error:
            raise CliError("port_in_use", "На порту Zapis отвечает другой сервис.") from error
        if status.get("project") != self.service.status()["project"]:
            raise CliError("wrong_project", "Запущена другая копия Zapis. Используйте её CLI или другой app.port.")
        if status.get("apiVersion", 0) < 1:
            raise CliError("restart_required", "Перезапустите Zapis, чтобы загрузить обновлённый командный API.")
        return status

    def ensure(self):
        status = self._probe()
        if status:
            return status
        if self.no_start:
            raise CliError("not_running", "Zapis не запущен. Уберите --no-start или запустите START-VIDEO.cmd.")
        root = self.service.renderer.parent
        self.service.storage.mkdir(parents=True, exist_ok=True)
        environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        with (self.service.storage / "cli-server.log").open("ab") as output:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            process = subprocess.Popen([sys.executable, "-m", "backend.marketing.launch", "--no-browser"],
                                       cwd=root, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                       env=environment, creationflags=flags, start_new_session=os.name != "nt")
        (self.service.storage / "cli-server.pid").write_text(str(process.pid), encoding="ascii")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = self._probe()
            if status:
                return status
            if process.poll() is not None:
                raise CliError("startup_failed", "Zapis завершился при запуске. См. video_runs/cli-server.log.")
            time.sleep(.25)
        raise CliError("startup_timeout", "Zapis не запустился за 30 секунд. См. video_runs/cli-server.log.")

    def upload(self, path: Path) -> str:
        # All uploads go through the same bounded endpoint used by the UI.
        import httpx
        with path.open("rb") as content:
            response = httpx.post(self.address + "/api/video/assets", files={"file": (path.name, content)}, timeout=60)
        if not response.is_success:
            raise CliError("upload_failed", response.text)
        return response.json()["asset"]

    def wait(self, identifier: str, timeout: float):
        deadline = time.monotonic() + timeout
        last = None
        while True:
            try:
                job = self.call("jobs/" + identifier)
            except CliError as error:
                error.details["job_id"] = identifier
                raise
            if self.progress and job["message"] != last:
                print(job["message"], file=sys.stderr, flush=True)
                last = job["message"]
            if job["status"] in {"done", "error", "interrupted"}:
                if job["status"] != "done":
                    raise CliError("job_failed", job["message"], 5, job_id=identifier, status=job["status"])
                return job
            if time.monotonic() >= deadline:
                raise CliError("timeout", "Время ожидания истекло. Сборка продолжается, используйте video wait.",
                               124, job_id=identifier, status=job["status"])
            time.sleep(min(1, max(0, deadline - time.monotonic())))
