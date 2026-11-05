# Gemini 검수 연결과 로컬 실행

기준일: 2026-11-05. 실제 Gemini HTTPS transport·키 설정·토큰 사전 확인·사용량 정산에 서버 상태 전이, 학생·운영자 화면, 예산·호출 원장과 복구를 연결했다. 전광판 복구 변경을 포함한 DB head는 `0008_display_diagnostics`, OpenAPI는 `0.11.0`이다. 기본 legacy 프로필을 유지하며 실제 Gemini는 명시적으로 선택한다. 이 작업에서 실제 API 키·결제 계정·학생 원문을 사용하거나 운영 DB를 변경하지는 않았다.

## 키를 받으면 실행할 명령

이 저장소의 로컬 개발 환경에서는 Python, Node.js/npm, 실행 중인 Docker Desktop의 Linux 컨테이너 환경이 필요하다. 기존 `.env`나 DB 볼륨을 지우지 말고 저장소 루트에서 실행한다.

```powershell
python scripts/setup_gemini.py --start
```

키가 없으면 `Gemini API key (hidden):`에서 입력한다. 입력 문자는 화면에 나오지 않는다. 기존 `.env` 또는 현재 프로세스의 `MOODWALL_GEMINI_API_KEY`/`GEMINI_API_KEY`가 있으면 재사용한다. **키를 명령 인수·채팅·Git·`VITE_*` 변수에 넣지 않는다.** 키만 바꾸려면 보호된 프로세스 환경으로 새 키를 전달하거나 `.env`의 해당 항목을 로컬에서 수정한다.

명령은 기존 로컬 비밀과 주석을 보존하면서 `.env`에 Gemini 설정을 저장하고 프론트 의존성 설치·빌드, 백엔드 이미지 빌드, DB migration, 최초 전광판 초기화, API·worker 시작을 순서대로 수행한다. 새 `.env`가 필요하면 세션·QR·DB 비밀을 서로 다른 무작위 값으로 만든다. 기존 DB 비밀번호나 운영자 계정을 교체하지 않고 접수·송출 중단 상태도 자동으로 해제하지 않는다. worker 시작·복구 시 합성 문장으로 Gemini 연결을 확인하므로 API 사용료가 발생할 수 있다.

처음 설치해 운영자 계정이 없다면 다음 명령을 대신 사용한다. API 키와 별도로 관리자 비밀번호를 대화식으로 정한다. 이미 계정이 있으면 `--create-operator`를 생략한다.

```powershell
python scripts/setup_gemini.py --start --create-operator --operator-name admin
```

완료하면 <http://localhost:8000/operator>에 로그인해 전광판을 등록하고, 검수 상태가 준비되었는지 확인한 뒤 접수·송출을 재개한다. `/display`에서 참여 QR을 열어 학생 작성 화면까지 시험한다. 첫 등록과 페어링 절차는 [백엔드 가이드](../backend/README.md)와 [프론트 가이드](../frontend/README.md)를 따른다.

이 실행기는 `localhost` 로컬 환경용이다. `.env`나 현재 프로세스가 `production`이면 파일을 수정하기 전에 종료하며 개발 설정으로 몰래 바꾸지 않는다. 휴대폰·학교 노트북이 함께 접속할 공개 서버는 도메인·HTTPS·공개 origin과 별도 배포 설정이 필요하다. 키 연결로 외부 호스팅까지 생성되지는 않는다.

| 옵션 | 동작 |
| --- | --- |
| 옵션 없음 | 키와 Gemini 설정만 로컬 `.env`에 저장. 서비스를 시작하지 않음 |
| `--start` | 프론트·이미지 빌드, 로컬 DB migration, 전광판 초기화, 서비스 시작 |
| `--create-operator` | `--start`와 함께 사용. 비밀번호를 대화식으로 받아 최초 ADMIN 생성 |
| `--dry-run` | 바꿀 변수 이름과 `[REDACTED]`만 표시. 쓰기·시작·통신 없음 |
| `--env-file 경로` | 다른 로컬 dotenv 파일 선택. 기본값은 저장소 `.env` |

