# 백엔드 실행과 팀 연결 가이드

이 프로젝트의 일반 백엔드는 FastAPI·PostgreSQL·Procrastinate와 독립 Python 검수 작업자다. 전광판 노트북은 최종적으로 브라우저를 실행하고, API·DB·AI는 서버에서 실행한다. **현재 구현은 백엔드 API이며 학생 작성·전광판 React 화면은 아직 없다.** 기본 모델과 금칙 사전은 개발 연결용이다. 실제 검증 결과와 출시 전 남은 작업은 [구현 상태](../docs/implementation-status.md)에서 확인한다.

## 1. 먼저 준비할 것

저장소를 받은 PC에 Python 3.12 이상과 Docker Compose를 실행할 Docker 환경을 준비한다. Windows에서는 Docker Desktop의 Linux 컨테이너 환경을 사용한다. API·작업자는 Linux 이미지에서 실행하므로 전광판 노트북에 Python 모델·DB를 설치하는 단계가 아니다.

아래 명령은 **저장소 최상위 폴더**에서 실행한다. 현재 폴더 안에 `compose.yaml`, `scripts/`, `backend/`가 보여야 한다. 명령을 한 줄씩 실행하고 오류가 나면 해당 단계의 원인을 먼저 확인한다.

```text
moodwall/
  compose.yaml                 로컬 DB·API·작업자·관리 명령 실행
  scripts/dev_setup.py          기존 값을 덮지 않는 로컬 비밀 생성
  backend/
    app/auth.py                세션·QR·로그인·장치 등록
    app/submissions.py         접수·최근 60분 한도·멱등성
    app/profanity.py            규칙 필터
    app/moderation.py           claim·최종 판정·기한·점유 반환
    app/worker.py               모델 작업자와 실행 감시
    app/display.py              전광판 말풍선 페이지·장치 상태
    app/operator.py             숨김·중단·제재 복구·감사
    app/cli.py                  최초 전광판·운영자 준비
    inference/                 AI 담당과 공유할 입출력·어댑터
    app/resources/lexicon.json  개발용 시작 사전
    app/migrations/            버전이 있는 DB 변경
    tests/                     정책·API·실제 DB·작업자 시험
  docs/openapi.json            프론트와 합의한 HTTP 계약
```

## 2. 로컬에서 처음 실행하기

```powershell
python scripts/dev_setup.py
docker compose build
docker compose up -d db
docker compose run --rm migrate
docker compose run --rm cli python -m app.cli init-board
docker compose run --rm cli python -m app.cli create-operator --username admin
docker compose up -d api worker
```

`dev_setup.py`는 Git에서 제외한 `.env`에 무작위 로컬 비밀을 만든다. 이미 파일이 있으면 값을 덮어쓰지 않고 종료하므로 최초 준비가 끝난 PC에서는 이 단계를 반복하지 않는다. `.env.example`의 자리표시자를 실제 비밀번호로 그대로 사용하는 것과 구분한다. 기존 DB 볼륨의 비밀번호는 `.env`만 새로 만든다고 바뀌지 않는다.

`migrate`는 앱 Alembic과 큐 schema를 명시적으로 적용한다. API 시작 때 자동으로 DB를 변경하지 않는다. `init-board`는 설정된 BOARD_ID를 만들고 **접수·송출을 모두 중단한 상태**로 시작한다. 같은 설정으로 재실행하면 기존 mode·자격·중단 상태를 보존한다.

`create-operator`는 비밀번호를 두 번 묻는다. 셸 명령에 비밀번호를 쓰지 말고 대화식으로 입력한다. 처음 계정의 기본 역할은 ADMIN이며 비밀번호 생성 규칙은 15~128자다. `admin`은 위 예시의 계정 이름이고 기본 비밀번호는 없다. Docker가 대화식 입력을 지원하는 터미널에서 실행한다. `cli` 서비스의 기본 command는 도움말이므로 위처럼 **`python -m app.cli`부터 전체 명령을 지정**한다.

