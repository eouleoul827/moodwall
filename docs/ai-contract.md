# AI 담당과 서비스 백엔드의 계약

상태: AI 연결 계약 · 최초 검토: 2026-10-29 · v0 연결 확인: 2026-11-03

현재 학생 모델 v0의 로컬 CPU 추론·버전/해시 검사·검토 필요 결과 연결을 구현했다. 아래 계약의 게시 허용·안전성 평가 수락을 완료했다는 뜻은 아니다. 실제 코드·실행 방법과 남은 평가는 [v0 연결 현황](student-model-integration.md)을 우선 확인한다.

이 문서는 일반 백엔드 1명과 AI 담당 1명이 독립적으로 개발하기 위한 경계와 검증 조건을 정한다. 코드 예시는 인터페이스 명세이며, 실행 가능한 서버나 학습된 모델이 이미 존재한다는 뜻은 아니다. 시간·횟수·평가 기준은 초기 팀 정책이지 업계 공통 표준이 아니다.

AI 담당에게 보낼 짧은 요청문, 작업별 담당·완료 조건과 인계 순서는 [AI 담당 요청서](ai-team-request.md)에 있다. 이 문서가 상세 입력·출력 계약이고 요청서는 업무 분담용이다.

무엇을 공개할지는 2026-11-09 갱신한 [게시 허용과 차단 기준](content-policy.md)을 따른다. 외모에 관한 자기 감정·취향·스타일 비평·가벼운 행동 비판·비하 없는 농담, 실명·별명의 단순 언급과 이름을 포함한 감사·응원·일상·비강요 호감 표현은 허용한다. 특정 학생·사적 개인을 향한 외모 모욕·공개 망신·따돌림 유도와 공개 제한 식별정보·민감 사생활은 차단한다. 일상적 대명사·설명 생략만으로 나쁜 맥락을 추정하지 않는다. 콘텐츠 기준의 확정과 모델 승인 임계값·안전성 평가의 수락은 구분한다.

최신 요구는 **최근 60분간 3회**다. `accepted_at`이 최근 60분 안이고 상태가 `PENDING`, `PROCESSING`, `APPROVED`인 제출을 포함한다. 하루 2회 정책은 사용하지 않는다. 금칙어 위반 3회 경고·5회 정지는 한국 날짜 기준으로 별도 유지한다.

## Gemini 검수 프로필 추가 설계

Gemini를 서버 검수에 사용하는 구조는 [전체 설계·프롬프트·스키마](gemini-moderation-design.md)를 따른다. 아래 로컬 모델 계약을 그대로 외부 API에 보내지 않는다. 2026-11-10부터 새 provider 결정은 `ALLOW/BLOCK/UNCERTAIN`이며, 구체적인 위해 계획·방법·권유 등 별도 위반이 없는 자기 감정·두려움·도움 요청은 ALLOW 뒤 로컬 감정 분석 또는 중립 fallback으로 처리한다. SUPPORT·SELF_CRISIS·CP08은 새 요청에서 제외한다. 과거 provider SUPPORT를 내부 `BLOCK + SELF_CRISIS`, 영수증 `SUPPORT_AVAILABLE`로 읽는 호환 계약은 유지하되 원문 없이 중립적 미표시 문구만 표시하고 도움 안내는 제공하지 않는다. 새 프로필은 자동 벌점이 없다. [Gemini 연결 구현](gemini-moderation-implementation.md)은 개발용 mock·provider 원장·학생 화면 연결을 포함한다. [2026-11-09 부분 확인](checks/gemini-live-connection-2026-11-09.json)은 이전 정책의 실측 기록이며 새 정책의 품질·전체 앱·현장 수락을 대신하지 않는다. 감정 라벨 SUPPORT는 응원이므로 계속 사용한다.

## 1. 책임과 첫 번째 납품

| 항목 | AI 담당 | 서비스 백엔드 담당 |
|---|---|---|
| 데이터 출처·라벨 정의·학습·평가 | 책임 | 정책·학교 상황 예문 검토 |
| 모델·토크나이저 로딩, CPU 추론 모듈 | 책임 | 작업자에서 모듈 호출 |
| 의존성 잠금·모델 파일 해시·실행 방법 | 책임 | 배포 이미지·설정 관리 |
| 금칙어 사전·우회 규칙 | 테스트 예문 공동 검토 | 판정·버전·제재 책임 |
| 세션·QR·사용량·DB·작업 큐 | 계약 협의 | 책임 |
| 게시 결정·위반 집계·전광판·운영 API | 분류 결과만 제공 | 책임 |
| 잘못된 AI 차단·누락 분석 | 모델 수정 책임 | 운영 기록·숨김·피드백 경로 |

AI 담당의 납품물은 가중치 파일만이 아니다. 다음을 함께 제공한다.

1. `Analyzer` 계약을 구현한 Python 모듈과 명령행 추론 예제.
2. 토크나이저, 모델 설정, 가중치, 클래스 순서, 임계값, 전처리 버전.
3. CPU 설치·실행 방법, 잠근 의존성, 모델 파일별 SHA-256 목록.
4. 라벨 지침, 분리된 평가셋, 평가 결과, 알려진 실패 유형.
5. 입력·출력 fixture, 오류 fixture, CPU 지연·메모리 측정 결과.
6. 원본 모델·데이터의 출처와 사용·배포 가능 범위.

