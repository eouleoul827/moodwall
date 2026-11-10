# Gemini 검수 운영 설계

작성일: 2026-11-04 · 갱신일: 2026-11-09. 이 문서는 [Gemini 검수 전체 설계](gemini-moderation-design.md)의 운영 상세안이다. Gemini를 서버 내부 게시 적합성 분류에 사용하는 전제로 작성했다. 실제 HTTPS transport·키 설정·토큰 사전 확인·사용량 정산·서버/화면/원장은 구현했다. **실제 키와 합성 문장으로 검수·승인 후 감정 연결을 부분 확인했으며 전체 앱 연결·실모델 정책 평가·청구서 대사·운영 배포는 완료하지 않았다.** [실제 연결 기록](checks/gemini-live-connection-2026-11-09.json)을 따른다. 아래 시간·비용·처리량은 무드월의 초기 운영값이며, 공급자가 보장한 성능이나 업계 표준 수치가 아니다.

정책·응답 스키마·SUPPORT 우선순위는 전체 설계, 모델의 통과 조건은 [평가 설계](gemini-moderation-evaluation.md), 일반 배포·복원은 [기존 운영 기준](delivery-and-operations.md)을 함께 적용한다. 정상 게시에는 사람의 승인이 필요 없지만, 장애·유해 글 사고에는 대응 책임자가 필요하다. 실제 담당자 이름과 연락 경로는 배포 기록에 채운다.

실제 키를 넣어 시작하는 방법과 모의 검수·예산 원장·회로·운영 화면의 현재 구현은 [실행 가이드](gemini-moderation-implementation.md)를 따른다. mock의 사용량과 금액은 합성 계측이며 실제 공급자 청구·지연·정책 품질의 수락 증거가 아니다. 이 문서의 담당자 지정·현장 대응·정기 canary·외부 경보는 운영 수락 계획으로 구분하고 실행 기록 없이 완료로 표시하지 않는다.

## 현재 구현에서 유지할 경계

| 현재 코드 | 유지할 성질 | Gemini 프로필의 적용 경계 |
| --- | --- | --- |
| [작업자](../backend/app/worker.py) | singleton 감독 프로세스, 동시 작업 1개, START/ACK/DONE, 프로세스 종료 경계 | 네트워크 호출도 동일 실행 경계 안에 둔다. concurrency만 올리지 않는다. |
| [claim·finalize](../backend/app/moderation.py) | DB 시각, 실행 token, 유효 lease, 정책 버전, 기한을 다시 확인 | provider 호출 이력과 비용 예약을 추가하되, 늦은 응답의 승인 권한을 되살리지 않는다. |
| [설정](../backend/app/config.py) | inference timeout 상한 60초, 별도 모델 readiness | Gemini 프로필은 15초 lease와 아래 별도 호출 예산을 사용한다. 기존 프로필 기본값을 몰래 변경하지 않는다. |
| [접수](../backend/app/submissions.py), [재생 접수](../backend/app/playback.py) | 접수 전체 기한 120초 또는 KST 자정 중 빠른 시점, 멱등 키, 혼잡 제어 | 두 접수 경로 모두 provider 회로·쿼터·예산 검사를 적용한다. |
| [로더](../backend/inference/loader.py) | 검증된 로컬 모듈·manifest·artifact hash와 배포 버전 일치 | 원격 프로필의 릴리스 hash를 검증한다. 유료 startup probe는 별도 원장을 거쳐 작업자가 실행하며 로더가 중복 warmup하지 않는다. |

현재 readiness는 5초 갱신·15초 유효인 작업자 생존/준비 임대다. Gemini 장애만으로 API 조회·긴급 숨김·이미 승인된 장면까지 차단하지 않는다. 신규 접수 가능 여부에는 **작업자 준비 + provider 상태 + 전광판 상태 + 예산/쿼터 여유**가 모두 필요하다.

새 Gemini 프로필의 `auto_strikes=false`를 릴리스 설정에 고정했다. 규칙에서 강한 욕설이 발견돼도 먼저 SUPPORT 여부를 판별하고, SUPPORT이면 공개하지 않으며 벌점을 주지 않는다. SUPPORT가 아니면 검증된 로컬 차단 규칙이 Gemini ALLOW보다 우선한다. 기존 규칙 우선·벌점 프로필은 별도 프로필로 남긴다. Gemini 분기에서는 규칙 즉시 확정을 미루고 모델 판정 계열로 최종화한다.

## 한 제출의 시간 예산과 호출 횟수