실행 뒤 같은 PC에서 다음 주소를 연다.

- API 설명 화면: <http://localhost:8000/docs>
- API 생존 상태: <http://localhost:8000/api/health/live>
- API 준비 상태: <http://localhost:8000/api/health/ready>

ready 성공은 DB·schema·API 제어 루프가 준비됐다는 뜻이며, 모델·실제 전광판 패널·학생 공개 품질의 완료를 뜻하지 않는다. 처음 작업자는 더미와 개발용 사전을 로드한다. 아직 승인된 글이 없거나 장치 heartbeat가 없으면 화면 상태에 카드가 없는 것이 정상이다.

## 3. 운영자·전광판·학생 API를 연결하는 순서

현재는 별도 운영자 웹 화면도 없으므로 API 도구로 기능을 확인한다. 요청 필드·응답은 [HTTP 계약](../docs/openapi.json)을 따른다. API 설명 화면의 표시는 실제 계약과 함께 확인하고, 쿠키를 지원하는 도구를 사용한다. 같은 PC에서는 역할별 별도 브라우저 프로필로 구분해 시험할 수 있다.

| 순서 | 호출 | 본문·필요한 값 |
| --- | --- | --- |
| 1 | `POST /api/operator/login` | `{"username":"admin","password":"입력한 비밀번호"}`. 운영자 쿠키와 응답 `csrfToken`을 받음 |
| 2 | `POST /api/operator/display/pairing-codes` | `{"reasonCode":"MAINTENANCE"}`. 운영자 쿠키·`X-CSRF-Token`, 5분짜리 일회용 code |
| 3 | `POST /api/display/pair` | 전광판 역할의 브라우저에서 `{"code":"받은 코드"}`. 그 브라우저에 장치 쿠키·CSRF 발급 |
| 4 | `POST /api/operator/board/mode` | 운영자가 `{"mode":"MOODWALL","reasonCode":"MAINTENANCE"}`로 송출 재개 |
| 5 | `POST /api/operator/board/intake` | 운영자가 `{"paused":false,"reasonCode":"MAINTENANCE"}`로 접수 재개 |
| 6 | `GET /api/display/qr` | 장치 쿠키로 현재 QR의 `joinUrl`·만료 확인 |
| 7 | `POST /api/admissions/exchange` | 학생 역할에서 `{"ticket":"joinUrl fragment의 ticket"}`. 익명 쿠키·CSRF 발급 |
| 8 | `POST /api/submissions` | 학생 쿠키·CSRF·새 UUID `Idempotency-Key`, `{"content":"오늘도 힘내","emojiId":"sparkles"}` |
| 9 | `GET /api/submissions/{id}` | 학생 본인의 검수 결과 조회. 최초 202는 게시 완료가 아님 |
| 10 | `POST /api/display/heartbeat` | 장치 쿠키·CSRF, `{"clientInstanceId":"브라우저 인스턴스 UUID"}`. 최종 프론트는 visible일 때 10초 간격 |
| 11 | `GET /api/display/state` | 승인된 일반 말풍선을 100개씩 페이지로 받음. nextCursor 끝까지 조회. SSE는 최신 목록을 다시 읽으라는 알림 |

모든 POST는 JSON과 `Origin: http://localhost:8000`이 필요하다. 브라우저는 Origin을 자동으로 보낸다. 첫 로그인·페어링·QR 교환을 제외한 해당 역할의 POST는 그 세션의 `X-CSRF-Token`도 보낸다. 운영자 token을 장치·학생 요청에 재사용하지 않는다. 쿠키는 브라우저가 관리하고 토큰·비밀번호를 Git이나 일반 로그에 붙여 넣지 않는다.

