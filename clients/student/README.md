# 학생 작성 화면 연결

[SubmissionClient](submission-client.mjs)는 기존 완료 카드에 새 v2 접수·복구 API를 연결한다. 화면 전체나 QR 입장 UI는 포함하지 않는다. 입력은 `{content}`이며 이모지 선택을 서버에 보내지 않는다. 30자 규칙의 최종 판정은 서버가 한다.

```js
import { SubmissionClient } from "./submission-client.mjs";

const client = new SubmissionClient({ csrfToken: () => currentStudentCsrfToken });

// 사용자가 새 글을 보내는 버튼을 눌렀을 때만 begin.
client.begin(content);
const result = await client.send();
if (result.state === "ACCEPTED") {
  showExistingCompletionCard(result.view); // 메시지를 보냈어요!
  void client.watchReceipt({ onChange: updateOnlyActionableNotice });
}
// UNKNOWN이면 client.recover()로 키에 해당하는 접수 결과 확인.
// 명시적으로 재전송해도 client.send()는 동일 키·본문을 사용.
```

위 UI 함수는 프론트에서 구현한다. PREPARING·WAITING·STARTING·SHOWING·SHOWN은 모두 동일한 완료 카드를 유지한다. `NEEDS_REWRITE`나 확정된 기술 실패만 짧게 안내한다. 정상 단계마다 progress 화면을 추가하지 않는다.

`begin()`은 메모리에 새 UUID와 본문을 고정한다. 같은 요청의 네트워크 유실·HTML 응답·5xx에는 새 키를 만들지 않는다. 결과가 불명확한 동안 다른 본문으로 `begin()`하면 오류가 나므로 먼저 기존 의도를 확인한다. `recover()`의 404도 새 접수가 안전하다는 확정으로 보지 않고 UNKNOWN을 유지한다. `send()`를 다시 호출하면 기존 의도를 그대로 재전송한다.

`getReceipt()`는 본인 글 경로만 조회한다. `watchReceipt()`는 기본 3초 간격에서 기한과 읽기 실패에 따라 조정하며 terminal에서 멈춘다. 10분 게시 대기와 실제 표시 시작 뒤의 기한을 구분해 기존 5분 종료로 결과를 놓치지 않게 한다. 카드가 닫히면 `stopWatching()`, 인증 경계가 바뀌면 `resetAuthentication()`으로 지연 응답을 무효화한다. 화면 복귀 때에도 결과를 다시 확인한다.

전송 직전에 UUID·영수증 ID·만료 시각만 `sessionStorage`에 저장한다. 본문·쿠키·CSRF는 저장하지 않는다. `restorePending()`은 새로고침 뒤 본인 접수 결과를 조회하고, 원문이 없는 상태에서는 글을 재전송하지 않는다. 일반 5xx와 조회 404는 미확정 결과로 유지하며 새 UUID를 자동 발급하지 않는다. 저장소를 사용할 수 없으면 현재 탭의 메모리 복구를 유지하고 새로고침 복구 한계를 짧게 안내한다. URL이나 로그에 본문을 넣지 않는다.

```sh
node --test clients/student/submission-client.test.mjs
```
