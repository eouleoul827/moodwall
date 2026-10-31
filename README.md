# 무드월 SLM (감정 분류)

학생이 쓴 멘션(30자 이내)을 읽고 **어떤 감정인지**와 **전광판에 띄워도 되는 글인지**를 판단하는 모델입니다.
무드월 파이프라인의 4번 단계(감정 분류 + 저격/비방 판별)를 맡습니다.

```
요청:  {"text": "지민아 생일 축하해"}
응답:  {"label": "축하", "score": 0.95, "inappropriate_score": 0.01, "ms": 45}
```

- `label`: 감정 라벨 (아래 8개 중 하나)
- `score`: 그 라벨일 확률 (0~1)
- `inappropriate_score`: 부적절한 글일 확률 (0~1)
- `ms`: 판단에 걸린 시간 (밀리초)

## 폴더 구조

```
moodwall-slm/
├─ README.md                  이 문서
├─ train/
│  └─ moodwall_train_v0.ipynb Colab 학습 노트북 (데이터 합치기 → 학습 → 평가 → 저장)
├─ data/
│  └─ seed_data.csv           학습용 시드 문장 300개 (text, label)
└─ api/                       전광판 PC에서 실행하는 파일
   ├─ app.py                  감정 분류 API 서버 (FastAPI)
   ├─ requirements.txt        설치할 라이브러리 목록
   ├─ check_pc.py             PC에서 모델 속도 측정
   └─ start_slm.bat           서버 실행 + 꺼지면 자동 재시작 (Windows)
```

학습된 모델(`moodwall_model/`, 약 500MB)은 깃허브 용량 제한 때문에 저장소에 없습니다.
구글 드라이브 `내 드라이브/moodwall_model.zip`에 보관합니다.

## 라벨 8개

| 라벨 | CSV 값 | 뜻 | 애니메이션 |
| --- | --- | --- | --- |
| 축하 | congrats | 누군가의 좋은 일을 축하 | 폭죽 |
| 기쁨 | joy | 이미 일어난 일이 좋음 | 하트 |
| 응원 | cheer | 앞으로 할 일을 잘하라고 힘을 줌 | 반짝임 |
| 설렘 | excited | 아직 안 온 일이 기대됨 | 미정 |
| 피곤 | tired | 지침, 졸림, 귀찮음 | 미정 |
| 슬픔 | sad | 아쉬움, 우울, 서운함 | 비 |
| 화남 | angry | 상황·일에 대한 짜증 (특정인 아님) | 미정 |
| 부적절 | inappropriate | 특정인 저격·비방, 혐오, 성적 내용, 개인정보 | 표시 안 함 |

헷갈릴 때는 부적절 → 축하 → 응원 → 나머지 감정 순서로 먼저 해당하는 것을 고릅니다.
자세한 기준은 팀 공유 문서 "무드월 감정 라벨 기준표"를 따릅니다.

## 사용 방법

### 1. 모델 학습 (Colab)

1. colab.research.google.com → 파일 → 노트 업로드 → `train/moodwall_train_v0.ipynb`
2. 런타임 → 런타임 유형 변경 → **T4 GPU**
3. 위에서부터 셀을 하나씩 실행 (Shift+Enter). 3번 셀에서 `data/seed_data.csv` 업로드
4. 마지막 셀이 구글 드라이브에 `moodwall_model.zip`, `test_set_v0.csv` 저장

### 2. 전광판 PC에 설치 (Windows)

1. `C:\moodwall-slm` 폴더를 만들고 `api/` 안의 파일 4개를 복사
2. 드라이브의 `moodwall_model.zip`을 같은 폴더에 압축 해제
   (`C:\moodwall-slm\moodwall_model\config.json`이 있어야 함)
3. Python 3.11 설치 ("Add python.exe to PATH" 체크)
4. `C:\moodwall-slm`에서 명령 프롬프트를 열고:

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python check_pc.py
```

5. 실행 테스트: `start_slm.bat` 더블클릭 → 브라우저에서 http://127.0.0.1:8000/docs
6. 자동 실행: 작업 스케줄러에 `start_slm.bat`을 "시작할 때" 실행으로 등록
   ("다음 시간 이상 작업이 실행되면 중지"는 반드시 해제)

## 서버(Node) 연동

| 항목 | 내용 |
| --- | --- |
| 주소 | `POST http://127.0.0.1:8000/classify` (같은 PC에서만 접속 가능) |
| 상태 확인 | `GET http://127.0.0.1:8000/health` → `{"status": "ok"}` |
| 빈 문자열 | 400 에러 |

판정 규칙 (위에서부터 먼저 맞는 것 하나 적용, v0 기본값):

| 순서 | 조건 | 처리 |
| --- | --- | --- |
| 1 | 응답 없음 (1초 초과, 에러) | 표시하지 않고 관리자 확인 대기 |
| 2 | label이 부적절 | 표시하지 않음, 관리자 화면에 기록 |
| 3 | inappropriate_score ≥ 0.3 | 표시하지 않고 관리자 확인 대기 |
| 4 | score < 0.6 | 기본 애니메이션 |
| 5 | 그 외 | 라벨별 애니메이션 |

## 데이터 출처

| 데이터 | 사용 | 비고 |
| --- | --- | --- |
| 시드 문장 (`data/seed_data.csv`) | 8개 라벨 각 20~40문장 | 팀 작성, 실명 없음 (OO 처리) |
| [Smilegate UnSmile](https://github.com/smilegate-ai/korean_unsmile_dataset) | 부적절 문장 40개 | 학습 시 원본 주소에서 직접 내려받음 (저장소에 재배포하지 않음). 라이선스 확인 중 |
| AI Hub 감성 대화 말뭉치 | v1부터 사용 예정 | 이용 신청 중 |
| 베이스 모델 [beomi/KcELECTRA-base-v2022](https://huggingface.co/beomi/KcELECTRA-base-v2022) | 파인튜닝 | |

## 버전 기록

| 버전 | 학습 데이터 | 상태 |
| --- | --- | --- |
| v0 | 시드 300 + UnSmile 40 | 전체 흐름 검증용 (실사용 정확도 낮음) |
| v1 | + 팀원 문장 라벨당 300, AI Hub | 예정 |
| v2 | + 베타 기간 실제 멘션 | 예정 |