| 항목 | Gemini 프로필 초기값 | 동작 |
| --- | --- | --- |
| claim lease | 15초 | 현재 허용 상한 60초 안의 설정값. 실제 lease는 제출 전체 기한보다 길 수 없다. |
| provider 전체 workflow | 최대 8초 | countTokens와 생성 두 요청의 연결·송신·응답 수신·대기를 포함한 단일 monotonic deadline. 개별 socket timeout 합으로 대체하지 않는다. |
| 연결 timeout | 최대 2초 | DNS·TLS 등 호출에 실제 사용하는 경로의 연결 예산을 포함한다. |
| 전처리·요청 구성·응답 검증 | 합계 최대 1초 | 로컬 정규화·PII 표시 치환·JSON 검증 포함. 무제한 정규식·JSON 수리는 허용하지 않는다. |
| 로컬 감정 분류 | 전체 12초 wrapper의 남은 시간 | ALLOW 뒤에만 실행. 일반 오류는 NEUTRAL. 별도 1초 강제 종료는 없으며 멈추면 전체 기술 실패·worker 회수. 안전성 실패에는 fallback 금지. |
| 최종 DB 확정 여유 | 최소 2초 | 최종화 재시도와 원장 정리를 위한 여유. 실제 잔여 lease가 부족하면 호출을 시작하지 않는다. |
| 분석 wrapper | 최대 12초 | supervisor의 15초 hard fence보다 먼저 종료한다. |
| SDK/HTTP 자동 재시도 | 0회 | 라이브러리의 기본 retry·redirect 재전송·프록시 retry도 비활성화한다. |
| 동일 제출의 provider dispatch | workflow 최대 1회 | countTokens 1회 뒤 생성 최대 1회. 429·5xx·timeout·잘못된 JSON·BLOCK·UNCERTAIN·안전 차단 모두 재호출하지 않는다. |

worker가 claim을 얻은 직후 DB 시각으로 잔여 lease를 읽고 monotonic deadline으로 옮긴다. `remote_deadline = min(now_monotonic + 8초, lease_deadline - 4초)`로 정하며, 12초의 전체 wrapper 예산을 확보할 수 없는 claim은 원격 호출 없이 기술 실패로 끝낸다. 네트워크에서 시간을 모두 쓰고 DB 확정을 억지로 진행하지 않는다. finalization은 여전히 DB 시각·token·lease를 최종 권위로 검사한다.

현재 `AnalysisInput`에는 text만 있으므로 deadline·submission/run 식별자를 전달할 신뢰된 서버 내부 실행 context가 필요하다. 학생 글이나 모델 JSON이 deadline·run token·예산을 결정하게 하지 않는다. HTTP 전체 상한을 보장하지 못하는 동기 호출은 기존 supervisor의 프로세스 종료 경계로 회수한다. 로컬 프로세스를 죽여도 공급자 측 계산·과금이 취소됐다고 단정하지 않는다.

재시도는 **모델 실행 이전의 기존 DB claim 재시도와 이미 받은 같은 결과의 DB finalize 재시도**만 유지한다. 이때도 token·lease fence를 적용한다. 응답을 잃은 요청이 실제 처리됐는지 알 수 없으므로 provider request ID를 공급자의 멱등 실행 보장으로 해석하지 않는다. 사용자가 실패 후 다시 제출할 때만 새 멱등 키의 새로운 업무가 만들어진다. 같은 키 재전송은 기존 영수증을 반환하며 모델을 호출하지 않는다. ALLOW가 나올 때까지 반복 분류하거나 다른 모델로 바꾸지 않는다.

접수 혼잡 예측의 초기 `moderation_p95_seconds=8`, `moderation_backlog_limit=8`을 사용하되 **현장 측정 전 가정**으로 표시한다. 기존 `(대기 수+1)×추정 시간 < 잔여 120초` 검사는 유지하고, 최근 15분 p95가 8초를 넘으면 추정값을 올리거나 접수를 줄인다. 호출 8초 상한이 있다는 이유로 실제 성공률·큐 지연을 달성했다고 보고하지 않는다. 큐 대기를 포함한 기한 만료와 provider timeout은 따로 집계한다.

## 결과와 실패를 구분하는 원칙

