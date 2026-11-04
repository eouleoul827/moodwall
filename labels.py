# 라벨 정의 (학습, API 공통으로 사용)
LABELS = ["축하", "기쁨", "응원", "설렘", "피곤", "슬픔", "화남", "일반", "부적절"]

LABEL2ID = {label: i for i, label in enumerate(LABELS)}
ID2LABEL = {i: label for i, label in enumerate(LABELS)}

# 판정 기준값 (학습 후 검증 데이터로 돌려보면서 조정)
BLOCK_THRESHOLD = 0.7      # 부적절 확률 이 값 이상이면 차단
PENDING_THRESHOLD = 0.4    # 부적절 확률 이 값 이상이면 관리자 승인 대기
MIN_TOP_SCORE = 0.6        # 1등 점수가 이 값보다 낮으면 기본 애니메이션
MIN_MARGIN = 0.3           # 1등과 2등 차이가 이 값보다 작으면 기본 애니메이션
                           # (1등이 0.6 이상이면 차이는 최소 0.2라서 0.2보다 커야 의미 있음)

# 판정 결과 코드 (서버 담당이랑 이 값으로 맞추기)
RESULT_SHOW = "show"        # 1등 감정으로 표시
RESULT_DEFAULT = "default"  # 기본 애니메이션으로 표시
RESULT_PENDING = "pending"  # 관리자 승인 대기
RESULT_BLOCK = "block"      # 차단

# 감정별 배경 이미지 분위기 (나노바나나 프롬프트에 사용)
MOOD_STYLE = {
    "축하": "festive confetti, warm gold and pink tones, celebratory light",
    "기쁨": "bright sunny sky, soft pastel colors, cheerful atmosphere",
    "응원": "sparkling stars, energetic blue and orange gradient, hopeful mood",
    "설렘": "soft pink cherry blossoms, dreamy light, gentle romantic mood",
    "피곤": "calm night sky, slow drifting clouds, muted lavender tones",
    "슬픔": "gentle rain on window, cool blue tones, quiet calm mood",
    "화남": "dramatic red sunset clouds, strong contrast, intense but not scary",
    "일반": "simple soft gradient, clean campus lobby vibe, neutral colors",
}
