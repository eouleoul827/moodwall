# 추가 설계·코드 검수 결과

검토일: 2026-10-29. 앞선 설계 검토에 실제 백엔드 코드 검수와 DB 최적화 결과를 추가했다. QR·작성·검수·표시 흐름과 FastAPI·Python 작업자·PostgreSQL 선택은 유지했다. 현재 실행 증거는 아래 추가 검수와 [구현 상태](implementation-status.md)를 따른다. 실제 모델 평가·React 연동·운영 배포는 별도 남은 작업이다.

**이 기록 이후 정책 변경:** 원문·제출 행과 멱등 응답은접수 1시간이다. 현재는 강조·4개 자리·표시 대기 없이승인 즉시일반 말풍선을공개한다. 받은 글은1시간 뒤내용담긴투명 말풍선으로다음 KST 자정까지로컬 유지한다. 아래10분강조·30일보관·OpenAPI0.4.0·193개시험은당시기록이다. 실행계획·벤치마크는과거쿼리의증거로보존하며현재 검증을대신하지않는다. [전체 흐름](service-flow.md)·[구현 상태](implementation-status.md)·OpenAPI 0.7.0을따른다.

[전체 분기와 수락 조건](backend-completeness-tree.md)에 정상·경계·실패·복구 102개 항목과 HTTP 27개 연결을 정리했다. 첫 제품은 독립 무드월이며 대화 챗봇 모델·UI·연동은 제외한다. 멘션의 공개 적합성·감정 분류 AI는 포함한다.

## 공식 기준별 대조

OWASP 번호는 변경될 수 있는 master가 아니라 **ASVS v5.0.0 태그**를 기준으로 고정한다. 아래는 관련 통제의 설계 대조이며 ASVS 등급 인증·전체 요구사항 충족 선언이 아니다. 문서와 DDL이 있어도 실제 서비스 계층에서 시험하기 전에는 구현 통과가 아니다.

