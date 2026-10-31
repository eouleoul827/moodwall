"""Measure load time and per-sentence CPU latency on the target machine."""

import os
import time

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

_MODEL_DIR = os.getenv("SLM_MODEL_DIR", "./moodwall_model")
_SAMPLES = ("민지야 생일 축하해!!", "과제 너무 많아 지친다", "내일 축제 개기대됨ㅋㅋ", "오늘 학식 맛있었다")


def main():
    torch.set_num_threads(int(os.getenv("SLM_THREADS", "2")))
    print(f"cpu={os.cpu_count()} threads={torch.get_num_threads()}")

    started = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(_MODEL_DIR, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(_MODEL_DIR, local_files_only=True).eval()
    print(f"load={time.perf_counter() - started:.1f}s")

    times = []
    with torch.inference_mode():
        for i in range(105):
            started = time.perf_counter()
            model(**tokenizer(_SAMPLES[i % len(_SAMPLES)], return_tensors="pt"))
            times.append((time.perf_counter() - started) * 1000)
    times = sorted(times[5:])  # first calls include warmup
    print(f"avg={sum(times) / len(times):.0f}ms p95={times[94]:.0f}ms max={times[-1]:.0f}ms")


if __name__ == "__main__":
    main()