실행기는 UTF-8/BOM·줄바꿈·관련 없는 설정·주석을 보존하고 임시 파일에서 원자적으로 교체한다. 중복 변수·닫히지 않은 따옴표·불완전한 기존 DB 비밀은 임의 수정하지 않고 오류를 반환한다. POSIX 권한은 `0600`으로 설정한다. Windows에서는 이 권한값이 ACL을 관리하지 않으므로 `.env`가 있는 작업 폴더를 개인 계정의 비공개 폴더로 관리한다.

## 구현한 흐름

| 영역 | 실행하는 기능 |
| --- | --- |
| 검수 입력 | NFC 원문 유지, 확정 전화·이메일·명시 비밀번호 메모리 치환, 서버가 만든 욕설 플래그 |
| 응답 검증 | 4개 결정·CP 사유 관계, 중복 JSON key·BOM·해설·추가 필드·refusal·잘림·예상 밖 출력 거절 |
| 개발용 mock | 패키징한 합성 예문 77개만 재현. 그 외 문장은 UNCERTAIN. 인터넷·API key 사용 없음 |
| 실제 Gemini | `gemini-3.6-flash` 고정, 서버 키 헤더 인증, countTokens 사전 확인 뒤 Interactions 1회, HTTPS·무재시도·연결/전체 기한·응답 크기 제한 |
| 최종 판정 | SUPPORT 우선, 로컬 확정 차단 병합, 자동 벌점 없음, 안전성 허용 뒤 NEUTRAL 감정 fallback |
| 영수증 | SELF_CRISIS를 SUPPORT_AVAILABLE로 저장한 뒤 원문 제거. 24시간 소유자 결과·멱등 복구 |
| 학생 화면 | 지원 안내 우선·위기 글 미리보기 제거, 고정 도움 안내, 중립적 보류 문구, 구버전 업데이트 요구 |
| 신고 | 고정 category만 제출, 별도 신고 한도, 같은 키 재전송, 운영 큐·실제 조치 확인 후 처리 기록 |
| 호출 관리 | 제출 ID당 검수 작업 STARTED 1회, 두 HTTP 요청의 DB 공통 RPM/TPM/RPD·최대 비용 예약, 실제 usage 정산·UNKNOWN 보수 정산 |
| 장애·복구 | 수동 중단, 회로 차단, 간격을 둔 2회 합성 복구 점검, 늦은 응답·이전 릴리스 승인 금지 |
| 운영 화면 | 실제/모의 검수 상태와 중단·재개 확인, 신고 목록과 처리 기록 |

프로필 이름은 `legacy`, `gemini_mock`, `gemini`다. `gemini_mock`은 development/test에서만 가능하다. `gemini`는 API와 worker에 키가 필요하며 키가 없거나 설정·릴리스가 맞지 않으면 worker 준비와 새 접수를 거절한다. 운영 상태 확인과 키 수정 후 재개를 위한 API는 별도로 유지한다. 통신 장애·인증 실패·할당량 초과·사용량 누락·응답 형식 오류는 공개 승인으로 바뀌지 않는다. mock의 토큰·금액은 원장 동작을 시험하는 **합성 계측**이며 실제 공급자 청구나 비용 견적이 아니다.

`compose.gemini.yaml`은 API·worker에만 키를 전달하며 브라우저·프론트 빌드·DB·migration·관리 명령에는 전달하지 않는다. API는 키 유무와 릴리스 설정을 확인하고 실제 Gemini 호출은 worker가 실행한다. 실행기는 npm 프로세스에서도 알려진 API 키 환경변수를 제거한다. `migrate`·`cli`는 AI를 호출할 필요가 없으므로 별도의 legacy/fake 설정으로 DB·운영자 준비만 수행한다.

실제 검수는 원문 전체를 로그로 남기지 않고 강한 식별자만 메모리에서 치환한 뒤 서버 프롬프트·스키마와 함께 전달한다. countTokens 후 한 번만 생성을 요청하며 자동 HTTP 재시도·외부 검색·도구·대화 이력·학생용 생성 답변을 사용하지 않는다. `store=false`로 Interaction 저장을 끄지만 공급자 전체 보관 정책까지 0일로 보장하는 설정은 아니다.

