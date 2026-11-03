# 학생 모델 v0 연결과 실행 확인

확인일: 2026-11-03. [추론 어댑터](../backend/inference/student_model.py)는 제공된 로컬 ELECTRA 8분류 가중치를 CPU에서 실행한다. 대용량 가중치는 Git 이력 대신 같은 비공개 저장소의 사전 릴리스 첨부 파일로 제공한다. 합성 예시 실행 결과와 모델 해시 명세는 문서에 포함하며, 기본 fake 어댑터는 계속 지원한다.

현재 모델은 감정 7종과 부적절 1종을 함께 예측한다. 감정별 확률은 로컬 검증 도구에서 확인할 수 있다. 독립적인 안전성 평가와 채택된 임계값이 없어 서비스 반환은 항상 `UNCERTAIN / INSUFFICIENT_CONTEXT / emotion=null`이다. 작업자는 이를 `NEEDS_REWRITE`로 저장한다. 게시 승인이나 학생 위반 횟수를 만들지 않는다. production에서는 v0 로딩을 거절한다. 안전성·감정 계약을 바꾸거나 임의의 점수 기준을 추가하지 않았다.

## 모델과 실행 방식

| 항목 | 현재 값 |
| --- | --- |
| 파일 | `config.json`, `model.safetensors`, `tokenizer.json`, `tokenizer_config.json` |
| 분류 순서 | 축하, 기쁨, 응원, 설렘, 피곤, 슬픔, 화남, 부적절 |
| 토큰화 | 원문 유지, `max_length=64`, truncation; 30글자 검사는 기존 API 책임 |
| 추론 | CPU 2스레드, eval, inference_mode, 8개 softmax 확률 검증 |
| 라이브러리 | Python 3.12, torch 2.14.1+cpu, transformers 5.21.0 |
| 가중치 SHA-256 | `0c45710941f29d873dfb5a521fa49b075e0d16d7db6de8bff999c645a2a77d81` |
| bundle 버전 | `student-emotion-v0-0c45710941f2` |
| 추론 정책 버전 | `student-review-only-v1` |

