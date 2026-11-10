# Gemini 검수 연결과 로컬 실행

기준일: 2026-11-05 · 실제 연결·게시 정책 갱신일: 2026-11-09. 실제 Gemini HTTPS transport·키 설정·토큰 사전 확인·사용량 정산에 서버 상태 전이, 학생·운영자 화면, 예산·호출 원장과 복구를 연결했다. 전광판 복구 변경을 포함한 DB head는 `0008_display_diagnostics`, OpenAPI는 `0.11.0`이다. 기본 legacy 프로필을 유지하며 실제 Gemini는 명시적으로 선택한다. 실제 키와 합성 문장으로 검수·승인 후 로컬 감정 연결을 부분 확인했다. 실제 학생 글·기존 운영 DB는 사용하지 않았으며 전체 앱 연결과 현장 수락은 완료하지 않았다. [실제 연결 기록](checks/gemini-live-connection-2026-11-09.json)의 범위를 따른다.

## 학교 담당자에게 요청할 API 준비

성인 대상 대학 서비스에서, 서버가 학생 글의 게시 적합성만 검수하고 Gemini 생성 답변을 학생에게 직접 제공하지 않는 구성을 전제로 한다. 교수님이나 학교의 프로젝트 관리 담당자에게 다음을 요청한다. 개인 Google 계정 비밀번호를 공유받는 방식은 사용하지 않는다.

