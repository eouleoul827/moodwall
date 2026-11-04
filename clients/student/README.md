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

`getReceipt()`는 본인 글 경로만 조회한다. `watchReceipt()`는 기본 2초 간격·최대 20회, terminal에서 멈추며 UI에 상세 처리를 강요하지 않는다. 40초 뒤 조회가 끝났어도 서버의 검수·표시는 계속된다. 카드가 닫히면 `stopWatching()`, 인증 경계가 바뀌면 `resetAuthentication()`으로 지연 응답을 무효화한다.

본문·쿠키·CSRF·접수 키를 localStorage에 자동 저장하지 않는다. 새로고침 이후 미전송 초안이나 응답 불명 요청을 유지하려면 학생에게 알리는 보관 정책과 별도 구현이 필요하다. URL이나 로그에 본문을 넣지 않는다.

```sh
node --test clients/student/submission-client.test.mjs
```
