# 전광판 재생과 당일 말풍선 캐시

**현재 v2 연결 모듈:** `playback-controller.mjs`, `playback-bubble-cache.mjs`, `playback-browser.mjs`를 새 API 0.8.0과 함께 사용한다. [로컬 구현 가이드](../../docs/playback-local-implementation.md)와 [15초 재생 설계](../../docs/playback-design.md)를 따른다. 아래 별도 v1 설명의 `bubble-cache.mjs`는 이전 호환 모듈이다.

공개 오류 비노출 연결에서는 `onProblem`을 직원 진단으로 보내고 인증·중단 제어는 유지한다. runtime의 선택적 `onState`는 `source`가 `playback` 또는 `snapshot`인 인증 메타데이터와 `connected`, `storageAvailable`, RTT를 차감한 `leaseRemainingMs`·`monotonicLeaseUntil`을 전달한다. snapshot 실패의 `connected:false`만으로 허가된 현재 글의 15초 재생을 강제 중단하지 않는다. 치명적인 RAF·루프 실패는 작업을 정리하고 `{source:"runtime",connected:false,fatal:true}`를 전달한다. 진단 콜백의 실패는 제어에 영향을 주지 않는다.

`cache.acceptCompleted(ack, item, receivedAt, requestStartedAt, expectedAuthEpoch)`는 새 `cacheScope`가 있는 확정 DONE만 검증해 즉시 보관한다. complete 전의 `cache.authEpoch`를 캡처하며 controller가 이를 연결한다. ACK는 snapshot 온라인 허가를 연장하거나 끊긴 캐시를 다시 표시하지 않는다. 초기 snapshot이 실패한 경우에도 저장은 가능하며, 다음 일치하는 인증 snapshot 전에는 공개하지 않는다. 숨김·세대·초기화·자정·인증 변경을 확인하고 최초 1시간 기한을 유지한다. UNKNOWN은 저장 근거가 아니다.

직원 설정은 `readPlaybackBubbleCatalog({boardId, storage, snapshots})`를 사용한다. `snapshots`는 현재 장치로 인증한 v2 말풍선 페이지 배열이며 모든 `nextCursor` 페이지를 읽는다. 결과는 `{bubbles,storageAvailable,cacheValid,businessDate,deviceGeneration}`이다. 온라인 lease가 끝나거나 PAUSED여도 같은 날 보관된 투명 말풍선을 숨김 관리용으로 읽을 수 있다. 이 함수는 조회 시 저장소를 쓰거나 지우지 않으며, 반환값은 공개 송출 허가가 아니다. 공개 화면은 기존 `cache.view()`만 사용한다.

v2 기본 `maxCards=5000`은 06:00~23:00 동안 15초씩 표시할 때의 이론상 4080개를 보관하도록 잡은 메모리 상한이다(설정 범위 1~10000). 공개 `view()`는 보관된 전체를 반환하며 내용·phase가 바뀌기 전에는 같은 결과 객체를 재사용한다. 복구 시 `raw.length <= 4_000_000` 검사는 그대로 유지한다. 이는 UTF-16 code unit 수이며 바이트 용량 보장이 아니다. 30개 조합 이모지는 저장 용량을 더 차지할 수 있어 저장 실패를 진단하고 현재 세션 메모리로 계속한다. synthetic Node 회귀는 실제 전광판 노트북의 DOM·애니메이션 FPS 측정과 별개다.

## v2 React 연결

```js
import { createMoodwallDisplayRuntime } from "./playback-browser.mjs";

const runtime = createMoodwallDisplayRuntime({
  boardId, // 현재 인증한 전광판 UUID
  csrfToken: () => currentDisplayCsrfToken,
  renderer: {
    async prepare(item, { signal }) {
      await document.fonts.ready;
      await prepareTheme(item.sceneId, item.sceneVersion, { signal });
    },
    show(item) { drawSceneAndText(item); },
    hide() { clearFullscreenScene(); },
  },
  renderBubbles(view) { drawFloatingBubbles(view); },
  onProblem(error) { showOperatorConnectionNotice(error.code); },
});
runtime.start();
// SSE state.changed를 받으면 runtime.refresh() 호출.
// 컴포넌트 종료 시 runtime.stop(). 재페어링/인증 해지 시 resetAuthentication().
```

