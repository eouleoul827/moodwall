# 백엔드 구현 계약

상태: 설계 기준 · 2026-10-29 · HTTP 계약 0.7.0 · 현재 실행 증거는 [구현 상태](implementation-status.md) 참조

이 문서는 클래스의 호출 경계, HTTP 오류와 재시도, 목록과 화면 조회의 일관성을 정한다. 실제 요청·응답의 필드와 경로는 [OpenAPI](openapi.json), 쓰기 트랜잭션과 잠금은 [데이터 설계](data-and-recovery.md), 인증은 [보안 설계](security-and-operations.md)가 기준이다. 코드 블록은 인터페이스 예시이며 서버 구현이 아니다.

## 1. 구조 선택과 책임

이 팀은 일반 백엔드와 AI 추론을 Python으로 개발하는 모듈형 단일 백엔드를 선택한다. API와 모델 작업자는 별도 프로세스이며 같은 프로젝트·배포 버전·PostgreSQL을 사용한다. 프레임워크는 업계 표준이 하나로 정해진 항목이 아니다.

| 선택지 | 이 팀에서의 이점 | 추가로 감당할 일 |
| --- | --- | --- |
| FastAPI + Python 작업자, 선택 | AI 어댑터와 언어·데이터 모델 도구 공유, 별도 모델 HTTP 서비스 생략 | 타입 검사·트랜잭션·작업 감시는 직접 명시적으로 구성 |
| NestJS + Python 작업자 | 프론트와 TypeScript 공유, 모듈·의존성 주입 구조 | Python과 TypeScript 사이 계약·의존성·빌드를 함께 관리 |
| Spring Boot + Python 작업자 | Java의 정적 타입과 트랜잭션·보안 도구 활용 | 처음 배우는 팀이 Java 설정과 Python 실행 환경을 함께 운영 |

셋 모두 가능한 선택이다. 현재 팀의 납기와 담당 범위를 기준으로 FastAPI를 유지하며, 검증되지 않은 성능 우위를 선택 근거로 삼지 않는다.

현재 업무 쿼리는 psycopg 3로 통일하고 앱 migration은 Alembic revision으로 관리하는 방향을 선택했다. Alembic의 SQLAlchemy 연결은 migration 전용이며 Procrastinate에 SQLAlchemy AsyncSession을 그대로 전달하는 것으로 해석하지 않는다. 구체적 개발 환경·의존성 검증·PR 순서는 [일반 백엔드 구현 계획](backend-owner-plan.md)을 따른다. 실제 환경과 migration 코드는 아직 만들지 않았다.

```text
backend/app/
  api/             라우터, 인증 의존성, 요청·응답 DTO, 예외 변환
  application/     제출·검수 확정·표시·운영자 유스케이스
  domain/          상태 enum, 길이·쿼터·제재·애니메이션 정책
  infrastructure/  psycopg 쿼리, Procrastinate, 세션·QR 암호 처리
backend/inference/ AI 담당의 고정된 추론 계약과 어댑터
```

`api`는 SQL과 모델을 직접 실행하지 않는다. `domain`은 FastAPI·DB·큐를 import하지 않는다. `application`이 트랜잭션을 소유하고, 저장소 구현과 큐 등록이 그 연결을 사용한다. 모든 테이블에 추상 Repository를 하나씩 만드는 것은 요구하지 않는다. 제출 저장과 동일 연결 작업 등록처럼 테스트 대체가 필요한 경계부터 분리한다.

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ParticipantContext:
    # 인증 의존성만 생성한다. 클라이언트 body의 participantId는 받지 않는다.
    participant_id: UUID
    session_id: UUID


@dataclass(frozen=True, slots=True)
class SubmitMention:
    idempotency_key: UUID
    content: str
    emoji_id: str


@dataclass(frozen=True, slots=True)
class SubmissionReceipt:
    submission_id: UUID
    created_new: bool


@dataclass(frozen=True, slots=True)
class ClaimedRun:
    submission_id: UUID
    run_token: UUID
    # 시간과 버전의 실제 필드는 데이터 설계와 AI 계약에서 정의한다.


class SubmissionService(Protocol):
    async def submit(
        self, actor: ParticipantContext, command: SubmitMention,
    ) -> SubmissionReceipt: ...


class ModerationLifecycle(Protocol):
    async def claim(self, submission_id: UUID) -> ClaimedRun | None: ...

    async def expire(self, submission_id: UUID) -> bool: ...