내부 호출 한도는 두 HTTP 요청을 모두 보수적으로 계산한다. 기본 RPM 6은 분당 검수 작업 약 3개이며 합성 연결·복구 점검도 한도에 포함한다. 기본 입력 상한 16,384토큰·출력 512토큰·입력 TPM 200,000·일 1,000요청·KST 하루 3달러 상한은 개발 시작값이다. Google 할당량·청구서·모델 품질 보장값이 아니다. 사전 계산은 스키마 등의 오차 여유를 둔 보수적 추정이며 공급자 비용의 수학적 보증이 아니다. 실제 사용량이 예약을 넘으면 추가 접수를 중단한다. [운영 기준](gemini-moderation-operations.md)을 따른다.

mock은 77개 정답만 재현하며 예문 밖의 일반 문장은 분류하지 않는다. 실제 Gemini의 한국어 판정 정확도는 이 재현 검사로 검증되지 않는다. 실제 운영에는 [독립 평가 계획](gemini-moderation-evaluation.md)을 적용해야 한다.

## 감정과 구름 색상 연결

검수와 감정 표현은 별도 결과다. 승인된 글의 `emotion`을 서버가 `sceneId`로 바꾸고, 전광판은 [감정 색상표](../frontend/src/display/themes.js)에서 색·배경색·효과를 선택한다. 예를 들어 `JOY → joy → 분홍`, `SADNESS → sadness → 파랑`, `TIRED → tired → 회색`이다. 구름 요소도 이 색상표의 `color` 또는 `tint`를 사용하도록 연결할 수 있다. 모델이 임의 CSS나 색상 코드를 보내는 방식은 사용하지 않는다.

현재 Gemini 프로필은 검수만 수행하며 감정은 `NEUTRAL` fallback이다. [분석기](../backend/inference/provider_moderation.py)의 `emotion_predictor` 연결 지점에 별도 감정 분석기를 붙일 수 있지만, 기본 로더에는 아직 실제 감정 분석기를 연결하지 않았다. 감정 분석 실패는 중립 색상으로 처리하고 검수의 공개 허용 여부를 바꾸지 않는다. 구름 도형 전체가 이미 감정별로 채색됐거나 실제 기분을 자동 분석하고 있다고 표시하지 않는다.

## API 키 없이 모의 실행

[기존 백엔드 초기 준비](../backend/README.md)와 [프론트 빌드](../frontend/README.md)를 먼저 따른다. 기존 DB는 새 migration을 적용해야 한다. 아래는 저장소 루트의 개발 환경 명령이다.

```powershell
npm.cmd --prefix frontend run build
docker compose -f compose.yaml -f compose.frontend.yaml -f compose.gemini-mock.yaml build api worker migrate
docker compose -f compose.yaml -f compose.frontend.yaml -f compose.gemini-mock.yaml run --rm migrate
docker compose -f compose.yaml -f compose.frontend.yaml -f compose.gemini-mock.yaml up -d api worker
```

override는 API와 worker의 `MOODWALL_MODERATION_PROFILE=gemini_mock`, `MOODWALL_ANALYZER=gemini_mock`을 함께 지정한다. 개발·테스트 환경에서만 사용하며 production으로 설정하면 시작을 거절한다. API key는 필요 없다. 이 명령은 기존 DB를 삭제하거나 최초 운영자·전광판 등록을 대신하지 않는다. 접수·송출의 기존 중단 상태도 자동으로 풀지 않는다.

브라우저에서 `/operator`로 로그인해 전광판 연결·접수/송출을 준비하고 `/display`의 참여 QR로 학생 화면을 연다. 신규 학생 화면은 `support-v1` 기능 헤더를 자동으로 보낸다. 모의 판정의 예시는 다음과 같다.

| 작성 예문 | 기대 동작 |
| --- | --- |
| `오늘 너무 우울해` | ALLOW, 중립 배경으로 기존 재생 경로에 진입 |
| `2반 OO 못생겼다` | 비공개 수정 안내, 벌점 없음 |
| `오늘 걔 안 와서 좋다` | 비공개 문맥 보류 안내 |
| `씨발 죽고 싶다` | 비공개 지원 안내, 욕설 벌점 없음, 원문 미리보기 제거 |
| fixture에 없는 문장 | UNCERTAIN, 자동 허용 없음 |

