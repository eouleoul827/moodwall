# moodwall
학생들의 기분과 응원 메시지를 공유하는 전광판 서비스

새 시안을 실제 학생·전광판 화면에 적용했습니다. **Gemini 검수 정책 우선·서버가 정하는 재생 시간(기본 15초)**이며 구름 16종과 낮/밤 색을 사용합니다. [디자인 연결 기준](docs/design-integration.md)을 참고하세요. 기존 감정 bundle이 준비된 PC에서는 `python scripts/setup_gemini.py --start --emotion-model models/student-v0`로 승인 후 구름 색을 자동 선택합니다. [감정 모델 연결](docs/gemini-emotion-integration.md)

## 팀 개발 시작

일반 백엔드 1명, AI 담당 1명, 프론트엔드 2명이 공동 저장소에서 개발합니다. **백엔드 API·DB migration·검수 작업자에 학생 작성·전광판·운영자 React 화면을 연결했습니다.** 기본 AI는 개발용 더미이며, 실제 학생 모델 v0의 CPU 추론을 선택해서 연결할 수 있습니다. v0는 평가된 게시 허용 기준이 없어 검토 필요 결과만 반환합니다. [프론트 실행 가이드](frontend/README.md), [실제 모델 연결·검증 가이드](docs/student-model-integration.md)

처음 실행할 때는 **[백엔드 실행 가이드](backend/README.md)**를 읽으세요. Docker Compose로 로컬 DB·API·작업자를 실행하고, 운영자를 생성한 뒤 접수·송출을 명시적으로 재개하는 순서입니다. 현재 구현·검증·미완료 범위는 [구현 상태](docs/implementation-status.md)에 구분합니다.

**Gemini API 키를 받았다면** 저장소 루트에서 `python scripts/setup_gemini.py --start`를 실행하세요. 키를 숨겨 입력받아 기존 로컬 비밀을 보존하고 프론트·이미지 빌드·DB migration·서비스 시작까지 연결합니다. 처음 운영자 계정도 만들려면 `--create-operator`를 추가합니다. 실제 키를 사용한 품질·청구 검증과 현장 배포는 별도입니다. [Gemini 연결·실행 가이드](docs/gemini-moderation-implementation.md)

팀에서 전달받은 [전광판·모바일 HTML 시안](prototypes/README.md)도 포함합니다. 실제 모델 bundle은 [학생 모델 v0 사전 릴리스](https://github.com/eouleoul827/moodwall/releases/tag/student-model-v0-0c45710941f2)의 첨부 파일로 제공합니다. 코드는 기능 브랜치와 PR에서 검토하며, AI 팀의 학습 코드·시드 데이터는 [AI PR #2](https://github.com/eouleoul827/moodwall/pull/2)에 있습니다.

**새 v2 백엔드와 실제 React 화면을 연결했습니다.** 검수·감정 테마 준비를 마친 순서로 한 글씩 전체 화면에 재생하고(서버가 대기열에 따라 15초~8초로 정함), 최초 시작 승인부터 1시간 후 원문 공급을 끝내며 같은 내용의 투명 말풍선은 자정까지 유지합니다. 새 글은 현재 재생이 끝날 때까지 기다립니다. [로컬 구현·연결 가이드](docs/playback-local-implementation.md), [세부 재생 설계](docs/playback-design.md), [접수·오류 계약](docs/submission-status-errors.md)을 참고하세요. OpenAPI는 0.12.0, DB head는 `0009_playback_duration`입니다. 감정 배경은 준비된 테마와 CSS/SVG 효과이며 AI 이미지 생성 기능은 포함하지 않습니다.

학생에게는 [기존 시안의 완료 카드](docs/student-ux.md)를 유지합니다. 접수를 확인하면 메시지를 보냈어요!를 보여주고 상세 처리 단계는 노출하지 않으며, 문제가 확정된 경우에만 짧게 안내합니다.

| 화면 | 주소 | 연결한 기능 |
| --- | --- | --- |
| 학생 | `/`, `/join#ticket=…`, `/student` | QR 입장·익명 세션·30자 작성·최근 1시간 3회·접수 완료·동일 요청 복구 |
| 전광판 노트북 | `/display` | 장치 등록·실제 참여 QR·감정 테마·15초 단독 재생·당일 말풍선·재연결 |
| 운영자 | `/operator` | 로그인·등록 코드 발급·접수/화면 중단과 재개·글 숨김·전광판의 투명 말풍선 직접 숨김 |

`frontend/`에서 `npm ci`와 `npm run build`를 실행하면 빌드 파일이 생깁니다. 초기 DB·운영자 준비를 마친 뒤 `docker compose -f compose.yaml -f compose.frontend.yaml up -d --build api worker`로 같은 서버에서 UI와 API를 제공합니다. 별도 Vite 개발 서버는 `http://127.0.0.1:5173`에서 열며 백엔드 `MOODWALL_PUBLIC_ORIGIN`도 정확히 맞춰야 합니다. [두 실행 방법과 최초 연결 순서](frontend/README.md)를 따르세요.

개발용 fake AI로 게시 흐름을 검증하는 것과 실제 모델의 게시 품질·학교 노트북/HDMI·휴대폰 현장 수락은 구분합니다. 실제 모델 v0의 production 차단과 `UNCERTAIN` 보호를 유지했습니다. 원본 HTML은 참고 자료로 보존하며 실제 동작하는 화면은 `frontend/src/`에 있습니다.

기존 v1 API와 캐시는 이전 정책의 호환 경로로 남깁니다. 새 작성·전광판 화면은 v2 API·모듈을 함께 사용하세요. 서버 목록에서 빠지는 것은 당일 말풍선 삭제 신호가 아니며 자정·운영자 숨김·기기 해지에는 캐시도 정리합니다. [전체 흐름](docs/service-flow.md)을 참고하세요.

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
- [게시 허용과 차단 기준](docs/content-policy.md)
- [Gemini 검수 연결·실행](docs/gemini-moderation-implementation.md) — 키 설정·실제 HTTP·모의 검수·지원/신고·예산 원장
- [Gemini 서버 검수 전체 설계·프롬프트·평가·운영](docs/gemini-moderation-design.md)
- [욕설 정규화·필터·제재 구현 정책](docs/profanity-policy.md)
- [권한과 운영](docs/security-and-operations.md)
- [배포·성능·관측·운영 수락 기준](docs/delivery-and-operations.md)
- [역할 분담과 구현 순서](docs/implementation-plan.md)
- [검증 기준과 현재 검증 상태](docs/verification.md)

팀의 다음 작업은 연결한 React 화면을 실제 브라우저·휴대폰·전광판 노트북에서 확인하고, AI 담당의 평가된 추론 모듈로 더미를 교체하는 것입니다. 기능 브랜치에서 작업하고 PR로 검토한 뒤 `main`에 합칩니다. API 변경은 관련 프론트 담당자와 함께 검토합니다. 실제 챗봇 연동은 첫 단계 범위에서 제외합니다.
