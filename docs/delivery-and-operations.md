# 구현 수락과 운영 실행 기준

기준일은 2026-10-29이다. [전체 설계](architecture.md)의 FastAPI API 1개·독립 검수 작업자 1개·HTTPS 프록시·관리형 PostgreSQL 구성을 유지한다. 이 문서는 실제 구현 PR에서 무엇을 확인하고 어떤 증거로 운영을 시작할지 정한다. 아래 성능·시간·연결 수는 **프로젝트 초기값 또는 측정 목표**이며, 업계 공통 의무나 이미 달성한 수치가 아니다. 공급자 구매·서버 배포·CI 설정은 아직 실행하지 않았다.

권한·키·개인정보·장애별 기본 행동은 [보안과 운영 정책](security-and-operations.md), 잠금·중복 처리·데이터 복구는 [데이터 설계](data-and-recovery.md), 실제 모델의 종료와 준비 임대는 [AI 계약](ai-contract.md)을 따른다. 여기서는 그 정책을 운영자가 실행하고 팀원이 검토할 기준으로 구체화한다.

## 실행 단위와 상태 확인

| 확인 대상 | 통과 의미 | 실패 때 행동 |
| --- | --- | --- |
| `/api/health/live` | API 이벤트 루프가 응답함. DB·모델 호출 없이 판정 | 연결 실패가 지속되면 API 프로세스·VM 자원 조사. DB 장애만으로 API를 반복 재시작하지 않음 |
| `/api/health/ready` | 설정·필요한 DB 스키마·짧은 DB 질의·API 제어 루프가 사용 가능 | 배포 성공 판정 금지. 세부 원인은 비공개 로그와 상태 계측으로 조사 |
| `worker_readiness` | 요구한 모델 bundle을 로드·warmup한 감독 프로세스의 유효한 준비 임대 | **신규 제출만** 503. 기존 제출 조회·운영자 복구·이미 승인된 화면은 가능한 범위에서 유지 |
| Procrastinate healthcheck | 큐 설정·DB 연결·라이브러리 테이블에 접근 가능 | 큐 연결·migration·권한 점검. 모델 추론 가능을 뜻하지 않음 |
| 표시·만료 정리 루프 | 약 2초·5초 루프가 최근 성공함 | 오래된 화면·만료 점유를 감지하고 신규 접수 중단·원인 조사 |

API ready는 모델 ready와 분리한다. 모델이 로드되지 않았다고 API 전체를 프록시에서 빼면 조회와 운영 복구도 차단된다. 초기 단일 VM에서는 ready를 **배포 gate와 진단 신호**로 사용하고, 모든 요청의 일괄 차단 신호로 연결하지 않는다. 공개 health 응답에는 DB 주소·예외·버전·내부 queue 이름을 넣지 않는다.

제어 루프는 작업이 없거나 표시가 일시정지여도 성공 시각을 갱신한다. 초기 stale 기준은 표시 10초·만료 정리 15초로 두고 현장 시험에서 조정한다. 모델 lease는 기존 계약의 갱신 5초·유효 15초를 따른다. health 조회마다 모델 inference를 실행하지 않는다.

초기 API는 workers=1이며 FastAPI lifespan이 풀과 표시·만료·stalled 루프의 시작·종료를 소유한다. 루프 예외는 readiness·새 접수·경보에 반영하고, 예상하지 못한 루프 종료는 운영자가 API 재시작 절차로 복구한다. 프로세스 종료와 unhealthy 표시는 구분한다. 환경·Alembic migration·위험 검증을 포함한 담당자 순서는 [백엔드 구현 계획](backend-owner-plan.md)을 따른다.