페어링·새로고침·heartbeat는 초기 중단을 해제하지 않는다. 접수·송출 재개는 각각 ADMIN이 해야 한다. heartbeat는 온라인 lease를 확인하며 별도 강조 시간은 없다. API만 실행했다고 자동 heartbeat·QR 이미지·애니메이션 화면이 생기지는 않는다. 위 파이프라인은 프론트 담당이 React에 연결할 계약이다. 실제 챗봇 화면은 이 연결에 필요 없다.

승인된 글은 즉시 일반 말풍선으로 공개한다. 4개 자리·강조·표시 대기열은 없다. 100개는 응답 페이지 크기이며 매 동기화마다 첫 페이지부터 nextCursor가 null일 때까지 수집한다. 서버 DB 원문은 접수부터 1시간 보관하고 중단·오프라인으로 연장하지 않는다. `expiresAt`부터 본인·운영자·전광판 API의 원문 공급에서 제외하고 약 5초 주기 작은 배치로 제출 행을 삭제한다. 전광판은 이미 받은 공개 카드를 당일 캐시에 저장하고 `expiresAt`부터 같은 글의 투명 말풍선으로 바꿔 `ambientUntil`인 다음 KST 자정까지 유지한다. 서버 목록의 ID 부재는 캐시 삭제 신호가 아니다. 자정·명시적 숨김·기기 해지에는 캐시를 정리한다. `deadlineAt`은 검수 120초 기한이다. DB 삭제 뒤 새로고침 복구는 노트북 당일 저장소에 의존하며 저장소 삭제·기기 교체 뒤에는 서버에서 복구할 수 없다. [캐시 모듈](../clients/display/bubble-cache.mjs)은 React 화면에 연결해야 한다. 멱등 응답 보장도 접수 후 1시간까지다. 기존 DB는 `docker compose run --rm migrate`로 `0005_floating_bubbles`까지 적용한다. [전체 흐름](../docs/service-flow.md)을 참고한다.

## 4. 상태 확인과 다시 실행

```powershell
docker compose ps
docker compose logs --tail 100 api worker
docker compose stop api worker
docker compose up -d api worker
```

코드를 수정했으면 `docker compose build` 후 API·worker를 다시 실행한다. 운영 배포에서는 먼저 신규 접수를 중단하고 기존 비최종 작업을 drain하거나 안전하게 종료해야 한다. 자세한 순서는 [운영 기준](../docs/delivery-and-operations.md)을 따른다. 일반 개발 종료는 `docker compose stop`으로 할 수 있고 기존 DB 볼륨을 유지한다. 데이터 초기화를 일상적인 재시작 단계에 섞지 않는다.

| 상황 | 먼저 확인할 것 |
| --- | --- |
| `.env already exists` | 이전 준비가 남은 것. 파일·DB 비밀번호를 무작정 교체하지 않음 |
| migrate 실패 | DB health·설정·앱/큐 migration·DB 역할 권한 |
| operator already exists | 생성 재실행으로 덮어쓰지 않음. 필요하면 `reset-password --username admin`을 명시적으로 실행 |
| 503 INTAKE_PAUSED | ADMIN의 접수 재개 여부 |
| 503 MODEL_NOT_READY | worker 로그·artifact·사전·모델 readiness |
| 403 CSRF_REJECTED | 해당 역할의 `/session`으로 새 CSRF 확인. 유효 학생 쿠키를 삭제해 우회하지 않음 |
| 403 ADMISSION_EXPIRED | 초안·요청 키를 보존하고 최신 QR 재스캔 |
| state에 카드가 없음 | 승인 상태·송출 중단·장치 heartbeat·당일 날짜·queue 순서 |

로컬 unit 검사 예시는 다음과 같다. 이 명령은 실제 PostgreSQL·Linux worker 통합 시험을 대신하지 않는다.

```powershell
docker build --target test -t moodwall-backend-tests backend
docker run --rm moodwall-backend-tests python -m pytest -q -m "not integration"
```

