"""Observable command contracts for local automation and coding agents."""
import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from backend.cli import __main__ as cli
from backend.cli.artifacts import describe_job, export_job
from backend.cli.client import CliError, Client
from backend.marketing.service import VideoService


@pytest.fixture
def service(tmp_path, monkeypatch):
    service = VideoService(Path(__file__).resolve().parents[1] / "video", tmp_path / "jobs")
    monkeypatch.setattr(cli, "service", service)
    return service


def result(capsys):
    return json.loads(capsys.readouterr().out)


def test_bad_arguments_return_machine_readable_error(capsys):
    assert cli.main(["video", "render", "--unknown"]) == 2
    data = result(capsys)
    assert data["ok"] is False and data["error"]["code"] == "usage"


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
def test_launcher_without_environment_returns_json(tmp_path):
    wrapper = Path(__file__).resolve().parents[1] / "zapis.cmd"
    target = tmp_path / "zapis.cmd"
    target.write_bytes(wrapper.read_bytes())
    response = subprocess.run(["cmd.exe", "/d", "/c", str(target), "video", "doctor"], capture_output=True, text=True)
    assert response.returncode == 3
    assert json.loads(response.stdout)["error"]["code"] == "dependencies"


def test_exported_example_is_a_portable_input_from_another_directory(service, tmp_path, monkeypatch, capsys):
    target = tmp_path / "my project/campaign.json"
    assert cli.main(["video", "example", "edo-short", "--output", str(target)]) == 0
    data = result(capsys)["result"]
    assert all(Path(file).is_file() for file in data["assets"])
    monkeypatch.chdir(tmp_path)
    assert cli.main(["video", "validate", "my project/campaign.json"]) == 0
    data = result(capsys)
    assert data["ok"] is True and data["result"]["scenes"] == 3
    assert data["result"]["assets"][0]["sha256"]
    assert not list(service.storage.glob("*/job.json"))


def test_validation_and_invalid_render_do_not_launch_server_or_tts(service, tmp_path, monkeypatch, capsys):
    def forbidden(*_): pytest.fail("Server and TTS must not run during invalid validation")
    monkeypatch.setattr(Client, "ensure", forbidden)
    campaign = service.example("edo-short")
    campaign["scenes"][0]["title"] = "Too long " * 100
    file = tmp_path / "campaign.json"
    file.write_text(json.dumps(campaign), encoding="utf-8")
    assert cli.main(["video", "render", str(file), "--assets", str(service.renderer / "public")]) == 2
    assert result(capsys)["ok"] is False


def test_stdin_scenario_can_use_explicit_asset_root(service, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(service.example("edo-short"))))
    assert cli.main(["video", "validate", "-", "--assets", str(service.renderer / "public")]) == 0
    assert result(capsys)["result"]["valid"] is True


def test_example_export_does_not_overwrite_existing_work(service, tmp_path, capsys):
    target = tmp_path / "campaign.json"
    target.write_text("my draft", encoding="utf-8")
    assert cli.main(["video", "example", "edo-short", "--output", str(target)]) == 4
    assert result(capsys)["error"]["code"] == "output_exists"
    assert target.read_text(encoding="utf-8") == "my draft"


def test_async_render_submits_exactly_once_and_returns_id(service, monkeypatch, capsys):
    submitted = []
    monkeypatch.setattr(Client, "ensure", lambda *_: {})
    def submit(self, path, payload=None, **kwargs):
        submitted.append((path, payload))
        return {"id": "a" * 32, "status": "queued", "message": "Queued", "outputs": []}
    monkeypatch.setattr(Client, "call", submit)
    monkeypatch.setattr(Client, "wait", lambda *_: pytest.fail("--no-wait must return immediately"))
    assert cli.main(["video", "render", "--example", "edo-short", "--no-wait"]) == 0
    assert result(capsys)["result"]["id"] == "a" * 32
    assert len(submitted) == 1 and submitted[0][0] == "jobs"


def test_timeout_keeps_job_id_and_never_resubmits(service, monkeypatch):
    client = Client(service, progress=False)
    calls = []
    def status(path, *_, **__):
        calls.append(path)
        return {"id": "a" * 32, "status": "running", "message": "Rendering"}
    monkeypatch.setattr(client, "call", status)
    ticks = iter([0, 10])
    monkeypatch.setattr("backend.cli.client.time.monotonic", lambda: next(ticks))
    with pytest.raises(CliError) as caught:
        client.wait("a" * 32, .1)
    assert caught.value.exit_code == 124 and caught.value.details["job_id"] == "a" * 32
    assert calls == ["jobs/" + "a" * 32]


def test_connection_failure_while_waiting_preserves_job_id(service, monkeypatch):
    client = Client(service)
    def unavailable(*_): raise CliError("connection", "Unavailable")
    monkeypatch.setattr(client, "call", unavailable)
    with pytest.raises(CliError) as caught:
        client.wait("b" * 32, 30)
    assert caught.value.details["job_id"] == "b" * 32


def test_probe_rejects_another_checkout(service, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *_, **__: io.BytesIO(b'{"project":"another-checkout","apiVersion":1}'))
    with pytest.raises(CliError, match="другая копия"):
        Client(service)._probe()


def test_voices_never_expose_credentials(service, capsys):
    assert cli.main(["video", "voices"]) == 0
    data = result(capsys)["result"]
    assert len(data["engines"]) == 5
    assert "api_key" not in json.dumps(data) and "client_secret" not in json.dumps(data)


def test_artifact_export_is_repeatable_and_never_clobbers_different_files(service, tmp_path):
    identifier = "c" * 32
    folder = service.storage / identifier / "output/runs/one/landscape"
    folder.mkdir(parents=True)
    (folder / "video.mp4").write_bytes(b"actual-result")
    job = {"id": identifier, "status": "done", "outputs": [{"directory":"runs/one/landscape", "format":"landscape"}]}
    described = describe_job(service, job, "http://127.0.0.1:8001")
    assert Path(described["outputs"][0]["files"]["mp4"]).read_bytes() == b"actual-result"
    exported = export_job(service, job, tmp_path / "exports")
    assert export_job(service, job, tmp_path / "exports") == exported
    output = Path(exported["files"][0]["path"])
    output.write_bytes(b"my edited video")
    with pytest.raises(CliError, match="другой файл"):
        export_job(service, job, tmp_path / "exports")
    assert output.read_bytes() == b"my edited video"


def test_bad_preview_does_not_call_provider(service, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Client, "ensure", lambda *_: pytest.fail("No provider call"))
    assert cli.main(["video", "preview", "--text", " "]) == 2
    assert result(capsys)["error"]["code"] == "usage"


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_wait_timeout_must_be_positive_finite(service, monkeypatch, capsys, timeout):
    monkeypatch.setattr(Client, "ensure", lambda *_: pytest.fail("No server call"))
    assert cli.main(["video", "wait", "d" * 32, "--timeout", timeout]) == 2
    assert result(capsys)["error"]["code"] == "usage"
