# 무드월 SLM 서버

감정 분류는 직접 학습한 SLM(KcELECTRA), 배경 이미지는 제미나이 나노바나나로 생성.

## 파일 구성

| 파일 | 역할 |
|---|---|
| labels.py | 라벨 목록, 판정 기준값, 감정별 이미지 분위기 |
| rules.py | 확률 보고 표시/기본/승인대기/차단 판정 |
| train.py | KcELECTRA 파인튜닝 (Colab) |
| app.py | FastAPI 서버 (/analyze, /background) |
| test_rules.py | 판정 규칙 테스트 (모델 없이 실행) |
| data/sample_train.csv | 학습 데이터 형식 예시 |

## 1. 학습 데이터 준비

`data/train.csv`를 `text,label` 형식으로 만들기 (sample_train.csv 참고).

- 라벨: 축하, 기쁨, 응원, 설렘, 피곤, 슬픔, 화남, 일반, 부적절
- 라벨당 최소 200~300개
- AI Hub 감성 대화 말뭉치 + 직접 만든 학생 말투 문장 + 혐오표현 데이터셋(부적절)

## 2. 학습 (Colab)

1. Colab에서 런타임 → 런타임 유형 변경 → GPU 선택
2. 폴더 전체 업로드 후 실행

```
!pip install transformers datasets scikit-learn accelerate
!python train.py --data data/train.csv
```

끝나면 `model/` 폴더가 생기고 테스트 결과(라벨별 정확도)가 출력됨. `model/` 폴더를 다운받아서 서버 폴더에 넣기.

## 3. 서버 실행

```
pip install -r requirements.txt
export GEMINI_API_KEY=발급받은키     # 윈도우는 set GEMINI_API_KEY=키
uvicorn app:app --host 0.0.0.0 --port 8000
```

`http://localhost:8000/docs`에서 바로 테스트 가능.

## 4. API

### POST /analyze

요청 `{"text": "민수야 생일 축하해!!"}`

응답
```json
{"label": "축하", "score": 0.93, "result": "show", "scores": {"축하": 0.93, "기쁨": 0.04}}
```

| result | 뜻 | 서버 처리 |
|---|---|---|
| show | 1등 감정으로 표시 | 감정 애니메이션 + 이미지 생성 |
| default | 확신 낮음 | 기본 애니메이션 + 이미지 생성 |
| pending | 부적절 의심 | 관리자 승인 대기 |
| block | 부적절 확실 | 차단 |

### POST /background

요청 `{"text": "민수야 생일 축하해!!", "label": "축하"}`

응답 `{"url": "/static/generated/abc.png", "generated": true}`

생성 실패, 20초 넘게 걸림, API 키 없음이면 `static/defaults/{라벨}.png` 기본 이미지 주소를 돌려줌 (`generated: false`).
기본 이미지는 감정별로 미리 만들어서 `static/defaults/축하.png` 같은 이름으로 넣어두기 (최소 `일반.png`는 필수).

## 5. 판정 기준값 조정

`labels.py`의 값을 바꾸면 됨.

- BLOCK_THRESHOLD = 0.7 (부적절 차단)
- PENDING_THRESHOLD = 0.4 (승인 대기)
- MIN_TOP_SCORE = 0.6 (1등 점수 최소)
- MIN_MARGIN = 0.3 (1·2등 차이 최소)

바꾼 뒤 `python test_rules.py`로 규칙이 의도대로 도는지 확인.

## 6. 재학습

default, pending, block으로 판정된 문장은 `logs/uncertain.csv`에 자동 저장됨.
`correct_label` 칸에 정답 라벨을 적고 train.csv에 추가해서 다시 학습하면 됨.

## 서버 담당이랑 맞출 것

- /analyze 응답의 `result` 값 4개로 분기
- 이미지 생성은 pending, block이 아닐 때만 /background 호출
- 이미지 주소는 이 서버 주소 + url (예: `http://SLM서버:8000/static/generated/abc.png`)
- 나노바나나 모델은 환경변수 `NANO_MODEL`로 변경 (기본값 Nano Banana 2, Pro는 `gemini-3-pro-image-preview`)
- 전광판 비율 확인되면 `ASPECT_RATIO` 수정 (기본 16:9)
