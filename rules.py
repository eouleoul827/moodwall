"""확률 기반 판정 규칙.

모델이 내놓은 라벨별 확률(scores)을 받아서
표시 / 기본 / 승인대기 / 차단 중 하나로 판정한다.
모델 없이도 테스트할 수 있게 따로 분리해둠.
"""
from labels import (
    BLOCK_THRESHOLD, PENDING_THRESHOLD, MIN_TOP_SCORE, MIN_MARGIN,
    RESULT_SHOW, RESULT_DEFAULT, RESULT_PENDING, RESULT_BLOCK,
)


def decide(scores: dict) -> dict:
    """scores 예: {"축하": 0.93, "기쁨": 0.04, ...}"""
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_label, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    bad = scores.get("부적절", 0.0)

    # 1. 부적절은 1등이 아니어도 확률만 넘으면 걸러냄
    if bad >= BLOCK_THRESHOLD:
        return {"label": "부적절", "score": round(bad, 4), "result": RESULT_BLOCK}
    if bad >= PENDING_THRESHOLD:
        return {"label": "부적절", "score": round(bad, 4), "result": RESULT_PENDING}

    # 2. 부적절이 1등인데 점수가 낮은 경우도 띄우지 않고 승인 대기
    if top_label == "부적절":
        return {"label": "부적절", "score": round(top_score, 4), "result": RESULT_PENDING}

    # 3. 확신이 낮으면 기본 애니메이션
    if top_score < MIN_TOP_SCORE or (top_score - second_score) < MIN_MARGIN:
        return {"label": top_label, "score": round(top_score, 4), "result": RESULT_DEFAULT}

    # 4. 그 외는 1등 감정으로 표시
    return {"label": top_label, "score": round(top_score, 4), "result": RESULT_SHOW}
