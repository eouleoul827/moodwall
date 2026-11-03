# 무드월 화면 시안 원본

[moodwall-display.html](moodwall-display.html)은 팀에서 전달받은 HTML을 변경 없이 보관한 파일이다. 전광판·새 멘션 풀스크린·모바일 작성·전송 완료·필터 경고·작성 제한의 6개 시안을 한 문서에 담고 있다. 압축 자산을 내장한 전용 런타임 형식이며, 일반 React 프로젝트의 컴포넌트 소스는 아니다.

입력·이모지 선택·전송 완료 전환은 로컬 데모이며 실제 서버에 글을 보내지 않는다. QR은 자리표시자이고 말풍선·통계도 합성 샘플이다. 브라우저 실행은 별도 확인 대상이다.

프론트 연결 시 아래 차이를 반영한다.

- 시안의 하루 2회 제한 대신 서버의 최근 60분 3회 한도를 표시한다.
- 풀스크린 강조와 표시 대기를 적용하지 않고, 승인 즉시 일반 말풍선을 띄운다.
- 접수 1시간 후 DB 원문이 삭제돼도 이미 받은 같은 내용은 투명 말풍선으로 유지하고 다음 KST 자정에 정리한다.
- 글자 수는 UTF-16 길이 대신 서버와 같은 grapheme 기준을 사용한다.
- 실제 QR·페어링·heartbeat·SSE·재접속·운영자 숨김 처리를 연결한다.

작성 화면은 `/api/emoji-options`에서 받은 목록으로 선택지를 그리고 문자 자체 대신 `party`, `heart` 같은 `emojiId`를 전송한다. 시안의 기본 `😊` 등 서버 목록에 없는 문자는 전송할 수 없다. `Intl.Segmenter('ko', { granularity: 'grapheme' })`로 글자 수를 표시하고 서버의 NFC·공백·제어문자 검사를 최종 기준으로 삼는다.

`/join#ticket=…`은 실제 프론트 라우트로 구현한다. 학생 세션·QR 교환·CSRF를 연결하고 제출에는 새 UUID `Idempotency-Key`를 사용한다. 네트워크 오류로 같은 요청을 다시 보내면 키와 본문을 유지한다. `202 PENDING`은 접수 성공이므로 전송 완료 화면에서 검수 중을 표시하고 `/api/submissions/{id}`를 조회해 `APPROVED`, `NEEDS_REWRITE`, 실패·만료를 구분한다. 최근 60분 횟수와 재시도 시각은 세션·접수 응답의 `usage`를 사용한다. API와 프론트는 같은 origin으로 제공하며 file:// 시안을 그대로 API에 연결하지 않는다.

전광판은 장치 페어링 후 visible일 때 10초 간격 heartbeat를 보내고, SSE 알림 또는 주기 동기화로 `/api/display/state`의 모든 페이지를 읽는다. SSE에 글 본문이 담겼다고 가정하지 않는다. 브라우저 인증·HTTP 호출·화면 렌더링은 프론트 담당이 연결하고, 페이지 수집·인증 변경·당일 캐시는 아래 모듈을 사용한다.

감정 표시명과 효과는 서버의 `emotion`·`animationId`로 매핑한다. 시안의 한글 감정명·`confetti`·`sparkle`을 그대로 서버 코드로 쓰지 않는다. 효과 ID는 `fireworks`, `hearts`, `sparkles`, `petals`, `clouds`, `rain`, `embers`, `float`다. 시안의 기분 날씨·감정 통계는 고정 샘플이며 현재 집계 API가 없다. 통계를 구현한다면 당일 로컬 캐시 기준임을 표시하거나 별도 집계 계약을 먼저 추가한다. 서버 `bubbleTotalCount`는 그 시점 서버에서 공급 가능한 원문 수다.

연결 기준은 [HTTP 계약](../docs/openapi.json), [전체 흐름](../docs/service-flow.md), [전광판 캐시 모듈](../clients/display/README.md)이다. 모델 파일과 실행 방법은 [AI 모델 연결 가이드](../docs/student-model-integration.md)를 따른다.
