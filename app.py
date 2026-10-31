# 무드월 감정 분류 API - 전광판 PC 운영용
# 같은 PC의 Node 서버만 접속하도록 127.0.0.1로 실행합니다.
# 실행: python -m uvicorn app:app --host 127.0.0.1 --port 8000
import os
import time
import logging

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import AutoTokenizer, AutoModelForSequenceClassification

# 전광판 화면(브라우저 애니메이션)이 끊기지 않도록 CPU 코어를 2개만 사용
torch.set_num_threads(int(os.getenv("SLM_THREADS", "2")))

MODEL_DIR = os.getenv("SLM_MODEL_DIR", "./moodwall_model")

# 로그에는 멘션 원문을 남기지 않음 (원문은 Node 서버 DB에서만 관리)
logging.basicConfig(
    filename="slm.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)

tok = AutoTokenizer.from_pretrained(MODEL_DIR)
model = AutoModelForSequenceClassification.from_pretrained(MODEL_DIR)
model.eval()
INAPP_ID = model.config.label2id["부적절"]

app = FastAPI(title="Moodwall SLM")


def run(text: str):
    enc = tok(text, truncation=True, max_length=64, return_tensors="pt")
    with torch.inference_mode():
        return torch.softmax(model(**enc).logits, dim=-1)[0]


run("준비 운동")  # 첫 요청이 느리지 않도록 미리 한 번 실행
logging.info("model loaded from %s, threads=%d", MODEL_DIR, torch.get_num_threads())


class Mention(BaseModel):
    text: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/classify")
def classify(m: Mention):
    text = m.text.strip()[:30]
    if not text:
        raise HTTPException(status_code=400, detail="empty text")
    start = time.perf_counter()
    prob = run(text)
    i = int(prob.argmax())
    result = {
        "label": model.config.id2label[i],
        "score": round(float(prob[i]), 4),
        "inappropriate_score": round(float(prob[INAPP_ID]), 4),
        "ms": round((time.perf_counter() - start) * 1000),
    }
    logging.info("label=%s score=%.3f inapp=%.3f ms=%d",
                 result["label"], result["score"], result["inappropriate_score"], result["ms"])
    return result
