from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..config import _app_dir
from .service import VideoRequest, VideoService
from .voice import VoiceOptions

router = APIRouter(prefix="/api/video")
service = VideoService(Path(__file__).resolve().parents[2] / "video", _app_dir() / "video_runs")


class PreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=420)
    voice: VoiceOptions = Field(default_factory=VoiceOptions)


@router.get("/status")
def status():
    return service.status()


@router.get("/examples/{name}")
def example(name: str):
    try:
        return service.example(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error


@router.post("/assets")
async def upload(file: UploadFile = File(...)):
    try:
        return service.upload(file.filename or "", await file.read(20 * 1024 * 1024 + 1))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/asset")
def asset(path: str):
    try:
        response = FileResponse(service.asset_file(path))
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error


@router.post("/preview")
async def preview(body: PreviewRequest):
    try:
        return await asyncio.to_thread(service.preview, body.text, body.voice)
    except Exception as error:
        raise HTTPException(422, str(error)) from error


@router.get("/previews")
def previews():
    return {"previews": service.previews()}


@router.post("/jobs", status_code=202)
def create_job(body: VideoRequest):
    try:
        return service.start(body)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    except (ValueError, OSError) as error:
        raise HTTPException(422, str(error)) from error


class ValidationRequest(BaseModel):
    campaign: dict


@router.post("/validate")
def validate(body: ValidationRequest):
    try:
        return service.validate(body.campaign)
    except (ValueError, OSError) as error:
        raise HTTPException(422, str(error)) from error


@router.get("/jobs")
def history():
    return {"jobs": service.history()}


@router.get("/jobs/{identifier}")
def job(identifier: str):
    try:
        return service.job(identifier)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error


@router.get("/files/{identifier}/{relative:path}")
def artifact(identifier: str, relative: str):
    try:
        path = service.public_file(identifier, relative)
        response = FileResponse(path)
        # Uploaded files cannot become scripts in the application's origin.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; media-src 'self'; script-src 'none'; frame-ancestors 'self'"
        return response
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error
