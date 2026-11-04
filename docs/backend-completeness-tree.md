# 백엔드 완성 범위와 수락 조건 나무

최초 기준일: 2026-10-29 · 검수 추가: 2026-11-04. 일반 백엔드 1명·AI 1명·프론트 2명으로 만드는 **독립 무드월**의 구현 범위다. 기존 12개 큰 가지·102개 세부 가지에 M 검수 가지 4개를 추가해 고정 ID로 연결한다. 기능 추가·분리 때 기존 ID를 다른 의미로 재사용하지 않는다.

**이 문서의 모든 앱 기능은 설계 상태다. 서버 코드·실제 HTTP·동시 요청·큐 작업자·모델·노트북·배포가 구현·검증되었다는 뜻이 아니다.** 현재 실행한 OpenAPI·예시·DDL 검사는 [검증 기록](verification.md)의 범위만 증명한다. 아래의 “수락”은 앞으로 구현 PR과 출시 시험에서 확보할 증거다. 한 항목의 정적 검사를 전체 가지의 런타임 통과로 표시하지 않는다.

각 항목은 정상 흐름과 경계, 실패 시 처리와 복구, 수락 증거를 한 줄로 적는다. 원래 정책·필드·잠금 순서의 소유 문서는 각 줄의 링크이며, HTTP 필드는 [OpenAPI](openapi.json)가 기준이다. 세부 정책 충돌은 구현 전에 관련 문서를 함께 수정한다. 일반 백엔드가 모든 서버 흐름을 연결하고 AI 담당은 모델·추론 계약·평가, 프론트는 각 화면의 상태 적용·출력을 책임진다.

## 근거를 읽는 방법