위의 함수는 렌더러를 설명하는 예시다. 실제 [React 전광판](../../frontend/src/display/DisplayApp.jsx)은 글꼴 준비·동기 DOM 반영·배경 효과·말풍선을 연결했다. `show()`는 실제 DOM 반영을 끝낸 뒤 반환해야 한다. React 상태 갱신 예약만 하고 반환하면 첫 프레임 기준이 맞지 않을 수 있다. 본문은 React 텍스트 노드나 `textContent`로 넣고 HTML로 해석하지 않는다. 자산 준비가 abort되면 렌더러도 중단해야 한다.

컨트롤러는 탭마다 새 UUID를 만들고 heartbeat·claim·start·첫 프레임·15초·complete를 연결한다. 준비가 끝나고 첫 프레임을 표시한 뒤 monotonic 시계로 15초를 센다. 완료 결과가 불명확하면 같은 명령 키·본문으로 재전송하고 다음 글을 먼저 재생하지 않는다. 새 글 수신은 현재 글의 15초를 줄이지 않는다.

서버의 첫 프레임 보고 시각부터도 15초가 지나야 완료할 수 있으므로 네트워크 지연만큼 실제 노출이 조금 길어질 수 있다. 정확히 15.000초 또는 실제 HDMI 출력의 확인을 보장하지 않는다. 탭 가려짐·프레임 중단·숨김·모드 변경·인증 만료는 안전하게 중단한다. 45초 시도 watchdog과 한 번의 재생 복구는 서버가 관리한다.

v2 캐시는 첫 시작 승인부터 1시간인 `contentDeleteAt` 뒤에 내용이 같은 AMBIENT 말풍선으로 바꾼다. `renderBubbles`는 `view.visible`과 각 말풍선의 `phase`를 적용한다. 새 기기·자정·기기 세대·명시적 숨김은 캐시를 지우며 빈 서버 목록만으로 지우지 않는다. v2 저장소는 v1과 분리되어 기존 내용을 가져오지 않는다. 재생 소유권이 없는 두 번째 탭을 전광판으로 쓰지 않는다.

현재 factory는 완료 말풍선 페이지를 5초 간격으로 수집하고, 재생 중 상태를 확인하며, idle에서도 heartbeat를 보낸다. 자체 UI·QR 이미지·SSE 연결·페어링 화면·배경 아트는 포함하지 않는다.

```sh
node --test clients/display/bubble-cache.test.mjs clients/display/playback-controller.test.mjs clients/display/playback-bubble-cache.test.mjs clients/student/submission-client.test.mjs
```

## v1 호환 모듈

이 아래는 이전 API와 `bubble-cache.mjs`의 동작이다. 새 v2 흐름에 타이머만 붙여 섞지 않는다.

`bubble-cache.mjs`는 이전 v1의 React 독립 상태·페이지 동기화 모듈이다. 새 [React 화면](../../frontend/README.md)은 위 v2 모듈로 페어링·SSE·heartbeat·렌더러를 연결한다. 아래 설명과 예시는 v1 호환 경로에 적용한다.

승인된 글은 처음부터 일반 말풍선으로 떠다닌다. 강조 단계, 4개 슬롯, 표시 차례 대기는 없다.

- `acceptedAt`부터 `expiresAt` 전까지는 `FLOATING`이다. `expiresAt`은 접수 시각 + 1시간이다.
- `expiresAt`부터 글 내용이 그대로 담긴 `AMBIENT` 말풍선으로 바꾼다. 서버 원문은 1시간 뒤 삭제하지만, 전광판이 이미 받은 내용은 당일 보관한다.
- `ambientUntil`은 해당 한국 날짜의 다음 자정이다. 자정에는 말풍선과 브라우저 저장 내용을 지운다. 앱이 닫혀 있었다면 다음 인증 동기화에서 지난 날짜 캐시를 버린다.
- 다른 페이지 또는 SQL 삭제 때문에 응답에 ID가 없더라도 로컬 말풍선을 지우거나 조기에 투명하게 만들지 않는다. `removedIds`가 명시한 숨김, 캐시 초기화, 기기 교체, 인증 해지, 자정만 별도로 반영한다.

## 연결 예시

