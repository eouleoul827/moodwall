"""Local HTTP wrapper around the fine-tuned KcELECTRA model. Text is classified, never stored."""

from __future__ import annotations

import logging
import os
import time

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from transformers import AutoModelForSequenceClassification, AutoTokenizer

_MODEL_DIR = os.getenv("SLM_MODEL_DIR", "./moodwall_model")
_MAX_CHARS = 30

# Leave CPU for the display browser running on the same machine.
torch.set_num_threads(int(os.getenv("SLM_THREADS", "2")))
logging.basicConfig(filename="slm.log", level=logging.INFO, format="%(asctime)s %(message)s", encoding="utf-8")
_log = logging.getLogger("slm")

_tokenizer = AutoTokenizer.from_pretrained(_MODEL_DIR, local_files_only=True)
_model = AutoModelForSequenceClassification.from_pretrained(_MODEL_DIR, local_files_only=True)
_model.eval()
_UNSAFE = _model.config.label2id["부적절"]

app = FastAPI(title="moodwall-slm")


class ClassifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


def _probs(text: str) -> torch.Tensor:
    inputs = _tokenizer(text, truncation=True, max_length=64, return_tensors="pt")
    with torch.inference_mode():
        return torch.softmax(_model(**inputs).logits, dim=-1)[0]


_probs("warmup")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/classify")
def classify(body: ClassifyRequest):
    text = body.text.strip()[:_MAX_CHARS]
    if not text:
        raise HTTPException(status_code=400, detail="empty text")

    started = time.perf_counter()
    probs = _probs(text)
    best = int(probs.argmax())
    result = {
        "label": _model.config.id2label[best],
        "score": round(probs[best].item(), 4),
        "inappropriate_score": round(probs[_UNSAFE].item(), 4),
        "ms": round((time.perf_counter() - started) * 1000),
    }
    # Metrics only; the submitted text is not written to the log.
    _log.info(
        "label=%s score=%.3f unsafe=%.3f ms=%d",
        result["label"],
        result["score"],
        result["inappropriate_score"],
        result["ms"],
    )
    return result
