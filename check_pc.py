# 전광판 PC에서 모델이 충분히 빠른지 확인하는 스크립트
# 실행: python check_pc.py
import os, time, platform
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification

torch.set_num_threads(2)
print("OS:", platform.platform())
print("CPU 코어 수:", os.cpu_count(), "/ 사용할 스레드:", torch.get_num_threads())

t = time.perf_counter()
tok = AutoTokenizer.from_pretrained("./moodwall_model")
model = AutoModelForSequenceClassification.from_pretrained("./moodwall_model").eval()
print(f"모델 로딩: {time.perf_counter() - t:.1f}초")

texts = ["민지야 생일 축하해!!", "과제 너무 많아 지친다", "내일 축제 개기대됨ㅋㅋ", "오늘 학식 맛있었다"] * 25
times = []
with torch.inference_mode():
    for s in texts:
        t = time.perf_counter()
        model(**tok(s, return_tensors="pt", truncation=True, max_length=64))
        times.append((time.perf_counter() - t) * 1000)
times = times[5:]  # 처음 몇 번은 느리므로 제외
print(f"문장당 평균 {sum(times)/len(times):.0f}ms, 가장 느림 {max(times):.0f}ms")
print("평균 300ms 이하면 전광판용으로 충분합니다.")