```js
import { BubbleCache, DisplaySynchronizer } from "./bubble-cache.mjs";

let storage = null;
try { storage = window.localStorage; } catch { /* 메모리로 동작 */ }
const cache = new BubbleCache({ boardId, storage });
const displaySync = new DisplaySynchronizer({
  cache,
  fetchPage: async ({ cursor, signal }) => {
    const url = new URL("/api/display/state", window.location.origin);
    if (cursor !== null) url.searchParams.set("cursor", cursor);
    const response = await fetch(url, {
      credentials: "same-origin", cache: "no-store", signal,
    });
    const body = await response.json();
    if (!response.ok) {
      throw Object.assign(new Error(body.title ?? "Display request failed"), {
        status: response.status, code: body.code,
      });
    }
    return body;
  },
});

await displaySync.synchronize();
const { visible, bubbles, totalCount } = cache.view(performance.now());
// React는 visible일 때 bubbles 전체를 렌더한다.
// phase === "AMBIENT"이면 opacity를 낮추되 content는 그대로 텍스트로 표시한다.
// dangerouslySetInnerHTML을 사용하지 않는다.

// 현재 인증 해지·재페어링 시작·현재 세션의 401/403
displaySync.resetAuthentication();
// 네트워크 단절·보호 화면 전환: 당일 캐시는 보존하되 화면을 가린다.
displaySync.disconnect();
```

`BubbleCache.synchronize(page, receivedAt, requestStartedAt)`를 직접 호출할 수도 있지만, 반드시 성공적으로 인증된 서버 응답만 넣어야 한다. `requestStartedAt`은 요청 직전, `receivedAt`은 응답 JSON을 모두 읽은 직후의 `performance.now()`다. 5초 이상 걸린 응답이나 역전된 시각은 적용하지 않고 화면을 가린다. 기존 두 인자 호출도 지원하지만 요청 소요 시간을 0으로 취급하므로, 호출자가 지연 검사를 끝낸 응답에만 사용한다. 일반 연결에는 요청 시간 측정과 timeout을 포함한 `DisplaySynchronizer`를 사용한다.

저장소의 내용을 먼저 그리지 않는다. `view()`를 주기적으로 호출해 1시간 경계와 자정을 반영한다. 반환형은 `{ visible, bubbles, totalCount }`이며 `bubbles`의 각 항목에는 `phase: "FLOATING" | "AMBIENT"`가 붙는다. `totalCount`는 현재 표시 가능한 당일 로컬 캐시 수이므로 서버의 현재 원문 수 `bubbleTotalCount`와 다를 수 있다.

## 전체 페이지 동기화

최초 접속·10초 polling·SSE 알림·재접속 때마다 cursor 없는 첫 `/api/display/state`부터 시작한다. `nextCursor !== null`인 동안 순차적으로 다음 페이지를 모두 읽는다. 페이지당 최대 100개는 전송 단위이며 화면 표시 개수 제한이 아니다. 105개라면 두 페이지를 받아 모두 합친다. ID가 같은 글은 upsert하므로 중복 페이지를 받아도 중복 말풍선이 생기지 않는다.

`DisplaySynchronizer.synchronize()`가 이 순서를 처리한다. 응답 전체의 `snapshotAt`은 같은 값이어야 하며 각 페이지의 `serverNow`와 `revision`은 최신 값일 수 있다. 다음 페이지 요청이 `422 INVALID_CURSOR`라면 첫 페이지부터 한 번 다시 수집한다. 다른 422 또는 반복된 커서 오류는 호출자에게 전달하고 화면을 가린다. 이미 받은 글은 오류만으로 지우지 않는다.

각 페이지 요청은 JSON 해석까지 5초 미만이어야 한다. 5초 timeout은 `AbortController`로 실제 요청을 중단하고 `DISPLAY_REQUEST_TIMEOUT` 오류를 반환한다. transport가 abort를 무시해도 대기 Promise는 종료되며 늦게 온 결과는 적용하지 않는다. 브라우저 중단으로 timer 실행이 늦어져도 수신 시점의 monotonic 소요 시간을 다시 검사한다. 실패하면 당일 캐시를 보존한 채 화면을 가리고, 재접속 시 첫 페이지부터 다시 동기화한다. 이 동작은 polling 예약 주기인 10초보다 짧으며 QR·SSE 요청의 timeout은 별도 호출자가 관리한다.