[모델 의존성](../backend/requirements-model.lock)은 기본 API 이미지와 분리한다. Dockerfile도 의존성 설치를 소스 복사보다 먼저 수행하므로, 코드 수정 때 CPU 라이브러리를 다시 설치하지 않는다. 모델은 worker에서 한 번 로드한다. 별도 AI HTTP 서버나 8000번 포트를 추가하지 않는다. loader가 각 파일의 SHA-256·경로·버전을 확인한 뒤 `create_analyzer(*, context)`를 호출하고 준비 추론을 실행한다. 누락·불일치 가중치를 무작위 초기화로 대신하는 로딩도 거절한다. 선택한 로컬 파일만 읽고 원격 모델 코드나 pickle 가중치를 사용하지 않는다. [Hugging Face 모델 로딩](https://huggingface.co/docs/transformers/main_classes/model)

## 팀 저장소에서 모델 받기

저장소에 접근할 수 있는 계정으로 [학생 모델 v0 사전 릴리스](https://github.com/eouleoul827/moodwall/releases/tag/student-model-v0-0c45710941f2)를 연다. Assets에서 `student-model-v0-0c45710941f2.zip`과 `SHA256SUMS.txt`를 받는다. ZIP에는 `student-v0/` 아래에 모델 4개 파일과 manifest만 들어 있다. 저장소 루트의 `models/`에 풀면 `models/student-v0/manifest.json`이 생긴다. 기존 bundle 폴더는 덮어쓰지 않는다.

다운로드 ZIP의 SHA-256을 `SHA256SUMS.txt`와 비교한 뒤 실행한다. [저장소의 manifest](models/student-v0-manifest.json)에는 내부 파일별 해시가 있으며 loader도 실제 파일을 다시 검증한다.

```powershell
Get-FileHash -Algorithm SHA256 ./student-model-v0-0c45710941f2.zip
```

ZIP에서 받은 bundle은 이미 준비되어 있으므로 아래 명령의 `tools.prepare_student_model` 단계는 생략하고 가상환경 설치 후 확인 도구를 실행하면 된다. 별도로 전달받은 모델 원본 폴더를 쓰는 경우에는 준비 단계도 실행한다.

## 로컬에서 모델 준비·확인

아래 PowerShell 명령은 저장소 루트에서 시작한다. `--source`에는 전달받은 모델 폴더를 지정한다. 기존 Python 환경을 바꾸지 않도록 가상환경을 사용한다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r backend/requirements-model.lock
Push-Location backend
..\.venv\Scripts\python.exe -m tools.prepare_student_model --source '전달받은 모델 폴더 경로' --destination ../models/student-v0
$env:HF_HUB_OFFLINE = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
..\.venv\Scripts\python.exe -m tools.check_student_model --manifest ../models/student-v0/manifest.json --output ../.local/student-model-check.json
Pop-Location
```

[준비 도구](../backend/tools/prepare_student_model.py)는 원본을 보존하며 4개 파일과 해시 manifest를 새 폴더에 복사한다. 같은 bundle은 재실행할 수 있고, 다른 내용의 기존 폴더는 덮어쓰지 않는다. 해시 검증 대상 폴더에는 추가 설정 파일·보고서·캐시를 넣지 않는다. `models/`와 `.local/`은 Git에서 제외된다. 다른 가중치를 전달받으면 별도 목적지에 준비하고 출력된 버전으로 설정을 맞춘다.

[확인 도구](../backend/tools/check_student_model.py)는 고정 합성 문장 9개와 같은 문장의 준비 후 추론 20회만 사용한다. 출력은 모델·런타임 버전, 파일 해시, 전체 분류 점수, 시간, 계약 검사 결과다. 학생 문장이나 `.env`를 읽지 않는다. 이 보고서는 정확도·안전성 평가 보고서가 아니다.

제공된 가중치로 실행한 [Windows 합성 예시·시간 측정 원본](checks/student-model-windows-2026-11-03.json)을 함께 보관한다. 이후 개인 실행 결과는 `.local/`에서 관리하며 자동으로 Git에 포함하지 않는다.

## DB·API·Linux 작업자와 함께 실행

먼저 [백엔드 실행 가이드](../backend/README.md)의 설정 생성·DB migration·운영자 생성 절차를 수행한다. 준비한 bundle이 있는 상태에서 저장소 루트에서 실행한다.

```powershell
docker compose -f compose.yaml -f compose.student-model.yaml up --build -d db api worker
docker compose -f compose.yaml -f compose.student-model.yaml logs --tail 50 worker
```

[선택적 Compose 설정](../compose.student-model.yaml)은 worker만 CPU 모델 이미지로 실행하고 bundle을 읽기 전용으로 마운트한다. API·worker의 모델·정책 버전을 함께 맞춰 readiness를 확인한다. 다른 가중치의 bundle을 쓴다면 준비 도구가 출력한 `model_bundle_version`을 `STUDENT_MODEL_BUNDLE_VERSION`에 지정한다. Linux 작업자 warmup 기본 제한은 180초다. 기본 실행으로 돌아갈 때는 두 서비스를 함께 다시 만든다.

```powershell
docker compose up --build -d --force-recreate api worker
```

## 검증 결과와 남은 일

제공된 가중치로 Windows CPU의 실제 로딩·9개 합성 예시·20회 측정을 완료했다. 이 PC의 첫 실행은 해시 검사·라이브러리 import·준비 추론 포함 약 62.4초였고, 같은 문장 준비 후 추론 중앙값 155.7ms, p95 234.5ms였다. 다른 서버의 성능이나 동시 부하 성능을 보장하는 값은 아니다.

7개 감정 예시가 각 감정으로, 저격 예시가 부적절로 분류됐다. `기분좋아`와 가족 이모지를 붙인 30 grapheme 예시는 부적절 점수가 높았다. 평가셋을 만들 때 정상 이모지·줄임말·부정 감정·저격 문장을 함께 검수해야 한다. 분류 점수를 실제 확률이나 승인 기준으로 해석하지 않는다.

기본 CI는 실제 가중치나 torch 없이 어댑터·manifest·원문 보존·실패 전파를 검사한다. 실제 가중치를 준비한 환경에서는 `MOODWALL_TEST_MODEL_MANIFEST`로 3개 실모델 검사와 3개 HTTP·DB·Linux 작업자 연결 검사를 추가 실행한다. 실행 환경은 development/test로 제한한다. Linux 작업자 검사는 Windows에서 건너뛴다.

2026-11-03 로컬 검증 결과는 다음과 같다. GitHub Actions에서 이 변경을 실행했다는 뜻은 아니다.

| 검증 | 결과 |
| --- | --- |
| Linux CPU 모델 이미지 + PostgreSQL 16 격리 DB, 백엔드 전체 | **348 passed**, 225.01초 |
| 위 전체 검사에 포함된 실제 작업자 | SHA 검증·준비 추론·READY → 실제 큐 소비 → NEEDS_REWRITE 저장 → 정상 종료·readiness 철회 |
| torch가 없는 기본 CI 이미지의 어댑터·bundle·계약 검사 | **99 passed**, 2.14초 |
| Windows CPU의 어댑터·bundle·계약·실모델 검사 | **99 passed, 3 skipped**, 69.16초; symlink 생성 권한이 없는 3개 검사만 생략 |
| 정적 검사·의존성·설정 | Ruff check/format 66개 파일, pip check, 선택적 Compose config 통과 |

Linux 검사는 외부 접속이 없는 전용 Docker 네트워크와 임시 DB를 사용했다. 모델과 최신 소스는 읽기 전용으로 마운트했다. 새 Dockerfile의 기본 `test` 이미지도 실제로 빌드하고, 모델 의존성이 없는 경로를 검증했다.

```powershell
Push-Location backend
..\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
$env:MOODWALL_TEST_MODEL_MANIFEST = (Resolve-Path ../models/student-v0/manifest.json).Path
# DB 연결 검사는 이름에 test가 들어간 격리 DB의 MOODWALL_TEST_DB_URL도 필요하다.
..\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_student_model.py tests/test_prepare_student_model.py tests/test_student_model_real.py tests/test_student_model_pipeline.py
Pop-Location
```

AI 담당에게는 학습·검증·최종 평가 분리, 부적절 누락/정상 글 오탐, 감정 혼동, 허용/차단/불확실 경계, 모델·데이터 출처와 재현 가능한 버전을 요청한다. 결과를 검토한 뒤 별도의 정책·bundle 버전으로 게시 허용을 구현한다. 이번 변경은 실제 추론과 백엔드 연결까지이며, React 화면 연결·공개 배포·학생 대상 운영 수락은 별도 작업이다.