| 관측 결과 | 분류 | 처리 |
| --- | --- | --- |
| 유효한 정책 JSON의 ALLOW | 의미 판정 | 로컬 확정 차단·지원 우선순위와 버전 검사 후에만 승인 |
| 유효한 BLOCK | 의미 판정 | 비공개, 고정 수정 안내, 벌점 없음 |
| 유효한 UNCERTAIN | 의미 판정 | 비공개, 문맥을 명확히 다시 쓰는 고정 안내, 벌점 없음 |
| 유효한 SUPPORT | 지원 판정 | 비공개 지원 화면, 일반 위반 안내·벌점 없음 |
| 공급자 safety/prompt/response block·refusal | `PROVIDER_SAFETY_BLOCK` | 비공개. 무드월 정책 BLOCK으로 꾸미지 않고 지원 안내 진입점을 함께 제공 |
| 429 | `PROVIDER_RATE_LIMITED` | 기술 실패, provider cooldown 반영, 동일 제출 재시도 없음 |
| 인증·권한 오류 | `PROVIDER_AUTH_ERROR` | 기술 실패, 회로 즉시 중단, 키·프로젝트 조사 |
| 5xx·연결 실패 | `PROVIDER_UNAVAILABLE` | 기술 실패, 회로 집계, 동일 제출 재시도 없음 |
| 호출/실행 기한 초과 | `PROVIDER_TIMEOUT` 또는 기존 `INFERENCE_TIMEOUT` | 기술 실패, 늦은 결과 폐기 |
| 빈 응답·incomplete/failed·truncation·tool/refusal 출력·스키마/enum/CP ID 오류 | `INVALID_PROVIDER_OUTPUT` | 기술 실패. JSON 수리·재질문·일부 필드 추정 금지 |
| 예산·쿼터 예약 실패 | `PROVIDER_CAPACITY_UNAVAILABLE` | dispatch 전이면 호출하지 않음. 이미 수락한 업무는 제재 없는 실패/기한 만료 |

표의 오류 이름은 내부 원장 분류이며 공개 API enum과 구분한다. 공개 API에는 전체 설계가 정한 일반 오류와 고정 안내만 내보내며, 공급자 오류 본문·정책 프롬프트·키·토큰 수·내부 상태를 학생에게 노출하지 않는다.

