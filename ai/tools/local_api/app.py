"""Local diagnostic API; scores never authorize public display.

Run uvicorn app:app --host 127.0.0.1 --port 8001 from this directory.
The main service uses its own queue worker, not this endpoint.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol

import regex
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_DIR = REPOSITORY_ROOT / "models" / "student-v0"
MAX_BODY_BYTES = 4096
MAX_TEXT_BYTES = 2048
MAX_GRAPHEMES = 30
REQUIRED_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors", "manifest.json")


class Prediction(Protocol):
    label: str
    score: float
    inappropriate_score: float


Predictor = Callable[[str], Prediction]
PredictorFactory = Callable[[Path], Predictor]


def resolve_model_dir(configured: str | Path | None = None) -> Path:
    value = configured if configured is not None else os.environ.get("SLM_MODEL_DIR")
    path = Path(value).expanduser() if value else DEFAULT_MODEL_DIR
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    return path.resolve()


def load_predictor(model_dir: Path) -> Predictor:
    """Reuse the backend's pinned, CPU-only, strict v0 bundle loader."""
    if not model_dir.is_dir() or any(not (model_dir / name).is_file() for name in REQUIRED_FILES):
        raise RuntimeError(
            f"Prepared student model bundle is missing at {model_dir}. "
            "Download the model release into models/student-v0 or prepare a bundle with "
            "backend/tools/prepare_student_model.py, then set SLM_MODEL_DIR if needed."
        )
    backend_dir = REPOSITORY_ROOT / "backend"
    if not (backend_dir / "inference" / "student_model.py").is_file():
        raise RuntimeError("Backend model integration is missing. Integrate the backend branch before using this tool.")
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    # No torch/transformers import or weight loading occurs when app.py imports.
    from inference.loader import load_analyzer

    manifest_path = model_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    settings = SimpleNamespace(
        environment="test",
        analyzer="inference.student_model:create_analyzer",
        model_manifest_path=str(manifest_path),
        model_bundle_version=manifest["model_bundle_version"],
        inference_policy_version=manifest["inference_policy_version"],
    )
    # SHA-256, v0 labels, policy and complete weights are validated before CPU
    # warmup. The shared loader uses safetensors, local_files_only and no remote
    # code. predict() exposes diagnostic scores, never a public safety gate.
    return load_analyzer(settings).predict


class BodySizeLimit:
    """Enforce the byte limit before JSON parsing, including streamed bodies."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                if not value.isdigit():
                    response = JSONResponse({"detail": "invalid Content-Length"}, status_code=400)
                    return await response(scope, receive, send)
                if int(value) > self.max_bytes:
                    return await self._too_large(scope, receive, send)
        chunks = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.max_bytes:
                return await self._too_large(scope, receive, send)
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _too_large(self, scope, receive, send):
        response = JSONResponse({"detail": "request body exceeds 4096 bytes"}, status_code=413)
        await response(scope, receive, send)


class ClassifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        try:
            text_bytes = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            raise ValueError("text must be valid UTF-8") from None
        if text_bytes > MAX_TEXT_BYTES:
            raise ValueError("text must not exceed 2048 UTF-8 bytes")
        if not value or value.isspace() or len(regex.findall(r"\X", value)) > MAX_GRAPHEMES:
            raise ValueError("text must contain 1 to 30 graphemes and cannot be blank")
        return value


def create_app(*, predictor_factory: PredictorFactory = load_predictor, model_dir: str | Path | None = None) -> FastAPI:
    selected_dir = resolve_model_dir(model_dir)

    @asynccontextmanager
    async def lifespan(application):
        application.state.predictor = await asyncio.to_thread(predictor_factory, selected_dir)
        try:
            yield
        finally:
            application.state.predictor = None

    application = FastAPI(title="Moodwall local model diagnostic", lifespan=lifespan)
    application.add_middleware(BodySizeLimit, max_bytes=MAX_BODY_BYTES)

    @application.exception_handler(RequestValidationError)
    async def invalid_input(_request, _error):
        # FastAPI's default validation response echoes the submitted input.
        return JSONResponse(
            {"detail": "expected only text: valid UTF-8, nonblank, at most 30 graphemes and 2048 UTF-8 bytes"},
            status_code=422,
        )

    @application.get("/health")
    def health():
        return {"status": "ok", "diagnostic_only": True, "public_display_authorized": False}

    @application.post("/classify")
    def classify(body: ClassifyRequest, request: Request):
        started = time.perf_counter()
        prediction = request.app.state.predictor(body.text)
        return {
            "label": prediction.label,
            "score": prediction.score,
            "inappropriate_score": prediction.inappropriate_score,
            "ms": (time.perf_counter() - started) * 1000,
            "diagnostic_only": True,
            "public_display_authorized": False,
        }

    return application


app = create_app()