| 종류 | 확인할 공식 근거 | 이 프로젝트에서의 의미 |
| --- | --- | --- |
| 보안 검증 기준 | [OWASP ASVS v5.0.0 고정 소스](https://github.com/OWASP/ASVS/tree/v5.0.0) | 인증·세션·권한·입력·로그 등 관련 통제를 골라 검토한다. 전체 ASVS 매핑·등급 충족·인증 선언은 아니다 |
| HTTP 표준 | [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html), [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html) | 상태·메서드 의미와 Problem Details 형식. 업무 오류 code·재시도 횟수·접수 정책은 팀이 정한다 |
| 계약 명세 | [OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html) | 소비자가 확인할 HTTP 계약. 문법 통과와 실제 구현의 계약 일치는 별도다 |
| Unicode 정의 | [UAX 15](https://www.unicode.org/reports/tr15/), [UAX 29](https://www.unicode.org/reports/tr29/) | NFC와 확장 grapheme 분절의 근거. 서버·브라우저의 실제 Unicode 버전과 공통 예문은 따로 고정한다 |
| 공식 구현 문서 | [PostgreSQL 16 격리](https://www.postgresql.org/docs/16/transaction-iso.html), [잠금](https://www.postgresql.org/docs/16/explicit-locking.html), [Procrastinate 외부 연결](https://procrastinate.readthedocs.io/en/stable/howto/production/external_connection.html), [중단 작업 복구](https://procrastinate.readthedocs.io/en/stable/howto/production/retry_stalled_jobs.html) | 선택한 도구의 실제 기능·제약이다. 라이브러리를 설치했다고 원자성·복구가 자동 검증되는 것은 아니다 |
| 프로젝트 결정 | [구조와 정책](architecture.md), [운영 기준](delivery-and-operations.md) | Python·PostgreSQL·SSE, 3회/60분, QR5분·10분, 승인 즉시 일반 말풍선·DB1시간/로컬 자정은 팀 선택이다 |

## A. 범위·초기화·실행 구성 — 8개

- **A01** 정상·경계: 학교 1곳·전광판 1대의 독립 무드월이며 멘션 AI는 포함한다; 실패·복구: 챗봇 미정으로 개발을 막지 않는다; 수락: 챗봇 UI·모델·실제 연동 없이 핵심 파이프라인이 완료된다. [전체 설계](architecture.md)
- **A02** 정상·경계: API·업무·정책·인프라·추론 어댑터의 책임을 분리한다; 실패·복구: 라우터의 SQL·모델 직접 실행을 제거한다; 수락: application이 트랜잭션을 소유하고 domain은 프레임워크·DB를 import하지 않는다. [구현 계약](backend-contracts.md)
- **A03** 정상·경계: 고정 BOARD_ID와 명시적 이름으로 CLI가 최초 board를 만든다; 실패·복구: 누락·불일치·다른 board 중복은 ready 실패다; 수락: 임의 첫 행 선택·공개 초기화 API 없이 같은 UUID를 재현한다. [초기화](delivery-and-operations.md)
- **A04** 정상·경계: 같은 UUID의 init-board 재실행은 현재 상태를 보존한다; 실패·복구: 동시 초기화는 advisory lock으로 직렬화한다; 수락: 이름·mode·revision·자격·중단 여부가 재실행으로 바뀌지 않는다. [초기화](delivery-and-operations.md)
- **A05** 정상·경계: 최초 삽입은 접수·송출 중단 true이고 첫 ADMIN은 신뢰된 CLI로 만든다; 실패·복구: 기본 관리자·평문 셸 인자·자동 해제를 금지한다; 수락: pairing 뒤에도 ADMIN의 각각 재개 전까지 중단된다. [초기 운영](delivery-and-operations.md)
- **A06** 정상·경계: PUBLIC_ORIGIN·BOARD_ID·키·DB 설정을 명시적으로 읽는다; 실패·복구: 빠진 비밀·가짜 Host·잘못된 환경을 안전하게 거절한다; 수락: 비밀 출력 없이 운영 설정 검사가 실패를 설명한다. [보안 설정](security-and-operations.md)
- **A07** 정상·경계: 앱 Alembic과 큐 schema revision을 별도로 검사한다; 실패·복구: 불일치면 배포·신규 접수를 막고 호환 절차로 복구한다; 수락: 빈 DB와 직전 버전 upgrade가 동일 제약을 만든다. [개발 환경](backend-owner-plan.md)
- **A08** 정상·경계: 초기 API workers=1, lifespan이 풀·제어 루프를 한 번 소유한다; 실패·복구: 루프 예외·stale은 ready와 접수에 반영한다; 수락: 종료 때 cancel·await·풀 정리가 되고 모델이 API에 로드되지 않는다. [실행 소유권](backend-owner-plan.md)

## B. 익명 식별·쿠키·요청 권한 — 8개

- **B01** 정상·경계: 첫 유효 QR 교환에서만 익명 참여자·세션을 발급한다; 실패·복구: GET·새로고침·인증 오류로 새 ID를 만들지 않는다; 수락: 같은 브라우저 재입장에 참여자·사용량·제재가 유지된다. [세션 정책](security-and-operations.md)
- **B02** 정상·경계: 학생·장치·운영자 자격을 별도 쿠키와 해시로 관리한다; 실패·복구: 다른 역할 쿠키·클라이언트 participantId를 신뢰하지 않는다; 수락: 역할 간 접근과 ID 변조가 거절된다. [권한 경계](security-and-operations.md)
- **B03** 정상·경계: CSPRNG 토큰과 Secure·HttpOnly·SameSite·Path 속성을 적용한다; 실패·복구: 원문 토큰은 DB·로그·URL·브라우저 저장소에 남기지 않는다; 수락: 발급·삭제 쿠키 속성과 DB 해시가 일치한다. [쿠키](security-and-operations.md)
- **B04** 정상·경계: 모든 POST는 정확한 Origin·JSON과 필요한 CSRF를 검사한다; 실패·복구: null·누락·위조 Origin을 거절한다; 수락: 최초 교환 예외는 해당 역할 쿠키가 실제 없을 때만 적용된다. [CSRF](security-and-operations.md)
- **B05** 정상·경계: stale 쿠키는 401과 삭제 후 bootstrap 한 번 재시도한다; 실패·복구: 유효 쿠키의 CSRF 오류는 403이며 세션을 유지한다; 수락: 두 오류가 새 참여자 생성·한도 우회로 섞이지 않는다. [오류 계약](backend-contracts.md)
- **B06** 정상·경계: 학생은 자기 제출만 조회하고 장치는 공개 표시 DTO만 받는다; 실패·복구: 남의 ID와 없는 ID는 같은 404다; 수락: 원문·참여자·내부 판정·세션 비밀의 역할별 노출 검사가 통과한다. [응답 분리](security-and-operations.md)
- **B07** 정상·경계: 세션 만료·해지·운영자 활성·현재 역할을 요청마다 검사한다; 실패·복구: 캐시된 옛 권한을 계속 허용하지 않는다; 수락: 회수 뒤 새 권한 판단이 실패한다. 학생의 당일 작성 정지는 자격 해지가 아니며 기존 결과·사용량 조회는 허용한다. [자격 회수](security-and-operations.md)
- **B08** 정상·경계: 학생 식별 30일과 짧은 QR 입장 허가를 분리한다; 실패·복구: 만료된 식별의 소유권을 새 ID에 이전하지 않는다; 수락: 재스캔·polling·heartbeat가 절대 세션 수명을 늘리지 않는다. [세션 수명](security-and-operations.md)

## C. QR 발급·교환·시간·키 — 8개

- **C01** 정상·경계: 등록 장치만 고정 HTTPS 호스트의 현재 QR을 받는다; 실패·복구: 학생·공개 파일·공개 이벤트에는 ticket을 넣지 않는다; 수락: QR GET은 입장·학생 ID·횟수를 변경하지 않는다. [QR 입장](security-and-operations.md)
- **C02** 정상·경계: 300초 구간 발급, 현재·직전 구간, exp=iat+600을 검사한다; 실패·복구: 미래·만료·이전 구간을 거절한다; 수락: 5분·10분 경계에 별도 leeway·최소 15분 허가가 없다. [QR 시간](security-and-operations.md)
- **C03** 정상·경계: HS256·typ·kid·issuer·audience·purpose·board를 고정한다; 실패·복구: none·다른 알고리즘·모르는 키·변조 claim을 거절한다; 수락: 외부 키 URL·파일 경로를 조회하지 않는다. [QR 서명](security-and-operations.md)
- **C04** 정상·경계: join fragment를 읽고 주소에서 지운 뒤 JSON으로 교환한다; 실패·복구: 유실·잘못된 ticket은 안내하고 초안을 유지한다; 수락: URL·분석·오류 로그에 ticket이 남지 않는다. [QR 전달](security-and-operations.md)
- **C05** 정상·경계: admission은 ticket.validUntil까지만 부여한다; 실패·복구: 늦은 직전 QR 교환은 더 긴 기존 허가를 단축하지 않는다; 수락: 동시 교환·재스캔·새로고침으로 기한을 임의 연장하지 않는다. [입장 갱신](data-and-recovery.md)
- **C06** 정상·경계: 입장 만료는 새 제출만 막고 접수 1시간 이내 유효 소유 세션의 기존 결과 조회는 허용한다; 실패·복구: 원고·같은 키를 보존하고 재스캔을 안내한다; 수락: 1시간 안의 key 재시도가 신규 접수로 바뀌지 않으며 이후 자동 재전송하지 않는다. [재시도](backend-contracts.md)
- **C07** 정상·경계: 학교 Wi-Fi·LTE·5G를 모두 허용한다; 실패·복구: IP 변경으로 참여자를 바꾸거나 현장 인증을 주장하지 않는다; 수락: 유효 QR 공유의 교외 참여 가능성과 쿠키 우회 한계를 안내한다. [접속망 결정](architecture.md)
- **C08** 정상·경계: 평상시 키 교체는 기존 exp까지만 공존한다; 실패·복구: 유출 시 구 키·기존 입장 허가를 폐기한다; 수락: 새 키 재시작·재스캔 후에도 학생 사용량·제재는 유지된다. [키 교체](security-and-operations.md)

## D. HTTP·입력·멱등성 — 9개

- **D01** 정상·경계: UTF-8 JSON 4 KiB·content 2 KiB·허용 필드만 받는다; 실패·복구: malformed·surrogate·초과 입력을 안전하게 거절한다; 수락: 400·413·415·422를 구분하고 제재를 올리지 않는다. [입력 정책](profanity-policy.md)
- **D02** 정상·경계: 지정 White_Space trim과 NFC 뒤 1~30 grapheme를 센다; 실패·복구: 공백만·31자·거대 결합 문자열을 거절한다; 수락: 한글 자모·결합 이모지의 프론트/서버 공통 예문이 일치한다. [Unicode 정책](profanity-policy.md)
- **D03** 정상·경계: 표시·DB·AI 입력은 같은 display_text다; 실패·복구: 검색 자모 뷰로 원문을 덮어쓰지 않는다; 수락: ZWJ·variation selector는 정상 보존하고 줄바꿈·일반/양방향 제어문자는 거절한다. [문장 정규화](profanity-policy.md)
- **D04** 정상·경계: 승인 문장은 HTML이 아닌 텍스트로 표시한다; 실패·복구: 마크업·URL·스크립트가 실행되지 않는다; 수락: CSP와 실제 빌드·SSE·애니메이션을 함께 확인한다. [출력 보안](security-and-operations.md)
- **D05** 정상·경계: v1 emojiId와 v2 감정 sceneId·animationId는 각각 서버의 고정 허용 목록을 쓴다; 실패·복구: 임의 HTML·외부 이미지 URL·알 수 없는 ID를 거절한다; 수락: v2 학생 입력은 content만 받으며 테마는 검수 결과에서 만든다. [HTTP 명세](openapi.json)
- **D06** 정상·경계: 신규 의도는 UUID Idempotency-Key와 정규화 본문 fingerprint를 사용한다; 실패·복구: 같은 키·다른 본문은 409다; 수락: 동시 재전송이 제출·쿼터 점유·job 하나만 만든다. [멱등 접수](data-and-recovery.md)
- **D07** 정상·경계: 기존 key 조회는 새 입장·정지·혼잡 검사보다 먼저다; 실패·복구: timeout·5xx·응답 유실에는 같은 키·본문을 유지한다; 수락: 처음 202·기존 요청 200이며 추가 차감·제재가 없다. [재전송 계약](backend-contracts.md)
- **D08** 정상·경계: 오류는 RFC 9457 Problem·안전한 code·requestId로 통일한다; 실패·복구: 프록시 HTML·빈 응답도 결과 미확정으로 처리한다; 수락: 예외·검증 응답과 로그에 입력·SQL·자격이 노출되지 않는다. [HTTP 오류](backend-contracts.md)
- **D09** 정상·경계: 202 후 자기 상태를 1초 뒤·이후 2초 간격으로 조회한다; 실패·복구: terminal에서 멈추고 네트워크 재시도는 제한한다; 수락: no-store·Location·Retry-After·405 Allow가 실제 상태와 맞는다. [조회·재시도](backend-contracts.md)

## E. 최근 60분 한도·쿨다운·당일 제재 — 9개

- **E01** 정상·경계: accepted_at의 열린 60분 하한과 닫힌 현재 상한으로 센다; 실패·복구: 정각·자정·승인 시각으로 reset하지 않는다; 수락: 정확히 60분이 된 접수가 창에서 빠진다. [rolling 조회](data-and-recovery.md)
- **E02** 정상·경계: v1 PENDING·PROCESSING·APPROVED와 v2 RESERVED·CONSUMED를 중복 없이 최근 60분에 합산한다; 실패·복구: v2 시작 전 실패는 반환하고 최초 시작 뒤 숨김·중단은 소비를 유지한다; 수락: 원문 삭제 뒤에도 학생·운영자·새 접수의 사용량이 일치한다. [쿼터](playback-local-implementation.md)
- **E03** 정상·경계: 같은 참여자의 접수·최종화를 참여자 행으로 직렬화한다; 실패·복구: 없는 daily_usage는 UPSERT 후 잠근다; 수락: 최초 동시 요청에도 검수 중 1개·최근 점유 3개 이하다. [잠금 순서](data-and-recovery.md)
- **E04** 정상·경계: 신규 의도는 10초 간격·참여자당 미확정 1개다; 실패·복구: 같은 키 재시도는 쿨다운에서 제외한다; 수락: 409·429에서 새 제출·job·위반이 생기지 않는다. [접수 트랜잭션](data-and-recovery.md)
- **E05** 정상·경계: 잠금 뒤 DB 시각으로 날짜·허가·정지·기한을 결정한다; 실패·복구: 자정 변경이면 전체 rollback 후 재시도한다; 수락: 시계 역행·잠금 지연에 날짜·accepted_at·기한이 모순되지 않는다. [DB 시간](data-and-recovery.md)
- **E06** 정상·경계: 3회 경고·5회 다음 KST 자정까지 정지다; 실패·복구: 위반 5회 확정은 같은 날 미확정만 취소한다; 수락: 승인 글은 유지하고 AI·입력·인프라 오류는 위반으로 세지 않는다. [당일 제재](data-and-recovery.md)
- **E07** 정상·경계: 접수 pause·미검수 혼잡·표시 혼잡을 구분한다; 실패·복구: soft gate 초과 가능성을 측정하고 기한 정리는 유지한다; 수락: 503 업무 code가 구분되고 기존 key 재조회는 가능하다. [혼잡 제어](delivery-and-operations.md)
- **E08** 정상·경계: Retry-After·nextSlotAt·nextSubmissionAt을 각각 계산한다; 실패·복구: 검수 결과로 일찍 반환될 수 있음을 안내한다; 수락: 쿼터·쿨다운·입장 만료·정지를 한 시간값으로 혼동하지 않는다. [사용량 응답](backend-contracts.md)
- **E09** 정상·경계: 보조 출처 제한은 세션·참여자 제한과 구분한다; 실패·복구: 신뢰 프록시 외 전달 헤더·공용 IP 제재 전파를 막는다; 수락: 속도 제한 재시도에 위반 수가 오르지 않고 망 전환이 정상이다. [남용 제한](security-and-operations.md)

## F. 금칙 사전·변형 검사·근거 — 8개

- **F01** 정상·경계: 표시·현대 자모 검색 뷰를 분리한다; 실패·복구: 옛한글·외국 문자를 임의 삭제하지 않는다; 수락: 같은 원문을 보존하면서 검토된 자모 규칙을 재현한다. [검색 뷰](profanity-policy.md)
- **F02** 정상·경계: 확정 사전은 토큰 경계·유한 접미사·예외를 포함한다; 실패·복구: 부분 문자열·한국어 정규식 경계를 맹신하지 않는다; 수락: 시발점·씨앗 발아 등 정상 예문에 자동 제재가 없다. [규칙 경계](profanity-policy.md)
- **F03** 정상·경계: 지정 구분기호와 위치당/전체 개수만 허용한다; 실패·복구: 임의 글자·무한 반복·전역 공백 삭제를 사용하지 않는다; 수락: 씨%발과 허용 범위 초과 사례를 함께 평가한다. [우회 표기](profanity-policy.md)
- **F04** 정상·경계: 삽입 자모는 규칙별 특정 위치에서만 허용한다; 실패·복구: 반복 문장 전체 축약·임의 편집거리로 확정하지 않는다; 수락: 씨ㅣ바ㄹ의 승인 fixture와 정상 포함형이 구분된다. [삽입 변형](profanity-policy.md)
- **F05** 정상·경계: CONFIRMED·CANDIDATE·NO_MATCH를 구분한다; 실패·복구: 후보는 AI로 넘기고 예외를 NO_MATCH로 바꾸지 않는다; 수락: 확정 규칙만 RuleRejected가 되고 여러 매칭도 제출당 제재 한 번이다. [필터 파이프라인](profanity-policy.md)
- **F06** 정상·경계: 사전 manifest·해시·출처·정규화 버전을 고정한다; 실패·복구: 손상·비어 있는 확정 사전·실행 오류는 ready 실패다; 수락: 접수 버전과 실행 bundle 불일치가 공개·제재 없이 끝난다. [사전 배포](profanity-policy.md)
- **F07** 정상·경계: 규칙 근거는 최대 5개 rule/variant ID만 저장한다; 실패·복구: 검색 문자열·매칭 조각·span·AI 사유를 섞지 않는다; 수락: REJECTED 근거가 재현 가능하며 학생·장치에는 노출되지 않는다. [근거 보관](profanity-policy.md)
- **F08** 정상·경계: 유한 실행·입력 크기·양성/음성 fixture로 검증한다; 실패·복구: 새 규칙의 오탐은 숨김·제재 복구와 버전 수정으로 처리한다; 수락: 사전별 오탐·탐지 누락·처리 비용을 실측한다. [필터 평가](profanity-policy.md)

## G. 멘션 AI·영속 큐·실행 종료 — 10개

- **G01** 정상·경계: 제출·last_submission_at·job을 같은 psycopg 연결로 저장한다; 실패·복구: enqueue·commit 실패면 모두 rollback한다; 수락: 강제 종료 전후 부분 접수·유령 job이 없다. [원자성](data-and-recovery.md)
- **G02** 정상·경계: 큐 payload는 제출 ID 중심이고 claim은 상태·버전을 확인한다; 실패·복구: terminal·중복 job은 모델 없이 no-op다; 수락: 새 run token·실행 횟수·기한이 DB에 원자적으로 기록된다. [작업 시작](data-and-recovery.md)
- **G03** 정상·경계: safety ALLOW/BLOCK/UNCERTAIN과 감정을 분리한다; 실패·복구: BLOCK·UNCERTAIN은 수정 요청·자동 제재 없음이다; 수락: 화남 감정 자체를 유해 판정으로 바꾸지 않는다. [AI 계약](ai-contract.md)
- **G04** 정상·경계: ALLOW 뒤 감정만 실패하면 NEUTRAL fallback을 허용한다; 실패·복구: 안전성 실패·NaN·잘못된 조합은 공개하지 않는다; 수락: RuleRejected·ModelAnalyzed·RunFailed가 별도 variant로 검증된다. [결과 envelope](ai-contract.md)
- **G05** 정상·경계: 최종화는 참여자→제재→제출 잠금 뒤 token·상태·정지를 재검사한다; 실패·복구: 늦은 옛 결과·중복 확정은 무시한다; 수락: 승인·위반·점유 반환이 한 번만 반영된다. [최종화](data-and-recovery.md)
- **G06** 정상·경계: 전체 기한은 min(접수+120초, 다음 KST 자정), 실행 lease는 최대 60초다; 실패·복구: 기한이 지나면 비공개 terminal이다; 수락: 잠금 대기 후 DB 시각 재검사와 EXPIRED 우선순위가 지켜진다. [시간 계약](ai-contract.md)
- **G07** 정상·경계: 고정 artifact·해시·사전·warmup 뒤 5초 갱신/15초 READY lease를 발급한다; 실패·복구: generation으로 옛 갱신을 차단한다; 수락: 프로세스 생존만으로 ready가 되지 않고 fake 운영을 거절한다. [작업자 준비](ai-contract.md)
- **G08** 정상·경계: 부모가 START/ACK/DONE과 남은 실행 예산을 감시한다; 실패·복구: TERM→5초→KILL·reap 뒤 컨테이너를 재시작한다; 수락: hang fixture의 CPU 프로세스가 실제 종료되고 다음 정상 글이 처리된다. [강제 종료](ai-contract.md)
- **G09** 정상·경계: 실행 전 일시 오류만 최대 2회, 새 token으로 재시도한다; 실패·복구: 실행 후 유실·timeout은 재추론하지 않는다; 수락: 최종화 응답 유실은 같은 결과로 확인하며 중복 CPU·제재가 없다. [재시도 정책](data-and-recovery.md)
- **G10** 정상·경계: 독립 루프가 업무 기한·stalled job을 정리한다; 실패·복구: 먼저 업무 terminal 커밋 후 지원 큐 API로 no-op 회수한다; 수락: 루프 중단·재시작에도 미확정 점유가 남지 않고 큐 내부 SQL을 수정하지 않는다. [중단 복구](data-and-recovery.md)

## H. DB 일관성·삭제·보관 — 8개

- **H01** 정상·경계: PK/FK/CHECK/부분 UNIQUE가 업무·실행 상태를 보강한다; 실패·복구: 잘못된 직접 저장을DB가 거절한다; 수락: DDL 부정 사례와 실제 동시성 시험을 따로 통과한다. [스키마](schema.sql)
- **H02** 정상·경계: 제재는 daily_usage, 개인 rolling 한도는 v1 제출·v2 영수증에서 유도한다; 실패·복구: 전광판 수용량 카운터와 개인 작성 한도를 혼동하지 않는다; 수락: 예약 해제는 한 번만 수행하고 운영자 제재 복구가 개인 소비를 되돌리지 않는다. [데이터 모델](playback-local-implementation.md)
- **H03** 정상·경계: 고정 SQL·파라미터와 짧은 트랜잭션을 사용한다; 실패·복구: 모델·SSE·외부 요청 중 잠금을 유지하지 않는다; 수락: timeout·교착 뒤 전체 rollback과 정상 풀 반환이 확인된다. [SQL 접근](backend-owner-plan.md)
- **H04** 정상·경계: 큐·참여자 경로와 표시 경로의 잠금 순서를 지킨다; 실패·복구: 표시 잠금 뒤 참여자 잠금을 추가하지 않는다; 수락: 접수·최종화·숨김·정리의 경합에 교착·부분 갱신이 없다. [잠금 표](data-and-recovery.md)
- **H05** 정상·경계: v2 원문은 최초 시작 승인 +1시간, 본문 없는 영수증은 접수 +24시간이다. v1은 접수 +1시간을 유지한다; 실패·복구: 시작 전 실패는 원문과 예약을 정리하며 제재·감사는 별도 보관한다; 수락: 기한 조회 제외·작은 원문 DELETE·당일 로컬 캐시·백업 잔존을 구분한다. [보관](playback-local-implementation.md)
- **H06** 정상·경계: 작은 배치로 큐→제출→집계→세션→참여자 참조를 정리한다; 실패·복구: 남은 FK는 RESTRICT로 막고 다음 주기에 재시도한다; 수락: 정리와 접수·최종화 경합에도 살아 있는 참조·멱등 결과가 보존된다. [정리 순서](data-and-recovery.md)
- **H07** 정상·경계: 일치성 조회로 점유 초과·기한 초과·연결 오류를 찾는다; 실패·복구: 승인 행을 임의 FAILED로 바꾸지 않고 접수를 중단해 조사한다; 수락: 원인·조치 기록과 불변식 재검사가 남는다. [데이터 진단](data-and-recovery.md)
- **H08** 정상·경계: 삭제·숨김·백업·재학습을 별도 취급한다; 실패·복구: 복원 후 삭제 정책을 재실행하고 외부 표시 사본 회수를 보장하지 않는다; 수락: 운영 원문이 자동 학습·Git·CI artifact로 유출되지 않는다. [데이터 경계](security-and-operations.md)

## I. 일반 말풍선·페이지·당일 캐시 — 9개

- **I01** 정상·경계: v2 접수·준비·시작·프레임·완료를 구분하며 실제 패널 시청 증명과 다르다; 실패·복구: 미표시 최대 10분·자정 경계를 지킨다; 수락: 원문 보관과 학생의 완료 카드가 분리된다. [상태](playback-design.md)
- **I02** 정상·경계: READY 확정 순서로 한 글씩 15초를 재생하고 완료 후 일반 말풍선으로 만든다; 실패·복구: 중단은 같은 글을 한 번만 재생하며 완료를 추측하지 않는다; 수락: 새 글이 현재 재생을 조기 교체하지 않고 정상 완료 뒤 다음 글을 고른다. [재생](playback-design.md)
- **I03** 정상·경계: v2 카드에 ID·acceptedAt·firstStartAuthorizedAt·contentDeleteAt·ambientUntil을 제공한다; 실패·복구: 중복 페이지를 ID로 합치고 이전 시도 응답으로 숨김을 되돌리지 않는다; 수락: 완료 이력과 현재 presentationStatus를 함께 확인한다. [렌더 계약](display-laptop.md)
- **I04** 정상·경계: 최초 시작 승인 +1시간에 DB 원문 공급이 끝나고 받은 내용은 로컬 AMBIENT로 자정까지 남는다; 실패·복구: 오프라인·재시도가 DB 수명을 늘리지 않는다; 수락: 삭제·빈 snapshot이 캐시 삭제로 이어지지 않는다. [수명](playback-design.md)
- **I05** 정상·경계: CHATBOT·PAUSED 전환은 현재 재생을 중단하고 복귀 시 복구를 판단한다; 실패·복구: 빠른 전환도 같은 모드 변경 트랜잭션에 중단을 기록한다; 수락: 실제 챗봇 없이 mode API로 검증하며 새 v2 접수는 활성 MOODWALL에서만 받는다. [mode](display-laptop.md)
- **I06** 정상·경계: 운영자 PAUSED는 기본 mode와 분리한다; 실패·복구: 장치가 해제하지 못하고 글·QR을 가린다; 수락: 늦은 GET·옛 장치 mode 요청이 중단을 되돌리지 않는다. [송출 중단](security-and-operations.md)
- **I07** 정상·경계: 자정에전날 공개상태·브라우저 캐시를 정리한다; 실패·복구: 정리 지연에도조회 날짜 필터가 전날 원문을 막는다; 수락: rolling쿼터는 자정에초기화되지않는다. [날짜](data-and-recovery.md)
- **I08** 정상·경계: 서버 카드와 당일 캐시를 합쳐 받은 말풍선 전체를 반환하며 브라우저 메모리 보호 상한은 별도로 둔다; 실패·복구: removedIds·cacheResetToken·deviceGeneration·날짜 변경은 캐시를 정리하되 목록 부재는 삭제 근거가 아니다; 수락: 4개·40개 순환 규칙 없이 1시간 뒤 내용 유지·인증 전 미표시·자정·저장소 실패를 확인한다. [말풍선 선택](../clients/display/README.md)
- **I09** 정상·경계: snapshot은 한 DB 스냅샷의 날짜·상태·개수·시간이다; 실패·복구: GET이 tick·lease·접수 상태를 갱신하지 않는다; 수락: 숨김·검수·자정 경합에도 혼합된 응답이 없다. [snapshot](backend-contracts.md)

## J. 전광판 장치·SSE·노트북 — 8개

- **J01** 정상·경계: ADMIN 발급 5분 단일 사용 pairing code를 노트북이 소비한다; 실패·복구: 응답 유실은 GET session으로 저장된 쿠키를 먼저 확인하고 없으면 새 코드로 복구한다; 수락: 재발급은 이전 코드를 취소하고 동시 소비는 한 번만 성공하며 원문 코드가 로그에 없다. [장치 등록](security-and-operations.md)
- **J02** 정상·경계: heartbeat·mode는 board 잠금 뒤 현재 hash/version/만료를 재검사한다; 실패·복구: 옛 요청은 재등록·해지 뒤 실패한다; 수락: 옛 heartbeat를 중단했다 재개해도 새 lease·mode가 변하지 않는다. [장치 경합](display-laptop.md)
- **J03** 정상·경계: SSE는 인증된 무효화 알림·15초 keepalive만 보낸다; 실패·복구: 5초 권한 재검사·버퍼 제한·5분 재연결로 정리한다; 수락: 느린 구독자·DB 오류·해지가 전송 누수·무한 메모리를 만들지 않는다. [SSE 운영](delivery-and-operations.md)
- **J04** 정상·경계: 단일 apply가 authEpoch·controlSeq·board·revision·조회 순번을 검사한다; 실패·복구: POST 뒤 GET 재조회와 401 clear로 옛 응답을 무효화한다; 수락: 같은 revision의 최신 대기 수는 적용하고 낮은 상태로 돌아가지 않는다. [응답 순서](display-laptop.md)
- **J05** 정상·경계: SSE 유실은 10초 REST 조회로 복구한다; 실패·복구: snapshot 30초 미갱신·QR exp·자정에는 글/QR을 가린다; 수락: navigator.onLine·로컬 시계만으로 정상 화면을 선언하지 않는다. [오래된 화면](display-laptop.md)
- **J06** 정상·경계: hidden은 heartbeat·애니메이션을 멈추고 visible은 재검증한다; 실패·복구: 절전·다중 탭·옛 SSE를 정리한다; 수락: 영속 프로필 단일 탭과 복귀 epoch에서 올바른 state·QR만 표시한다. [가시성](display-laptop.md)
- **J07** 정상·경계: 노트북은 공개 HTTPS 브라우저와 영상 출력만 맡는다; 실패·복구: 케이블·패널 전원·학교망 장애를 각각 점검한다; 수락: 휴대폰 LTE→클라우드→실제 패널 표시를 같은 장비에서 확인한다. [현장 연결](display-laptop.md)
- **J08** 정상·경계: 재부팅은 전원→망→브라우저→세션→최신 상태 순으로 복구한다; 실패·복구: 쿠키 소실은 재페어링하며 캐시된 글/QR을 복구하지 않는다; 수락: 절전·OS·키오스크·해상도·2시간 지속 시험을 기록한다. [노트북 운영](display-laptop.md)

## K. 운영자·목록·복구·감사 — 8개

- **K01** 정상·경계: 개인별 계정·로컬 차단 목록·Argon2id로 비밀번호를 관리한다; 실패·복구: CLI 복구·비활성화가 기존 세션을 해지한다; 수락: 짧은/유출 비밀번호 거절과 긴 passphrase·공백 지원을 검증한다. [운영자 인증](security-and-operations.md)
- **K02** 정상·경계: 로그인은 자격을 새로 발급하고 계정·출처 제한을 둔다; 실패·복구: 계정 존재를 구분하지 않는 오류·점진 지연을 제공한다; 수락: 재로그인 CSRF·stale 쿠키 규칙과 세션 고정 방어가 유지된다. [로그인](security-and-operations.md)
- **K03** 정상·경계: 절대 8시간·유휴 30분, 성공한 권한 POST만 활동을 갱신한다; 실패·복구: GET polling·로그아웃 뒤 세션을 연장하지 않는다; 수락: 현재 역할·활성·해지와 만료를 먼저 검사한다. [운영자 세션](security-and-operations.md)
- **K04** 정상·경계: ADMIN과 MODERATOR의 관리 권한을 구분한다; 실패·복구: 학생·장치·낮은 역할의 제어를 거절한다; 수락: pairing·회수·mode·intake·감사는 ADMIN만, 숨김·당일 복구는 양쪽만 가능하다. [역할 표](security-and-operations.md)
- **K05** 정상·경계: 오늘 승인 글의 원문이 있으면 HIDDEN과 감사를 함께 저장하고, DB 삭제 뒤에는 원문 없는 당일 숨김 감사를 저장한다; 실패·복구: 같은 전광판·날짜·ID의 재시도는 재감사 없이 200으로 수렴한다; 수락: removedIds로 로컬 말풍선도 제거되며 rolling 사용량·원래 판정·DB 보관은 바뀌지 않는다. [숨김](data-and-recovery.md)
- **K06** 정상·경계: 당일 제재 복구는 최신 상태 조회·명시한 명령과 같은 트랜잭션의 감사다; 실패·복구: 중복·낡은 복구 요청이 새 위반을 지우지 않는다; 수락: 최근 60분 사용량·과거 strike·거절 글 공개 상태가 바뀌지 않는다. [운영 복구 계약](backend-contracts.md)
- **K07** 정상·경계: 제출·감사 목록은 고정 날짜·안정 정렬·HMAC 커서를 쓴다; 실패·복구: 만료·변조·필터 변경은 422, 새 첫 페이지로 복구한다; 수락: 추가·삭제·자정에서도 커서 권한과 bigint 문자열 정밀도가 유지된다. [목록 계약](backend-contracts.md)
- **K08** 정상·경계: 관리 동작의 actor·대상·고정 reasonCode·이전/이후·시각을 남긴다; 실패·복구: 감사 실패면 함께 rollback하며 대상이 삭제돼도 감사 사건은 남는다; 수락: 원문·토큰·자유 입력 사유가 감사에 복제되지 않고 삭제된 대상의 Usage 조회·복구는 404다. [감사](security-and-operations.md)

## L. 배포·관측·장애·복원 — 9개

- **L01** 정상·경계: live·API ready·모델 ready·제어 루프 성공을 분리한다; 실패·복구: 모델 장애는 신규 접수만 막고 조회·복구를 유지한다; 수락: health에 내부 비밀을 노출하거나 단순 DB 장애로 재시작 폭주하지 않는다. [준비 상태](delivery-and-operations.md)
- **L02** 정상·경계: TLS·같은 origin·프록시 신뢰·DB 인증서와 사설/제한망을 사용한다; 실패·복구: API·DB 직접 공개와 위조 전달 헤더를 차단한다; 수락: 실제 브라우저·학교망에서 인증서·SSE 버퍼·유휴 timeout이 검증된다. [배포 보안](security-and-operations.md)
- **L03** 정상·경계: 이미지·모델·사전·앱/큐 revision을 한 release로 고정한다; 실패·복구: 신규 접수 중단 후 구 업무를 drain/terminal 처리해 PENDING·PROCESSING 0개와 구 worker 종료를 확인한다; 수락: 버전이 다른 새 worker가 옛 글을 조용히 재판정하지 않는다. [배포 순서](delivery-and-operations.md)
- **L04** 정상·경계: migration은 한 실행자·호환 추가 변경을 우선한다; 실패·복구: 이전 이미지 복귀와 schema 복원을 구분한다; 수락: 파괴 변경에는 forward fix/격리 복원 지점·소요를 미리 확인한다. [migration](delivery-and-operations.md)
- **L05** 정상·경계: 요청·큐·판정·장치·루프·DB pool·메모리 지표를 모은다; 실패·복구: 고정 경보와 담당자 조치·복구를 기록한다; 수락: 원문·토큰·개인 ID metric label 없이 실제 장애를 찾는다. [관측](delivery-and-operations.md)
- **L06** 정상·경계: 실제 CPU 모델 p95·RSS·혼잡·화면 용량을 측정한다; 실패·복구: 과부하에 제한·만료·반환·다음 정상 글 복구를 확인한다; 수락: fake 성능을 실제 모델 성능으로 제시하지 않는다. [부하 수락](delivery-and-operations.md)
- **L07** 정상·경계: 일일 암호화 외부 백업·7일 보관·RPO 24시간/RTO 4시간을 목표로 한다; 실패·복구: 백업 누락과 복원 실패를 조사한다; 수락: 별도 DB 복원 기록과 학교의 허용 유실 폭을 확보한다. [백업 목표](delivery-and-operations.md)
- **L08** 정상·경계: 격리 복원 후 임시 자격·readiness·lease·admission을 정리한다; 실패·복구: 옛 worker 종료·stale job terminal화·재페어링·smoke 후 재개한다; 수락: 옛 READY·입장·운영자·장치가 부활하지 않는다. [복원 순서](delivery-and-operations.md)
- **L09** 정상·경계: PR·계약·정적·실제 PG·파이프라인·현장 시험으로 합친다; 실패·복구: 필수 조건 미달이면 실제 학생 공개를 보류한다; 수락: commit·artifact·실행자·날짜·결과가 있고 미실행은 통과로 바뀌지 않는다. [출시 검증](verification.md)

## 아직 필요한 출시 입력과 판단

이 목록은 설계가 비어 있다는 뜻이 아니라, 실제 값을 넣고 실행해야 완료되는 입력이다. 챗봇 형태의 미정은 이 목록의 필수 입력에 포함하지 않는다.

| 입력 | 책임 | 완료 증거 |
| --- | --- | --- |
| 실제 참여 규모·운영 시간·담당 관리자·허용 장애/유실 폭 | 팀·학교 | 부하 프로필·화면 처리량·운영 연락 책임과 RPO 선택 기록 |
| 클라우드 공급자·VM CPU/RAM·관리형 PG·영속 저장·접근 권한 | 일반 백엔드 | 실제 모델 측정에 맞는 자원·DB 연결 예산·백업·TLS 검증 |
| 도메인·DNS·PUBLIC_ORIGIN·BOARD_ID·비밀 주입·키 교체 | 일반 백엔드 | 운영 설정·초기화·실제 HTTPS 및 복원 절차 기록 |
| 활성 확정 사전·정규화 표·라이선스·양성/음성 fixture | 일반 백엔드, AI 교차 검토 | 해시·버전·근거 ID·오탐/누락·실행 비용 보고서 |
| 멘션 검수/감정 artifact·어댑터·고정 평가·CPU 실측 | AI, 일반 백엔드 연결 | [AI 전달물](ai-team-request.md)과 준비·기한·오류·복구 수락 |
| 노트북 OS·프로필·케이블·패널·학교 외부 HTTPS/SSE 허용 | 현장 담당·전광판 프론트 | 같은 장비·망에서 LTE 작성·단절·재부팅·지속 시험 |
| 원문 공개·보관·제재 이의 처리와 관리자 사용 정책 | 학교·팀 | 학생 안내·운영자 권한·숨김·복구·데이터 보관 승인 기록 |
| 애플리케이션 구현·migration·CI·배포·복구 실행 | 해당 담당자 | 각 leaf의 수락 로그와 [출시 시험](verification.md)의 실제 결과 |

## M. 외부 API 연결 전의 검수 구현

- **M01** 정상·경계: 지원 사유는 일반 수정 안내보다 먼저 표시하고 원문 purge 뒤에도 영수증에 남는다; 실패·복구: 구버전 클라이언트는 업데이트를 요구한다; 수락: 소유권·멱등 복구·프로필 전환 뒤에도 지원 화면 계약을 지킨다. [구현 가이드](gemini-moderation-implementation.md)
- **M02** 정상·경계: 합성 fixture mock은 개발·테스트 전용이며 알 수 없는 글은 보류한다; 실패·복구: 실제 API transport가 없으면 접수를 닫는다; 수락: 단일 dispatch·실행 token·릴리스·프로세스 종료 경계를 실제 DB와 Linux에서 검증한다. [검수 계약](gemini-moderation-design.md)
- **M03** 정상·경계: 프로젝트 공용 예산·쿼터·수동 중단·회로를 DB에서 직렬화한다; 실패·복구: UNKNOWN 비용을 보수 처리하고 간격을 둔 합성 probe로 복구한다; 수락: 운영자는 모의 상태와 중단·복구를 확인할 수 있고 실제 과금으로 오인하지 않는다. [운영](gemini-moderation-operations.md)
- **M04** 정상·경계: 신고는 고정 분류와 공개 글 ID만 받으며 원문을 복제하지 않는다; 실패·복구: 한도·소유 범위·멱등·7일 정리를 적용한다; 수락: 실제 숨김·송출 중단 전에 해당 처리 완료를 기록할 수 없다. [구현](gemini-moderation-implementation.md)

## HTTP operationId와 가지 연결

아래는 API 하나가 어떤 수락 범위를 구현해야 하는지 연결한 표다. 각 operationId는 한 번만 등장하며 CLI·worker·OS 작업은 HTTP API를 억지로 추가하지 않고 위 가지에서 검증한다. 표에 연결됐다는 사실은 실제 endpoint 구현·호출 성공을 뜻하지 않는다.

| operationId | leaf IDs |
| --- | --- |
| `liveness` | `A08`, `L01` |
| `readiness` | `A03`, `A06`, `A07`, `A08`, `L01` |
| `emojiOptions` | `D05`, `D09` |
| `exchangeAdmission` | `B01`, `B04`, `B05`, `C02`, `C03`, `C04`, `C05`, `C07`, `C08` |
| `participantSession` | `B02`, `B03`, `B07`, `B08`, `C06` |
| `participantUsage` | `B06`, `E01`, `E02`, `E04`, `E06`, `E08` |
| `submitMention` | `D01`, `D02`, `D03`, `D05`, `D06`, `D07`, `D08`, `E03`, `E04`, `E05`, `E07`, `E09`, `G01` |
| `getOwnSubmission` | `B06`, `C06`, `D09`, `G05`, `I01` |
| `submitMentionV2` | `D01`, `D02`, `D03`, `D06`, `D07`, `D08`, `E03`, `E04`, `E05`, `E07`, `E09`, `G01`, `H01` |
| `getOwnSubmissionV2` | `B06`, `C06`, `D09`, `G05`, `H05`, `I01` |
| `lookupSubmissionReceipt` | `B06`, `D06`, `D07`, `D08`, `H05` |
| `getDisplayPlayback` | `B06`, `I01`, `I09`, `J02`, `J04` |
| `getDisplayStateV2` | `B06`, `I03`, `I04`, `I07`, `I08`, `I09`, `J04` |
| `claimDisplayPlayback` | `D06`, `D07`, `H01`, `H04`, `I02`, `J02`, `J06` |
| `startDisplayPlayback` | `D06`, `D07`, `H01`, `H04`, `I02`, `I04`, `J02` |
| `frameDisplayPlayback` | `D06`, `D07`, `H01`, `I02`, `J02`, `J04` |
| `completeDisplayPlayback` | `D06`, `D07`, `H01`, `I02`, `I03`, `J02`, `J06` |
| `interruptDisplayPlayback` | `D06`, `D07`, `H01`, `I05`, `I06`, `J02`, `J06` |
| `pairDisplay` | `B03`, `B04`, `B05`, `J01`, `J02` |
| `getDisplaySession` | `B02`, `B07`, `J04`, `J06` |
| `getCurrentQr` | `B06`, `C01`, `C02`, `C03`, `J05` |
| `getDisplayState` | `B06`, `I01`, `I03`, `I07`, `I08`, `I09`, `J04` |
| `displayEvents` | `J03`, `J04`, `J05`, `L02` |
| `displayHeartbeat` | `I04`, `J02`, `J06` |
| `setDeviceMode` | `I04`, `I05`, `I06`, `J02`, `J04` |
| `operatorLogin` | `B04`, `B05`, `K01`, `K02`, `K03` |
| `getOperatorSession` | `B07`, `K03`, `K04` |
| `operatorLogout` | `B03`, `B04`, `B07`, `K03` |
| `createPairingCode` | `J01`, `K04`, `K08` |
| `revokeDisplay` | `J02`, `J03`, `J04`, `K04`, `K08` |
| `setOperatorMode` | `A05`, `I04`, `I06`, `J04`, `K04`, `K08` |
| `listOperatorSubmissions` | `B06`, `F07`, `K04`, `K07` |
| `hideSubmission` | `E02`, `I03`, `I08`, `K04`, `K05`, `K08` |
| `restoreDailyRestriction` | `E06`, `H02`, `K04`, `K06`, `K08` |
| `listAudit` | `H05`, `K04`, `K07`, `K08` |
| `setIntakePause` | `A05`, `E07`, `K04`, `K08`, `L03` |
| `getOperatorParticipantUsage` | `B06`, `E01`, `E02`, `E06`, `H02`, `K04`, `K06` |
| `submitContentReport` | `B06`, `C06`, `M04` |
| `listContentReports` | `K04`, `K07`, `M04` |
| `resolveContentReport` | `K04`, `K08`, `M04` |
| `getModerationProvider` | `K04`, `M03` |
| `setModerationProviderPause` | `K04`, `K08`, `M03` |