통합 검사는 실제 격리 DB와 테스트 환경변수를 사용한다. 운영 DB에 테스트 fixture·부하를 넣지 않는다. 실행 결과는 [구현 상태](../docs/implementation-status.md)에 실제 범위로 기록한다.

## 5. AI 담당이 연결할 부분

기본 `MOODWALL_ANALYZER=fake`는 학습 모델이 아니다. 정해진 예문으로 파이프라인·오류·시간 초과를 시험하는 어댑터다. 개발용 `resources/lexicon.json`도 `starter_only=true`인 작은 시작 사전이며 모든 한국어 욕설을 검수하는 사전으로 소개하지 않는다. production 설정은 fake를 거절하고 production 필터는 시작 사전을 거절한다.

AI 담당은 [AI 요청서](../docs/ai-team-request.md)와 [실제 입출력 모델](inference/contracts.py)에 맞는 동기식 `analyze(AnalysisInput) -> AnalysisResult` 객체를 제공한다. 안전성·감정은 구분하며 모델 결과로 학생 제재를 직접 실행하지 않는다. 일반 백엔드는 큐·timeout·게시·제재·저장·배포를 담당한다.

실제 모델 연결 설정의 형태는 다음과 같다. 아래 이름·경로는 예시이고 모델 코드·가중치는 아직 제공된 것이 아니다.

```dotenv
MOODWALL_ANALYZER=inference.student_model:create_analyzer
MOODWALL_MODEL_MANIFEST_PATH=/srv/models/student-v1/manifest.json
MOODWALL_MODEL_BUNDLE_VERSION=student-v1
MOODWALL_INFERENCE_POLICY_VERSION=inference-v1
MOODWALL_LEXICON_PATH=/srv/policies/reviewed-lexicon.json
MOODWALL_LEXICON_VERSION=school-lexicon-v1
```

일반 백엔드가 검토한 로컬 모듈의 factory만 설정하며, API 입력으로 모듈·경로·다운로드 URL을 받지 않는다. 모듈은 import할 때 모델을 로드하지 않고 factory에서 로드한다. manifest는 `model_bundle_version`, `inference_policy_version`, `artifacts`에 상대 경로→SHA-256을 담는다. loader는 경로 경계·해시·버전을 검사한 뒤 factory를 호출하고 warmup 결과를 검증한다. 모델과 사전 파일은 worker 이미지 또는 읽기 전용 로컬 마운트에 준비한다. 설정 경로만 바꿔 실제 파일이 생기지는 않는다.

AI 담당은 코드뿐 아니라 의존성·고정 모델 artifact·평가 보고서·CPU 처리 시간과 메모리를 전달한다. 사전은 일반 백엔드가 검토·확장하고 버전·양성/음성 fixture·출처를 함께 관리한다. `starter_only` 표시만 수정하는 것으로 품질 검증을 대체하지 않는다. 실제 학생 공개 전에는 모델 평가·검수 누락·정상 문장 오차단·장애 복구를 확인한다.

## 6. 노트북·휴대폰·클라우드 연결

현재 Compose는 API `127.0.0.1:8000`, 개발 DB `127.0.0.1:55432`만 호스트에 노출한다. 다른 PC·휴대폰에서 스캔한 `localhost`는 그 기기 자신이므로 이 로컬 주소로 LTE 참여를 제공하지 않는다.

실제 학교 운영은 공개 도메인·DNS·HTTPS 프록시·서버와 관리형 DB를 준비하고, `PUBLIC_ORIGIN`·키·production 모델·사전·쿠키 정책을 맞춘 별도 배포가 필요하다. 노트북은 그 공개 전광판 주소를 열어 페어링한다. 노트북의 영상 케이블과 인터넷 연결은 각각 점검한다. 이 저장소의 로컬 실행으로 클라우드 배포·도메인 구매·TLS 설정이 실행된 것은 아니다. [노트북 현장 운영](../docs/display-laptop.md)
