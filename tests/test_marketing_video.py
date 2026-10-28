"""Contracts that prevent silent videos, leaked files and paid invalid requests."""
import builtins
import copy
import json
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.marketing import routes, voice
from backend.marketing.service import VideoRequest, VideoService, contained
from backend.marketing.voice import VoiceOptions


@pytest.fixture
def service(tmp_path):
    return VideoService(Path(__file__).resolve().parents[1] / "video", tmp_path / "jobs")


@pytest.fixture
def client(service, monkeypatch):
    monkeypatch.setattr(routes, "service", service)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("name", ["../settings.json", "C:/secret.png", "a\\b.png", "a/%2e.png", "/file.png", "a/../b.png"])
def test_asset_paths_cannot_escape_project(tmp_path, name):
    with pytest.raises(ValueError):
        contained(tmp_path, name)


def test_snapshot_preserves_the_exact_images_and_original_scenario(service, tmp_path):
    campaign = service.example("edo-short")
    original = copy.deepcopy(campaign)
    snapshot = service._snapshot(campaign, tmp_path / "snapshot")
    snapshot["scenes"][0]["narration"] = "Changed take"
    assert campaign == original
    image = campaign["scenes"][0]["image"]
    assert (tmp_path / "snapshot/public" / image).read_bytes() == (service.renderer / "public" / image).read_bytes()


def test_invalid_scenario_never_reaches_paid_tts(service, monkeypatch):
    campaign = service.example("edo-short")
    campaign["scenes"][0]["type"] = "unknown-layout"
    monkeypatch.setattr(service, "_execute", lambda *_: pytest.fail("TTS must not start"))
    with pytest.raises(ValueError, match="тип сцены"):
        service.start(VideoRequest(campaign=campaign, command="render", voice=VoiceOptions(engine="yandex", speaker="alena")))
    assert not service.status()["busy"]


def test_only_one_render_can_start(client, service):
    service._busy.acquire()
    try:
        response = client.post("/api/video/jobs", json={"campaign": service.example("edo-short")})
        assert response.status_code == 409
    finally:
        service._busy.release()


@pytest.mark.parametrize("audio", [np.zeros(2400), np.array([np.nan]), np.array([np.inf]), np.array([])])
def test_failed_tts_cannot_become_a_successful_silent_take(tmp_path, monkeypatch, audio):
    class SilentEngine:
        accepts_accent_marks = False
        def list_speakers(self): return ["test"]
        def initialize(self): pass
        def resolve_sample_rate(self, *_): return 24000
        def synth(self, *_, **__): return audio
    monkeypatch.setattr(voice, "get_engine", lambda *_, **__: SilentEngine())
    output = tmp_path / "voice.wav"
    with pytest.raises(ValueError, match="тишину"):
        voice.synthesize("Проверка голоса.", VoiceOptions(engine="edge", speaker="test"), output)
    assert not output.exists()


def test_runtime_error_is_visible_in_history_and_unlocks_next_job(service, monkeypatch):
    def fail(*_): raise ValueError("Voice provider unavailable")
    monkeypatch.setattr("backend.marketing.service.synthesize", fail)
    job = service.start(VideoRequest(campaign=service.example("edo-short"), command="render"))
    deadline = time.monotonic() + 5
    while service.status()["busy"] and time.monotonic() < deadline:
        time.sleep(.02)
    saved = service.job(job["id"])
    assert saved["status"] == "error"
    assert "Voice provider unavailable" in saved["message"]
    assert service.history()[0]["status"] == "error"
    assert not service.status()["busy"]


def test_artifact_route_hides_logs_and_supports_media_ranges(client, service):
    identifier = "a" * 32
    folder = service.storage / identifier
    (folder / "output").mkdir(parents=True)
    (folder / "renderer-request.json").write_text("private")
    (folder / "output/video.mp4").write_bytes(b"0123456789")
    response = client.get(f"/api/video/files/{identifier}/output/video.mp4", headers={"Range":"bytes=2-5"})
    assert response.status_code == 206 and response.content == b"2345"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"/api/video/files/{identifier}/renderer-request.json").status_code == 404


def test_saved_running_jobs_become_interrupted_after_restart(service):
    identifier = "b" * 32
    directory = service.storage / identifier
    directory.mkdir(parents=True)
    (directory / "job.json").write_text(json.dumps({"id":identifier,"status":"running"}))
    assert service.job(identifier)["status"] == "interrupted"


def test_missing_gigaam_does_not_prevent_video_only_startup(monkeypatch):
    from backend.asr.gigaam_engine import _check_v3_available
    original_import = builtins.__import__
    def unavailable(name, *args, **kwargs):
        if name == "gigaam":
            raise ModuleNotFoundError("No module named 'gigaam'", name="gigaam")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", unavailable)
    assert _check_v3_available("v3") is False
