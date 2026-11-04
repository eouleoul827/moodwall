# moodwall
학생들의 기분과 응원 메시지를 공유하는 전광판 서비스

## 팀 개발 시작

일반 백엔드 1명, AI 담당 1명, 프론트엔드 2명이 공동 저장소에서 개발합니다. **백엔드 API·DB migration·검수 작업자·전광판 상태·운영자 제어 코드를 구현했습니다.** 기본 AI는 개발용 더미이며, 실제 학생 모델 v0의 CPU 추론을 선택해서 연결할 수 있습니다. v0는 평가된 게시 허용 기준이 없어 검토 필요 결과만 반환합니다. React 작성·전광판 화면은 별도 연결 대상입니다. [실제 모델 연결·검증 가이드](docs/student-model-integration.md)

처음 실행할 때는 **[백엔드 실행 가이드](backend/README.md)**를 읽으세요. Docker Compose로 로컬 DB·API·작업자를 실행하고, 운영자를 생성한 뒤 접수·송출을 명시적으로 재개하는 순서입니다. 현재 구현·검증·미완료 범위는 [구현 상태](docs/implementation-status.md)에 구분합니다.

팀에서 전달받은 [전광판·모바일 HTML 시안](prototypes/README.md)도 포함합니다. 실제 모델 bundle은 [학생 모델 v0 사전 릴리스](https://github.com/eouleoul827/moodwall/releases/tag/student-model-v0-0c45710941f2)의 첨부 파일로 제공합니다. AI 팀의 학습 코드·시드 데이터·로컬 진단 도구는 [ai/ 안내](ai/README.md)에 모았습니다. 서비스는 기존 검수 worker를 사용하며 진단 도구는 별도로 127.0.0.1:8001에서 실행합니다. 코드는 기능 브랜치와 PR에서 검토하고 `main`으로 병합합니다.

멘션은 **승인 즉시 떠다니는 말풍선**으로 공개합니다. 별도 강조·4개 자리 제한·표시 대기 없이 일반 말풍선으로 보여 주고, 접수 1시간 뒤에는 같은 글 내용이 담긴 투명 말풍선으로 자정까지 남깁니다. 서버 DB는 접수 후 1시간에 글을 삭제하며 전광판 노트북의 당일 캐시는 별도로 유지합니다. 서버 목록에서 빠지는 것은 말풍선 삭제 신호가 아닙니다. 자정·운영자 숨김·기기 해지에는 캐시도 정리합니다. [쉽게 보는 전체 흐름](docs/service-flow.md)을 참고하세요. 전광판 React 화면과 당일 캐시는 연결할 구현 범위입니다.

실제 코드를 대상으로 한 [추가 검수와 DB 최적화 기록](docs/standards-review.md)을 확인하세요. DB 잠금·인덱스·화면 조회를 보완했으며, [합성 1만 건 실행 계획](docs/checks/db-plan-2026-10-29.json)은 이전 표시 정책에서 측정한 이력입니다.

아래 문서는 구현의 기준이 된 설계 문서입니다. 문서에 남아 있는 “구현 전”은 작성 당시의 상태이므로 현재 동작·시험 결과는 위 구현 상태를 우선 확인하세요. 설계의 모든 수락 조건이 구현·운영 검증을 끝냈다는 뜻은 아닙니다.

- [전체 구조와 서비스 정책](docs/architecture.md)
- [백엔드 전체 분기와 공식 기준 대조표](docs/backend-completeness-tree.md)
- [일반 백엔드 담당자의 구현 순서와 기술 선택](docs/backend-owner-plan.md)
- [전광판 노트북의 역할과 현장 운영](docs/display-laptop.md)
- [추가 검토에서 보완한 점](docs/standards-review.md)
- [클래스·오류·재시도·조회 계약](docs/backend-contracts.md)
- [DB 스키마](docs/schema.sql)와 [트랜잭션 및 장애 복구](docs/data-and-recovery.md)
- [HTTP API 계약](docs/openapi.json)
- [AI 담당과의 연결 계약](docs/ai-contract.md)
- [AI 담당에게 보낼 요청서와 작업 분담](docs/ai-team-request.md)
- [욕설 정규화·필터·제재 구현 정책](docs/profanity-policy.md)
- [권한과 운영](docs/security-and-operations.md)
- [배포·성능·관측·운영 수락 기준](docs/delivery-and-operations.md)
- [역할 분담과 구현 순서](docs/implementation-plan.md)
- [검증 기준과 현재 검증 상태](docs/verification.md)

팀의 다음 연결 작업은 백엔드 API에 React 작성·전광판 화면을 붙이고, AI 담당의 평가된 추론 모듈로 더미를 교체하는 것입니다. 기능 브랜치에서 작업하고 PR로 검토한 뒤 `main`에 합칩니다. API 변경은 관련 프론트 담당자와 함께 검토합니다. 실제 챗봇 연동은 첫 단계 범위에서 제외합니다.
