# AI 학습 자료와 로컬 진단 도구

학생 멘션을 축하·기쁨·응원·설렘·피곤·슬픔·화남·부적절 8종으로 분류하는 ELECTRA v0의 학습 자료다. 원래 학습 노트북과 시드 데이터의 내용은 보존하고 저장 위치만 옮겼다.

서비스 모델 호출은 `backend/inference/student_model.py`를 사용하는 독립 작업자가 담당한다. 이 폴더의 HTTP API는 개발자가 모델 점수와 CPU 실행만 확인하는 진단 도구다. `부적절` 점수는 평가된 안전성 판정이 아니며 진단 결과가 게시나 학생 제재를 승인하지 않는다. 현재 백엔드 v0도 모든 모델 결과를 `UNCERTAIN`으로 처리한다.

```text
ai/
  train/moodwall_train_v0.ipynb
  data/seed_data.csv
  tools/local_api/
    app.py
    check_pc.py
    start_slm.bat
    requirements.txt
    tests/test_local_api.py
```

## 학습 자료

Colab에서 `train/moodwall_train_v0.ipynb`를 열고 T4 GPU 런타임으로 실행한다. 파일 업로드 단계에서 저장소의 `ai/data/seed_data.csv`를 직접 선택한다. Colab에 업로드된 파일 이름은 `seed_data.csv`이므로 노트북의 상대 경로를 저장소 폴더 구조로 바꾸지 않는다.

원본 노트북은 `beomi/KcELECTRA-base-v2022`를 사용하고 시드 300문장에 UnSmile에서 뽑은 부적절 문장 40개를 합친 뒤 중복을 제거한다. `random_state=42`로 15%를 나누며 학습 설정은 `max_length=64`, batch 16, AdamW 5e-5, 8 epochs다. 저장 단계에서 `moodwall_model.zip`과 `test_set_v0.csv`를 Google Drive의 `MyDrive`로 복사한다. 다운로드 원본은 Git에 넣지 않는다.

노트북의 패키지 설치와 외부 데이터 URL은 당시 실험을 보존한 것으로, 실행 환경과 데이터 revision이 고정된 재현 절차는 아니다. 재학습 전에 AI 담당이 모델·데이터 revision, 학습용 의존성, 출처·이용 조건, train/validation/test 분리와 평가 결과를 확정해야 한다. 임계값 비교 셀은 탐색 기록이며 서비스에 채택한 차단 기준이 아니다. `test_set_v0.csv`만으로 별도 검증·최종 평가가 완료됐다고 보지 않는다.