1. **학교 관리 프로젝트와 담당 권한:** 무드월 전용 Google Cloud 프로젝트, 결제·키 관리 책임자와 담당 개발 계정의 필요한 협업 권한을 정한다. Google AI Studio에서 해당 프로젝트에 새 전용 API 키를 발급하고 현재 키 형식과 Gemini API 사용 권한을 확인한다. [공식 키 관리 안내](https://ai.google.dev/gemini-api/docs/api-key)
2. **테스트 예산과 실제 한도:** 월 예산·초기 합성 문장 테스트 사용을 승인하고, AI Studio에서 `gemini-3.6-flash`의 프로젝트별 RPM·TPM·RPD와 사용 가능 여부를 확인한다. 이번 개발 계정의 합성 호출 성공이 다른 계정의 모델 접근이나 실제 할당량을 보장하지 않는다. 키를 여러 개 만들어도 프로젝트 한도가 따로 생기지 않는다. [호출 한도 안내](https://ai.google.dev/gemini-api/docs/rate-limits)
3. **본문 외부 처리와 유료 API 조건:** 검수 요청에는 학생이 본문에 쓴 실명이 포함될 수 있다. 무료 서비스에는 개인정보를 제출하지 말라는 조건이 있으므로 유료 API 데이터 처리 조건과 학교의 외부 처리 방침을 확인한다. 감정 분류는 로컬 모델을 사용하며 Gemini에는 검수용 본문과 서버 검사 플래그만 전송한다. [Gemini API 약관](https://ai.google.dev/gemini-api/terms)
4. **비밀 키 전달과 보관:** 키는 승인된 비밀 전달 경로로 개발 담당자에게 전달하거나 담당자가 서버에 직접 입력한다. 채팅·Git·프론트 코드·`VITE_*` 변수에 넣지 않는다. 개발자는 아래 실행기의 숨김 입력이나 서버 비밀 설정으로 연결한다.
5. **비용 알림과 중지 담당:** 사용량·과금 알림을 받을 담당자와 비상 시 키 해지·접수 중지 담당자를 정한다. 알림용 Cloud Billing 예산은 자동 과금 차단을 보장하지 않으므로 서버의 별도 예산·쿼터 제한도 유지한다. [예산과 알림 안내](https://docs.cloud.google.com/billing/docs/how-to/budgets)

처음에는 실제 학생 글 대신 합성 문장으로 인증·모델·응답 스키마를 확인한다. 이 준비는 API 연결을 위한 것이며 학교 전광판의 운영 수락이나 휴대폰 접속용 호스팅을 대신하지 않는다.

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
| `--emotion-model 경로` | 준비된 로컬 감정 bundle 선택·경로 보존. Gemini ALLOW 뒤 색 선택에만 사용 |

실행기는 UTF-8/BOM·줄바꿈·관련 없는 설정·주석을 보존하고 임시 파일에서 원자적으로 교체한다. 중복 변수·닫히지 않은 따옴표·불완전한 기존 DB 비밀은 임의 수정하지 않고 오류를 반환한다. POSIX 권한은 `0600`으로 설정한다. Windows에서는 이 권한값이 ACL을 관리하지 않으므로 `.env`가 있는 작업 폴더를 개인 계정의 비공개 폴더로 관리한다.

## 구현한 흐름

| 영역 | 실행하는 기능 |
| --- | --- |
| 검수 입력 | NFC 원문 유지, 확정 전화·이메일·명시 비밀번호 메모리 치환, 서버가 만든 욕설 플래그 |
| 응답 검증 | 신규 3개 결정·CP 사유 관계, 중복 JSON key·BOM·해설·추가 필드·refusal·잘림·예상 밖 출력 거절. 과거 SUPPORT 디코더는 호환용 |
| 개발용 mock | 패키징한 합성 예문만 재현. 그 외 문장은 UNCERTAIN. 인터넷·API key 사용 없음 |
| 실제 Gemini | `gemini-3.6-flash` 고정, 서버 키 헤더 인증, countTokens 사전 확인 뒤 Interactions 1회, HTTPS·무재시도·연결/전체 기한·응답 크기 제한 |
| 최종 판정 | 자기 감정은 별도 위반 없으면 ALLOW, 로컬 확정 차단 병합, 자동 벌점 없음, 안전성 허용 뒤 감정 분석 또는 NEUTRAL fallback |
| 영수증 | 과거 SELF_CRISIS·SUPPORT_AVAILABLE 호환과 원문 제거 유지. 24시간 소유자 결과·멱등 복구 |
| 학생 화면 | 일반 감정 완료 흐름, 과거 비공개 결과는 원문 없이 중립적 미표시 문구. 도움 안내·도움 버튼 제거, 구버전 업데이트 요구 유지 |
| 신고 | 고정 category만 제출, 별도 신고 한도, 같은 키 재전송, 운영 큐·실제 조치 확인 후 처리 기록 |
| 호출 관리 | 제출 ID당 검수 작업 STARTED 1회, 두 HTTP 요청의 DB 공통 RPM/TPM/RPD·최대 비용 예약, 실제 usage 정산·UNKNOWN 보수 정산 |
| 장애·복구 | 수동 중단, 회로 차단, 간격을 둔 2회 합성 복구 점검, 늦은 응답·이전 릴리스 승인 금지 |
| 운영 화면 | 실제/모의 검수 상태와 중단·재개 확인, 신고 목록과 처리 기록 |

프로필 이름은 `legacy`, `gemini_mock`, `gemini`다. `gemini_mock`은 development/test에서만 가능하다. `gemini`는 API와 worker에 키가 필요하며 키가 없거나 설정·릴리스가 맞지 않으면 worker 준비와 새 접수를 거절한다. 운영 상태 확인과 키 수정 후 재개를 위한 API는 별도로 유지한다. 통신 장애·인증 실패·할당량 초과·사용량 누락·응답 형식 오류는 공개 승인으로 바뀌지 않는다. mock의 토큰·금액은 원장 동작을 시험하는 **합성 계측**이며 실제 공급자 청구나 비용 견적이 아니다.

`compose.gemini.yaml`은 API·worker에만 키를 전달하며 브라우저·프론트 빌드·DB·migration·관리 명령에는 전달하지 않는다. API는 키 유무와 릴리스 설정을 확인하고 실제 Gemini 호출은 worker가 실행한다. 실행기는 npm 프로세스에서도 알려진 API 키 환경변수를 제거한다. `migrate`·`cli`는 AI를 호출할 필요가 없으므로 별도의 legacy/fake 설정으로 DB·운영자 준비만 수행한다.

실제 검수는 원문 전체를 로그로 남기지 않고 강한 식별자만 메모리에서 치환한 뒤 서버 프롬프트·스키마와 함께 전달한다. countTokens 후 한 번만 생성을 요청하며 자동 HTTP 재시도·외부 검색·도구·대화 이력·학생용 생성 답변을 사용하지 않는다. `store=false`로 Interaction 저장을 끄지만 공급자 전체 보관 정책까지 0일로 보장하는 설정은 아니다.

내부 호출 한도는 두 HTTP 요청을 모두 보수적으로 계산한다. 기본 RPM 6은 분당 검수 작업 약 3개이며 합성 연결·복구 점검도 한도에 포함한다. 기본 입력 상한 16,384토큰·출력 512토큰·입력 TPM 200,000·일 1,000요청·KST 하루 3달러 상한은 개발 시작값이다. Google 할당량·청구서·모델 품질 보장값이 아니다. 사전 계산은 스키마 등의 오차 여유를 둔 보수적 추정이며 공급자 비용의 수학적 보증이 아니다. 실제 사용량이 예약을 넘으면 추가 접수를 중단한다. [운영 기준](gemini-moderation-operations.md)을 따른다.

mock은 패키징한 합성 예문의 정답만 재현하며 예문 밖의 일반 문장은 분류하지 않는다. 실제 Gemini의 한국어 판정 정확도는 이 재현 검사로 검증되지 않는다. 실제 운영에는 [독립 평가 계획](gemini-moderation-evaluation.md)을 적용해야 한다.

## 감정과 구름 색상 연결

검수와 감정 표현은 별도 결과다. 승인된 글의 `emotion`을 서버가 `sceneId`로 바꾸고, 전광판은 [감정 색상표](../frontend/src/display/themes.js)에서 구름 색·배경색·효과를 선택한다. 예를 들어 `JOY → joy → 분홍`, `SADNESS → sadness → 파랑`, `TIRED → tired → 회색`이다. [새 디자인](design-integration.md)의 구름 16종에 낮/밤 색과 중립색을 적용했다. 모델이 임의 CSS나 색상 코드를 보내는 방식은 사용하지 않는다.

Gemini는 검수만 수행한다. `--emotion-model models/student-v0`로 기존 bundle을 선택하면 [감정 전용 연결](gemini-emotion-integration.md)이 ALLOW 뒤에만 실행된다. 선택하지 않으면 `NEUTRAL`이다. 일반적인 감정 추론 오류·애매한 결과·로컬 ‘부적절’ 분류는 중립색으로 처리하고 Gemini 판정을 바꾸지 않는다. 감정 실행이 멈추면 전체 12초 wrapper/15초 추론 경계에서 기술 실패로 비공개 종료될 수 있다. 감정 모델 정확도는 별도 평가한다.

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
| `OO야 고마워` | ALLOW, 이름의 단순 언급으로 차단하지 않음 |
| `민수 오늘 발표 너무 빨랐어` | ALLOW, 구체적 행동에 대한 가벼운 비판 |
| `오늘 걔 안 와서 좋다` | ALLOW, 숨은 위해 맥락을 추정하지 않음 |
| `그 애랑은 알아서 하자` | ALLOW, 설명 생략만으로 보류하지 않음 |
| `그 별명으로 다들 처리하자` | 비공개 문맥 보류 안내 |
| `씨발 죽고 싶다` | BLOCK/CP06, 실제 강한 욕설로 비공개 수정 안내. 자동 벌점 없음 |
| `살기 싫다`, `죽고 싶다` | ALLOW, 별도 도움 안내 없이 감정 분석과 일반 표시 흐름 |
| `너머 피곧해 살려줘` | ALLOW, 오타가 있는 일상 피로 표현 |
| fixture에 없는 문장 | UNCERTAIN, 자동 허용 없음 |

해당 예문은 [합성 fixture](checks/gemini-moderation-cases.json)에서 가져왔다. 학생 입력이나 API body에 fake 결과를 지정하는 옵션을 추가하지 않았다. 모델 결과·플래그·정책·버전은 학생이 바꿀 수 없다.

2026-11-10 게시 정책은 실명·별명의 단순 언급, 감사·응원·일상 의견, 강요 없는 자신의 비노골적 호감 표현과 구체적인 실행 계획 없는 자기 감정·두려움·도움 요청을 허용한다. 개인 학번·개별 반번호·연락처·타인의 연애 소문·민감 사생활, 명백한 개인 모욕·외모 비하·강한 욕설·구체적인 위해 방법·실행 계획·타인 자해 권유는 계속 수정 대상이다. 새 요청은 SUPPORT·SELF_CRISIS·CP08을 선택하지 않으며 과거 결과만 호환한다. 정책·프롬프트·fixture 변경은 새 릴리스로 적용하며 이전 검증 기록을 새 정책의 실모델 평가 결과로 해석하지 않는다.

## 로컬 API 추가 계약

| 경로 | 역할과 의미 |
| --- | --- |
| POST `/api/v2/reports` | 학생, CSRF·UUID 멱등 키. 새 신고 202, 같은 요청 200. category와 선택적 공개 글 ID만 수신 |
| GET `/api/operator/reports` | 운영자, 서명 cursor 페이지. 원문·신고자 ID를 반환하지 않음 |
| POST `/api/operator/reports/{reportId}/resolve` | 운영자, CSRF·명령 키. 실제 숨김/중단이 없으면 REPORT_ACTION_REQUIRED |
| GET `/api/operator/moderation/provider` | 운영자, 현재 검수 프로필·회로·원장 상태 |
| POST `/api/operator/moderation/provider` | ADMIN, CSRF, paused boolean. 변경을 감사에 기록하며 재개는 복구 점검부터 시작 |

새 영수증의 필요 클라이언트 기능은 접수 시 메타데이터로 저장한다. 과거 SUPPORT_AVAILABLE의 원문 제거·중립적 미표시 안내는 원문 삭제나 legacy로의 설정 전환 후에도 유지한다. `support-v1`은 이 호환 계약 이름으로 남으며 도움 안내 기능이 아니다. 소유권 확인 뒤 기능 헤더가 없으면 409 CLIENT_UPDATE_REQUIRED다. 기존 v1 결과는 유지하고 provider 프로필의 v1 새 접수는 업데이트를 요구한다.

신고는 자동 제재·자동 숨김을 만들지 않는다. 신고 원문·이름·스크린샷을 받지 않으며 메타데이터는 7일 후 정리한다. 신고 기능은 유지하고 별도의 도움 안내·상담 연결·자동 교사 연락 기능은 제공하지 않는다.

## 검사 방법과 남은 범위

2026-11-09 [실제 연결 기록](checks/gemini-live-connection-2026-11-09.json)은 **PARTIAL**이다. 실제 학생 글 없이 합성 문장으로 5개 workflow·10개 HTTP 요청을 실행했고 자동 재시도는 없었다. 정상 응답은 HTTP 200 9개이며 공격 문장 한 건은 countTokens 성공 뒤 HTTP 400 `SAFETY`로 생성이 거절됐다.

- 일반 연결 문장과 이름을 포함한 감사 문장은 실제 검수 연결에서 `ALLOW`였다.
- 기쁨 문장은 `ALLOW → JOY/MODEL`로 전달됐고 감정 함수를 실제 1회 호출했다. 모델 적재 59.985초, 해당 검수·감정 분석 2.703초는 이번 호스트의 단일 측정값이다.
- 마스킹한 합성 연락처는 신뢰된 로컬 탐지 결과를 병합한 최종 분석기 판정에서 `BLOCK/PERSONAL_INFORMATION`이었다. 원시 Gemini 응답 자체의 BLOCK을 독립 검증한 결과는 아니다. 감사·연락처 두 사례의 추가 검사는 감정 모델 없이 수행했다.
- 공격 문장의 공급자 거절은 정상적인 모델 `BLOCK` 결과가 아니다. 기대한 BLOCK이 반환되지 않아 두 사례 감정 연결 검사의 exit code 1을 보존한다. 비공개 실패·원문 삭제·예약 반환·감정 미호출 경로는 코드 검토로 확인했으며 실제 DB에서 재현한 결과는 아니다.

격리 Docker DB의 migration·전광판 초기화·API 초기 health는 성공했지만 모델 이미지 내보내기 중 C 드라이브 공간이 소진되어 Docker 엔진이 멈추고 빌드가 EOF로 끝났다. 따라서 실제 worker를 포함한 QR 입장·접수·지속 큐·15초 표시·영수증·1시간 보관의 전체 연결은 확인하지 못했다. Docker 복구 후 격리 프로젝트 정리도 남아 있다. 호스트 AI 호출 성공을 전체 앱 연결 완료로 표시하지 않는다.

[키 연결 준비 검증 기록](checks/gemini-key-ready-2026-11-05.json)에 이번 HTTP 구현·전체 회귀·실행기·화면·계약 검사 결과를 남긴다. [이전 오프라인 구현 기록](checks/gemini-offline-implementation-2026-11-04.json)은 당시 mock 구현의 이력이다. 전체와 집중 테스트는 서로 겹치므로 합산하지 않는다.

백엔드에서 격리된 PostgreSQL 테스트 DB를 준비한 뒤 `python -m pytest -q`로 실행한다. source와 같은 migration을 적용하고 `MOODWALL_TEST_DB_URL`에는 이름에 test가 포함된 전용 DB만 넣는다. Linux supervisor·강제 종료 시험까지 포함하려면 Docker/Linux 환경을 사용한다. 실제 학생 모델 파일이 필요한 선택 시험은 해당 artifact를 지정하지 않으면 생략된다.

```powershell
python -B -m unittest discover -s scripts -p test_setup_gemini.py -v
node --test 'clients/**/*.test.mjs'
npm.cmd --prefix frontend test -- --maxWorkers=1
npm.cmd --prefix frontend run build
node docs/checks/verify-gemini-design.mjs
```

HTTPS transport·인증 헤더·countTokens·공급자 usage 파싱·공식 단가 구간 적용·연결 실행기는 구현했다. 가짜 HTTP 응답과 격리된 합성 `.env`로 오류·한도·비밀 보존 경계를 시험한다. 위 합성 실호출로 인증·승인 후 감정 연결을 부분 확인했지만 명시적 probe는 앱의 비용 원장을 거치지 않았다. 전체 백엔드 회귀, Google 계정별 할당량 수락·청구서 대사, 실모델 3,200건 품질 평가, 현장 장치 수락·운영 배포는 미완료다. 학교 내부 별명·보이지 않는 관계·분할 공격이 완전히 해결됐다는 보장도 하지 않는다.
