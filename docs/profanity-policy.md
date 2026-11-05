# 규칙 기반 욕설 필터 구현 정책

상태: 구현 전 설계 · 2026-10-29

일반 백엔드 담당자는 금칙어 사전, 우회 표기 규칙, 검사 코드와 제재 집계를 맡는다. AI 담당자는 원래 표시 문장으로 문맥 검수·감정을 판단한다. 검증된 명시 규칙만 자동 제재의 근거가 되며, 문맥이 필요한 후보는 기존 AI 검수로 넘긴다. 특정 라이브러리나 정규화만으로 모든 욕설 우회를 막을 수 있다는 목표는 두지 않는다.

콘텐츠의 허용·차단 경계는 [게시 기준](content-policy.md)을 따른다. 이 문서는 그중 명시적인 욕설 규칙과 제재 근거를 다룬다. 외모 관련 일반 단어를 확정 금칙어로 추가해 자기 외모 고민·일반 취향·스타일 비평까지 차단하지 않는다. 특정인을 향한 외모 모욕·공개 망신은 게시 기준에 따라 문맥을 판정하며, 해당 판정만으로 확정 규칙 위반을 만들지 않는다.

새 [Gemini 검수 프로필](gemini-moderation-design.md)은 규칙 매칭을 먼저 관찰하되 위기 지원 여부를 확인한 뒤 비공개 결정을 확정한다. 초기 프로필 전체에서 자동 벌점을 끄며 `RuleRejected/REJECTED` 제재 경로를 사용하지 않는다. 아래 규칙·제재 표는 기존 로컬 프로필의 계약이다. 이 추가 설계가 현재 실행 코드를 즉시 변경하지는 않는다.

## 1. 표준과 프로젝트 선택