Procrastinate의 `procrastinate healthchecks`는 DB와 큐 테이블 확인용이다. 배포 시 고정한 `PROCRASTINATE_APP`과 라이브러리 버전으로 명령을 확인한다. 모델 검증은 별도 fixture를 실행한다. [Procrastinate 모니터링](https://procrastinate.readthedocs.io/en/stable/howto/production/monitoring.html)

## 빈 DB에서 최초 운영까지

다음은 구현할 신뢰된 CLI의 계약이다. 아직 명령 프로그램이 존재한다는 뜻은 아니다. 공개 초기화 API·기본 관리자 비밀번호·시작할 때마다 실행되는 seed는 만들지 않는다.

1. 서비스의 고정 `PUBLIC_ORIGIN`과 단일 `BOARD_ID` UUID를 결정한다. 이 UUID는 비밀이 아니며 같은 운영 DB에서 매 배포마다 바꾸지 않는다. TLS·DB 인증서 검증·필수 비밀의 존재와 UTC 시계 동기화를 확인한다. 비밀값은 출력하지 않는다.
2. migration 전용 계정으로 앱·큐 schema를 적용한 뒤 런타임의 제한된 권한을 구성한다. API 프로세스는 migration을 실행하지 않는다.
3. `init-board` CLI가 명시적 UUID·이름으로 board_runtime을 생성한다. 초기 접수·송출 중단 true와 MOODWALL을 명시하고 DDL 기본false에 의존하지 않는다. 최초 장치·pairing·heartbeat·lease는 비어 있다.
4. 같은 UUID로 다시 실행하면 현재 이름·mode·revision·장치 자격·중단 상태를 바꾸지 않고 존재만 확인한다. 다른 board가 이미 있는 단일 board DB에서 새 UUID 생성을 요구하면 실패한다. 이름 변경이나 운영 재개는 초기화의 부작용으로 처리하지 않는다. 동시에 두 초기화가 실행되지 않도록 CLI가 DB 초기화용 advisory lock을 트랜잭션 동안 보유한다.
5. `create-operator` CLI가 개인별 username·ADMIN 역할과 대화식 비밀 입력으로 첫 계정을 만든다. 생성·로그인 username은 공통 3~64 Unicode code point이며 trim·NFC 적용 뒤 대소문자를 구분해 정확히 비교한다. 제어·공백 문자는 허용하지 않는다. DB의 100자 상한은 더 넓은 저장 제약일 뿐 생성 규칙이 아니다. 비밀번호 검사는 [보안 정책](security-and-operations.md)을 따르고 같은 username은 덮어쓰지 않는다. 비밀번호 복구는 별도 명시적 명령으로 세션 해지와 함께 처리한다.
6. API·worker는 설정된 `BOARD_ID`가 존재하고 단일 board DB와 일치하는지 시작 시 확인한다. 누락·불일치는 ready 실패이며 임의 첫 행을 선택하거나 새 board를 자동 생성하지 않는다. pairing 요청의 코드도 이 설정 행에서만 검증한다. API 본문에 없는 board ID를 추정하거나 요청 Host를 사용하지 않는다.
7. ADMIN이 별도 운영자 기기에서 로그인하고 노트북을 페어링한다. 초기 송출 중단은 pairing·heartbeat·새로고침으로 풀리지 않는다. 모델 준비·상태 확인 뒤 ADMIN이 송출을 재개해 합성 smoke를 확인하고 접수도 명시적으로 재개한다. 최초 smoke에 필요하면 접수를 제한된 시험 시간에만 열고 확인 직후 다시 중단한다.

최초 실행·같은 UUID 재실행·다른 UUID 거절·잘못된 BOARD_ID·첫 ADMIN 생성·페어링만으로 중단 해제 불가를 격리 DB에서 시험한다. PostgreSQL advisory lock은 협력하는 초기화 명령 사이 직렬화 수단이며 권한 검사를 대신하지 않는다. [PostgreSQL 명시적 잠금](https://www.postgresql.org/docs/16/explicit-locking.html#ADVISORY-LOCKS)

## DB 연결과 시간 예산

| 항목 | 초기 설정 | 확인할 실패 동작 |
| --- | --- | --- |
| API DB 풀 | 최대 5개, API 프로세스 1개 | 풀 대기 상한 초과 시 503. 요청을 무한 대기시키지 않음 |
| 검수 DB 풀 | 최대 3개, 동시 추론 1개 | 추론 중 업무 트랜잭션을 열어 두지 않음 |
| 제어·관리 연결 | 최대 2개 예산 | 표시·만료 정리가 API 요청에 모두 밀리지 않는지 확인 |
| 풀 획득 대기 | 500ms | 지연 지표와 오류 코드를 기록하며 학생 제재 없음 |
| 새 DB 연결 | 3초 | 매 요청마다 새 연결을 만들지 않고 풀 사용 |
| 업무 SQL | `statement_timeout=2s` | 전체 업무 트랜잭션 롤백. 부분 성공 응답 금지 |
| 행 잠금 대기 | `lock_timeout=500ms` | 짧은 요청 예산 안에서만 한정 재시도 |
| 열린 채 유휴 트랜잭션 | `idle_in_transaction_session_timeout=5s` | 추론·외부 네트워크 호출 때문에 DB 잠금이 남지 않음 |
| 일반 REST 처리 예산 | API 전체 8초, 프록시 15초, 브라우저 20초 | 결과 불명확 시 같은 멱등 키 재시도. 새 제출을 자동 생성하지 않음 |

숫자를 DB 전역 설정으로 일괄 적용하지 않는다. 업무 역할·커넥터의 설정 또는 업무 트랜잭션의 `SET LOCAL`에 적용하고, migration·백업에는 별도 예산을 둔다. `statement_timeout`은 각 SQL의 상한이고 전체 요청 상한이 아니므로 여러 SQL·재시도가 8초를 넘기지 않게 별도 잔여 예산을 관리한다. timeout 뒤에는 오류 상태의 연결을 롤백한 후 반환한다. [PostgreSQL 16 timeout 정의](https://www.postgresql.org/docs/16/runtime-config-client.html)

위 풀은 상한 예산이다. 실제 풀을 중복 생성해 API 5개와 Procrastinate 발행 전용 5개를 별도로 만들지 않는다. 접수 트랜잭션은 기존 계약대로 **같은 psycopg3 연결**을 작업 등록에 전달한다. Procrastinate의 LISTEN 전용 연결 등 풀 밖 연결도 산정하며, 정확한 수는 고정 버전의 동작과 `pg_stat_activity`로 확인한다. [Procrastinate 연결 수](https://procrastinate.readthedocs.io/en/stable/howto/production/connections.html)

배포 전에 다음 식의 각 항목을 기록한다.

```text
API 프로세스 수 × API 최대 풀
+ 검수 프로세스 수 × 검수 최대 풀
+ 제어·관리 풀
+ LISTEN 등 전용 연결
+ 동시에 허용한 migration·백업 연결
+ 사고 조사용 운영 연결 여유
<= 관리형 DB가 애플리케이션에 허용한 연결 수
```

추론 감독 부모와 자식이 각각 같은 최대 풀을 갖는다면 둘 다 합산한다. 제어 작업 2개를 API 풀에서 예약해 쓰는 구현이라면 별도 2개를 다시 더하지 않고 실제 구조를 기록한다. 애플리케이션 예약량과 DB의 관리자 전용 예약 연결도 구분한다. 현재 풀 예산은 PostgreSQL 연결 한도를 설정했다는 뜻이 아니다.

## 프록시와 SSE의 수락 조건

| 경로 | 적용할 동작 | 검증 |
| --- | --- | --- |
| 일반 `/api/` | HTTPS, JSON 본문 4 KiB 상한, 신뢰한 프록시만 전달 헤더 사용 | 초과 요청 거절, 위조 전달 헤더로 속도 제한 우회 불가 |
| `/api/display/events` | `text/event-stream`, 캐시·버퍼링 비활성화, 유휴 keepalive 15초 | 변화가 없어도 keepalive가 실제 브라우저에 도착 |
| SSE 프록시 연결 | 읽기 유휴 timeout 60초, 정해진 최대 연결 수명 5분 허용 | 5분 종료 후 재연결·snapshot 복구. 60초는 전체 연결 수명이 아님 |
| SSE 권한 | 연결 시작·각 데이터 이벤트 전·최대 5초마다 장치 자격 검사 | 장치 해지 뒤 5초 이내 종료를 목표로 실측 |
| 정적 React 파일 | 해시된 asset은 장기 캐시, HTML은 재검증 | 배포 후 이전 HTML과 새 API의 호환 확인 |

15초 keepalive는 전송 경로를 살리는 주석이고, 장치의 10초 POST heartbeat는 화면의 온라인 시간을 계산하는 신호다. 어느 것도 다른 쪽을 대신하지 않는다. 5초 권한 재검사도 별도 짧은 DB 조회이며 SSE 연결 하나에 DB 연결을 계속 붙잡지 않는다. 모델 실행이나 DB 행 잠금 안에서 SSE 전송을 기다리지 않는다.

초기 SSE 발행기는 API 한 프로세스의 메모리에서 연결별 대기 알림을 **종류별 최신 1개, 총 2개**까지만 보유한다. `state.changed`·`qr.changed`는 최신 revision·날짜로 병합할 수 있는 무효화 신호이며 원문·QR ticket은 큐에 넣지 않는다. 느린 연결에 무한 task·이벤트 목록을 쌓지 않고 개별 쓰기가 5초를 초과하면 연결을 닫는다. 재연결·REST polling으로 복구한다. 연결 해제 시 구독 큐·권한 확인 task를 정리한다. 2개·5초는 이 프로젝트의 자원 예산이며 SSE 표준이 요구하는 값이 아니다. 5분 연결 수명·5초 권한 재검사와 독립적으로 시험한다.

현재 FastAPI 공식 SSE 구현은 keepalive 주석·캐시 방지·일부 프록시 버퍼링 방지 헤더를 제공한다. 실제 선택 버전을 잠그고 프록시에서도 동일 동작을 확인한다. 헤더가 있다는 이유만으로 프록시의 buffering 해제가 검증된 것은 아니다. [FastAPI SSE 전송 세부 사항](https://fastapi.tiangolo.com/tutorial/server-sent-events/)

이벤트 알림은 최신 상태를 다시 읽으라는 신호다. 프론트는 새 연결·재연결·변경 알림·10초 주기마다 snapshot을 적용한다. snapshot revision이 같아도 검수로 변한 대기 수 등은 적용하며, 역순 응답은 인증 epoch·제어 경계·요청 순번·revision을 검사하는 단일 적용 경로로 제외한다. mode POST의 상태와 오래된 GET을 각각 별도 렌더 경로에 넣지 않는다. 세부 규칙은 [노트북 동기화](display-laptop.md)를 따른다. 전광판의 재연결 자체를 새 QR 입장이나 학생 새 세션으로 해석하지 않는다.

전광판 노트북의 실제 패널 출력은 현장 확인 항목이다. 문서 hidden 중 heartbeat를 중단하고 복귀 때 session·snapshot·QR을 동기화한다. 마지막 성공 snapshot 30초 미갱신 시 글·QR을 가리며, 서버 mode와 다른 앱 전환이 어긋나지 않게 한다. 초기에는 영속 브라우저 프로필을 사용하고 키오스크의 쿠키 삭제·재부팅 동작은 별도 시험한다. [노트북 운영 수락](display-laptop.md)

## 릴리스와 migration 순서

일반 백엔드가 배포를 실행하고 AI 담당이 모델·작업자, 전광판 프론트 담당이 실제 표시를 교차 확인한다. 최초에는 수동 배포하되 저장소의 검증된 커밋으로부터 이미지를 한 번 만들고 API와 작업자에서 같은 digest를 사용한다. 프론트 asset·API·모델 bundle·앱 migration·Procrastinate schema revision을 한 릴리스 기록에 묶는다.

1. CI 결과·대상 커밋·이전 이미지 digest·새 모델 manifest를 확인한다. 운영 환경변수를 출력하지 않고 필요한 이름과 존재 여부만 확인한다.
2. 별도 시험 DB에서 빈 DB 생성과 직전 릴리스 DB로부터의 upgrade를 모두 실행한다. 앱과 큐 schema의 실제 적용 revision을 남긴다. 모델 fixture와 이전 버전 API 호환도 확인한다.
3. 운영 백업의 마지막 성공 시각과 복원 가능한 지점을 확인한다. 삭제·컬럼 변경 등이 있으면 단순 이미지 rollback으로 가능한지 먼저 판정한다.
4. 신규 접수를 중단하고 기존 API 조회·기한 정리는 유지한다. 우선 기존 작업자가 접수된 `PENDING/PROCESSING`을 해당 버전에서 모두 처리하도록 drain한다. 기다릴 수 없으면 소비를 멈추고 실행 중 프로세스를 기존 감시·종료 절차로 회수한 뒤, 남은 비최종 제출을 참여자 잠금·기한 정책에 따라 제재 없이 terminal화한다. 처리 기한이 끝났으면 EXPIRED, 운영 취소는 CANCELLED로 구분한다. **비최종 제출 0개와 구 작업자 종료를 확인한 뒤** 새 버전 작업자를 시작한다. 남은 큐 job은 terminal 제출의 no-op 경로로 회수하며 옛 접수를 새 모델로 조용히 재판정하지 않는다. 단순히 `doing`이 안 보인다는 이유로 drain을 완료 처리하지 않는다.
5. **배포 프로세스 한 곳만** migration을 실행한다. 앱 migration과 Procrastinate의 pre/post migration을 버전별 순서대로 적용한다. post migration이 이전 worker와 호환되는지 확인한 뒤 실행한다.
6. 새 API와 작업자를 시작한다. API ready·큐 healthcheck·실제 모델 READY·제어 루프 갱신을 각각 확인한다. 시작 중 모델이 준비되지 않은 동안 신규 접수는 계속 중단한다.
7. 전용 시험 참여자로 QR 입장 → 접수 202 → 최종 검수 → 표시, 숨김·중단을 확인한다. 실제 학생 원문을 smoke-test payload나 로그에 넣지 않는다.
8. 새 접수를 재개하고 15분간 지연·오류·메모리·큐·장치 연결을 관찰한다. 릴리스 기록에 결과·확인자·시각을 남긴다.

migration은 API 시작마다 자동 실행하지 않는다. 다중 프로세스의 시작 전 작업은 한 번 실행하는 주체를 두라는 배포 원칙을 따른다. [FastAPI 배포 전 작업](https://fastapi.tiangolo.com/deployment/concepts/)

Procrastinate migration은 앱의 `schema.sql`에 포함된 것으로 간주하지 않는다. 고정 버전의 큐 SQL·적용 이력과 pre/post 호환성을 별도로 관리한다. 큐 테이블을 수동 수정해 revision을 맞추거나 `doing`을 임의로 `todo`로 바꾸지 않는다. [Procrastinate migration](https://procrastinate.readthedocs.io/en/stable/howto/production/migrations.html)

| 실패 지점 | 복구 기준 |
| --- | --- |
| migration 전 | 접수 중단 유지, 새 배포 취소, 기존 실행 상태와 모델 준비 확인 후 재개 |
| 추가만 한 호환 migration 뒤 새 앱 실패 | 이전 이미지·이전 모델로 복귀. 확장한 schema는 유지하고 검사 |
| 삭제·형식 변경 뒤 실패 | 임의 down migration 금지. 합의한 forward fix 또는 격리 복원 절차를 실행 |
| 모델만 새 버전이 불안정 | 접수 중단, 실행 중 작업 정리, 검증된 이전 bundle로 작업자 재시작. 기존 작업의 모델 버전을 조용히 바꾸지 않음 |

파괴적인 컬럼 제거는 새 컬럼 추가 → 구·신 버전 호환 → 데이터 전환 검증 → 다음 릴리스 제거 순으로 분리한다. 11월 5일 전에는 호환되는 추가 변경을 우선하고, 무중단 배포를 약속하지 않는다.

## 백업을 복원 가능한 결과물로 확인하기

기존 목표인 일일 백업·7일 보관·RPO 최대 24시간·RTO 4시간은 그대로다. 자동 백업 설정 화면만으로 통과하지 않으며, 다음 결과를 운영 기록에 남긴다.

| 결과물 | 실제 확인 |
| --- | --- |
| 백업 목록 | 마지막 성공 시각, 저장 위치, 암호화·접근권한, 만료일 |
| 복원 기록 | 대상 백업, 시작·완료 시각, DB·앱·큐 schema revision |
| 데이터 확인 | 샘플 제출의 상태·사용량·감사 연결, 깨진 FK와 비정상 미확정 상태 |
| 인증 확인 | 운영자·장치·미사용 pairing 자격 폐기, 학생 세션의 원래 만료 검사 |
| 작업 복구 | 오래된 비최종 제출 만료, 중복·늦은 결과 방어, 보관 기간 지난 원문 삭제 재실행 |
| 재개 승인 | 복구 시점 이후 유실될 수 있는 글·제재·쿼터를 운영자가 확인하고 신규 접수 재개 |

복원 DB는 외부와 운영 worker의 자동 연결을 막은 상태에서 연다. 필요하면 비밀값을 배포 저장소에서 다시 주입하고 보안 사고에 노출된 키는 교체한다. 보안 사고 외 단순 DB 복원에서도 오래된 기기·운영자 세션이 부활하지 않게 한다. 실제 복원 소요가 4시간을 넘으면 목표를 수정하거나 절차를 줄이고 다시 측정한다.

격리 복원의 sanitize 명령은 다음 순서로 구현하고 실행 기록을 남긴다. 해당 명령도 아직 구현된 도구가 아니다.

1. 외부 프록시와 운영 worker를 차단한 상태에서 복원한다. 접수·송출 중단을 true로 두고 설정 BOARD_ID·앱/큐 revision·외부 DB 역할 권한을 확인한다.
2. 운영자 세션을 모두 해지하고 장치 token hash·만료·paired 시각, 미사용 pairing hash·만료, heartbeat 시각·lease를 NULL로 지운다. 장치 version·표시 revision은 초기화하지 않고 증가시켜 옛 상태와 구분한다.
3. `worker_readiness`를 비운다. 과거 supervisor의 generation·미래 READY lease를 신뢰하지 않고, 구 worker 종료를 확인한 뒤 새 generation으로만 시작한다.
4. 학생의 익명 식별 세션은 원래 만료를 검사해 유지하되 **모든 세션의 admission 관련 필드는 NULL**로 지워 최신 QR을 다시 요구한다. 작성 이력·최근 60분 사용량·당일 제재는 복원된 값을 유지한다. 보안 사고라면 학생 세션도 전부 해지한다.
5. 전광판 → 제출 잠금 순서로 과거 날짜 공개 상태를 만료시킨다. 강조 checkpoint·슬롯·잔여 시간은 없다. 새 장치는 재페어링과 첫 최신 state·heartbeat 확인 뒤 렌더한다.
6. 구 worker가 모두 종료된 뒤 남은 비최종 업무를 기한에 따라 EXPIRED 또는 제재 없는 CANCELLED로 정리하고 점유를 해제한다. 지원되는 큐 API로 stalled 작업을 회수하며 task는 terminal 제출을 읽고 모델 없이 종료한다. 큐 테이블을 직접 수정하지 않는다. 현재 보관·삭제 정책도 재실행한다.
7. API·새 supervisor를 시작해 ready·모델 warmup·제어 루프·DB 일치성을 확인한다. ADMIN 재로그인과 노트북 재페어링 후 송출을 재개해 합성 smoke를 실행하고, 복구 지점 이후 유실 가능한 글·제재·쿼터를 검토한 ADMIN이 접수를 재개한다.

자격 정리와 초기화는 복원 환경의 내부 명령으로 수행한다. 복원된 오래된 운영자 쿠키로 로그인 상태를 되살려 정리 작업을 실행하지 않는다. 24시간 RPO에서는 이미 표시된 글이 다시 대기하거나 최근 제재가 유실될 수 있으므로 복원만으로 사용자 관점의 정확히 한 번 표시를 보장하지 않는다.

`pg_dump`를 사용할 때는 DB 내용의 일관된 dump와 cluster 역할·외부 비밀 설정을 구분한다. role·권한·프로바이더 설정은 별도 재구성 항목이다. 복구 시 학생 원문을 저장소에 업로드하지 않는다. [PostgreSQL dump와 복원](https://www.postgresql.org/docs/16/backup-dump.html)

## 사용자 경험을 측정하는 지표

아래는 첫 운영 전 시험할 내부 목표다. 초기 사용자가 적은 상황에서 한 달 99.9%처럼 큰 가용성 약속을 임의로 만들지 않는다. 운영 기간의 실제 이벤트 수와 분모가 0인 구간을 함께 기록하며, 표본이 부족하면 비율을 달성했다고 보고하지 않는다. 서비스 지표는 관측 구간·대상·성공 조건을 함께 정의한다. [Google SRE SLI·SLO 정의](https://sre.google/sre-book/service-level-objectives/)

| 지표 | 분모와 성공 조건 | 초기 시험 목표 |
| --- | --- | --- |
| REST 처리 지연 | health·SSE·정적 파일 제외, 서버에 도달해 응답한 REST 요청. 정상·거절을 나눠 측정 | 예상 정상 부하에서 p95 500ms 이하 |
| API 인프라 실패 | 같은 REST 대상의 전체 요청 대비 5xx·프록시 timeout·서버 예외. 알려진 정책503 MODERATION_BUSY·INTAKE_PAUSED는 분자에서 제외하고 별도 거절률로 보고. MODEL_NOT_READY·DB_UNAVAILABLE는 실패에 포함 | 정상 부하 15분에서 1% 미만, 실패 건수·정책 거절률도 함께 보고 |
| 검수 정상 종료 | 202를 반환한 신규 제출 중 처리 기한이 관측 구간 내 이미 지난 건. 기한 전에 APPROVED·REJECTED·NEEDS_REWRITE로 확정된 비율 | 95% 이상. FAILED·EXPIRED는 실패, 운영 CANCELLED도 별도 건수와 포함 비율 보고 |
| 검수 지연 | 최종 판정된 제출의 `finalized_at-accepted_at`; queue 대기·추론 시간을 별도 기록 | p95 30초 이하를 목표로 실제 CPU에서 측정. 만료 건을 빼고 빨라졌다고 해석하지 않음 |
| 화면 동기화 | 제어된 시험에서 hide·mode 커밋 시각부터 최신 snapshot 렌더까지 | 정상 연결에서 5초 이하. SSE 차단 시 10초 polling+요청 지연으로 별도 기록 |
| 장치 회수 | 회수 커밋 뒤 기존 SSE 종료까지의 제어 시험 | 5초 이내 목표, 네트워크 지연과 실제 측정 결과 포함 |
| 데이터 안전 | 동시성·중복·늦은 결과 fixture 각각의 불변식 | 오승인·중복 제재·초과 점유 0건. 발생하면 비율과 무관하게 출시 중단 |

정책 거절·429·검수 혼잡503과 시스템5xx를 별도로 집계한다. 모든 글을NEEDS_REWRITE로 돌려 정상 종료율만 좋아 보이지 않게 오차단·누락·UNCERTAIN을 고정 평가셋으로 확인한다. 화면 중단·장치 오프라인·만료 전 미수신은 원인별로 기록한다.

### 원문 없이 남길 관측값과 경보

JSON 로그의 공통 필드는 서버 UTC 시각, level, event, request ID, 경로 템플릿, status, error code, duration, 릴리스·모델 버전이다. 경로의 실제 UUID를 지표 label로 넣지 않는다. 제출 ID가 필요한 제한된 상관관계 로그와 원문 없는 집계 지표를 구분한다. 쿠키·Authorization·CSRF·QR·pairing code·비밀번호·본문·DB 바인딩 인자를 자동 dump하지 않는다. 입력 오류 예외에도 원문이 포함되지 않게 시험한다. 로그 인젝션 방지와 보관 정책도 적용한다. [OWASP 로깅 지침](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)

| 신호 | 초기 경보 조건 | 조치 |
| --- | --- | --- |
| 모델 준비 없음 | 운영 중 신규 접수가 열린 상태에서 lease 만료, 15초 이상 지속 | 신규 접수 차단 확인, 작업자·artifact·감시 원인 점검 |
| 제어 루프 지연 | 표시 10초 또는 만료 정리 15초 이상 성공 없음 | 잠금·풀 대기·API 이벤트 루프 확인 |
| 미검수 대기 | 가장 오래된 비최종 제출 60초 초과, 또는 120초 기한 초과가 남음 | 새 접수 혼잡 제어, worker와 정리 루프 조사 |
| REST 실패 | 5분간 인프라 실패 5건 이상 또는 총 100건 이상에서 5% 초과 | 최근 배포·DB·프록시 확인. 낮은 표본의 비율 폭등을 구분 |
| DB 풀 | 획득 timeout 1분에 3건 또는 대기 p95 250ms 초과가 5분 지속 | 긴 transaction·중복 풀·쿼리 조사 |
| 장치 단절 | 운영 중 마지막 heartbeat 30초 경과 | 브라우저·노트북 절전·망 확인. 실제 무드월 사용 예정 시간에만 알림 |
| 메모리·디스크 | 메모리 제한 85%가 5분 지속, OOM 1회, 디스크 80% 이상 | 모델 메모리·로그 순환·dump 보관 확인 |
| 백업 누락 | 마지막 성공 후 26시간 초과 또는 복원 검증 실패 | 백업 원인 조사와 수동 복구 지점 확인 |

자동 알림은 서비스를 운영할 사람이 확인할 채널을 정한 뒤 연결한다. 이 문서 작성으로 팀원에게 메시지를 보내거나 외부 알림 서비스를 만들지는 않는다. 동일 장애를 반복 전송하지 않고 상태 변화와 복구를 묶어 기록한다.

## 부하 시험에서 무엇을 증명할지

예상 학생 수가 미정이므로 아래는 초기 시험 프로필이다. 실제 행사 시간·동시 접속 예상이 정해지면 더 큰 쪽으로 갱신한다. 합성 문장과 시험 세션만 사용하고 운영 DB에 부하를 주지 않는다.

1. **API 기준 시험:** 모델이 아닌 fake adapter로 100개 익명 브라우저 세션, 등록 전광판 1개를 만든다. 상태·사용량·입장 조회를 합쳐 20 REST 요청/초로 15분 실행한다. 제출은 브라우저별 10초 간격·최근 60분 3회 규칙을 지킨다. 풀 대기·지연·오류·CPU·메모리를 기록한다.
2. **업무 경합 시험:** 같은 참여자에 서로 다른 신규 요청 4개 동시 전송, 같은 key 5회, commit 직후 연결 단절, worker 종료를 각각 반복한다. [검증 기준](verification.md)의 V05~V13을 실제 PostgreSQL에서 확인한다. 응답 속도보다 DB 최종 상태가 수락 근거다.
3. **실제 모델 용량 시험:** 목표 서버의 CPU에서 한 문장 처리 시간·p95·RSS를 warm/cold로 측정한다. 하나의 추론 동시성으로 지속 가능한 완료율을 구한 뒤 그 70%를 초기 정상 유입으로 시험한다. 70%는 여유를 위한 프로젝트 초기값이다.
4. **과부하 시험:** 정상 유입의 2배 burst를 60초 가하고, 혼잡 거절·기한 만료·점유 해제·다음 정상 요청의 복구를 확인한다. 모델 한 개가 모든 burst를 120초 안에 처리할 수 있다고 가정하지 않는다.
5. **현장 지속 시험:** 실제 전광판 노트북에서 2시간 실행한다. 예약 mode API·운영 중단·학교망↔LTE 전환·브라우저 새로고침·SSE 차단·장치 해지·시각 경계를 넣고 서버와 화면을 함께 확인한다. 실제 챗봇 화면·모델·외부 연동은 독립 무드월의 출시 조건이 아니다.

검수 큐와 승인된 표시 큐의 한계는 다르다. 운영 전 실제 서버에서 한 작업의 `PROCESSING` 획득부터 결과 확정까지의 서비스 시간 p95를 측정한다. queue 대기·모델 최초 로드 시간과 구분하고, 실패·timeout 건수도 함께 기록한다. 성공한 빠른 문장만 골라 서비스 시간을 계산하지 않는다. p95가 60초를 넘으면 현재 작업 기한의 모델 수락 조건을 통과하지 못한 것이다.

초기 검수 혼잡 기준은 단일 작업자·동시 추론 1개에서 다음처럼 설정한다. `q`는 접수 직전 관측한 `PENDING/PROCESSING` 수, `T=max(측정 p95 초, 1)`, `remaining`은 신규 제출의 접수 시각부터 `min(120초 뒤, 다음 한국 자정)`까지 남은 초다.

```text
configured_pending_cap = max(1, min(8, floor(110 / T)))
새 접수 허용 조건:
  q + 1 <= configured_pending_cap
  (q + 1) * T + 10 <= remaining
```

10초는 DB·작업 전달 등의 초기 여유 예산이고 상한 8건도 프로젝트의 보수적 시작값이다. 개발 fake adapter는 임시 cap 8로 연결 시험하지만, 운영 설정에는 **실제 서버·고정 모델 bundle의 측정 결과**가 있어야 한다. 검수 혼잡이면 503과 재시도 안내를 반환하고 점유·job·제재를 만들지 않는다. 멱등 키 재시도는 혼잡 판정보다 기존 결과 조회가 먼저다.

이 count는 READ COMMITTED soft gate다. 동시 관측으로 상한을 넘을 수 있고 p95 식이 p99·재시작·실패를 보장하지 않는다. 기한 검사·멱등 확정·독립 정리를 유지하고 혼잡·오버슈트·timeout을 실측한다. 승인 표시 대기열과 DISPLAY_BUSY는 없다.

승인 글은 즉시 일반 말풍선으로 공급하며 고정 4개·강조·표시 대기는 없다. 전광판은 100개 전송 페이지를 모두 읽어 당일 캐시에 합친다. 접수 1시간에는 DB에서 제외·삭제하고 받은 글은 내용 담긴 투명 말풍선으로 다음 KST 자정까지 유지한다. 새로고침 복원 전에 인증·숨김·세대를 확인한다. 저장소 삭제·기기 교체 뒤 DB 삭제 글은 복구할 수 없다.

현장 부하는 승인량·100개 초과 전체 페이지 수집 시간·브라우저 렌더 지연·RSS·저장소 사용량·접수 1시간 내 미수신을 측정한다. 응답 페이지 크기는 표시 상한이 아니다. 브라우저 보호용 보관 상한과 저장소 실패에 의한 유실을 운영 안내에 포함한다. 최근60분3회 제한은 화면 정책과 별도다.

시험 보고서는 대상 commit·이미지·모델 해시, CPU/RAM·DB 구성, 요청 프로필, 데이터 수, 202 수·최종 상태별 수, p50/p95/p99, 최대 queue age·RSS, 불변식 결과를 포함한다. fake adapter의 높은 처리량을 실제 모델 성능으로 제시하지 않는다.

## 4명이 합칠 때의 수락 기준

기본 Git 명령과 날짜별 순서는 [협업·구현 계획](implementation-plan.md)을 따른다. Issue에는 동작 하나와 확인 방법을 쓰고, 담당자가 자기 부분을 구현한 뒤 다른 사람이 PR을 검토한다.

| 경로·계약 | 주 담당 | 필수 교차 확인 |
| --- | --- | --- |
| `backend/app/`, `docs/schema.sql` | 일반 백엔드 | DB·권한·작업 변경은 AI 담당 또는 관련 프론트가 시나리오 확인 |
| `backend/inference/`, `training/`, 모델 manifest | AI | 일반 백엔드가 입출력·실패·시간 예산 확인 |
| 작성 React 화면 | 작성 프론트 | 백엔드가 상태·멱등 재시도 확인 |
| 전광판 React 화면 | 전광판 프론트 | 백엔드가 snapshot·모드·재연결 확인 |
| `docs/openapi.json` | 일반 백엔드 | 영향을 받는 프론트·AI 담당이 먼저 계약 합의 |
| `infra/`, migration | 일반 백엔드 | 다른 팀원 한 명이 변경 전후와 복구 절차 확인 |

코드가 생기면 다음 검사를 고유한 CI job 이름으로 고정한다. 최초 코드 PR에서 실제 실행 명령과 lock 파일을 함께 추가한다. 빈 테스트나 항상 성공하는 명령을 필수 검사로 등록하지 않는다.

| 필수 검사 이름 | 확인 범위 | 검증 증거 |
| --- | --- | --- |
| `contract` | OpenAPI 구조·내부 참조·예시, AI schema, 프론트 공통 fixture | 실제 계약 검증 로그, 변경된 계약의 소비자 확인 |
| `backend-static` | Python formatter·linter·선택한 type checker | 잠근 도구 버전과 실행 결과 |
| `frontend-build` | TypeScript 검사·React production build | lock 파일 기반 설치와 build 결과 |
| `postgres-integration` | 실제 PostgreSQL 16 migration·트랜잭션·경합·job 원자성 | 격리 DB의 V05~V13 등 해당 시나리오 결과 |
| `pipeline-smoke` | fake adapter로 QR→202→판정→snapshot | 앱·worker 실제 프로세스에서 하나의 제출이 완료됨 |

학습 전체를 매 PR마다 돌리지 않는다. AI 계약 fixture는 CI에서 실행하고, 실제 bundle 변경은 별도 고정 평가 보고서와 CPU 측정을 PR에 첨부한다. 문서만 있는 현재 상태에는 위 CI가 설치됐다고 표시하지 않는다.

가능한 GitHub 요금제에서 `main`은 PR·승인 1명·필수 검사 통과를 요구하고 작성자 자신의 승인으로 대체하지 않는다. 필수 검사 이름은 workflow 사이에 중복시키지 않는다. path filter로 필요한 job이 영원히 대기하지 않도록 전체 required check의 완료 조건을 시험한다. [GitHub 보호 브랜치](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)

PR workflow에는 운영 비밀을 주지 않고 token은 읽기 권한을 기본으로 한다. 배포 job은 PR 코드 실행과 분리하고 수동 승인된 `main` 커밋만 배포한다. 동시 배포는 1개로 직렬화하며 진행 중인 migration을 새 push 때문에 취소하지 않는다. workflow의 permission·concurrency·job 의존성을 명시한다. [GitHub Actions workflow 설정](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)

최종 공개 전에는 [출시 시험 전체](verification.md)에 실행자·실행일·대상 버전·결과를 채운다. 아직 실행하지 않은 항목을 통과로 옮기지 않는다. 11월 3일 모델 연결 gate와 11월 5일 공개 판단은 기존 계획을 유지하며, 계약·보안·검수 안전성·복원 조건이 빠지면 실제 학생 대상 운영을 시작하지 않는다. 첫 단계의 검수 모델은 필수이나 챗봇 모델·챗봇 UI·실제 전환 연동은 범위 밖이며 출시를 막지 않는다.