| 공식 기준 | Moodwall 설계에 반영한 것 | 구현에서 남은 증거 |
| --- | --- | --- |
| ASVS 2.1.1·2.1.3·2.2.1·2.2.2 — 입력·업무 제한·서버 검증 | JSON·Unicode·emoji 허용 목록, 쿼터·기한·제재의 문서화와 서버 권한 | V05·V06·V15·V23·V35·V50. [ASVS V2](https://raw.githubusercontent.com/OWASP/ASVS/v5.0.0/5.0/en/0x11-V2-Validation-and-Business-Logic.md) |
| ASVS 2.3.1·2.3.3·2.3.4 — 순서·트랜잭션·동시 점유 | 검수 전 비공개, 같은 연결의 제출+job, 참여자 우선 잠금 | V07~V13·V33·V41. DDL 검사가 실제 동시성 시험을 대체하지 않음. [ASVS V2](https://raw.githubusercontent.com/OWASP/ASVS/v5.0.0/5.0/en/0x11-V2-Validation-and-Business-Logic.md) |
| ASVS 7.1.1·7.2.2·7.2.3·7.3.1·7.3.2·7.4.1 — 동적 세션·수명·회수 | 무작위 역할별 쿠키·해시, 절대/유휴 만료, stale와 CSRF 구분 | V24·V34·V36·V49·V51. 숫자·쿠키 수명은 팀의 선택. [ASVS V7](https://raw.githubusercontent.com/OWASP/ASVS/v5.0.0/5.0/en/0x16-V7-Session-Management.md) |
| ASVS 8.1.1·8.1.2·8.2.1~8.2.3·8.3.1 — 기능·객체·필드 권한 | 자기 글 조회, 운영자 역할, 장치 DTO, 업무 잠금 뒤 현재 자격 확인 | V21·V22·V24·V51. 요청마다 권한 검사, 이미 선형화된 업무의 완료와 새 판단을 구분. [ASVS V8](https://raw.githubusercontent.com/OWASP/ASVS/v5.0.0/5.0/en/0x17-V8-Authorization.md) |
| ASVS 16.1.1·16.2.5·16.3.1~16.3.3·16.5.1~16.5.3 — 로그·오류·안전한 실패 | 원문·비밀 없는 요청 ID와 감사, 인증·권한 실패 기록, 검수 오류 비공개 | V16·V25·V35·V53·V54. 실제 로그 내용·접근·정리·경보 확인. [ASVS V16](https://raw.githubusercontent.com/OWASP/ASVS/v5.0.0/5.0/en/0x25-V16-Security-Logging-and-Error-Handling.md) |
| RFC 9110·9457·8259 — HTTP·Problem·JSON | 202+상태 조회, 405 Allow, Problem, UTF-8 JSON. 중복 이름 거절은 파서 간 모호성을 없애는 팀 정책 | V35·V50, 실제 프록시·handler 검증. [HTTP](https://www.rfc-editor.org/rfc/rfc9110.html), [Problem](https://www.rfc-editor.org/rfc/rfc9457.html), [JSON](https://www.rfc-editor.org/rfc/rfc8259.html) |
| Unicode UAX 15·29 — NFC·확장 grapheme | 표시 원문과 검색 뷰 분리, 사용자에게 보이는 30자 기준 | V15·V44, 서버·브라우저 버전과 공통 fixture를 고정. [NFC](https://www.unicode.org/reports/tr15/), [분절](https://www.unicode.org/reports/tr29/) |
| PostgreSQL·Procrastinate 공식 계약 | 짧은 트랜잭션·동일 잠금 순서·부분 UNIQUE, 같은 psycopg 연결의 enqueue, task at-least-once를 견디는 최종화 | V10·V11·V32·V41·V48·V55. [잠금](https://www.postgresql.org/docs/16/explicit-locking.html), [외부 연결](https://procrastinate.readthedocs.io/en/stable/howto/production/external_connection.html), [종료](https://procrastinate.readthedocs.io/en/stable/howto/advanced/shutdown.html) |

`Idempotency-Key` 문서는 검토일 현재 **활성 Internet-Draft**이며 RFC가 아니다. 초안의 wire 타입은 따옴표를 쓰는 structured string이다. Moodwall 0.3.0은 raw UUID를 쓰는 프로젝트 계약으로, 초안 적합성을 주장하지 않는다. 키 범위·본문 충돌·보관 기간·응답 유실 정책을 명시하는 원칙을 참고했다. [IETF 초안 상태와 본문](https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/)

## 보완한 빈틈

| 발견한 문제 | 결정과 근거 | 구현에서 확인할 증거 |
| --- | --- | --- |
| 60초 실행이 끝나도 120초 접수 기한만 보면 늦은 결과가 승인될 수 있음 | 최종화 때 잠금 획득 뒤 DB 시각으로 실행 lease와 전체 기한 모두 검사. PostgreSQL의 시각·잠금 의미에 맞춰 단일 기준 시각 사용 | 임대 종료 뒤 같은 token 결과도 승인·위반 없음. DDL 일부 제약은 실제 DB 검사 완료 |
| stalled heartbeat가 끊겼다는 사실로 CPU가 종료됐다고 가정할 위험 | 모델 실행 후 lost·timeout은 terminal 실패. 기존 모델을 다시 실행하지 않고 큐는 terminal 제출의 no-op로 정리 | 고아 프로세스·중복 task·늦은 결과·다음 정상 요청의 복구 시험 |
| 자정을 기다리는 잠금과 배치 제출 우선 잠금의 경합 | 새 접수는 잠금 뒤 날짜가 바뀌면 전체 재시도. 배치는 후보만 먼저 읽고 참여자부터 잠금 | 자정 경합·숨김·만료 정리·최종화 동시 시험 |
| 오류 body와 상태·재시도 의미가 충분히 고정되지 않음 | RFC 9457 Problem으로 통일, 업무 code·429/503·전송 결과 미확정과 같은 키 재전송 정의 | OpenAPI 예시 검증 완료. 실제 HTTP handler·프록시는 구현 후 확인 |
| 현재 QR·CSRF·쿠키 교체·SSE의 인증 경계가 구현마다 달라질 수 있음 | QR JWS 알고리즘·claims·키 교체, bootstrap CSRF, stale401와CSRF403, 5초 재인증·5분 SSE 수명 정의 | 실제 브라우저의 쿠키·SSE Origin 누락·키 교체·해지 전파 시험 |
| 목록 순서와 화면의 여러 조회가 일관되지 않을 수 있음 | keyset 커서와 고정 필터, 단일 snapshot, 역순 응답 방지와 같은 revision 적용 정의 | 페이지 변경·동시 숨김·늦은 HTTP 응답 시험 |
| 승인된 표시 대기 120건만으로 실제 AI 용량을 제어할 수 없음 | 미검수 queue와 표시 queue 분리. 실제 서버의 p95와 잔여 기한으로 별도 soft gate | 실제 모델·burst에서 queue age, 만료, 초과 접수 폭 측정 |
| 서버가 켜지거나 백업 설정이 있다는 사실만으로 운영 성공을 판단할 위험 | API·모델 readiness 분리, 풀 총합·timeout, migration·rollback·복원·SLI 분모 정의 | 릴리스별 기록과 복원·부하 보고서. 현재는 수락 기준 작성 단계 |
| 사전 거절도 AI 결과 형식으로 저장하려 해 가짜 모델 판정이 필요해짐 | RuleRejected·ModelAnalyzed·RunFailed를 분리하고 REJECTED의 모델 필드를 DB에서 금지 | Pydantic tagged union과 실제 PG CHECK 검사 완료. 실제 필터·최종화 연결은 V52 |
| 복구 성공 응답 유실 후 재시도하면 나중 위반까지 다시 지울 수 있음 | 복구 명령 UUID·hash를 감사와 원자 저장, 기존 명령은 현재 Usage만 조회 | 감사 UNIQUE·필드 검사 완료. 실제 HTTP 중복·새 위반 보존은 V53 |
| 옛 장치 요청이 잠금 대기 후 새 장치의 lease를 덮을 수 있음 | board 잠금 뒤 현재 hash·version·expiry 재검사, 클라이언트 epoch 적용 | V51·V56의 지연 요청과 인증 오류 시험 |
| 구 버전 대기 작업이 배포 뒤 새 모델로 처리될 수 있음 | intake 중단 뒤 구 PENDING·PROCESSING을 모두 종료한 다음 worker 교체, manifest 비교 | V54. 추론 실패를 새 모델 재시도로 감추지 않음 |
| 슬롯 위치·규칙 근거·제재 복구 응답의 필드가 명세에서 빠짐 | slot·RuleEvidence·RestrictionRestoreResult·현재 Usage 조회를 OpenAPI 0.3.0으로 고정 | DTO 양성·음성 검사 완료. 화면·권한·DB 매핑은 V52·V53·V56 |
| 초기 DB·복원 DB에 ready·인증·입장이 남아 재개될 수 있음 | 중단된 CLI 초기화와 격리 복원 시 임시 권한·lease 폐기 | V49·V57, 실제 운영자·장치 재등록과 smoke 확인 |

PostgreSQL의 [시각 함수](https://www.postgresql.org/docs/16/functions-datetime.html#FUNCTIONS-DATETIME-CURRENT)·[잠금](https://www.postgresql.org/docs/16/explicit-locking.html), Procrastinate의 [stalled 작업 회수](https://procrastinate.readthedocs.io/en/stable/howto/production/retry_stalled_jobs.html)를 확인했다. 애플리케이션 상태와 큐 실행을 따로 관리하는 방식은 이 프로젝트의 안전한 실패 정책이며 라이브러리가 자동 보장하는 동작으로 표현하지 않았다.

HTTP 오류는 [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html), 보안은 [OWASP 세션](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)·[CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html), 운영 지표는 [Google SRE의 SLI·SLO 정의](https://sre.google/sre-book/service-level-objectives/)와 비교했다. 모든 문서가 OWASP 전체를 준수한다는 선언이나 인증은 아니다.

## 실제 코드 추가 검수와 DB 최적화

2026-10-29 구현 후 소스를 세 갈래로 나눠 교차 검수했다. 아래는 실제 수정한 범위다. PostgreSQL 공식 문서·OWASP 권고와 비교했으며 제품 전체의 표준 인증이나 운영 성능 보장은 아니다.

| 우선순위 | 발견한 문제 | 적용한 수정 |
| --- | --- | --- |
| P1 | 최초 DB claim 실패가 재시도 구간 밖에 있어 작업이 즉시 실패 | 모델 시작 전 claim을 한 번 재시도. 불명확한 commit 뒤 PROCESSING이면 재추론하지 않음 |
| P1 | 모델 leader만 죽으면 TERM을 무시하는 helper가 남을 수 있음 | leader 생존과 별도로 그룹 TERM→KILL, Linux subreaper로 helper 회수 |
| P2 | 운영자/전광판 FOR UPDATE와 자식 테이블 FK의 KEY SHARE가 역순 대기 | 키를 바꾸지 않는 부모 잠금을 FOR NO KEY UPDATE로 변경. 동일 부모의 쓰기 직렬화는 유지 |
| P2 | Usage 여러 SELECT 사이에 검수 결과가 바뀌어 응답 수치가 섞임 | 학생 session·Usage를 짧은 읽기 전용 REPEATABLE READ로 처리 |
| P2 | 매 tick 하루의 모든 말풍선을 읽고 잠금 | 현재 spotlight 최대 4개와 과거 공개 글 최대 100개만 정산. 화면에서는 오늘 날짜만 조회 |
| P2 | 하루 전체 원문 로드·정렬 뒤 Python에서 40개 선택 | 부분 인덱스에서 ID·시각을 먼저 페이지 처리한 뒤 선택한 원문만 PK 조회 |
| P2 | 일반 401이 늦게 도착하면 새 인증 쿠키를 삭제 | 쿠키 삭제를 동일 역할 bootstrap으로 제한. 프론트의 bootstrap·logout 직렬화 계약 명시 |
| P2 | 설정 오류 문자열에 비밀 입력 일부 노출 가능, 보안 이벤트 로그 부재 | Pydantic 입력 은닉과 DSN repr 은닉, 비밀을 제외한 제한된 구조화 로그 추가 |
| P2 | wheel 설치본에 사전·API 계약·migration 누락 | app 내부 package-data로 포함하고 저장소 밖 로딩 검증 |

추가로 중복 HTTP 헤더 보존, 보이지 않는 문자만 있는 멘션 거절, 과거 날짜 제재 복구의 409 계약을 보완했다. 가족 이모지와 한글 자모 입력은 유지한다.

DB 구조는 PostgreSQL 한 곳을 원본으로 유지한다. `participants`는 익명 참여자, `browser_sessions`는 해시 토큰과 QR 입장 기한, `submissions`는 접수·검수·표시의 원본이다. `daily_usage`는 일별 제재만 관리하며 최근 60분 작성 횟수는 제출 상태에서 계산한다. 서로 다른 기준의 수치를 이중 저장해 어긋나게 만들지 않는다. `board_runtime`과 운영자 감사는 별도로 관리한다. Redis는 이번 수정의 필수 구성으로 추가하지 않았다.

| DB 보장/조회 | 현재 구현 |
| --- | --- |
| 같은 제출 재전송 | `(participant_id,idempotency_key)` UNIQUE와 request hash 비교 |
| 한 참여자 동시 검수 1개 | PENDING/PROCESSING 부분 UNIQUE, 참여자 잠금 아래 제한 판정 |
| 한 슬롯에 한 카드 | `(board_id,spotlight_slot)` 부분 UNIQUE |
| 접수·작업 등록 원자성 | 동일 DB 트랜잭션/연결로 제출과 Procrastinate job 등록 |
| FK와 상태 무결성 | 일자별 참여자 composite FK, 만료/검수 token/공개 상태 CHECK. 실제 30 grapheme 제한은 앱 검증 책임 |
| 최근 60분 한도 | 참여자·accepted_at 부분 인덱스. 동적으로 변하는 now()를 인덱스 조건에 넣지 않음 |
| 대기열/말풍선 정렬 | board·business_date·finalized_at·id 인덱스. 신규 ambient 전용 부분 인덱스는 0003으로 추가 |
| 보관 | 원문 30일·감사 90일, 작은 배치 정리. 자정에는 화면을 비우고 원문은 보관 정책을 따름 |

새 인덱스는 [0003 migration](../backend/app/migrations/versions/0003_ambient_order.py)에 있다. 기존 0001 SQL은 바꾸지 않았다. 개발 DB도 `python -m app.migrate`/Compose migrate로 head를 적용한다. 이번 인덱스 생성은 트랜잭션 방식이므로 기존 데이터가 있는 배포는 접수를 중단한 점검 시간에 수행한다. 큰 운영 테이블에 무중단 적용할 때는 별도의 CONCURRENTLY migration을 검토한다.

합성 데이터 10000개, PostgreSQL 17.8, VACUUM ANALYZE 후의 실제 결과다. [재현 도구](../backend/tools/explain_display.py)는 `moodwall_review_test`라는 별도 DB만 허용하며 실제 학생 데이터를 쓰지 않는다.

| 조회 | 반환 행 | 실행 시간 | 실행 계획 |
| --- | ---: | ---: | --- |
| 이전 전체 snapshot | 10000 | 21.215ms | 전체 scan + 3483KB sort |
| 변경, offset 0 | 40 | 1.494ms | ID Index Only Scan + PK 조회 40회 |
| 변경, offset 5000 | 40 | 1.652ms | ID 5040개 순회 + PK 조회 40회 |
| 변경, offset 9960 | 40 | 2.972ms | ID 10000개 순회 + PK 조회 40회 |

COUNT와 깊은 OFFSET의 인덱스 순회는 여전히 O(n)이다. 모든 물리 I/O가 40행으로 고정된다는 뜻은 아니며, index-only의 heap fetch는 autovacuum/visibility map 상태에 영향을 받는다. 이번 개선의 목적은 불필요한 원문 전송·정렬·애플리케이션 메모리와 행 잠금을 줄이는 것이다. 시간은 캐시가 있는 로컬 단일 측정이며 네트워크·실제 모델·동시 접속을 포함한 p95 수치가 아니다. 실제 학교 부하에서 지표를 본 뒤 집계 캐시·별도 read model 여부를 결정한다.

수정 후 전체 회귀·Linux 경계·wheel 시험의 실행 환경과 한계는 [구현 상태](implementation-status.md)에 기록했다. 새 운영 서버의 HTTPS·백업 복원·모니터링, AI 평가와 React 연동은 별도 남은 작업이다.

공식 근거: [PostgreSQL 행 잠금](https://www.postgresql.org/docs/16/explicit-locking.html#LOCKING-ROWS), [트랜잭션 격리](https://www.postgresql.org/docs/16/transaction-iso.html), [부분 인덱스](https://www.postgresql.org/docs/16/indexes-partial.html), [EXPLAIN](https://www.postgresql.org/docs/16/using-explain.html), [OWASP 보안 로그](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html), [Pydantic 오류 입력 은닉](https://docs.pydantic.dev/latest/api/config/#pydantic.config.ConfigDict.hide_input_in_errors).

## 유지한 사용자 정책

- LTE·5G와 학교 Wi-Fi를 모두 허용한다. 유효 QR 공유에 따른 교외 참여 차단은 보장하지 않는다.
- QR은 5분 교체, 현재·직전 인정, 발급 후 10분 절대 만료다. 익명 식별 세션과 짧은 작성 허가는 별개다.
- 최근 60분에 승인·검수 중 접수 3회, 동시 검수 1개, 신규 접수 간격 10초다. 확정 금칙 위반만 한국 날짜별 3회 경고·5회 정지다.
- 새 글은 4개 동시로 약 10분 강조하고 오늘의 말풍선으로 남긴다. 자정은 화면 초기화이며 기록은 보관 정책대로 유지한다.

숫자·보관 기간·프레임워크·queue soft gate는 팀의 선택이다. 표준이 학교 서비스에 반드시 10분 QR·시간당 3회·특정 언어를 요구하지 않는다. 표준 근거와 선택의 구분은 [백엔드 계약](backend-contracts.md)에 모았다.

## 팀이 읽고 구현할 순서

1. 일반 백엔드는 [클래스·오류·조회 계약](backend-contracts.md)과 [DB·잠금](data-and-recovery.md)을 기준으로 제출을 구현한다. AI 담당은 [추론 계약](ai-contract.md)의 fake 어댑터부터 연결한다.
2. 프론트 두 명은 [OpenAPI 0.4.0](openapi.json)의 Problem·202·같은 키 재전송·QR 만료·10분 spotlight·snapshot을 함께 확인한다.
3. 실제 학생에게 공개하기 전에 [보안](security-and-operations.md), [운영 수락 기준](delivery-and-operations.md), [검증표](verification.md)의 미실행 시험을 수행한다.

앞선 설계 검토의 범위는 **설계의 구체화와 명세·DDL의 검증**이었다. 이후 실제 코드 수정·회귀·DB 실행 계획을 검증한 범위는 이 문서의 추가 검수와 [현재 구현 상태](implementation-status.md)를 따른다. 설계 검사 코드와 SQL은 [설계 검사](checks/README.md)에 있으며, 실제 앱 의존성은 backend의 lock 파일로 고정했다. 서비스 분할·Redis·Kubernetes·자동 재학습은 이번 보완의 필수 작업으로 추가하지 않았다.