| 근거 | 적용 | 프로젝트가 별도로 정할 부분 |
| --- | --- | --- |
| [Unicode UAX 15](https://www.unicode.org/reports/tr15/) | 표시·AI 입력은 NFC로 일치시킨다. 호환 정규화는 의미 차이를 없앨 수 있으므로 원문에 일괄 적용하지 않는다 | 어떤 자모·우회 표기를 동일한 검색 키로 취급할지 |
| [Unicode UAX 29](https://www.unicode.org/reports/tr29/) | 30자는 extended grapheme cluster로 센다 | 서버 분절 구현·버전과 프론트 공통 fixture |
| [Python 3.12 unicodedata](https://docs.python.org/3.12/library/unicodedata.html) | NFC·NFD와 문자 속성의 기반. 실행 환경의 `unidata_version`을 기록한다 | 이 모듈만으로 grapheme 분절을 구현했다고 간주하지 않는다 |
| [OWASP 입력 검증](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html) | 형식·크기 검증, 자유 텍스트 보존, 제한된 정규식 실행 | 욕설 사전은 콘텐츠 정책이며 SQL 바인딩·출력 인코딩을 대체하지 않는다 |

표준은 한국어 욕설 목록이나 자동 정지 기준을 지정하지 않는다. 아래 규칙·경계·개수는 이 프로젝트의 운영 정책이다.

## 2. 표시 문장과 검색 문장

1. HTTP 계층에서 제출 JSON 전체를 4 KiB로 제한하고, 파싱 후 `content`의 UTF-8 크기를 2 KiB 이하로 검사한다. 잘못된 UTF-8·고립 surrogate와 잘못된 JSON은 일반 입력 오류이며 위반이 아니다.
2. 줄바꿈, 일반 제어문자, 양방향 표시 제어문자를 거절한다. 정상 이모지에 쓰이는 ZWJ·variation selector·결합 문자를 포괄 삭제하지 않는다. 삭제해서 통과시키는 방식 대신 허용·금지 정책을 명시한다.
3. 앞뒤 공백 제거는 고정한 Unicode White_Space 집합으로 수행하고 NFC를 적용한다. 내부 공백과 문장부호는 보존한다. 공백만 있으면 거절한다.
4. 결과를 1~30 extended grapheme, UTF-8 2 KiB 이하로 다시 검사해 표시 문장 `display_text`를 확정한다. Python `len`, SQL `char_length`, JavaScript `text.length`로 대신하지 않는다.
5. DB·전광판·AI에는 같은 `display_text`를 사용한다. 입력 이전 원시 바이트는 별도 저장하지 않으며, 동일 요청 키의 본문 비교도 기존 API의 정규화 계약을 따른다.
6. 필터는 표시 문장을 바꾸지 않고 메모리에서만 검색용 뷰를 파생한다. 검색 뷰·삭제 전후 문자열·욕설 매칭 조각을 응답이나 일반 로그에 넣지 않는다.

선택한 서버 분절 구현과 브라우저 Intl.Segmenter의 Unicode 버전이 다를 수 있다. 공통 예문으로 차이를 확인하며 서버 판정이 최종이다. 앞뒤 공백 제거·NFC·자모 표준화는 각각 별도 단계로 버전을 관리한다.

## 3. 검색 뷰와 탐지 순서

| 단계 | 처리 | 제한 |
| --- | --- | --- |
| A 표시 뷰 | NFC 표시 문장에서 사전의 정확 표기 검색 | 규칙마다 토큰 경계·허용 접미사 조건 검사 |
| B 자모 뷰 | 현대 한글 음절을 NFD로 분해하고 현대 초성·중성·종성을 고정된 자모 키로 매핑 | 호환 자모도 같은 표에 있는 문자만 변환. 옛한글·외국 문자는 임의 삭제하지 않음 |
| C 구분기호 변형 | 규칙이 허용한 위치의 작은 구분기호 집합을 건너뛰며 매칭 | v1 후보 집합은 공백 U+0020, `%`, `.`, `*`, `_`, `-`. 위치당 최대 2개, 매칭 구간 전체 최대 4개 |
| D 명시 삽입 변형 | 특정 규칙의 특정 위치에서 특정 자모의 중복만 허용 | 예시 한 규칙에서만 `ㅣ` 1개 추가 허용. 전체 문장의 반복 문자를 일괄 축약하지 않음 |

자모 키는 초성·종성의 위치가 달라도 같은 현대 자음을 비교할 수 있게 한다. 예를 들어 `발`의 종성 `ᆯ`과 독립 `ㄹ`을 키 `ㄹ`로 매핑한다. 이 변환은 검색 전용이며 표시 문장이나 AI 입력을 다시 조립하는 데 쓰지 않는다. 표에 없는 호환 문자에 NFKC를 무조건 적용하지 않는다.

C·D는 전체 문장에서 문자를 지워 새 단어를 만드는 방식이 아니다. 규칙의 유한한 패턴을 원래 순서대로 비교하고, 허용한 위치·개수를 넘으면 확정 매칭하지 않는다. 임의의 한글·숫자·영문·이모지 건너뛰기, 무제한 편집거리, 모든 `\W` 제거는 v1에서 사용하지 않는다.

경계와 문맥 조건도 사전의 일부다. 각 규칙에 `EXACT_TOKEN`, `CURATED_SUFFIX`, `REVIEW_ONLY` 중 하나를 지정한다. 정확 토큰은 시작·끝 또는 사전에 고정한 경계 문자에 둘러싸인 구간이다. 접미사형은 검토한 유한 목록만 인정한다. 일반 한국어의 단어 경계를 정규식 `\b`로 판정하지 않는다.

### 예시 규칙의 기대 동작

| 입력 | 검색 근거 | 기대 |
| --- | --- | --- |
| `씨발` | `ㅆㅣㅂㅏㄹ` 정확 토큰 | 승인된 확정 규칙이면 REJECTED·위반 1회 |
| `씨%발` | 같은 키 사이 `%` 1개, 허용 위치·예산 내 | 검증된 구분기호 규칙이면 같은 판정 |
| `씨ㅣ바ㄹ` | `ㅆㅣㅣㅂㅏㄹ`, 두 번째 `ㅣ`를 허용한 명시 변형 | 해당 변형 fixture 승인 후 같은 판정 |
| `시발점` | 정상 명사 전체 | `시발` 부분 문자열만으로 확정 제재하지 않음 |
| `시 발점 찾았어` | 공백 제거 시 정상 단어의 일부가 욕설과 겹침 | 전역 공백 삭제로 자동 제재하지 않음 |
| `씨앗 발아했어` | 의미 있는 글자가 사이에 있음 | 임의 글자 삭제로 욕설을 생성하지 않음 |
| 새 우회 표기·모호한 포함형 | 확정 규칙 없음 | AI 문맥 검수 계속, 규칙만으로 위반 증가 없음 |

위 예시는 정책 fixture의 요구사항이다. 실제 전체 사전·필터의 탐지율을 측정한 결과는 아니다. `시발` 같은 별칭을 추가하면 정상 명사·띄어쓰기 변형을 포함한 음성 fixture를 함께 추가한다. 예외가 문장 전체 검사를 건너뛰게 만들지 않고 해당 구간·규칙에만 적용한다.

## 4. 내부 인터페이스와 사전 데이터

아래는 Python 도메인 인터페이스 명세다. 검사 구현, 실행 제한, 사전 로더는 포함하지 않는다.

```python
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class RuleMatch:
    rule_id: str
    variant_id: str
    view: Literal["DISPLAY", "JAMO"]
    evidence: Literal["CONFIRMED", "CANDIDATE"]


@dataclass(frozen=True, slots=True)
class RuleScan:
    lexicon_version: str
    normalization_version: str
    decision: Literal["NO_MATCH", "CONFIRMED", "CANDIDATE"]
    matches: tuple[RuleMatch, ...]


class ProfanityFilter(Protocol):
    def scan(self, display_text: str) -> RuleScan: ...
```

`RuleScan`은 규칙 기반 근거이고 AI의 안전성 결과가 아니다. CONFIRMED는 활성화된 확정 규칙이 하나 이상 있다는 뜻이다. CANDIDATE는 후보만, NO_MATCH는 둘 다 없는 경우다. 예외를 NO_MATCH나 CONFIRMED로 바꾸지 않는다. 검색 뷰의 오프셋을 원문 인덱스라고 주장하지 않으며, 원문 매핑을 구현하기 전에는 span을 제공하지 않는다.

사전의 필수 메타데이터는 `lexicon_version`, SHA-256, 출처·이용 조건, 작성·검토자, 정규화 버전이다. 규칙별로 ID, 별칭, 자모 키, 경계 방식, 유한 접미사, 구분기호 위치·예산, 삽입 변형, 예외 구간 조건, CONFIRMED 또는 REVIEW_ONLY, fixture ID를 둔다. 사전과 정규화 표를 하나의 고정 bundle로 배포한다.

한 규칙의 데이터 형태는 다음처럼 제한을 명시한다. 숫자 인덱스는 검색 자모 키의 0부터 시작하는 위치이며 원문 위치가 아니다. 실제 bundle 스키마와 로더는 별도 구현하고 검증한다.

```json
{
  "rule_id": "ko-explicit-001",
  "view": "JAMO",
  "key": ["ㅆ", "ㅣ", "ㅂ", "ㅏ", "ㄹ"],
  "scope": "EXACT_TOKEN",
  "separator_after_keys": [1],
  "separator_max_per_position": 2,
  "separator_max_total": 4,
  "optional_insert_after": {"key_index": 1, "token": "ㅣ", "max": 1},
  "evidence": "CONFIRMED",
  "fixture_ids": ["base-001", "gap-001", "jamo-insert-001", "benign-001"]
}
```

이는 설명용 규칙이며 fixture ID가 실제 존재하거나 검토를 통과했다는 뜻은 아니다. 누락 필드·알 수 없는 view·무한 반복·과도한 대체 분기·검토하지 않은 확정 규칙을 로더에서 거부한다. 운영자 웹 화면에서 임의 정규식을 입력받지 않는다.

현재 DB의 `lexicon_version`은 이 bundle을 식별한다. 정규화 버전은 bundle manifest에서 복원하므로 이 명세 때문에 새 DB 열을 요구하지 않는다. 모델 artifact에 기록한 사전 버전과 실제 검사 bundle도 확인한다. 여러 규칙이 매칭되어도 제출 단위 제재는 한 번이다.

확정 거절 근거는 DB `submissions.rule_evidence`에 최대 5개 `{rule_id, variant_id}`로 보존한다. 활성 CONFIRMED 매칭만 ID 쌍으로 정렬·중복 제거하여 앞의 5개를 선택하고, `lexicon_version`으로 당시 규칙·정규화 manifest를 복원한다. 각 ID는 `[A-Za-z0-9_.-]`의 1~100자다. DB 검증은 객체의 두 키만 허용하고, REJECTED에만 비어 있지 않은 배열을 요구한다. 운영자 DTO는 `ruleId`, `variantId`로 매핑하며 학생·전광판 DTO에는 근거를 넣지 않는다. 검색 문자열·일치 조각·span을 이 JSON에 추가하지 않는다. AI reason code는 별도 필드이며 규칙 ID를 섞지 않는다.

## 5. 파이프라인과 제재 경계

검사는 기존 독립 작업자의 ModerationPipeline 안에서, AI보다 먼저 실행한다. 트랜잭션 안에서 모델이나 필터를 실행하지 않는다. 확정 규칙이면 AI 호출을 생략하고, 최종화가 상태·run token·기한·참여자 제재를 다시 확인한 뒤 REJECTED와 당일 위반을 원자적으로 반영한다. DB 잠금은 참여자 → 당일 집계 → 제출 순서다.

이 경로는 [AI 계약](ai-contract.md)의 `RuleRejected`를 사용하고 가짜 `AnalysisResult`를 만들지 않는다. 최종 DB 값의 모델·inference 버전, 안전성 verdict, 감정은 NULL이고 AI reason code는 빈 배열이다. `ModelAnalyzed`와 `RunFailed`는 별도 variant다. 규칙 결과의 사전 버전은 접수·실제 로드 bundle과 같아야 하며, 불일치 시 게시·제재 없이 실패한다.

CANDIDATE·NO_MATCH 모두 기존 `AnalysisInput(schema_version, text=display_text)`로 AI 검수를 계속한다. 후보 점수·rule ID·검색 뷰를 AI 입력에 추가하지 않는다. AI BLOCK·UNCERTAIN은 NEEDS_REWRITE이며 위반 수를 올리지 않는다. 규칙 로딩·실행 오류는 FAILED와 점유 해제 대상이며 위반 수를 올리거나 공개 허용으로 바꾸지 않는다.

사전 손상·버전 불일치·규칙 컴파일 실패는 작업자 준비 상태 실패로 처리해 새 접수를 막는다. 재전송·중복 작업은 같은 제출의 결과를 재사용한다. 확정 위반 3회 경고·5회 한국 자정까지 정지는 기존 정책이며, 운영자 복구도 [AI 계약](ai-contract.md)과 [데이터 설계](data-and-recovery.md)를 따른다.

운영 bundle에 규칙이 없거나 모든 규칙이 REVIEW_ONLY이면 준비 상태를 실패시킨다. 최소 한 개의 검토된 활성 CONFIRMED 규칙과 해당 양성·음성 fixture가 있어야 한다. 이는 필터가 비어 있는 운영을 막는 시작 조건이며 전체 사전의 탐지율·오탐률이나 AI 안전성 평가가 통과했다는 뜻은 아니다. fake 사전·시연용 규칙을 실제 검증된 전체 사전으로 소개하지 않는다.

## 6. 구현 완료 조건과 운영 변경

- 정상 문장·정상 포함어·띄어쓰기·자모·구분기호·이모지·금지 제어문자·결합문자 fixture를 고정한다. 위 예시와 30/31 grapheme·2048/2049 UTF-8 바이트·4096/4097 요청 바이트 경계를 포함한다.
- 같은 Unicode 문장의 NFC·NFD, 호환 자모와 종성 혼합, 키를 바꾸는 미지원 문자에도 기대 결과가 명시되어야 한다. 미지원 우회가 누락될 수 있어도 임의 문자 삭제로 해결하지 않는다.
- 활성 CONFIRMED 규칙마다 차단 양성 예문과 오탐을 방지하는 음성 예문을 사람이 검토한다. 정상 fixture 오탐 0은 회귀 조건이며 실사용 전체 오탐 0의 증명은 아니다.
- 규칙 개수·버전·검사 지연을 실제 서버에서 측정한다. 입력 상한과 단순한 유한 매칭으로 시작하고, 악성 근접 일치 입력에서 정규식 backtracking이 폭증하지 않는지 확인한다. Aho–Corasick 도입은 필수가 아니다.
- 중복 제출·큐 재실행·사전 오류·AI 거절·제재 복구 후 위반 수가 중복 증가하지 않는 통합 시험을 통과한다. 규칙 검사와 DB 제재 시험은 구분한다.
- 사전 변경은 버전 증가 → fixture 리뷰 → staging → 고정 bundle 배포 순서로 진행한다. 진행 중 제출을 새 사전으로 조용히 재판정하지 않고, 과거 승인 글에 소급 자동 제재하지 않는다.

후속 GitHub Issue는 `표시 문장 검증과 공통 Unicode fixture`, `버전형 금칙어 bundle과 ProfanityFilter`, `검수 최종화의 단일 제재 반영` 세 개로 나눈다. AI 담당은 정상·저격·우회 예문의 라벨 검토에 참여하고, 구현과 제재 책임은 일반 백엔드 담당자가 가진다.