class DisplayCoordinator(Protocol):
    async def reconcile(self, board_id: UUID) -> int: ...

    async def set_device_mode(
        self, board_id: UUID, mode: Literal["MOODWALL", "CHATBOT"],
    ) -> int: ...
```

초기 제품 화면 연결은 MOODWALL과 운영자 PAUSED만 포함한다. CHATBOT 값과 시간·대기 보존은 예약 API 계약으로 시험하되 챗봇 화면·시작 종료 연동 없이 제출·검수·표시·운영 기능을 완성할 수 있다.

`SubmissionReceipt.created_new`는 HTTP 202 또는 기존 요청의 200을 결정한다. 라우터가 소유자의 `Submission` DTO로 변환하며 공개 적합성 판정을 직접 수행하지 않는다. `claim()`이 None이면 terminal·중복 실행 등으로 추론을 시작하지 않는다. `expire()`는 실제 DB 기한을 확인해 상태를 바꿨을 때만 true다. 표시 메서드의 정수는 커밋한 revision이며, 호출자는 먼저 장치·운영자 권한을 검사한다.

최종화 입력은 [AI 계약의 FinalizationOutcome](ai-contract.md)에 `submission_id`·실행 token을 붙인 서버 내부 envelope다. 규칙 거절·모델 분석·실행 실패를 서로 다른 결과로 구분한다. `FinalizeSubmission`은 결과 검증 후 참여자 → 제재 집계 → 제출 순으로 잠그고 상태·token·접수 기한·실행 임대·정지를 확인한다. 모델 어댑터가 이 유스케이스나 DB에 직접 접근하지 않는다.

저장소·작업 등록의 트랜잭션 인터페이스는 다음 세 조건을 반드시 만족한다.

1. 신규 제출, `last_submission_at`, Procrastinate 작업 등록에 **같은 psycopg 연결**을 사용한다.
2. 저장소 메서드와 큐 어댑터는 내부에서 독립 commit이나 새 연결을 열지 않는다.
3. application이 한 번 commit하고, 어떤 단계든 실패하면 전체 rollback한다. 모델·SSE 전송은 이 트랜잭션 밖이다.

이는 커밋 전 장애의 부분 저장을 막기 위한 조건이다. 작업 실행·네트워크 전달이 정확히 한 번이라는 보장은 하지 않는다. [Procrastinate 외부 연결](https://procrastinate.readthedocs.io/en/stable/howto/production/external_connection.html)

## 2. 오류 형식과 HTTP 상태

오류는 RFC 9457의 `application/problem+json`으로 통일한다. `type`은 고정 문제 유형, `status`는 실제 HTTP 상태와 같고 `detail`은 안전한 사용자 안내다. `code`·`requestId`·`retryAfterSeconds`·`errors`는 프로젝트 확장이다. 클라이언트는 알 수 없는 확장을 무시하며, 모르는 `code`에는 일반 안내를 표시한다. 이는 팀의 채택 결정이며 모든 API가 이 형식을 써야 한다는 뜻은 아니다. [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html)

```json
{
  "type": "/problems/quota-exceeded",
  "title": "Too Many Requests",
  "status": 429,
  "detail": "최근 60분 작성 한도를 사용했습니다.",
  "instance": "urn:uuid:7e9c7097-a6d1-4324-a90c-108285ef84a3",
  "code": "QUOTA_EXCEEDED",
  "requestId": "7e9c7097-a6d1-4324-a90c-108285ef84a3",
  "retryAfterSeconds": 120,
  "errors": []
}
```

`type`은 `/problems/` 뒤에 code를 소문자·하이픈으로 바꾼 경로를 붙인다. 문서 식별자이며 별도 오류 조회 API는 필요하지 않다. `instance`는 이 요청의 식별 URN이고 자동으로 가져올 URL이 아니다. `title`은 문제 유형별 고정 문구를 사용하고 `detail` 문자열을 파싱해서 로직을 나누지 않는다.

| HTTP | code | 프론트 처리 |
| --- | --- | --- |
| 400 | MALFORMED_JSON, INVALID_QR, QR_EXPIRED, PAIRING_CODE_INVALID | 입력·QR 확인. 만료 QR은 원고를 보존하고 재스캔 |
| 401 | SESSION_REQUIRED, SESSION_EXPIRED, INVALID_CREDENTIALS | 해당 역할 인증 갱신. stale 쿠키를 삭제했다는 응답일 때 bootstrap만 한 번 재시도 |
| 403 | FORBIDDEN, CSRF_REJECTED, ORIGIN_REJECTED, ADMISSION_EXPIRED, PARTICIPANT_SUSPENDED | 권한·세션·QR·정지 안내. 자동 새 학생 ID 발급 금지 |
| 404 | NOT_FOUND | 대상 없음. 학생 조회에서 타인 소유와 구분하지 않음 |
| 405 | METHOD_NOT_ALLOWED | 호출 오류. 자동 반복하지 않음 |
| 409 | IDEMPOTENCY_CONFLICT, SUBMISSION_IN_PROGRESS, BOARD_PAUSED, INVALID_STATE | 기존 요청 상태 확인 또는 명시적인 수정. 다른 내용에 기존 키 재사용 금지 |
| 413 | PAYLOAD_TOO_LARGE | 요청 크기 축소 |
| 415 | UNSUPPORTED_MEDIA_TYPE | JSON content type 수정 |
| 422 | VALIDATION_ERROR, INVALID_CURSOR | 필드 수정. 커서 오류는 첫 페이지부터 조회 |
| 429 | SUBMISSION_COOLDOWN, QUOTA_EXCEEDED, RATE_LIMITED | Retry-After 이후 사용자 동작 허용. 자동 새 키 생성 금지 |
| 500 | INTERNAL_ERROR | 결과 미확정으로 안내하고 동일 키로 확인 |
| 503 | DB_UNAVAILABLE, MODEL_NOT_READY, MODERATION_BUSY, INTAKE_PAUSED, SERVICE_UNAVAILABLE | 같은 키 보존. 지연 후 제한적으로 재시도 |

401의 코드 의미를 고정한다. `SESSION_REQUIRED`는 해당 역할 쿠키가 없다는 뜻이다. `SESSION_EXPIRED`는 만료·해지·불명 쿠키가 있다는 뜻이다. 일반 GET·업무 POST의 401은 쿠키를 삭제하지 않는다. 같은 역할의 bootstrap POST(입장 교환·운영자 로그인·장치 페어링)만 stale 쿠키를 동일 속성으로 삭제하고 한 번 재시도할 수 있다. `INVALID_CREDENTIALS`는 운영자 로그인 실패이고 자동 재시도하지 않는다. 브라우저 JavaScript는 Set-Cookie를 읽어 분기하지 않고 이 코드를 사용한다. 세션 오류를 처리하기 전에는 로그인 비밀번호나 pairing code를 소비하지 않는다.

늦은 옛 요청의 401이 새 쿠키를 삭제하지 않도록 인증 쿠키 변경은 bootstrap·logout 경계에 모은다. 프론트는 이 경계를 브라우저 저장소를 공유하는 탭 사이에서도 직렬화하고 이전 polling·SSE를 중단한다. `authEpoch`는 JS 응답 적용만 제어하며 브라우저의 Set-Cookie 적용 순서를 막지 못한다. bootstrap과 logout끼리 겹치면 여전히 쿠키 교체 경합이 있으므로 새 인증 작업은 이전 작업의 응답이 끝난 뒤 시작한다.

일반 요청 제한은 RATE_LIMITED, 미검수 작업 혼잡은 MODERATION_BUSY다. 승인 뒤 표시 자리를 기다리는 큐와 DISPLAY_BUSY는 제거했다. 업무 코드별 지표를 집계하며 새 code를 모르는 프론트도 일반 오류 안내로 처리해야 한다.

모든 API 성공·오류에 서버 생성 `X-Request-ID`와 `Cache-Control: no-store`를 넣는다. requestId에는 클라이언트의 임의 헤더를 그대로 반사하지 않는다. 429는 초 단위 정수 `Retry-After`와 같은 `retryAfterSeconds`를 제공하고, 503은 예상 대기가 있을 때만 제공한다. 계산은 `max(0, ceil(가능시각 - DB현재시각))`이며 가장 오래된 소비 접수의 60분 경계도 같은 기준이다. 503에 일일 한도·한국 자정을 일괄 안내하지 않는다. [HTTP Retry-After](https://www.rfc-editor.org/rfc/rfc9110.html#name-retry-after)

FastAPI 기본 HTTPException·RequestValidationError·응답 직렬화 오류를 각각 안전한 공통 handler로 변환한다. 응답과 로그에 `input`, `ctx`, 비밀번호, 원문, token, SQL, traceback을 그대로 넣지 않는다. `errors`는 허용된 JSON Pointer와 오류 코드만 최대 20개 반환한다. 프록시·네트워크가 HTML이나 빈 응답을 반환할 수도 있으므로 프론트는 JSON 파싱 실패도 결과 미확정으로 처리한다. [FastAPI 오류 handler](https://fastapi.tiangolo.com/tutorial/handling-errors/)

로그아웃도 JSON `{}`와 CSRF·Origin으로 호출한다. 허용하지 않은 메서드의 405에는 `Allow`를 실제 경로에 맞게 설정한다. SSE가 200으로 시작한 뒤의 오류는 중간에 Problem JSON을 끼워 넣지 않고 stream을 닫아 재연결·상태 조회로 복구한다.

### HTTP 입력의 단일 해석

모든 POST는 JSON object를 받으며 DTO는 알려진 필드와 엄격한 타입만 허용한다. 문자열·숫자·bool을 조용히 상호 변환하지 않는다. 공통 입력 계층에서 다음 순서를 적용하고, 어떤 실패도 금칙 위반으로 집계하지 않는다.

1. 정확한 Origin과 요청 속도 제한을 검사한다. 요청 `Content-Encoding`은 없거나 `identity`만 허용하고 다른 값은 415다. `Content-Type`은 `application/json`이며 charset이 있으면 UTF-8만 허용한다. 미지원 media type·charset은 415다.
2. 제출 JSON은 실제 수신 stream을 누적하여 4096바이트에서 제한한다. Content-Length만 믿지 않고 누락·분할 전송도 같은 상한을 적용한다. 초과하면 413이며 압축 본문을 자동 해제해서 받지 않는다. 나머지 JSON POST도 프록시와 앱의 4 KiB 기본 상한을 적용한다.
3. UTF-8을 엄격히 해석하고 JSON 객체의 중복 이름, 고립 surrogate, Unicode noncharacter, NaN·Infinity 등 비유한 수치, 잘못된 JSON을 400 MALFORMED_JSON으로 거절한다. 이름 중복은 escape를 해석한 뒤 비교한다. 최상위 object가 아니거나 필수·타입·추가 필드 오류이면 422다.
4. 길이·표시 문장 검사는 [텍스트 정책](profanity-policy.md)을 따른다. 누적 본문 제한은 파싱 전에, content UTF-8 상한은 파싱 후에 다시 확인한다.

이는 RFC 8259와 I-JSON의 상호운용 원칙을 채택한 프로젝트 입력 정책이다. RFC 8259의 중복 이름 SHOULD를 모든 JSON 수신자의 MUST 거절로 바꾸어 설명하지 않는다. FastAPI 기본 parser를 사용했다는 사실만으로 위 정책을 구현했다고 보지 않는다. [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259.html), [I-JSON RFC 7493](https://www.rfc-editor.org/rfc/rfc7493.html)

## 3. 작성 재시도와 사용자 안내

신규 접수는 202와 `Location: /api/submissions/{id}`, 첫 조회용 `Retry-After: 1`을 반환한다. 202는 검수·공개 완료를 의미하지 않는다. 이후 기본 2초 간격으로 조회하며 terminal 검수 상태에서 멈춘다. GET으로 조회한 FAILED·REJECTED도 자원의 정상 조회이므로 HTTP 200이고 상태 필드로 구분한다. [HTTP 202](https://www.rfc-editor.org/rfc/rfc9110.html#name-202-accepted)

응답 유실·timeout·5xx로 커밋 여부를 모르면 **같은 키와 본문**으로 재전송한다. 서버는 유효한 소유 세션으로 기존 접수를 먼저 찾아 돌려준다. 최초 성공 응답은 202, 같은 의도의 기존 응답은 현재 상태로 200이다. 최초 응답의 바이트를 그대로 재생하는 계약은 아니다.

네트워크 오류 자동 재전송은 초기 최대 2회, 지수 지연과 작은 무작위 지연을 적용한다. 횟수를 넘으면 원고·키를 유지하고 사용자가 확인하도록 한다. 403·409·422에는 무조건 재시도하지 않는다. 새로운 키는 사용자가 명시적으로 새 글을 보낼 때만 생성한다. 세션 만료 뒤에는 과거 제출을 다른 새 익명 ID의 소유라고 주장하지 않는다.

입력 크기·엄격한 구조·고정 v1 표시 문장 정규화 후 기존 키를 찾는다. 현재 emoji 활성 목록·새 금칙 정책은 기존 키 조회보다 먼저 적용하지 않는다. 기존 키가 없을 때만 현재 emoji 허용 여부와 신규 접수 정책을 검사한다. 예전에 허용한 emoji가 비활성화되어도 보관 기간 안의 동일 요청은 기존 결과를 돌려준다. 표시 자산은 보관 기간 동안 제공하거나 고정 기본 자산으로 대체한다. v1 canonical hash는 서버가 정한 키 이름·정렬·공백·UTF-8 escaping을 fixture로 고정하며 정책 배포로 변경하지 않는다.

원고·키 보존의 기본 범위는 현재 살아 있는 탭의 메모리다. 새로고침 뒤 본문과 키를 추측해 자동 재전송하지 않는다. session·usage를 조회하고 `activeSubmissionId`가 있으면 상태 조회를 복구한다. 이미 최종 상태가 되어 키까지 잃은 경우 모든 과거 의도를 복원한다고 약속하지 않는다. 초기에는 학생 원문을 localStorage에 상시 보관하지 않는다.

`Idempotency-Key`는 프로젝트가 선택한 UUID 헤더다. RFC로 확정된 형식이라고 부르지 않으며, IETF 초안의 Structured Field 문자열 형식을 구현했다고 주장하지 않는다. 클라이언트는 현재 OpenAPI에 적힌 원문 UUID 형식을 사용한다.

`Usage.nextSlotAt`은 사용량 3개일 때 가장 이른 `accepted_at+60분`, 여유가 있으면 null이다. 검수 결과로 더 일찍 여유가 생길 수 있다. `nextSubmissionAt`은 10초 쿨다운의 종료 시각만 나타낸다. 검수 중·QR 만료·당일 정지는 별도 필드로 표시하고 시간을 하나로 합쳐 사용 가능을 단정하지 않는다.

### 입장 응답 유실과 QR 재스캔

첫 QR 교환의 본문이 유실되어도 브라우저에는 쿠키가 저장되었을 수 있다. 이때 무-CSRF 교환을 바로 반복하지 않고 `GET /api/session`으로 성공 여부와 CSRF를 확인한다. 유효한 입장이면 완료하고, 세션은 있으나 새 ticket 갱신이 필요하면 CSRF를 붙여 교환한다. 세션이 없고 ticket이 아직 유효하면 최초 교환을 제한적으로 다시 시도한다. stale cookie의 401 삭제 후 1회 bootstrap과 유효 cookie의 403 CSRF 오류는 구분한다. 쿠키가 계속 저장되지 않으면 발급을 반복하지 않고 브라우저 설정·지원 브라우저 안내를 표시한다.

프론트는 한 탭의 입장 작업을 중복 실행하지 않는다. 같은 브라우저의 여러 탭 조정은 UX 보완이며, 쿠키 없는 동시 최초 요청이 한 참여자만 생성한다거나 의도적 쿠키 삭제를 막는다고 보장하지 않는다. 카메라·메신저 인앱 브라우저와 외부 Chrome/Safari의 저장소가 분리되면 다른 익명 참여자로 보일 수 있음을 안내한다.

QR 만료 시 초안은 원래 탭에 남긴다. 새 QR이 새 탭에서 열려도 같은 브라우저 쿠키의 입장 허가를 갱신하며, 원래 탭은 focus/visibility 복귀 때 session을 조회한 후 제출을 재개한다. 탭 사이에 원문이 자동 이동한다고 가정하지 않는다.

### 운영자 변경 요청의 재시도

운영자 POST는 일반 HTTP 재시도기에서 제외한다. timeout·5xx이면 GET으로 현재 상태를 확인하고, 아래 명시 계약이 있는 동작만 같은 의도로 다시 보낸다. POST에 `Idempotency-Key`라는 이름만 붙여 모든 명령이 멱등하다고 가정하지 않는다. [RFC 9110의 멱등 메서드](https://www.rfc-editor.org/rfc/rfc9110.html#name-idempotent-methods)

- 제재 복구는 UUID `Idempotency-Key`가 필수다. `(operator_id, RESET_RESTRICTION, key)`를 90일 보관하며 대상 참여자·원래 업무 날짜·reasonCode·고정 명령 버전을 해시한다. 다른 운영자가 보낸 같은 키는 다른 scope다.
- 인증·권한 확인 뒤 기존 감사 명령을 먼저 찾고, 신규 처리의 참여자·집계 잠금 안에서 다시 확인한다. 기존 동일 해시는 재초기화하지 않고 현재 Usage를 반환하며 다른 해시는 409다. 기존 명령 조회는 새 명령의 당일 날짜 검사보다 먼저 수행하므로 자정 후에도 90일 안의 명령 결과를 확인할 수 있다. 과거 날짜를 대상으로 하는 새 명령은 409 INVALID_STATE다.
- 새 명령만 제재 초기화와 감사 기록을 함께 커밋한다. 다른 대상과 같은 키의 UNIQUE 경합은 전체 롤백 후 기존 명령을 조회한다. 복구 성공 후 새 위반이 쌓여도 같은 키 재전송이 그 위반을 지우지 않는다.
- 응답은 `RestrictionRestoreResult={auditId, replayed, businessDate, usage}`다. `auditId`는 원래 감사 행의 decimal string, replayed는 신규 명령이면 false·동일 명령 확인이면 true, businessDate는 원래 복구 대상 날짜, usage는 응답 시점의 오늘 사용량이다. 현재 결과를 반환하는 계약이며 최초 응답 바이트를 재생하지 않는다. ADMIN·MODERATOR는 `GET /api/operator/participants/{participantId}/usage`로 현재 Usage를 확인한다. 보관 정리로 대상 참여자가 이미 삭제되었다면 404이며 가상 사용량·새 참여자를 만들지 않는다. 90일 중복 방어는 재초기화 방지이지 삭제된 대상의 상태 복원 보장이 아니다.
- 이미 HIDDEN인 글을 다시 숨기면 200이며 중복 숨김 감사 기록을 만들지 않는다. 등록 코드 발급을 다시 실행하면 이전 미사용 코드를 교체하므로 자동 재전송하지 않는다. 장치 pairing 응답 유실은 `/api/display/session`부터 확인하고, 새 쿠키가 없으면 새 코드를 발급받는다. 사용한 코드로 쿠키를 재발급하지 않는다.

숨김 endpoint는 오늘 해당 전광판에서 UUID의 표시를 금지하는 명령이다. DB 원문이 남아 있으면 오늘의 APPROVED만 허용하고 EXPIRED·HIDDEN도 대상으로 삼는다. 전날·미승인 글은 409, 다른 전광판의 글은 404다. DB 행이 이미 삭제됐다면 404로 끝내지 않고 원문 없는 당일 숨김 감사를 저장해 200을 반환한다. 같은 전광판·날짜·ID의 기존 감사가 있으면 재감사하지 않는다. 이 명령은 원문을 복원하거나 보관을 연장하지 않으며, 최신 state의 `removedIds`로 브라우저 말풍선을 제거한다.

## 4. 목록 페이지와 화면 snapshot

운영자 목록은 첫 요청의 KST `businessDate`를 기본 오늘로 고정한다. 제출은 `(accepted_at DESC, id DESC)`, 감사는 `(occurred_at DESC, id DESC)` 정렬이다. 기본 50개, 최대 100개이고 `limit+1`개를 조회해 다음 커서 유무를 판단한다. 감사 bigint ID는 JavaScript 정밀도 손실을 피하기 위해 decimal string으로 보낸다.

커서는 버전·목록 종류·고정 날짜·상태 필터·limit·snapshotAt·마지막 정렬 키·30분 만료를 담는 base64url payload와 서버 HMAC이다. 서명 키는 QR·세션 키와 별도 문맥으로 파생한다. 커서 자체에 조회 권한을 부여하지 않으며 모든 페이지에서 운영자 권한을 검사한다. 크기 2048바이트 이하, 형식·서명·필터·날짜·limit·만료 불일치는 422 INVALID_CURSOR다. 다음 요청에서 필터와 limit은 첫 요청과 같게 유지한다. 자정 이후에도 커서에 고정된 날짜를 사용한다.

조회 조건은 첫 페이지 기준 시각보다 이후의 새 행을 제외하고 마지막 키보다 작은 행으로 전진한다. cursor를 SQL 문자열로 이어 붙이지 않는다. 상태 필터의 소속 변경이나 동시 삭제는 페이지 사이 결과를 바꿀 수 있다. 커서로 여러 요청의 동일 MVCC snapshot이나 완전한 감사 내보내기를 보장하지 않는다.

전광판 페이지는 짧은 `REPEATABLE READ READ ONLY`에서 board·카드·개수·날짜를 같은 DB 시각으로 읽는다. 조회는 상태를 바꾸지 않는다. 각 응답의 스냅샷과 여러 페이지에 걸친 HTTP 동기화는 다르다. [PostgreSQL 격리](https://www.postgresql.org/docs/16/transaction-iso.html)

학생·운영자·전광판 조회는 중첩하지 않는다. 겹친 경우 요청 순번으로 오래된 응답을 버린다. 같은 revision이어도 서버 시각·말풍선·숨김이 달라질 수 있으므로 최신 응답을 적용한다. 전광판은 매 동기화마다 첫 페이지부터 nextCursor=null까지 순차 수집한다.

전광판은 [노트북의 단일 응답 적용 규칙](display-laptop.md)을 사용한다. 인증 해지·재등록·hidden/절전 복귀 재검증은 authEpoch를, 로컬 제어 POST는 controlSeq를 증가시킨다. 응답의 epoch·controlSeq·board가 현재와 같고 조회 순번이 최신이며 revision이 마지막 적용값 및 SSE·제어에서 이미 확인한 최소 revision 이상일 때만 적용한다. POST 응답 state를 직접 그리지 않고 성공·실패·응답 유실 뒤 GET으로 재동기화한다. 401이면 epoch를 바꾸고 글·QR·SSE를 비우므로 이전 GET이 늦게 와도 다시 노출하지 않는다. 같은 revision의 최신 snapshot을 버리는 규칙으로 대신하지 않는다.

제어 POST는 한 화면에서 하나만 전송한다. 결과 미확정이면 GET 상태를 확인한 뒤 다음 명령을 허용한다. 서로 다른 운영자·기기의 명령은 board 잠금 아래 마지막 커밋이 최종 상태가 되며, 가장 최근에 클릭한 명령이 이긴다는 보장은 없다. authEpoch·controlSeq는 클라이언트 응답 적용 경계이며 서버 쓰기 순서나 운영자 PAUSED의 우선권을 대체하지 않는다.

승인 글은 즉시 일반 FLOATING 말풍선이다. 4개 자리·강조·표시 대기가 없다. `acceptedAt`은 접수 시각, `expiresAt=acceptedAt+1시간`은 **DB 원문 보관과 API 공급 종료**다. 프론트는 받은 글을 이때 내용 담긴 투명 AMBIENT 말풍선으로 바꿔 `ambientUntil`인 다음 KST 자정까지 유지한다. 중단·오프라인으로 DB 보관을 연장하지 않는다. `deadlineAt`은 검수 120초 기한이다. 만료된 본인 조회는404, 운영자 목록에서는 제외하고 작은 배치로 행을 삭제한다.

`DisplayState.bubbles`는 한 페이지 최대 100개이며 전체 노출 상한이 아니다. `bubbleTotalCount`는 현재 DB 공개 원문의 개수로 로컬 당일 총수와 다르다. `snapshotAt`은 첫 페이지의 승인 기준 시각으로 같은 cursor 사슬에서 고정한다. 커서는 전광판·기기 세대·업무 날짜·기준 시각·마지막 (finalized_at,id)·5분 만료를 서명한다. 각 페이지는 현재 인증·숨김·TTL을 다시 검사한다. nextCursor가 null일 때까지 읽으며 변조·scope 불일치·만료는 422 INVALID_CURSOR다. 매 새 동기화는 cursor 없이 첫 페이지부터 시작한다. 여러 HTTP 페이지에 동일 MVCC snapshot을 보장하지 않으며 중간 숨김·만료로 개수가 줄어들 수 있다. 페이지 사이 revision/serverNow는 최신 값으로 증가할 수 있다. 받은 카드를 ID로 합치고 **목록에 없다는 이유로 캐시를 지우지 않는다.** 전체 당일 목록을 일반·투명 말풍선으로 렌더하며 고정 슬롯·40개 순환 제한은 없다.

`DisplayState.removedIds`는 해당 전광판의 오늘 명시적 숨김 ID 목록(최대 1000개)이다. 프론트는 이 ID의 원문과 카드를 캐시에서도 지우며 이후 같은 ID를 다시 넣지 않는다. DB 보관 만료는 여기에 넣지 않는다. 숨김이 1000개를 넘으면 `cacheResetToken`이 잘린 경계의 감사 ID로 바뀐다. 토큰이 바뀐 프론트는 당일 캐시를 초기화한 뒤 현재 snapshot의 허용 카드만 다시 넣어, 누락된 과거 숨김이 부활하지 않게 한다. `cacheResetToken`·`deviceGeneration`은 JavaScript 정수 정밀도 손실을 막는 decimal string이다. 기기 세대·전광판·날짜가 바뀌면 기존 캐시를 비운다. 본문 없는 숨김 감사는 제출 행이 삭제된 뒤에도 유효하며, 같은 전광판·날짜·ID의 숨김은 중복 기록하지 않는다.

전광판 전용 프로필은 localStorage에 당일 공개 카드만 저장한다. 장치 cookie·CSRF·QR·참여자 ID·차단 글을 넣지 않는다. 저장이 불가능하면 메모리로 동작하고 새로고침 복구를 보장하지 않는다. 캐시는 session/state 인증과 숨김 동기화 전에 화면에 복원하지 않는다. 자정에는 메모리와 저장소를 지우고, 앱이 실행되지 않던 동안 자정이 지났다면 다음 시작 때 이전 날짜를 먼저 지운다. DB 삭제 뒤 저장소 삭제·기기 교체로 잃은 원문은 서버가 복구할 수 없다. 숨김·자정·기기 해지·최신성 상실은 캐시 존재와 별개로 화면 표시를 차단한다.

[캐시 모듈](../clients/display/bubble-cache.mjs)의 기본 당일 보관 상한 2000개(설정 최대 10000개)는 브라우저 메모리 보호 설정으로 제품의 동시 4개 제한과 다르다. 초과 시 오래된 카드를 제거하므로 현장 규모·저장소 제한을 확인한다. `BubbleCache.view()`는 `{visible,bubbles,totalCount}`를 반환하며 phase는 FLOATING/AMBIENT다. `DisplaySynchronizer`가 전체 페이지를 순차 수집하고 INVALID_CURSOR는 1회 재시작한다. React는 인증된 동기화·view·해지 clear를 연결해야 한다.

전광판 브라우저는 마지막 성공 snapshot 이후 30초 동안 갱신이 없으면 글·QR을 가리고 연결 확인 화면을 보여 준다. SSE가 끊겨도 REST snapshot이 계속 성공하면 최신성을 유지한 것으로 판단한다. 문서 hidden 중에는 heartbeat를 멈추고 visible 복귀 때 session·snapshot·QR을 재동기화한 후 재개한다. 향후 챗봇을 같은 웹 문서 안에 연결하면 연결은 유지하며 별도 mode API를 호출한다. 초기 구현에는 챗봇 연동을 요구하지 않는다. [노트북 화면 수락 조건](display-laptop.md)

## 5. 표준 근거와 프로젝트 선택의 경계

| 항목 | 적용한 근거 | 이 프로젝트가 별도로 정한 값·방식 |
| --- | --- | --- |
| HTTP 오류 | RFC 9457의 Problem Details | 업무 code, `/problems/` 유형 경로, 필드 오류와 재시도 확장 |
| 비동기 접수 | HTTP 202와 현재 상태 조회 | 조회 1초 후 2초, 새 요청 202·기존 요청 200 |
| API 계약 | OpenAPI 3.1.1과 JSON Schema | 계약 0.7.0, 일반 말풍선·100개 전송 페이지·접수 1시간 DB 만료·당일 로컬 투명 말풍선 |
| 보안 | OWASP 세션·CSRF·권한·출력·키 관리 | 쿠키 30일, QR 5분 교체·10분 만료, 운영자 유휴 30분 |
| 데이터 정확성 | PostgreSQL 트랜잭션·잠금·제약 | 참여자 우선 잠금, rolling 3회, 60초 실행 임대 |
| 운영 | 준비 상태 분리·관측·복구 시험 | 초기 SLO와 부하·경보 값은 [운영 수락 기준](delivery-and-operations.md)에서 측정 |

표준 채택은 앱의 보안 인증이나 성능 보장이 아니다. 구현 후 [검증 기준](verification.md)의 해당 시험을 통과해야 한다. HTTP 계약 변경 PR은 작성·전광판 프론트 담당자가 확인하고, 모델 계약 변경은 AI 담당자가 확인한다. 구현 전 계약 0.1.0의 Error/message 형식은 0.2.0의 Problem/detail로 교체했고, 0.3.0에서 운영자 복구 명령·조회 계약을 보완했다. 이미 배포된 클라이언트는 없다.