초기에는 별도 HTTP 모델 서버를 만들지 않는다. PostgreSQL 작업 큐를 소비하는 독립 Python 프로세스가 같은 저장소의 추론 모듈을 호출한다. CPU 추론은 API 프로세스에서 실행하지 않는다. FastAPI 공식 문서도 머신러닝 같은 CPU 중심 작업과 웹의 동시성 처리를 구분한다. [FastAPI 설명](https://fastapi.tiangolo.com/async/)

첫 공동 납품은 실제 모델보다 **계약을 만족하는 fake adapter**로 한다. 정상·차단·불확실·예외·지연 결과를 재현해 프론트와 백엔드를 먼저 연결한다. fake adapter는 개발·테스트에서만 허용하고 운영 환경에서 선택되면 시작을 실패시킨다.

## 2. 모델의 역할

KcELECTRA는 후보 베이스 모델이다. 한국어 댓글 기반 사전학습 모델이라는 점은 학생 말투 실험에 유리할 수 있지만, 학교 비방 탐지나 아래 8개 감정 분류 성능이 검증된 완제품은 아니다. 해당 작업에 맞춘 학습·평가가 필요하다. [제작자 모델 카드](https://huggingface.co/beomi/KcELECTRA-base)

감정과 공개 적합성은 별도 결과로 낸다. 서로 다른 두 모델, 공유 인코더의 두 분류 헤드 중 어떤 구현을 쓸지는 AI 담당이 측정 후 선택한다. 서비스 계약은 바꾸지 않는다.

| 축 | 값 | 의미 |
|---|---|---|
| 공개 적합성 | `ALLOW` | 평가·임계값 정책상 공개 가능 |
| 공개 적합성 | `BLOCK` | 모델이 부적절하다고 분류 |
| 공개 적합성 | `UNCERTAIN` | 문맥·지원 범위·점수로 결정하기 어려움 |
| 감정 | `CELEBRATION`, `JOY`, `SUPPORT`, `EXCITEMENT`, `TIRED`, `SADNESS`, `ANGER`, `NEUTRAL` | 애니메이션 선택용 |

`ANGER`와 `SADNESS`는 차단 사유가 아니다. 반대로 축하나 응원 문장도 저격·조롱이면 차단될 수 있다. 모델의 `ALLOW`도 무해함을 보장하는 인증은 아니다.

## 3. 입력·출력 인터페이스

다음은 Pydantic v2 기준 명세다. 필드·모델 검증으로 알 수 없는 필드와 모순된 결과를 거부한다. [Pydantic validators](https://docs.pydantic.dev/latest/concepts/validators/)

```python
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Emotion = Literal[
    "CELEBRATION", "JOY", "SUPPORT", "EXCITEMENT",
    "TIRED", "SADNESS", "ANGER", "NEUTRAL",
]
SafetyVerdict = Literal["ALLOW", "BLOCK", "UNCERTAIN"]
ReasonCode = Literal[
    "HARASSMENT", "HATE", "SEXUAL_CONTENT", "THREAT",
    "PERSONAL_INFORMATION", "OTHER_UNSAFE",
    "INSUFFICIENT_CONTEXT", "OUT_OF_DOMAIN",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, allow_inf_nan=False,
    )


class AnalysisInput(ContractModel):
    schema_version: Literal["1"] = "1"
    text: str = Field(min_length=1)


class SafetyResult(ContractModel):
    verdict: SafetyVerdict
    score: float | None = Field(default=None, ge=0, le=1)
    reason_codes: list[ReasonCode] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_reasons(self) -> Self:
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("duplicate reason code")
        if self.verdict == "ALLOW" and self.reason_codes:
            raise ValueError("ALLOW must not contain block reasons")
        if self.verdict != "ALLOW" and not self.reason_codes:
            raise ValueError("BLOCK/UNCERTAIN requires a reason code")
        return self


class EmotionResult(ContractModel):
    label: Emotion
    score: float | None = Field(default=None, ge=0, le=1)
    source: Literal["MODEL", "FALLBACK"] = "MODEL"

    @model_validator(mode="after")
    def validate_fallback(self) -> Self:
        if self.source == "FALLBACK":
            if self.label != "NEUTRAL" or self.score is not None:
                raise ValueError("fallback must be unscored NEUTRAL")
        return self


class AnalysisResult(ContractModel):
    schema_version: Literal["1"] = "1"
    model_bundle_version: str = Field(min_length=1, max_length=100)
    inference_policy_version: str = Field(min_length=1, max_length=100)
    safety: SafetyResult
    emotion: EmotionResult | None

    @model_validator(mode="after")
    def validate_combination(self) -> Self:
        if self.safety.verdict == "ALLOW" and self.emotion is None:
            raise ValueError("ALLOW requires emotion or NEUTRAL fallback")
        if self.safety.verdict != "ALLOW" and self.emotion is not None:
            raise ValueError("non-public result must not contain emotion")
        return self


class Analyzer(Protocol):
    def analyze(self, value: AnalysisInput) -> AnalysisResult: ...
```

`Analyzer`는 로딩을 마친 뒤 호출하는 동기 인터페이스다. 큐 작업자와 감시 프로세스가 실행 시간과 재시도를 책임진다. `analyze()`에 넘기는 값은 문장뿐이며 쿠키·IP·익명 참여자 ID·학생 식별 메타데이터를 전달하지 않는다. 본문에 학생이 적은 이름·별명은 포함될 수 있다.

상위 작업 envelope의 `submission_id`, `run_token`, `expires_at`은 DB 작업 조율용이다. 모델 입력과 분리한다. `policy_version`이라는 모호한 필드 하나에 섞지 않고, 모델의 임계값·전처리는 `inference_policy_version`, 서비스의 게시·제재 규칙은 `moderation_policy_version`, 사전 규칙은 `lexicon_version`으로 기록한다.

점수는 해당 모델에서 정의한 점수다. 검증 없이 실제 확률로 해석하거나 모든 모델에 공통인 `0.8` 같은 임계값을 적용하지 않는다. 모델 버전별 점수 의미와 `ALLOW/BLOCK/UNCERTAIN` 경계를 artifact manifest에 기록한다. 미지원 안전성 범주를 지원한다고 주장하지 않는다.

### 정상 결과 예시

```json
{
  "schema_version": "1",
  "model_bundle_version": "moodwall-ko-v1",
  "inference_policy_version": "ko-short-1",
  "safety": {"verdict": "ALLOW", "score": 0.97, "reason_codes": []},
  "emotion": {"label": "JOY", "score": 0.86, "source": "MODEL"}
}
```

모델 버전·점수·상세 reason code는 학생 응답에 그대로 노출하지 않는다. 학생은 `NEEDS_REWRITE`와 고정된 일반 안내를 받는다. 탐지 규칙과 확신도 조절 방법을 응답으로 제공하지 않는다.

### 서비스 내부 최종화 결과

`AnalysisResult`는 AI가 실제 실행됐을 때의 결과만 표현한다. 확정 사전 규칙은 모델을 생략하므로, 백엔드는 별도의 tagged union을 사용한다. 아래 계약은 모델 담당의 `Analyzer` 반환 타입을 바꾸지 않는다. 제출 ID·run token은 외부 envelope이며 모델 입력에 전달하지 않는다.

```python
from typing import Annotated
from uuid import UUID


class RuleEvidence(ContractModel):
    rule_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    variant_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")


class RuleRejected(ContractModel):
    kind: Literal["RULE_REJECTED"] = "RULE_REJECTED"
    lexicon_version: str = Field(min_length=1, max_length=100)
    matches: list[RuleEvidence] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_matches(self) -> Self:
        keys = [(item.rule_id, item.variant_id) for item in self.matches]
        if keys != sorted(set(keys)):
            raise ValueError("rule evidence must be unique and sorted")
        return self


class ModelAnalyzed(ContractModel):
    kind: Literal["MODEL_ANALYZED"] = "MODEL_ANALYZED"
    analysis: AnalysisResult


class RunFailed(ContractModel):
    kind: Literal["RUN_FAILED"] = "RUN_FAILED"
    error_code: Literal[
        "PROVIDER_SAFETY_BLOCK",
        "INPUT_INVALID", "MODEL_NOT_READY", "TRANSIENT_DEPENDENCY",
        "INFERENCE_TIMEOUT", "INVALID_MODEL_OUTPUT", "WORKER_LOST",
        "POLICY_VERSION_MISMATCH", "LEXICON_ERROR", "INTERNAL_ERROR",
        "DEADLINE_EXCEEDED",
    ]


FinalizationOutcome = Annotated[
    RuleRejected | ModelAnalyzed | RunFailed, Field(discriminator="kind"),
]


class FinalizationEnvelope(ContractModel):
    submission_id: UUID
    run_token: UUID
    outcome: FinalizationOutcome
```

`RuleRejected`만 `REJECTED`와 위반 한 번을 만들며, `lexicon_version`은 제출·로드된 사전과 같아야 한다. 활성 CONFIRMED 매칭을 정렬·중복 제거한 뒤 처음 5개만 근거로 남긴다. 저장 시 `rule_evidence`에는 `{rule_id, variant_id}`만 담고 모델 버전·안전성·감정은 NULL, AI reason code는 빈 배열로 둔다. 운영자 DTO만 `ruleId`, `variantId`로 매핑한다. 매칭 원문·검색 뷰·offset은 저장하지 않는다.

`ModelAnalyzed`는 ALLOW면 승인, BLOCK·UNCERTAIN이면 NEEDS_REWRITE다. 실제 로드 manifest와 결과의 model·inference 버전이 다르면 `INVALID_MODEL_OUTPUT`이며 공개·제재하지 않는다. `RunFailed`는 시스템 실패 요약이며 모델 판정이나 규칙 근거로 대체하지 않는다. 최종화가 token·상태·기한·참여자 정지를 먼저 검사하므로 어떤 variant도 늦은 결과를 되살릴 수 없다. DB 제약은 `REJECTED`에만 비어 있지 않은 규칙 근거를 허용하고 가짜 모델 필드를 거부한다.

## 4. 원문과 정규화

API가 공백·길이·금지 제어문자·요청 크기 정책을 적용한 표시용 문자열을 저장한다. AI에는 그 문자열의 NFC 표현을 전달한다. 사용자에게 보이는 30자는 API의 공통 문자 계수 규칙으로 검사하며, Pydantic의 `len` 검사만으로 대체하지 않는다.

금칙어 검사용으로는 별도의 문자열을 만든다. 띄어쓰기·일부 구분 기호·한글 자모 변형 규칙은 버전과 테스트를 갖춘다. 임의 문자 삭제나 무제한 편집 거리 매칭을 확정 위반 근거로 쓰지 않는다. 모든 정규화가 욕설 우회를 완벽히 잡는다고 약속하지 않는다.

| 값 | 목적 | 보존·노출 |
|---|---|---|
| 표시용 문장 | 모델 입력·승인 후 전광판 표시 | 승인 글만 전광판 응답 |
| 사전 검색용 정규화본 | 검증된 우회 패턴 검색 | 공개하지 않음, 기본 별도 저장 안 함 |
| 탐지 rule ID | 운영 검토·규칙 회귀 확인 | 제한된 운영자만 조회 |

HTML을 해석하는 입력 형식은 지원하지 않는다. React 화면도 문자열로 렌더링하며 모델 결과를 HTML로 사용하지 않는다. 이모지는 서버 allowlist의 ID로 받는다.

## 5. 최종 판정은 백엔드가 수행

검수 순서는 `금칙어 검사 → 모델 공개 적합성 → 감정 → 최종화`다. 금칙어가 확정 차단이면 비싼 모델을 호출하지 않는다.

| 조건 | 제출 최종 상태 | 최근 60분 한도 점유 | 당일 위반 수 |
|---|---|---|---|
| 검증된 사전 규칙 차단 | `REJECTED` | 해제 | 한 번 증가 |
| 모델 `BLOCK` | `NEEDS_REWRITE` | 해제 | 유지 |
| 모델 `UNCERTAIN` | `NEEDS_REWRITE` | 해제 | 유지 |
| 모델 `ALLOW` + 정상 감정 | `APPROVED` | 원래 accepted_at + 60분까지 유지 | 유지 |
| 공개 적합성 `ALLOW` 확인 후 감정만 실패 | `APPROVED`, 감정 `NEUTRAL/FALLBACK` | 원래 accepted_at + 60분까지 유지 | 유지 |
| 공개 적합성 실행 실패·잘못된 출력 | `FAILED` | 해제 | 유지 |
| 기한·제출 날짜 만료 | `EXPIRED` | 해제 | 유지 |
| 확정 시 이미 참여자 정지·운영 취소 | `CANCELLED` | 해제 | 유지 |

새 접수와 최종 상태 전이를 참여자 잠금 아래 직렬화한다. 한도는 `accepted_at > 검사시각 - 60분`인 `PENDING/PROCESSING/APPROVED`의 수로 판단한다. 승인 시각부터 60분을 다시 세거나 한국 자정에 초기화하지 않는다. 승인된 글을 숨기거나 화면 표시를 만료시켜도 승인 상태는 유지하므로 한도가 되돌아가지 않는다. 실패·거절·기한 만료는 최종 상태로 바뀐 뒤 한도에서 제외된다. 예약 반환은 이 논리적 점유를 해제하는 뜻이며, 하루 승인 카운터를 감소시키는 구현을 뜻하지 않는다.

감정 fallback은 **공개 적합성의 정상 `ALLOW` 결과가 확인된 경우만** 적용한다. 모델 전체 실패를 `NEUTRAL`로 바꿔 게시하지 않는다. 승인 후에는 모델이 임의로 애니메이션 코드를 생성하지 않고, 서버 allowlist 매핑을 사용한다.

사전 위반 3회에 경고, 5회에 당일 정지를 적용한다. 재전송·중복 큐 작업·모델 오류·운영자 수정으로 위반을 중복 증가시키지 않는다. 3회째 경고와 5회째 정지 여부는 원문이 아닌 당시 확정된 일일 집계에서 결정한다.

모델 거절이 제재되지 않더라도 처리 비용은 제한해야 한다. 새 idempotency key의 제출에는 참여자 기준 10초 간격과 검수 중 최대 1건을 적용한다. 같은 키의 정당한 재전송은 기존 결과를 돌려주며 새 시도로 계산하지 않는다. 학교의 공유 공인 IP를 개인 식별자처럼 제재하지 않는다. 전역 큐 혼잡은 접수 단계에서 거절하고 슬롯을 예약하지 않는다.

QR은 5분마다 교체하고 현재·직전 티켓을 인정하므로 티켓 하나의 유효기간은 발급 후 총 10분이다. 교환 시각부터 새 10분을 주지 않고 작성 허가의 만료를 **해당 티켓의 `validUntil`로 고정**한다. 30초 유예·당일 참여권은 사용하지 않는다. 새 접수 때 권한이 유효해야 하지만, 유효한 시각에 접수한 글의 검수는 QR 만료만으로 취소하지 않는다. 접수 120초·한국 자정 기한은 그대로 적용한다. 최신 티켓을 다시 스캔해도 동일 참여자의 rolling 횟수·당일 제재는 유지한다.

사용자 선택에 따라 학교 Wi-Fi·LTE·5G를 모두 허용하고 학교망 IP allowlist는 적용하지 않는다. QR만으로 교외 사용이나 실시간 링크 공유를 막을 수는 없다. AI 문서에서 현장 참여가 증명된다고 가정하지 않는다.

## 6. 오류 분류와 재시도

판정과 시스템 오류는 같은 값이 아니다. 예외를 임의로 `BLOCK` 또는 `ALLOW`로 변환하지 않는다.

| 내부 오류 | 예 | 기본 정책 |
|---|---|---|
| `INPUT_INVALID` | API 규칙을 통과하면 안 되는 문자열 | 자동 재시도 안 함, 정책·구현 오류 점검 |
| `MODEL_NOT_READY` | 파일 없음·해시 불일치·메모리 부족 | 작업자 readiness 실패, 신규 검수 접수 일시 중단 |
| `TRANSIENT_DEPENDENCY` | 단기 DB 연결 실패 등 | 남은 기한 안에서 총 2회 이하 실행 |
| `INFERENCE_TIMEOUT` | 실행 임대 상한 초과 | `FAILED`, 프로세스 회수, 같은 실행의 자동 재추론 없음 |
| `INVALID_MODEL_OUTPUT` | 알 수 없는 라벨·NaN·잘못된 구조 | 자동 재시도 안 함, 게시 금지·버전 조사 |
| `EMOTION_ONLY_FAILURE` | 공개 적합성은 정상, 감정 헤드만 실패 | `NEUTRAL/FALLBACK`, 오류 지표 기록 |
| `WORKER_LOST` | 실행 시작 후 OOM·강제 종료 | `FAILED`로 점유 해제, 이미 시작한 모델의 자동 재추론 없음 |
| `POLICY_VERSION_MISMATCH` | 접수의 사전·서비스 정책과 로드 bundle 불일치 | `FAILED`, 추론·제재 없이 배포 상태 점검 |
| `LEXICON_ERROR` | 사전 실행 실패·규칙 bundle 손상 | `FAILED`, 공개·제재 없음, 준비 상태 해제 |
| `DEADLINE_EXCEEDED` | 접수 120초 또는 한국 자정 도달 | `EXPIRED`, 재실행하지 않음 |

재시도 대상은 알려진 일시적 의존성 오류만 allowlist로 정한다. 이미 모델을 실행한 뒤 종료를 알 수 없는 lost·timeout은 재추론하지 않고 실패로 확정한다. 총 2회는 초기 정책이며, 큐 라이브러리의 attempts 의미를 테스트로 확인한다. 두 번째 실행이 가능한 예산이 없으면 즉시 종료한다. 사전 차단과 모델 판정은 재실행해 다른 답을 기대하지 않는다.

Procrastinate의 `retry=정수`는 재시도 횟수이므로 총 실행 횟수와 다르다. 무제한 `retry=True`나 `retry=2`를 서비스의 총 2회 정책으로 해석하지 않는다. 고정한 `RetryStrategy`의 의미를 확인하고 DB `attempt_count`를 최종 상한으로 시험한다. 모델 실행 뒤 저장 결과를 다시 최종화하는 DB 재시도는 새 모델 실행 attempt가 아니다.

Procrastinate는 재시도 전략을 지원하지만, 기본값이나 모든 예외에 대한 무제한 재시도를 사용하지 않는다. DB에 보존된 작업은 프로세스 재시작 뒤에도 남을 수 있다. [Procrastinate 재시도 문서](https://procrastinate.readthedocs.io/en/stable/howto/advanced/retry.html)

모델이 계속 준비되지 않으면 학생에게 일시 사용 불가를 알린다. 이미 예약한 작업은 만료 정리로 반환한다. 미검수 글을 장애 우회용으로 공개하지 않는다.

모델·사전·서비스 정책은 프로세스가 시작할 때 고정하고 hot reload하지 않는다. claim은 제출의 사전·서비스 정책 버전과 로드 manifest의 일치를 확인하고, API 기대 bundle·유효 readiness·실제 모델 bundle을 대조한다. 실행 envelope는 실제 로드 model·inference 버전을 고정하여 최종화에서 다시 비교한다. 배포·모델 rollback 전에는 신규 접수를 멈추고 **PENDING와 PROCESSING 모두** 이전 bundle로 완료하거나 기존 기한·취소 절차로 terminal화한다. 구 작업자 종료를 확인한 뒤 새 bundle을 시작한다. 소비만 멈추고 오래된 PENDING를 새 모델에 넘기지 않는다.

준비 상태는 Procrastinate 작업자 heartbeat와 분리한다. 작업자 프로세스가 살아 있어도 모델이 로드되었다는 뜻은 아니다. 감시 부모는 모델의 `READY`를 받은 뒤에만 DB의 `worker_readiness`를 5초마다 갱신하고 lease를 15초로 유지한다. readiness는 작업자 세대, 모델 bundle 버전, ready 여부와 만료 시각에 연결한다. 모델 종료·load 실패·warmup 초과 시 ready를 해제하고, 갱신 실패 시에도 기존 lease를 임의로 연장하지 않는다.

API는 새 접수 직전 DB 시각으로 유효한 model readiness를 확인한다. 준비되지 않았거나 lease가 만료됐으면 `503`으로 새 접수를 거절하고 한도를 점유하지 않는다. 같은 멱등 키의 기존 결과 조회는 허용한다. readiness 검사 직후의 장애까지 막을 수는 없으므로 이미 접수한 작업에는 실패·만료·점유 해제가 반드시 필요하다. 하나의 서비스용 작업자만 운영하는 초기 구성에서는 더 오래된 세대의 종료 보고가 새 세대의 readiness를 덮어쓰지 않도록 조건부 갱신한다.

## 7. 처리 기한과 늦은 결과 차단

`expires_at = min(accepted_at + 120초, 다음 Asia/Seoul 자정)`으로 정한다. 이 기한은 서비스 대기열까지 포함한다. 실행 claim부터 START/ACK·추론·DB 최종화까지 공유하는 최대 60초 실행 임대와 구분한다.

작업자가 시작할 때 제출이 살아 있는지 검사하고, 유일한 `run_token`을 원자적으로 발급받는다. 실행 중인 유효한 작업이 있으면 중복 큐 전달은 no-op다. 모델 시작 전의 허용된 일시 의존성 오류만 새 토큰으로 최대 총 2회 시도한다. 모델 실행 후 lost·임대 만료는 terminal화하며, 회수된 큐 task는 그 상태를 읽고 추론 없이 종료한다.

최종화 트랜잭션에서 다음을 모두 확인한다. 정확한 잠금 순서는 데이터 설계의 참여자 → 당일 위반 행 → 제출 순서를 따른다.

```text
상태 == PROCESSING
그리고 DB의 run_token == 이번 실행의 run_token
그리고 DB의 현재 시각 < expires_at
그리고 DB의 현재 시각 < processing_lease_until
그리고 원래 제출 business_date가 유효
그리고 참여자가 현재 정지되지 않음
```

허용되지 않은 늦은 결과는 버린다. 60초 실행 예산은 실행 claim부터 DB 최종화까지 포함하며 START ACK 지연이나 감독 heartbeat로 연장하지 않는다. 실행 임대만 끝났으면 `FAILED/INFERENCE_TIMEOUT`, 전체 접수 기한이나 원래 날짜도 끝났으면 `EXPIRED`다. API 측 만료 정리 작업은 추론 프로세스와 독립적으로 움직여, 작업자가 멈춰도 점유를 해제한다. 최종화는 매번 같은 잠금 순서와 조건부 상태 전이를 사용하며 해제·승인·위반은 한 번만 반영한다. 자정에 rolling 한도를 초기화하지 않는다. 원래 날짜 승인 글도 접수 후 60분까지는 새날의 한도 조회에 포함될 수 있다. 이 절차는 추론이 정확히 한 번 실행된다는 보장이 아니다.

작업 인자에는 `submission_id`만 저장하고, 문장은 DB에서 읽는다. 큐 기록과 오류 로그에 거절 원문이 복제되지 않게 한다. `run_token`은 작업 실행 때 얻으며 영속 큐 인자에 고정해서 재사용하지 않는다.

## 8. 실제 CPU 작업 중단과 프로세스 감시

`asyncio.wait_for`, 스레드 future 취소, Procrastinate의 graceful shutdown timeout만으로 동기·native 추론이 강제 종료된다고 가정하지 않는다. Procrastinate도 취소를 따르지 않는 작업은 계속 실행되며 강제 종료는 프로세스 관리자가 해야 한다고 명시한다. [공식 종료 문서](https://procrastinate.readthedocs.io/en/stable/howto/advanced/shutdown.html)

Linux 배포의 작업자 컨테이너를 다음처럼 설계한다. 이는 **구현하고 실제 멈춤 테스트를 통과해야 하는 출시 조건**이다.

큐 task는 async 조율 코드로 유지하고 동기 `Analyzer.analyze()` 실행만 `await asyncio.to_thread(analyzer.analyze, value)`로 넘긴다. 필터도 실행 시간이 제한된 동기 작업으로 같은 순차 파이프라인에서 실행한다. 동시 추론은 1개이며 모델이 실행되는 동안 event loop의 DB·heartbeat 처리가 계속 가능한지 실제로 확인한다. 취소된 thread가 추론을 중단한다고 가정하지 않고 아래 부모 프로세스의 hard kill로 전체 자원을 회수한다. Procrastinate는 sync task를 별도 thread에서 실행하고 async task는 동일 event loop에서 실행하므로, async task 안에서 동기 추론을 직접 호출하지 않는다. [공식 task 실행 설명](https://procrastinate.readthedocs.io/en/stable/howto/basics/tasks.html)

```text
외부 감시 부모 프로세스
  └─ 독립 프로세스 그룹의 큐 작업자 1개
       └─ 모델 1회 로드, 동시 검수 1개

시작 → 180초 이내 READY
작업 시작 → START(job_id, run_token, budget_ms)
부모의 ACK를 받은 뒤 추론
완료·실패 최종화 → DONE(job_id, run_token)
```

부모는 문장이 없는 전용 IPC pipe를 감시한다. 일반 로그 stdout과 제어 메시지를 섞지 않는다. 작업 예산은 DB가 발급한 `processing_lease_until`까지의 남은 시간이며, claim 뒤 START까지 이미 지난 시간도 차감한다. START/ACK에서 새 60초를 부여하지 않는다. 이후 경과 시간은 `monotonic` 시계로 잰다. `DONE`은 일치하는 작업과 실행 토큰일 때만 인정한다.

상한을 넘으면 부모는 자식 프로세스 그룹에 TERM을 보내고 최대 5초 기다린 뒤 KILL한다. 자식 종료를 수거하고 부모도 오류 코드로 종료한다. 컨테이너 재시작 정책이 전체 작업자를 새로 시작한다. 부모 자체가 막히거나 종료되는 경우에도 자식이 고아 프로세스로 남지 않도록 컨테이너 종료·프로세스 그룹 동작을 함께 테스트한다. 작업자 내부 재시작과 컨테이너 재시작을 중첩해 무한 반복하지 않는다.

프로세스와 pipe 제어는 Python subprocess 계층에서 구현할 수 있다. 종료·강제 종료 후 대기와 자원 회수까지 명시적으로 처리해야 한다. 컨테이너가 `unhealthy`라는 표시만으로 재시작된다고 가정하지 않고, 메인 프로세스 종료와 restart policy를 연결한다. [Python subprocess](https://docs.python.org/3/library/subprocess.html), [Docker restart policy](https://docs.docker.com/engine/containers/start-containers-automatically/)

워커 종료와 DB 예약 반환은 별개다. 감시 부모는 DB 최종화를 대신하지 않는다. 독립 정리 루프가 실행 임대·전체 기한으로 업무 상태를 먼저 terminal화하고, 약 10초 간격의 stalled 조회에서 terminal 제출의 작업만 지원 API로 재전달한다. task는 모델을 호출하지 않는 no-op로 종료한다. 아직 유효한 실행을 heartbeat 소실만으로 재추론하지 않는다. 자세한 순서는 데이터 설계를 따른다. 기본 예시의 10분 회수 주기를 2분짜리 서비스에 그대로 적용하지 않는다. [중단 작업 회수 문서](https://procrastinate.readthedocs.io/en/stable/howto/production/retry_stalled_jobs.html)

모델 로드와 반복 crash가 관찰되면 운영 경고와 신규 접수 중단으로 전환한다. 재시작 성공을 서비스 복구의 증거로 삼지 않고 다음 정상 검수가 완료되는지 확인한다.

## 9. 모델 artifact와 기록

manifest에는 다음을 포함한다.

```text
schema_version
base_model_id + 고정 revision
weights/tokenizer/config 파일별 SHA-256
클래스 순서와 label ID 매핑
model_bundle_version + 모델/토크나이저 artifact 버전
inference_policy_version + 임계값 + 전처리 규칙
moderation_policy_version + 서비스 게시/제재 정책
lexicon_version + 확정 금칙어/우회 규칙
학습·평가 데이터 출처와 사용 조건
평가 보고서, CPU 환경과 p50/p95 지연·최대 메모리
필요 패키지 버전, 학습·추론 코드 revision
```

런타임에 임의의 최신 모델을 다운로드하지 않는다. 검토한 artifact를 배포 전에 준비하고 해시를 확인한다. 가능한 경우 safetensors를 사용하고, 검토하지 않은 pickle 가중치나 원격 Python 코드를 자동 실행하지 않는다. Transformers 문서도 pickle 기반 가중치의 위험과 safetensors를 설명한다. [모델 로딩 문서](https://huggingface.co/docs/transformers/en/models)

운영 DB의 문장을 자동으로 학습 데이터에 추가하지 않는다. 원문과 제출 행은 서버 접수 후 1시간에 삭제한다. 작업자는 원문을 별도 파일·모델 로그·평가 데이터로 복제하지 않고, 삭제된 제출의 늦은 결과는 반영하지 않는다. 원문 보관, 로그, 백업, 평가용 반출, 재학습 동의·정제는 각각 다르다. 실학생 문장은 기본 로그·metrics·Git·공유 평가 파일에서 제외한다.

기본 지표는 verdict별 개수, 오류 코드별 개수, 큐 대기·추론·전체 시간, 만료 수, 감정 fallback 수, 작업자 재시작 수다. 참여자 ID·문장·제출 ID를 metrics label로 쓰지 않는다. 제출 ID를 상관관계 로그에 기록할 때도 접근을 제한한다.

## 10. 함께 통과할 테스트와 출시 gate

아래는 평가 데이터 전체가 아니라 계약·상태 전이용 대표 fixture다. 콘텐츠 라벨은 [게시 기준](content-policy.md)을 적용하고, 애매한 예문은 AI 담당과 팀이 근거를 검토해 판정하거나 `UNCERTAIN`으로 구분한다.

| fixture | 기대 |
|---|---|
| `오늘 시험 끝났다!` | 공개 가능·기쁨 후보 |
| `합격 축하해!` | 공개 가능·축하 후보 |
| `오늘 좀 화나네` | 부정 감정만으로 차단하지 않음 |
| `다들 수고했어 내일도 힘내` | 공개 가능·응원 후보 |
| `민수야 고마워`, 이름을 포함한 가벼운 행동 비판 | 이름의 단순 언급으로 차단하지 않음 |
| 명백한 모욕·외모 조롱을 담은 이름 포함 표현 | 안전성 평가셋에 별도 라벨·근거, 이름 유무로 판정을 바꾸지 않음 |
| 검증된 사전 우회 표기 | 해당 rule ID·위반 한 번 |
| `NaN` 점수·미지원 라벨·누락 결과 | 계약 오류·비공개·제재 없음 |
| 안전성 `ALLOW`, 감정 예외 | `NEUTRAL/FALLBACK` |
| 안전성 예외 | 게시 금지·제재 없음 |
| 실행 중 120초 만료 후 정상 결과 도착 | 승인 안 됨·예약 중복 반환 없음 |
| 구 run_token 결과가 새 실행 뒤 도착 | 구 결과 무시 |
| 같은 작업 두 번 전달 | 한 실행만 유효·집계 한 번 |
| 최근 60분 안에 승인 2건·검수 중 1건 | 새 접수 거절, 중복 요청은 기존 결과 |
| 승인 접수 시각에서 정확히 60분 경과 | 해당 건은 rolling 한도에서 제외 |
| 자정 직전 승인 뒤 새날 접수 | 전날 승인도 60분 안이면 한도에 포함 |
| 유효한 QR로 접수한 뒤 검수 중 QR 만료 | 접수 자체는 유지, 검수 기한만 검사 |
| native 추론을 대체한 무한 대기 fixture | 제한 시간 후 실제 프로세스 종료·다음 작업 정상 |
| 모델 로드 180초 초과·파일 해시 불일치 | readiness 실패·접수 중단 |
| 큐 작업자 heartbeat 정상·모델 READY 없음 | 새 접수 503, 미검수 게시 없음 |
| readiness lease 만료·오래된 세대의 종료 보고 | 새 접수 차단, 최신 세대 상태를 오염시키지 않음 |
| 확정 규칙 차단·여러 규칙 동시 매칭 | 모델 호출 0회, 정렬·중복 제거한 근거 최대 5개, 위반 1회 |
| 규칙 결과에 가짜 AnalysisResult·모델 필드 추가 | 계약·DB 거절, AI reason code와 rule 근거 분리 |
| 교체 전 old PENDING·결과가 다른 모델 버전 주장 | 교체 gate 실패 또는 게시·제재 없는 버전 오류 |
| 동기 추론 중 heartbeat·정리 조회 | event loop는 응답, timeout 시 실제 프로세스 회수 |

모델 평가에서는 감정 macro-F1과 클래스별 혼동, 정상 글 오차단율, 유해 글 누락률, `UNCERTAIN` 비율을 함께 보고한다. 작성자·원본·유사 문장 묶음을 나눠 학습/평가 누수를 줄이고, 임계값을 고른 데이터와 최종 평가 데이터를 분리한다.

초기 최소 회귀 fixture와 실제 안전성 검증은 다르다. 공식적인 제품 성능 주장은 실제 평가 없이 하지 않는다. 교내 공개 전에는 별도의 짧은 한국어 평가셋, 위장 욕설·학생 저격·다른 사람 정보·정상 부정 감정 사례를 검토하고 학교 상황에서 허용할 오차를 팀과 운영자가 정해야 한다.

출시 gate는 다음과 같다.

1. fake adapter로API·즉시일반 말풍선·전광판페이지·실패경로를통과한다.
2. 실제 artifact가 계약·입출력 회귀 테스트를 통과한다.
3. 목표 CPU에서 p95와 최대 메모리·콜드스타트를 측정하고 60초/120초 예산을 만족한다.
4. 강제 종료·늦은 결과·중복 작업·자정 테스트를 통과한다.
5. 안전성 평가 보고서와 임계값을 승인하고 즉시 숨김·송출 중단 기능을 준비한다.
6. 모델 롤백 방법과 이전 artifact를 검증한다. 진행 중 작업의 버전을 조용히 바꾸지 않는다.

AI 정확도나 작업 시간 때문에 gate를 통과하지 못하면 공개 일정을 조정하거나 기능 범위를 줄인다. 검수가 없는 상태에서 자동 공개하도록 바꾸지는 않는다.

## 11. 11월 5일 목표의 AI 일정과 중단 기준

목표일은 **2026년 11월 5일**이다. 일반 백엔드 1명과 AI 담당 1명이 일주일 안에 계정 없는 참여·정확한 제한·실시간 화면·검수·배포를 새로 완성해야 한다. 예산이 충분해도 라벨 합의·학교 문맥 평가·회귀 검증 시간을 생략할 수 없다. 아래 날짜는 납품 목표이며 성능이나 출시를 보장하는 일정이 아니다.

| 시점 | AI 담당 납품 | 함께 확인할 것 |
|---|---|---|
| 10월 29~30일 | 라벨 지침·인터페이스·fake adapter·데이터 사용 조건 | 프론트·API가 실제 모델 없이 성공/실패 경로 연결 |
| 10월 31일~11월 2일 | 첫 실제 감정/안전성 모델과 CPU 실행 모듈 | 예상 입력 범위·초기 오탐/누락·작업자 로딩 |
| **11월 3일 연결 gate** | 고정 artifact·추론 모듈·평가 보고서·버전/해시 | 계약·안전성·CPU 자원·readiness 검증을 시작할 수 있음 |
| 11월 4일 | 수정 bundle 또는 이전 bundle 유지 | 강제 종료·늦은 결과·정상 글/유해 글 통합 검증 |
| 11월 5일 | 검증된 동일 bundle만 사용 | 출시 gate 통과 시 제한된 운영 시작 |

AI Hub 감정 데이터 준비만으로 학교 비방 분류가 완성되지는 않는다. 안전성 라벨과 평가셋이 없거나 첫 학습부터 시작하는 경우 11월 3일까지의 납품은 위험도가 높다. 11월 3일에 가중치가 있다는 사실보다 **합의한 계약으로 실행되고, 별도 평가와 재현 가능한 CPU 측정이 있는지**를 본다. 임의 정확도 수치를 만들어 합격시키지 않는다.

자동 검수가 필수이므로 모델이 gate를 통과하지 못하면 **실제 학생 대상 공개 운영을 보류**한다. 일정상 화면 시연이 필요하면 팀이 미리 검토한 합성 예문과 fake adapter로 제한된 데모만 한다. 데모를 실제 안전성 검수가 완성된 서비스로 소개하지 않는다. 별도 외부 분류 API로 교체하는 경우에도 학생 문장의 외부 전송·비용·한국어 성능·계약을 검토해야 하므로 자동 대체 경로로 넣지 않는다.
