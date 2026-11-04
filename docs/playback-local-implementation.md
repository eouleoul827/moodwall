# v2 로컬 구현과 프론트 연결

기준일 2026-11-04. 현재 소스는 HTTP 계약 0.8.0, DB head `0006_playback_receipts`다. 실제 실행 결과는 [구현 상태](implementation-status.md)에 기록한다. 팀의 HTML 시안은 보존했고 [학생·전광판·운영자 React 화면](../frontend/README.md)을 v2 API에 연결했다.

## 지금 동작하는 흐름

1. 학생이 최근 QR로 입장하고 30자 이내 글을 보낸다. 학생이 고르는 배경 이모지는 v2 입력에서 제거했다.
2. 세션·QR 허가·정지·최근 60분 3회·10초 간격·검수 중 1개를 확인한다. 원문·본문 없는 접수 영수증·수용량 예약·처리 job을 같은 DB 트랜잭션에 저장한다.
3. 기존 독립 작업자가 필터와 AI 결과를 확정한다. ALLOW와 유효한 감정일 때 고정 테마의 `sceneId`, `sceneVersion`, `sceneSeed`, `animationId`를 준비한다.
4. 전광판 행을 잠그는 짧은 트랜잭션에서 준비 완료 순번을 확정한다. 접수 순서가 아니라 이 READY 확정 순서대로 표시한다.
5. 전광판 탭 하나가 다음 글을 claim한다. 렌더러의 글꼴·자산 준비가 끝나야 start를 요청한다. 첫 프레임을 보고하고 15초를 채운 뒤 complete를 보낸다. 다른 글은 기다린다.
6. 완료된 글은 일반 말풍선으로 떠다닌다. 최초 start 승인부터 1시간이 되면 API가 원문 공급을 중단하고 정리 루프가 원문을 삭제한다. 이미 받은 내용은 노트북에서 투명 말풍선으로 다음 KST 자정까지 남긴다.

202/200은 접수 확인이다. 학생 UI는 곧바로 기존 완료 카드를 표시한다. PREPARING·WAITING·SHOWING 같은 내부 단계를 각각 새 화면으로 만들 필요는 없다. 수정/실패가 확정됐을 때만 짧은 안내를 반영한다.

현재 학생 모델 v0는 모든 결과를 `UNCERTAIN`으로 반환하며 production 로딩을 거절한다. 이 보호를 바꾸지 않았다. 로컬 fake로 서버 흐름을 검증할 수 있지만 실제 게시에는 AI 담당의 평가된 허용 기준이 필요하다. 생성형 이미지 API는 구현하지 않았고 고정 감정 테마를 사용한다.

## 중요한 파일과 책임

| 소스 | 책임 |
| --- | --- |
| [playback.py](../backend/app/playback.py) | v2 접수·영수증, READY 순번, 재생 명령, 예약·복구·원문 보관 |
| [0006 migration](../backend/app/migrations/versions/0006_playback_receipts.sql) | 원문과 분리된 영수증·재생 항목·시도·성공 명령·수용량 예약 |
| [moderation.py](../backend/app/moderation.py) | 기존 검수 확정 뒤 READY 동기화. 확정과 발행 사이 장애는 재조정 루프가 복구 |
| [request_limits.py](../backend/app/request_limits.py) | 역할별 GET/POST 제한과 공유 IP 입장 제한. 개인 작성 횟수와 별개 |
| [학생 연결 모듈](../clients/student/submission-client.mjs) | content 전송, 동일 요청 재시도, 결과 불명 복구, 최소 완료 카드 |
| [전광판 연결 모듈](../clients/display/playback-browser.mjs) | 재생 컨트롤러·heartbeat·가시성·말풍선 동기화 연결 |
| [API 계약](openapi.json) | 기존 27개와 새 10개 작업, 엄격한 요청·응답·오류 계약 |

## 새 API

모든 요청은 기존 같은 origin 쿠키 인증을 사용한다. POST에는 Origin·CSRF가 필요하다. 접수·재생 명령에는 UUID `Idempotency-Key`가 필요하며 lookup은 조회할 키를 JSON에 담는다.

| 요청 | 용도 |
| --- | --- |
| `POST /api/v2/submissions` | `{content}` 접수. 신규 202, 동일 요청 200 |
| `GET /api/v2/submissions/{id}` | 본인 영수증. 원문 삭제 후에도 접수 후 24시간 조회 |
| `POST /api/v2/submission-receipts/lookup` | `{idempotencyKey}`로 응답을 잃은 접수 확인 |
| `POST /api/display/heartbeat` | `{clientInstanceId, displayProtocolVersion:"2"}`로 v2 소유권·상태 확인 |
| `GET /api/display/playback?clientInstanceId=...` | 현재 재생 상태 조회. lease를 갱신하지 않음 |
| `GET /api/v2/display/state` | 완료된 당일 말풍선 페이지. 100개는 전송 단위 |
| `POST /api/display/playback/claim` | 다음 READY 항목. 없으면 204 |
| `POST /api/display/playback/{attemptId}/start` | 자산 준비 후 시작 승인 |
| `POST /api/display/playback/{attemptId}/frame` | 첫 화면 표시 보고 |
| `POST /api/display/playback/{attemptId}/complete` | 첫 프레임 보고부터 최소 15초 뒤 완료 |
| `POST /api/display/playback/{attemptId}/interrupt` | 가려짐·새로고침·프레임 중단 등 보고 |