- `seed_data.csv`: 팀 작성 300문장. 실명은 `OO`로 가렸다.
- UnSmile: 노트북에서 [공식 데이터 저장소](https://github.com/smilegate-ai/korean_unsmile_dataset)의 train v1.0을 받는다. 여기에 원본 데이터는 재배포하지 않으며 사용 조건 확인은 남아 있다.
- AI Hub 감성 대화 말뭉치는 기존 팀 기록상 이용 신청 상태이며, 이 저장소에 해당 데이터가 포함됐다는 뜻은 아니다.

## 모델 파일 준비

이 도구는 백엔드 브랜치를 통합한 뒤 사용한다. `backend/requirements-model.lock`, `backend/inference/student_model.py`가 없다면 먼저 해당 변경을 통합한다. AI 도구가 따로 최신 패키지를 설치하거나 모델을 다운로드하지 않는다.

[학생 모델 연결 가이드](../docs/student-model-integration.md)의 릴리스 ZIP을 저장소 `models/`에 풀거나 그 가이드의 준비 도구로 전달받은 원본 폴더를 복사한다. 기본 경로는 `models/student-v0/`이며 `config.json`, `tokenizer.json`, `tokenizer_config.json`, `model.safetensors`, `manifest.json` 5개가 필요하다. 해시 검증 대상 폴더에 보고서·캐시·다른 설정 파일을 넣지 않는다.

준비 도구는 원본을 보존한다. 이 도구도 원본을 수정하지 않는다. 모델이 없거나 manifest·해시·라벨·가중치가 불완전하면 시작이 실패하며 임의의 모델이나 무작위 분류기로 대체하지 않는다.

## Windows에서 진단하기

저장소 루트에서 Python 3.12로 실행한다. CPU 추론 패키지는 백엔드의 고정 버전을 공유한다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r ai/tools/local_api/requirements.txt
.\.venv\Scripts\python.exe ai/tools/local_api/check_pc.py
.\.venv\Scripts\python.exe -m uvicorn ai.tools.local_api.app:app --host 127.0.0.1 --port 8001 --no-access-log
```

`ai/tools/local_api/start_slm.bat`도 같은 서버 명령을 실행한다. 저장소 `.venv`를 기본으로 사용하며 다른 환경은 `SLM_PYTHON`에 Python 실행 파일 절대 경로를 지정할 수 있다. 상시 운영용 작업 스케줄러나 자동 재시작 서비스로 등록하지 않는다. 이 API는 서비스 작업자를 대체하지 않는다.

다른 **준비된 bundle**은 `$env:SLM_MODEL_DIR = 'C:\path\to\prepared-bundle'`로 지정한다. 기본 모델 경로는 `app.py` 위치에서 저장소 루트를 계산하므로 현재 작업 폴더와 무관하다. 상대 환경변수 경로도 저장소 루트를 기준으로 해석한다. 모델은 시작 시 한 번 로드하며 CPU 2스레드·64토큰·eval/inference_mode 설정을 공유한다. 원격 코드·모델 다운로드·pickle 가중치는 사용하지 않는다.

Linux에서 단독 진단할 때는 Python 3.12의 `.venv/bin/python`으로 같은 requirements와 uvicorn 모듈을 실행한다. 서비스 전체 API·DB·작업자 실행은 [백엔드 가이드](../backend/README.md)의 Linux Docker 절차를 따른다. 예전 Intel macOS용 Transformers 4.x 조합은 이 v0의 검증된 실행 환경으로 안내하지 않는다.

## HTTP 계약과 입력 보존

메인 백엔드는 8000번, 진단 도구는 **127.0.0.1:8001**을 사용한다. 응답 점수는 보정되지 않은 softmax 값이며 실제 안전 확률이나 승인 기준이 아니다.

```text
GET  http://127.0.0.1:8001/health
POST http://127.0.0.1:8001/classify
Content-Type: application/json

{"text":"합격 축하해!"}
```

응답 필드는 `label`, `score`, `inappropriate_score`, `ms`, `diagnostic_only=true`, `public_display_authorized=false`다. 원문을 응답·로그에 남기지 않는다. 전송된 JSON 본문은 4096바이트 이하여야 하며 초과하면 413이다. 원문의 UTF-8 크기는 별도로 2048바이트 이하여야 한다. `text`만 허용하고 빈 문자열·공백뿐인 글·31 grapheme 이상·원문 바이트 초과·UTF-8로 인코딩할 수 없는 문자·다른 타입은 422로 거절한다. 이모지 결합 문자도 grapheme 단위로 센다. 입력을 자르거나 `strip`하지 않고 검증한 원문 전체를 토크나이저에 보낸다. 토크나이저의 64토큰 truncation은 길이 검증과 별도다.

`check_pc.py`는 고정 합성 문장 4개로 준비 후 100회 CPU 지연을 측정한다. 해당 기기의 단일 요청 실행 확인이며 정확도·안전성·학교 부하 시험이 아니다. 이전 README의 다른 PC 측정값은 이 코드의 재현 가능한 성능 보증으로 사용하지 않는다.

## 가벼운 회귀 검사

백엔드의 test 의존성이 설치된 환경에서 저장소 루트 기준으로 실행한다. 가짜 predictor를 주입하므로 torch나 실제 가중치 없이 경로·포트·본문 상한·30 grapheme·원문 보존·시작 실패를 확인한다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.lock
.\.venv\Scripts\python.exe -m pytest -q ai/tools/local_api/tests
```