해당 예문은 [합성 fixture](checks/gemini-moderation-cases.json)에서 가져왔다. 학생 입력이나 API body에 fake 결과를 지정하는 옵션을 추가하지 않았다. 모델 결과·플래그·정책·버전은 학생이 바꿀 수 없다.

## 로컬 API 추가 계약

| 경로 | 역할과 의미 |
| --- | --- |
| POST `/api/v2/reports` | 학생, CSRF·UUID 멱등 키. 새 신고 202, 같은 요청 200. category와 선택적 공개 글 ID만 수신 |
| GET `/api/operator/reports` | 운영자, 서명 cursor 페이지. 원문·신고자 ID를 반환하지 않음 |
| POST `/api/operator/reports/{reportId}/resolve` | 운영자, CSRF·명령 키. 실제 숨김/중단이 없으면 REPORT_ACTION_REQUIRED |
| GET `/api/operator/moderation/provider` | 운영자, 현재 검수 프로필·회로·원장 상태 |
| POST `/api/operator/moderation/provider` | ADMIN, CSRF, paused boolean. 변경을 감사에 기록하며 재개는 복구 점검부터 시작 |

새 영수증의 필요 클라이언트 기능은 접수 시 메타데이터로 저장한다. 원문 삭제나 legacy로의 설정 전환 후에도 지원 사유를 일반 수정 안내로 바꾸지 않는다. 소유권 확인 뒤 기능 헤더가 없으면 409 CLIENT_UPDATE_REQUIRED다. 기존 v1 결과는 유지하고 provider 프로필의 v1 새 접수는 업데이트를 요구한다.

신고는 자동 제재·자동 숨김을 만들지 않는다. 신고 원문·이름·스크린샷을 받지 않으며 메타데이터는 7일 후 정리한다. 도움 안내는 앱 안의 고정 안내다. 상담원 연결·자동 교사 연락·응답 보장을 제공하지 않는다.

## 검사 방법과 남은 범위

[키 연결 준비 검증 기록](checks/gemini-key-ready-2026-11-05.json)에 이번 HTTP 구현·전체 회귀·실행기·화면·계약 검사 결과를 남긴다. [이전 오프라인 구현 기록](checks/gemini-offline-implementation-2026-11-04.json)은 당시 mock 구현의 이력이다. 전체와 집중 테스트는 서로 겹치므로 합산하지 않는다.

백엔드에서 격리된 PostgreSQL 테스트 DB를 준비한 뒤 `python -m pytest -q`로 실행한다. source와 같은 migration을 적용하고 `MOODWALL_TEST_DB_URL`에는 이름에 test가 포함된 전용 DB만 넣는다. Linux supervisor·강제 종료 시험까지 포함하려면 Docker/Linux 환경을 사용한다. 실제 학생 모델 파일이 필요한 선택 시험은 해당 artifact를 지정하지 않으면 생략된다.

```powershell
python -B -m unittest discover -s scripts -p test_setup_gemini.py -v
node --test 'clients/**/*.test.mjs'
npm.cmd --prefix frontend test -- --maxWorkers=1
npm.cmd --prefix frontend run build
node docs/checks/verify-gemini-design.mjs
```

HTTPS transport·인증 헤더·countTokens·공급자 usage 파싱·공식 단가 구간 적용·연결 실행기는 구현했다. 가짜 HTTP 응답과 격리된 합성 `.env`로 오류·한도·비밀 보존 경계를 시험한다. 실제 키를 사용한 인증 성공·네트워크 지연·Google 계정별 할당량·청구서 일치·실모델 3,200건 평가·현장 장치 수락·운영 배포는 수행하지 않았다. 키를 받으면 같은 코드로 합성 문장부터 실제 연결·비용·정책 품질을 검증한다. 학교 내부 별명·보이지 않는 관계·분할 공격이 완전히 해결됐다는 보장도 하지 않는다.