재생 명령은 탭 UUID·장치 세대·playback epoch로 이전 요청을 구분한다. 명령의 상태 값과 세대·순번의 문자열 형식은 OpenAPI를 그대로 사용한다. SSE는 재조회 힌트이며 실제 상태 기준은 GET과 DB다.

## 시간과 장애의 절충

- 준비 작업은 접수 후 최대 120초, 첫 표시 전 대기는 최대 10분이다. 미완료 수용량은 전광판당 최대 32개이며 남은 당일 시간과 예상 처리 시간 때문에 더 일찍 접수를 막을 수 있다. 일반 말풍선의 4개 제한은 없다.
- claim은 10초, start 승인으로 받은 첫 프레임 기한은 5초다. 동일 start를 재전송해도 기한과 최초 1시간 보관 시각은 늘어나지 않는다.
- heartbeat는 약 10초마다 소유권의 30초 lease를 갱신한다. 재생 시도 자체는 start 승인 후 최대 45초다. heartbeat만 살아 있고 완료 보고가 없는 탭이 대기열을 한 시간 동안 막지 않도록 한다.
- 정상 재생은 15초를 채워야 한다. 긴급 숨김·장치 해지·가려진 탭·중단·자정은 재생을 중단한다. 불확실한 재생은 같은 글을 처음부터 한 번만 다시 시도한다. 완료를 추측해 SHOWN으로 만들지 않는다.
- DB 기한이 지나면 조회는 즉시 제외한다. 물리 삭제는 정상 정리 루프의 약 1초 주기 배치에서 수행하며 서버 중단 중 즉시 삭제까지 보장하는 것은 아니다. 재시작하면 만료 데이터를 정리한다.
- 영수증에는 본문·이모지·AI 원문 결과가 없다. 접수 후 24시간 동안 HMAC fingerprint와 결과만 유지해 중복 접수를 막는다. 새 키는 사용자가 새 글을 보내는 의도일 때만 만든다.
- 같은 키의 성공 명령은 원래 시도의 결과를 조회한다. 예를 들어 완료 후 숨김이면 `status=DONE`, `presentationStatus=HIDDEN`, `item=null`이다. 완료 이력과 현재 공개 여부를 구분해 숨긴 글을 다시 그리지 않는다.
- 이미 노트북에 받은 투명 말풍선은 빈 서버 목록만으로 삭제하지 않는다. 명시적 숨김·인증 초기화·장치 세대 변경·자정에는 지운다. 노트북 저장소를 지우거나 기기를 바꾸면 DB에서 삭제한 내용을 되찾을 수 없다.

## 로컬 실행과 전환

[백엔드 실행 가이드](../backend/README.md)의 Compose 초기화 순서를 따른다. 기존 DB는 백업·버전을 확인한 뒤 `docker compose run --rm migrate`로 새 migration을 적용하고 API/작업자를 같은 코드 버전으로 다시 시작한다. 이 작업에서는 실제 운영 DB를 변경하지 않고 독립된 합성 테스트 DB에 적용했다.

v1 접수·즉시 말풍선 경로는 호환용으로 보존했다. 새 학생 화면과 전광판은 v2를 함께 사용해야 한다. v2 heartbeat를 보내는 활성 전광판이 없으면 새 v2 접수는 `DISPLAY_OFFLINE`으로 거절된다. v1과 v2는 같은 접수 키를 공유해 새 글로 바꿔 보내지 못한다.

기존 v1 글을 v2 전체 화면에 다시 재생하거나 시작 시각을 지어내지 않는다. v2 캐시는 v1 캐시를 가져오지 않으므로 당일 종료 후 전환하는 방법을 권한다. 같은 날 전환한다면 기존 v1 글과 로컬 말풍선이 남는 처리 방식을 프론트와 먼저 맞춘다.

교내 공유 IP 입장 한도와 GET/POST 한도는 [.env.example](../.env.example)의 `MOODWALL_BOOTSTRAP_*`, `MOODWALL_READ_*`, `MOODWALL_WRITE_PER_MINUTE`로 조정한다. 현재 값은 학교 규모의 측정 전 초기값이며 개인 제재·시간당 3회 규칙과 별개다.

## 팀에서 이어서 할 일

React 화면은 [전광판 모듈](../clients/display/README.md)과 [학생 모듈](../clients/student/README.md)을 사용하며 감정 테마와 글꼴 준비를 연결했다. 프론트 담당은 실제 브라우저 저장·HDMI 출력·가려짐·재부팅 시험을 진행한다. AI 담당은 평가된 게시 판단과 감정 결과를 제공한다. 일반 백엔드는 실제 서버·HTTPS·학교망·공유 IP 부하·모니터링·보관 안내를 현장 조건에 맞춰 마무리한다.
