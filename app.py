"""무드월 SLM 서버 (FastAPI)

- POST /analyze    : SLM 감정 분류 + 판정 (표시/기본/승인대기/차단)
- POST /background : 감정에 맞는 배경 이미지 생성 (제미나이 나노바나나)
- GET  /health     : 서버 상태 확인

실행:
    export GEMINI_API_KEY=발급받은키
    uvicorn app:app --host 0.0.0.0 --port 8000
"""
import asyncio
import csv
import os
import uuid
from datetime import datetime
from pathlib import Path

import torch
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from labels import LABELS, MOOD_STYLE, RESULT_BLOCK, RESULT_DEFAULT, RESULT_PENDING
from rules import decide

MODEL_DIR = os.getenv("MODEL_DIR", "model")
NANO_MODEL = os.getenv("NANO_MODEL", "gemini-3.1-flash-image-preview")  # Nano Banana 2 (최신 ID는 공식 문서 확인)
IMAGE_TIMEOUT = float(os.getenv("IMAGE_TIMEOUT", "20"))
ASPECT_RATIO = os.getenv("ASPECT_RATIO", "16:9")  # 전광판 해상도 확인 후 수정

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
GEN_DIR = STATIC_DIR / "generated"
DEFAULT_DIR = STATIC_DIR / "defaults"
LOG_PATH = BASE_DIR / "logs" / "uncertain.csv"
for d in (GEN_DIR, DEFAULT_DIR, LOG_PATH.parent):
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Moodwall SLM")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ---------- SLM 로드 (서버 켤 때 한 번만) ----------
torch.set_num_threads(max(1, os.cpu_count() or 1))
tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR)
model = AutoModelForSequenceClassification.from_pretrained(MODEL_DIR)
model.eval()


class AnalyzeReq(BaseModel):
    text: str = Field(..., max_length=60)


class BackgroundReq(BaseModel):
    text: str = Field(..., max_length=60)
    label: str


def log_uncertain(text, scores, result):
    """기본/승인대기/차단 문장은 재학습용으로 따로 저장"""
    new_file = not LOG_PATH.exists()
    with open(LOG_PATH, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["time", "text", "result", *LABELS, "correct_label"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), text, result,
                    *[scores[l] for l in LABELS], ""])


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_DIR}


@app.post("/analyze")
def analyze(req: AnalyzeReq):
    text = req.text.strip()
    if not text:
        return {"label": "일반", "score": 0.0, "scores": {}, "result": RESULT_DEFAULT}

    with torch.no_grad():
        inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=64)
        probs = torch.softmax(model(**inputs).logits, dim=-1)[0].tolist()

    scores = {model.config.id2label[i]: round(p, 4) for i, p in enumerate(probs)}
    decision = decide(scores)

    if decision["result"] in (RESULT_DEFAULT, RESULT_PENDING, RESULT_BLOCK):
        log_uncertain(text, scores, decision["result"])

    return {**decision, "scores": scores}


# ---------- 나노바나나 배경 이미지 ----------
def default_image(label):
    path = DEFAULT_DIR / f"{label}.png"
    if not path.exists():
        path = DEFAULT_DIR / "일반.png"
    return f"/static/defaults/{path.name}"


def build_prompt(text, label):
    style = MOOD_STYLE.get(label, MOOD_STYLE["일반"])
    return (
        f"Abstract background image for a school lobby display. Mood: {style}. "
        f"Inspired by the feeling of this short message: \"{text}\". "
        "No text, no letters, no words, no logos, no people, no faces. "
        "Soft, clean, wide composition with empty space in the center for overlay text."
    )


def generate_image(text, label):
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    resp = client.models.generate_content(
        model=NANO_MODEL,
        contents=[build_prompt(text, label)],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=ASPECT_RATIO),
        ),
    )
    for part in resp.candidates[0].content.parts:
        if part.inline_data and part.inline_data.data:
            name = f"{uuid.uuid4().hex}.png"
            (GEN_DIR / name).write_bytes(part.inline_data.data)
            return f"/static/generated/{name}"
    raise RuntimeError("이미지가 응답에 없음")


@app.post("/background")
async def background(req: BackgroundReq):
    # 부적절이거나 모르는 라벨이면 생성하지 않고 기본 이미지
    if req.label not in MOOD_STYLE or not os.getenv("GEMINI_API_KEY"):
        return {"url": default_image(req.label), "generated": False}
    try:
        url = await asyncio.wait_for(asyncio.to_thread(generate_image, req.text, req.label),
                                     timeout=IMAGE_TIMEOUT)
        return {"url": url, "generated": True}
    except Exception as e:
        print("[background] 생성 실패, 기본 이미지 사용:", e)
        return {"url": default_image(req.label), "generated": False}