동기화가 끝난 뒤 다음 polling을 예약하고, SSE 조회와 polling을 합쳐 불필요하게 중첩하지 않는 편이 좋다. 새 동기화가 시작되면 이전 요청을 abort하고 epoch도 바꾸므로, abort를 무시한 오래된 응답도 적용할 수 없다. 현재 요청의 401/403은 저장 내용을 지우며, 이전 요청의 늦은 401은 새 인증의 화면을 지우지 않는다. 페어링 교체·세션 재검증 시작 때는 `resetAuthentication()`을 호출해야 한다. 이 모듈은 SSE·QR·mode 제어 요청의 수명까지 관리하지 않으므로 프론트가 해당 요청도 같은 인증 경계에서 정리해야 한다.

30초 온라인 lease가 만료되거나 PAUSED/CHATBOT이면 화면 내용을 숨기고 당일 캐시는 유지한다. heartbeat로 lease를 갱신하고 새로 인증한 state를 받아 복원한다. 오프라인 동안 자정이 지나면 재접속 여부와 관계없이 `view()` 호출에서 이전 날짜 내용을 비운다. 서버가 요청 시작 뒤에 `serverNow`를 채집한다는 계약 아래, 수신 시각의 서버 시각 상한을 `serverNow + 전체 요청 소요 시간(RTT)`으로 잡고 이후 `performance.now()`로 경과 시간을 더한다. 네트워크 지연 때문에 lease·1시간·자정 경계가 늦어지는 일을 막는 보수적 정책이다. 응답 생성 뒤의 지연만 따로 알 수 없으므로 최대 해당 RTT만큼, 항상 5초 미만으로 화면 숨김·투명 전환·자정 캐시 정리가 앞당겨질 수 있다. 서버의 DB 보관 기한은 바뀌지 않는다. 기존에 추정한 시각보다 뒤로 가지 않도록 보정도 유지한다.

## 저장과 제한

캐시는 같은 origin·브라우저 프로필의 Web Storage를 사용한다. 새로고침 때 먼저 인증된 서버 응답의 전광판·날짜·기기 세대·`cacheResetToken`을 확인한 뒤 복원한다. v2 저장 형식을 사용하며 이전 강조 슬롯 방식의 v1 캐시는 읽지 않고 폐기한다. 쿠키·CSRF·인증 토큰은 이 모듈의 저장 대상이 아니다.

숨김 목록이 1000개를 넘어 서버가 일부 ID를 생략해야 할 때는 `cacheResetToken` 변경으로 이전 캐시를 비워 숨긴 글의 재등장을 막는다. 새 노트북, 다른 브라우저, 저장소 삭제 후에는 서버에서 이미 삭제한 원문을 복원할 수 없다. 저장 실패 때는 메모리로 계속 동작하고 `storageAvailable=false`로 알리지만 새로고침 복원은 보장하지 않는다. 단일 전광판 탭 기준이며 여러 탭의 저장소 쓰기 조정은 별도 구현 대상이다.

`view()`는 저장된 말풍선 전체를 반환하며 40개 순환 창도 없다. 기본 `maxCards=2000`은 브라우저 메모리·저장소 보호를 위한 조정 가능한 상한(1~10000)이다. 초과하면 접수 시각이 가장 오래된 항목을 버린다. 이는 승인이나 표시를 4개로 제한하는 제품 규칙과 관계없다. 실제 학생 수와 노트북 저장 용량을 확인해 충분히 설정하고, 애니메이션 성능 최적화는 렌더러에서 처리한다.

## 검증

Node 24에서 다음 명령으로 실행한다. 외부 패키지는 필요 없다.

```sh
node --test clients/display/bubble-cache.test.mjs
```

테스트는 5개 동시 승인, 105개 전체 페이지 수신, 모든 캐시 반환, 1시간 투명 전환, 자정 초기화, 인증 후 복원, 숨김·기기 세대, 커서 재시작, 오래된 인증 응답 차단, 저장 실패를 확인한다. 지연된 첫 응답·재접속의 lease 복구 차단, RTT를 반영한 lease·1시간·자정 경계, timer timeout·실제 abort·abort 무시 후의 늦은 성공/401, 인증 초기화 시 대기 종료도 검사한다. 주입한 저장소·시계·timer로 수행하는 Node 테스트이므로 실제 브라우저의 영구 저장·용량·종료/재시작 시험을 대신하지 않는다.