사용할 wire API는 **Interactions REST `POST /v1beta/interactions`, `Api-Revision: 2026-05-20`**로 고정한다. `store=false`, `background=false`를 명시하고 `previous_interaction_id`·tools·검색·파일·대화 이력을 사용하지 않는다. `generation_config`는 `max_output_tokens=512`, `thinking_level=minimal`, `thinking_summaries=none`으로 고정한다. `generateContent`의 요청/응답 문법을 혼용하지 않는다. [Interactions 공식 문서](https://ai.google.dev/gemini-api/docs/interactions)

응답은 `status=completed`이고 마지막 `model_output`이 단일 text JSON인 경우에만 엄격한 정책 스키마 검증으로 넘긴다. failed/incomplete·tool call·refusal·다른 출력 형식은 승인으로 해석하지 않는다. Interactions의 HTTP 400 `safety`·`jailbreak`·`prohibited_content`·`blocklist` 등 공급자 안전 차단도 해당 API fixture로 검증한다. 공급자 안전 차단을 없애려고 설정을 자동 완화하거나 입력을 재작성해 재전송하지 않는다. [공급자 안전 필터 설명](https://ai.google.dev/gemini-api/docs/safety-settings)

## 프로젝트 공통 쿼터와 비용 상한

Google의 한도는 API key별이 아니라 **프로젝트별**이며 RPM·입력 TPM·RPD 등을 함께 적용한다. 실제 한도는 모델·프로젝트 상태에 따라 달라지므로 배포 시 계정의 활성 한도를 기록한다. RPD의 공급자 일자는 `America/Los_Angeles` 자정을 따른다. KST 업무일과 혼동하지 않는다. [공식 rate limit 설명](https://ai.google.dev/gemini-api/docs/rate-limits)

무드월 전용 프로젝트를 사용한다. 다른 서비스가 같은 프로젝트를 사용한다면 모든 키의 호출·시험·복구 probe가 **동일 원장**을 지나야 한다. 그 외 소비를 통제할 수 없으면 아래 상한은 무드월 내부 상한일 뿐 프로젝트 전체 지출 상한으로 주장하지 않는다. 키를 여러 개 만드는 것으로 쿼터를 늘리거나 장애를 우회하지 않는다.

| 내부 한도 | 초기값 | 계산 범위 |
| --- | --- | --- |
| 동시 provider workflow | 1 | 현재 singleton 작업자 + 별도 시험/probe도 같은 원장 사용 |
| RPM | 6 | rolling 60초, 프로젝트·모델 공통. countTokens+생성 두 HTTP를 예약하므로 workflow 약 3회/분 |
| 입력 TPM | 200,000 | rolling 60초. 두 HTTP 각각에 입력 예약량을 보수적으로 반영 |
| RPD | 1,000 | 공급자 일자 기준의 내부 HTTP 예약 수. startup·복구 probe·시험도 포함 |
| 개별 입력 토큰 예약 상한 | 16,384 | 시스템 정책·예시·스키마·학생 글·trusted findings 전체 포함 |
| 출력 토큰 상한 | 512 | Interactions의 `max_output_tokens`로 thinking과 보이는 JSON의 합계를 제한. 사고 분석용 장문 설명 생성 금지 |
| 일일 모델 사용 예산 | USD 3.00 | KST 자정 기준의 별도 비용일. 세금·환율·서버비는 별도 |
| 경보 계획 | 예산 50%·80%, 실패 1회 | 외부 알림은 미구현 운영 계획. 예산 100% 또는 다음 예약 불가의 신규 dispatch 차단은 구현 |

배포 시 내부값을 검증된 공급자 잔여 한도 이하로 설정한다. 현재 코드는 공급자 계정의 할당량을 자동 조회하지 않으며 기본 내부값을 적용한다. 예상 학생 수에 맞춰 값을 올리려면 평가 부하와 예상 비용을 함께 다시 확인한다. 공급자 표의 숫자를 코드에 복사해 영구 사실로 취급하지 않는다.

현재 입력은 [정규화 코드](../backend/app/domain.py)에 따라 **1~30 grapheme cluster, UTF-8 최대 2,048byte**다. 결합 문자·ZWJ 때문에 30 cluster를 30 code point나 30 token으로 계산하지 않는다. 실제 transport는 매 workflow에서 정책·학생 입력·스키마를 `countTokens`로 사전 확인하고 1,024토큰 여유를 더한다. 합계가 16,384를 넘으면 생성을 호출하지 않고 설정/입력 한도 실패로 처리한다. 사전 확인의 generateContent 표현과 Interactions의 실제 토큰 계산 차이가 있으므로 이 여유는 **보수적 추정이며 과금 상한 보증이 아니다.** 예약 초과 usage는 실제액으로 정산하고 신규 접수를 중단한다. 두 요청을 자체 RPM/RPD/TPM에 넣는 것은 내부의 보수적 제어이며 공급자가 countTokens를 생성 쿼터와 똑같이 차감한다는 주장이 아니다.

Interactions의 `max_output_tokens=512`는 thinking과 보이는 출력의 합계에 적용하는 hard cutoff로 사용한다. `thinking_level=minimal`은 thinking token 0개를 보장하는 설정으로 해석하지 않는다. `thinking_summaries=none`으로 reasoning summary를 요청하지 않고 trace도 저장하지 않는다. 512 token 안에서 JSON을 끝내지 못하면 incomplete 기술 실패로 끝내며 추가 생성하지 않는다. 무료 토큰·캐시 적중·할인·청구 크레딧을 가정해 예약 비용을 줄이지 않는다. [공식 thinking·토큰 상한 설명](https://ai.google.dev/gemini-api/docs/thinking#token-limits)

현재 실제 프로필의 단가는 [설정 코드](../backend/app/config.py)의 공식 standard-tier 유효일 구간에서 시작 시 자동 선택한다. 2026-11-04~2026-12-31은 입력/출력 백만 토큰당 USD 0.75/3.75, 2027-01-01~2027-12-31은 USD 1.50/7.50이다. 출력에는 thought를 합산한다. 이 값과 날짜는 릴리스 hash에 포함하고 키만으로 시작할 때 별도 단가 입력을 요구하지 않는다. 고정한 단가 구간의 유효일이 지나거나 workflow가 가격 경계를 넘을 가능성이 있으면 신규 접수·dispatch를 닫는다. 새 구간을 적용하려면 접수를 중단하고 이전 작업을 정리한 뒤 API·worker를 함께 재시작해 새 릴리스로 연결한다. 알려진 구간 밖은 과거 가격을 연장하지 않고 설정 오류로 중단한다. 공급자의 추가 변경은 공식 가격표를 다시 확인해 코드·평가·릴리스를 갱신한다. [공식 가격표](https://ai.google.dev/gemini-api/docs/pricing)

```text
요청 예약액 = ceil_μUSD(
  입력 최대 토큰 × 해당 구간의 최대 입력 단가
  + 출력/thinking 합계 최대 512 token × 해당 범주의 최대 단가
)
하루 사용 가능액 = 3.00 USD - 확정 사용액 - 미확정 예약액
```

백만 token 단가는 식에 넣기 전에 1 token 단가로 환산한다. 출력과 thinking의 단가가 다르면 상한 예약에는 높은 단가를 적용한다. provider의 usage 필드가 출력에 thinking을 이미 합산한다면 이중 계산하지 않는다. 정확한 합산 규칙은 선택 모델 fixture로 고정한다. 툴·검색·파일·명시적 캐시를 사용하지 않으므로 관련 비용은 0으로 설정하되, 요청 검증에서 해당 기능이 활성화되면 거절한다.

2026년 기본 예약은 workflow당 14,208 micro-USD(USD 0.014208)이며 2027년 구간은 두 배다. 이는 16,384 입력+512 출력 예산으로 산정한 **내부 예약액**이다. 사용량을 검증하면 실제액으로 정산하고, 인증 오류·429·사전 확인 단계 실패처럼 사용량이 없으면 보수적으로 예약액을 내부 비용으로 남긴다. 이것이 해당 오류에 공급자가 같은 금액을 청구했다는 의미는 아니며 실제 청구서는 별도 대조한다.

### 원자적 예약과 정산

현재 `0007_provider_moderation`의 DB 원장은 다음 구조다. 미래 다중 전광판 전용 gate나 별도 rate 예약 테이블을 이미 구현한 것으로 표시하지 않는다.

- `moderation_provider_control`: 공통 회로·cooldown·generation·수동 중단 상태.
- `moderation_provider_daily`: 비용일별 확정 micro-USD·HTTP 수·토큰 집계.
- `moderation_provider_attempts`: `submission_id` 기본키로 workflow를 한 번만 생성하고 profile·release ID·run token·비용일·쿼터일·`RESERVED/STARTED/COMPLETED/UNKNOWN/CANCELLED`·예약/실제 토큰과 비용·시각을 기록한다. 실제 요청은 두 HTTP로 자체 쿼터를 계산한다. 합성 probe도 고유 ID로 같은 원장을 사용한다. 원문 source에 cascade되는 외래키는 두지 않는다.

API의 새 접수 트랜잭션에서 정책/버전과 provider 상태를 확인하고, 예산·쿼터·해당 workflow 예약을 함께 생성한다. 부족하면 503으로 거절하고 학생 게시 쿼터나 비용을 점유하지 않는다. 멱등 키의 기존 결과 조회는 예약 전에 처리한다. RPM/TPM/RPD는 예약 행을 보수적으로 포함하고 worker가 dispatch 직전에 다시 확인한다. permit 대기로 실행 lease를 소모하지 않으며 확보 불가이면 비공개 기술 실패로 끝낸다.

RPD 예약은 dispatch 때 사용으로 이동하며 두 번 더하지 않는다. 접수 후 공급자 일자가 바뀌었다면 이전 날짜 예약 해제와 새 날짜 예약 확보를 한 트랜잭션에서 처리하고, 새 날짜 여유가 없으면 호출하지 않는다. RATE/비용 원장의 TTL만으로 STARTED·UNKNOWN 예약을 자동 해제하지 않는다. 원장 정리 뒤에도 UNKNOWN 최대 비용은 일별 확정 집계에 남긴다.

모든 경로는 `provider_control → 비용일(날짜순) → rate 범위(정렬) → 전광판/참여자/제출의 기존 잠금 순서`의 한 방향을 따르도록 구현한다. 기존 참여자 잠금 안에서 역순으로 provider 원장을 잠그는 보조 경로를 만들지 않는다. provider 원장 정산은 업무 최종화와 분리된 짧은 트랜잭션으로 하고, 필요한 기존 데이터는 스냅샷을 읽고 재확인한다. 외부 HTTP 중에는 DB 트랜잭션·행 잠금을 보유하지 않는다.

dispatch 전 `STARTED`를 DB에 먼저 확정하고 그 뒤 한 번만 송신한다. 이 원장 전이가 실패하면 호출하지 않는다. 프로세스 재시작·stalled job·수동 큐 재시도는 STARTED 이력의 재송신 권한을 만들지 않는다. 실제 네트워크에 도달하기 전 crash라도 송신 여부를 증명할 수 없으면 보수적으로 UNKNOWN이다.

성공 응답의 usage가 검증되면 실제액으로 정산하고 남은 예약을 해제한다. 응답이 없거나 usage를 믿을 수 없으면 UNKNOWN으로 남기고 **예약 최대액을 비용으로 간주**한다. 외부 청구 확인 없이 환급하지 않는다. dispatch 전임이 원장 상태로 증명되는 만료·취소만 예약을 해제한다. 불명확한 호출을 별도 재호출해 정산하지 않는다. 실제 사용량이 예약을 초과하면 즉시 전체 provider 회로를 열고 단가/상한 오류를 조사한다.

학생 quota의 반환과 provider 과금은 별개다. timeout으로 게시 한도를 반환해도 이미 사용한 provider 비용이 되돌아오지는 않는다. KST 자정에는 새 비용일을 만들되, 진행 중 요청은 dispatch 시 고정한 비용일에 정산한다. KST 자정 만료 제출을 새 비용일로 재호출하지 않는다.

## 회로 차단과 준비 상태 복구

구현의 상태 이름은 `OPEN`이 정상 접수 가능, `OPEN_CIRCUIT`이 장애 차단, `HALF_OPEN`이 복구 검사, `PAUSED`가 수동/설정 오류 중단이다. 일반적인 회로 문헌에서 OPEN을 장애 차단으로 부르는 표기와 혼동하지 않는다. worker는 시작 시 같은 예산 원장을 통해 합성 ALLOW probe를 한 번 통과한 뒤 READY가 된다. 이 호출도 실제 키에서는 유료 사용량이 생길 수 있다.

worker 생존 readiness와 provider 가용 상태를 분리한다. DB에 있는 provider 회로가 신규 접수의 즉시 권위이며, 모든 API 프로세스와 worker가 같은 상태를 읽는다. 단순 메모리 회로나 15초 heartbeat 만료만으로 전파하지 않는다.

| 원인 | 회로 초기 동작 | 복구 |
| --- | --- | --- |
| 연속 기술 실패 3회, 또는 최근 20개 중 5개 이상 | 60초 OPEN_CIRCUIT | 이후 합성 probe를 1개만 허용하는 HALF_OPEN |
| 429 | 최소 60초, 유효한 Retry-After가 더 길면 그 시각까지 OPEN_CIRCUIT | 이미 실패한 제출이 아닌 합성 probe로 확인. 과도한 Retry-After는 짧은 값으로 임의 절삭하지 않음 |
| 인증·권한 실패, schema/모델/manifest 불일치 | 즉시 PAUSED, 자동 회복 금지 | 설정을 고치고 배포 검증 후 기술 담당자가 해제 |
| 예약 초과·단가 오류 | 즉시 중단 | 비용 담당/기술 담당 확인 후 해제. UNKNOWN 증가는 별도 운영 조사 대상 |
| 예산/RPD 소진 | 신규 접수/dispatch 거절 | 새 일자라도 수동 중단·다른 장애가 남으면 자동 재개 금지 |
| 원문 유출·유해 글 송출 사고 | 수동 PAUSED + 전광판 긴급 중단 | 아래 사고 절차와 정책/평가 확인 후 명시적 재개 |

BLOCK·UNCERTAIN·SUPPORT는 정상 의미 결과이며 가용성 실패 횟수에 넣지 않는다. provider safety block은 별도 비율로 보고한다. 안전 차단만 급증하는 경우 정책 우회로 재시도하지 않고 잘못된 API 설정·모델 변경 가능성을 조사한다. 작은 표본의 비율만으로 자동 장애 선언하지 않는다.

HALF_OPEN은 30초 이상 간격으로 합성 정상 글 probe 2개가 연속 통과해야 정상 `OPEN`으로 돌아간다. probe는 운영과 같은 고정 모델 ID·Api-Revision·설정 hash·Interactions 출력 parser를 사용하고, completed의 유효한 정책 JSON이 기대 ALLOW인지 검사한다. 단순 HTTP 200만으로 회복시키지 않는다. probe도 시간·RPM/TPM/RPD·USD 예산에 포함하고 학생 글은 쓰지 않는다. 실패하면 다시 차단하며 provider 연결이 회복돼도 전광판 수동 중단은 풀지 않는다. 이전 generation의 결과나 probe가 최신 회로를 닫지 못하도록 조건부 갱신한다. 실패 횟수별 60→120→240→최대 900초의 자동 지수 대기는 향후 운영 조정안이며 현재 적용됐다고 표시하지 않는다.

provider 차단은 해당 모델을 사용하는 접수에 공통 적용한다. 개별 전광판의 공격/신고 사고는 해당 전광판의 intake와 송출을 별도로 중단한다. 현재 앱은 단일 BOARD_ID이므로 실제 적용은 그 1개이며, 다중 전광판 지원을 이미 구현했다고 가정하지 않는다. 상태 변경 시 서버 state revision을 올리고 SSE/polling으로 안내를 갱신한다. 최종 ALLOW 반영과 재생 동기화도 현재 gate를 다시 확인해 중단 이후 새 장면이 공개되지 않게 한다.

## 비밀·전송 데이터·보관

현재 Compose는 API·worker에만 API key를 주입한다. API는 설정·접수 가능 여부를 확인하고 실제 HTTP는 worker만 실행한다. 프론트 빌드·HTML·Vite asset·학생 응답·manifest 본문·로그·DB·migration·관리 명령에는 넣지 않는다. 키는 설정의 SecretStr에 담고 repr·release hash·직렬화 출력에서 제외한다. Google이 제공하는 API 제한을 적용하고 HTTPS의 고정 origin·countTokens/Interactions 두 경로만 호출한다. 키를 URL query에 붙이지 않고 검증된 요청 header로 전달한다. 임의 base URL·redirect·학생 지정 URL은 거절한다.

provider에 보내는 것은 정규화된 이번 글, 고정 정책/예시, 서버가 만든 최소한의 trusted local findings뿐이다. participant/session/receipt/run UUID, IP, 학교·반 명부, 쿠키, 이름-별명 매핑, 이전 게시글·대화·주변 학생 글은 보내지 않는다. 명확한 전화번호·이메일·연락처 등의 로컬 탐지는 메모리에서 `[CONTACT]` 등으로 치환하고 서버의 개인정보 차단 근거로 유지한다. 원문을 저장한 redaction 매핑을 새로 만들지 않는다. 이름·은어를 완벽히 익명화했다고 주장하지 않으며, 공격의 대상/관계 맥락을 지우는 과도한 치환도 평가한다.

운영은 유료 서비스 프로필로 구성한다. 공식 조건상 무료 서비스는 입력·출력을 개선에 사용할 수 있고 사람 검토가 포함될 수 있으며, 유료 서비스는 제품 개선에 사용하지 않는 조건을 구분한다. **유료이거나 `store=false`라는 이유로 공급자 보관이 0일이라고 안내하지 않는다.** store 설정은 Interaction 저장 선택이며 공급자 측 남용 탐지·운영 기록과 우리 DB 보관은 별개다. [데이터 처리 조건](https://ai.google.dev/gemini-api/terms)

| 우리 쪽 데이터 | 신규 보관 기준 |
| --- | --- |
| 학생 원문 | 기존 더 짧은 재생/비공개 정리 규칙과 최대 1시간 원문 정책 유지. 검수 도입을 이유로 보관 연장하지 않음 |
| 학생 영수증 | 기존 24시간. SUPPORT 상세를 공개 목록·전광판·다른 학생에게 제공하지 않음 |
| per-call 운영 감사 | 7일. call/submission 상관 ID, 릴리스/모델/정책/스키마 버전, 판정 종류, 고정 사유 코드, latency, usage/cost, 내부 오류 분류만 보관 |
| 청구 집계 | 일자·프로젝트·모델별 금액/토큰 합계 90일. 7일 뒤 개별 제출과 연결 제거 |
| 보안/운영자 행위 감사 | 기존 [보안 정책](security-and-operations.md)의 범위·기한. 새 원문 복제 금지 |

원문·완성된 prompt·모델 원시 응답·예외 body·생성 설명·reasoning trace·원문 hash는 감사 로그에 남기지 않는다. SUPPORT와 사유 코드는 민감할 수 있으므로 제한된 운영 권한에만 보이고, 지표 label에는 board/student/submission ID를 넣지 않는다. 프록시·HTTP instrumentation·APM·error collector가 body/header를 자동 수집하지 않는지 유출 fixture로 확인한다. 백업에도 원문 보관 정책을 적용하고 복원 후 만료 데이터를 먼저 정리한다. 원문을 학습 데이터로 자동 편입하지 않는다.

## 배포·canary·rollback

릴리스 bundle은 정책 CP ID·prompt/예시 hash·JSON schema hash·모델의 구체 stable ID·`/v1beta/interactions`와 `Api-Revision: 2026-05-20`·generation/safety/thinking/store 설정·SDK 또는 HTTP client lock·tariff·quota·실행 프로필·평가 보고서·UI 안내 버전을 함께 고정한다. `latest`나 자동 모델 선택을 사용하지 않는다. 이 API에서 `model_version` 응답 필드를 보장한다고 가정하지 않는다. 모델 ID와 설정 hash를 고정해도 원격 가중치의 불변성을 보장하지 않으므로 변화는 canary와 정기 평가로 감시한다.

1. 합성 평가·오류 fixture와 원장 동시성/복원 시험을 먼저 통과시킨다. 실제 키가 없는 개발에서는 mock 전용이며 결과에 운영 준비 표시를 붙이지 않는다.
2. 시험 프로젝트/전광판에서 합성 글로 호출 예산·유료 프로필·버전·usage 정산·SUPPORT 화면·hide/stop을 확인한다. 시작 probe를 로더와 운영 도구에서 중복 실행하지 않는다.
3. 운영 신규 접수를 멈춘다. 기존 PENDING/PROCESSING을 구 버전에서 끝내거나 제재 없이 terminal화한 후 구 worker 종료를 확인한다. 새 버전으로 옛 업무를 재분류하지 않는다.
4. migration은 배포 작업 한 곳에서 실행한다. 새 프로필과 원장을 시작하고 singleton·API ready·worker readiness·provider 회로·예산을 각각 확인한다.
5. 제한한 시험 전광판/운영 시간에서 새 접수를 열고 15분 또는 실제 완료 30건 중 늦은 조건까지 관찰한다. 처음에는 실제 원문을 구·신 공급자에 이중 전송하지 않는다. 나머지 전광판은 중단 상태를 유지한다.
6. 평가 보고서의 기능/품질 gate를 만족하고 새로운 유해 송출·원장 불일치가 없을 때 기술 담당자가 범위를 넓힌다. 표본 30건은 품질 보증을 위한 평가셋을 대체하지 않는다.

다음은 즉시 중단 조건이다: 미검수 송출, 늦은 결과 승인, SUPPORT 벌점/공개, 같은 제출의 이중 dispatch, 실제 비용의 예약 초과, 원문/키 유출, 잘못된 정책 버전으로 승인. 복구 시 검증된 이전 **동일 프로필 bundle**로 돌아가며, 검증되지 않은 학생 모델·다른 Gemini 모델·규칙만 통과한 글로 자동 대체하지 않는다. 적합한 이전 버전이 없으면 새 접수를 중단하고 이미 승인된 장면이나 고정 배경을 유지한다.

## 사고 대응과 역할

| 역할 | 책임 | 실제 배포에서 남길 값 |
| --- | --- | --- |
| 서비스 운영 책임자 | 전광판 숨김·송출/접수 중지·재개 결정 | 담당자, 대체 담당, 사용 가능한 시간, 연락 경로 |
| 기술 대응 담당 | provider/DB/키·원장·배포 조사, rollback | 담당자, 대체 담당, 경보 경로 |
| 학생 지원 책임자 | 학교가 정한 비공개 지원 절차·안내 문구 관리 | 학교의 실제 절차와 담당 경로. 기능이 없으면 없다고 명시 |
| 비용 책임자 | 예산 변경·청구 UNKNOWN 조정 | 권한 보유자와 변경 이력 |

정상 글은 이 역할들의 사전 승인을 기다리지 않는다. 누군가 상주한다고 가정하지 않으며 대응 담당이 없는 시간에는 자동 회로 중단을 유지한다. SUPPORT 결과만으로 교사·보호자·외부기관에 연락하거나 학생을 추적하는 기능을 만들지 않는다. 실제 연결 기능이 없으면 “담당자에게 전달됨” 또는 “곧 연락함”이라고 안내하지 않는다.

| 상황 | 바로 할 일 | 재개 조건 |
| --- | --- | --- |
| 유해 글이 공개됨 | 해당 글 hide와 전광판 송출/접수 중단, 원문 없는 incident ID·버전 기록 | 보관 중인 자료는 제한된 기존 권한 안에서 확인. 원인 예문을 비식별 합성 재현하고 평가 보완, bundle 검증 후 명시적 재개 |
| Gemini 장애·429 | 자동 OPEN 확인, 학생 한도 반환/기한 처리 확인, 고정 실패 안내 | 회로 probe와 쿼터/예산 회복. 실패 글 자동 재송출 금지 |
| 키 노출 의심 | 해당 키 비활성·교체, 접수 중단, 저장소/로그/브라우저 경로 조사 | 이전 키 폐기·접근 원인 제거·과금 확인·합성 smoke 후 재개 |
| 과금 급증·UNKNOWN 증가 | 전역 dispatch 중단, 고유 호출 원장과 공급자 청구 비교 | 이중 호출/단가/토큰 오류 해소, 비용 책임자의 변경 기록 |
| SUPPORT 안내 문제 | 공개와 벌점 중단 불변식 우선 확인, 비공개 고정 안내로 정정 | 실제 지원 경로를 확인하고 화면·접근권한·지원 우선순위 회귀 시험 |
| DB 복원/worker crash | 접수 중단, 옛 generation과 dispatch 권한 폐기, STARTED 재송신 금지 | 기존 복원 sanitize, UNKNOWN 비용 보수 정산, 새 worker·합성 smoke 확인 |

경보 계획은 `technical_failure / semantic_nonpublish / provider_safety_block / SUPPORT / budget_denied / quota_denied`를 분리한다. 검수 지연에는 queue 시간과 HTTP 시간을 따로 남기고, 202 수락 전체의 실패·만료도 분모에 포함한다. ALLOW 비율이 높아졌다는 사실만으로 품질 향상이라고 해석하지 않는다. **현재 자동 합성 호출은 startup 연결 확인과 장애 복구 probe다.** 운영 시간 중 30분에 1회 실행하는 품질 canary와 외부 경보 연계는 향후 운영 계획이며 아직 실행 스케줄러를 구현하지 않았다. 도입할 때는 동일 한도·비용에 포함하고 canary 오류의 신규 접수 중단·원인 확인을 검증한다. 정책에 맞는 BLOCK은 가용성 장애로 취급하지 않는다.

구현 수락에는 정상 호출뿐 아니라 **timeout 후 늦은 응답·동일 키 재전송·worker 강제 종료·STARTED 후 crash·중복 finalize·quota 경합·자정 전환·OPEN 중 ALLOW 도착·SUPPORT와 욕설의 동시 존재·실제 사용량 없는 오류·가격 변경일·rollback 후 옛 queue job** 시험을 포함한다. 모두 통과했다는 실행 증거가 생기기 전에는 이 운영안을 이미 구현된 안전장치로 설명하지 않는다.
