# 전광판의 당일 말풍선 캐시

`bubble-cache.mjs`는 React와 독립된 상태·페이지 동기화 모듈이다. 실제 React 화면, 로그인·페어링 화면, SSE 연결, heartbeat와 애니메이션 렌더러는 프론트 담당이 연결해야 한다.

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

`BubbleCache.synchronize(page, performance.now())`를 직접 호출할 수도 있지만, 반드시 성공적으로 인증된 서버 응답만 넣어야 한다. 저장소의 내용을 먼저 그리지 않는다. `view()`를 주기적으로 호출해 1시간 경계와 자정을 반영한다. 반환형은 `{ visible, bubbles, totalCount }`이며 `bubbles`의 각 항목에는 `phase: "FLOATING" | "AMBIENT"`가 붙는다. `totalCount`는 현재 표시 가능한 당일 로컬 캐시 수이므로 서버의 현재 원문 수 `bubbleTotalCount`와 다를 수 있다.

## 전체 페이지 동기화

최초 접속·10초 polling·SSE 알림·재접속 때마다 cursor 없는 첫 `/api/display/state`부터 시작한다. `nextCursor !== null`인 동안 순차적으로 다음 페이지를 모두 읽는다. 페이지당 최대 100개는 전송 단위이며 화면 표시 개수 제한이 아니다. 105개라면 두 페이지를 받아 모두 합친다. ID가 같은 글은 upsert하므로 중복 페이지를 받아도 중복 말풍선이 생기지 않는다.

`DisplaySynchronizer.synchronize()`가 이 순서를 처리한다. 응답 전체의 `snapshotAt`은 같은 값이어야 하며 각 페이지의 `serverNow`와 `revision`은 최신 값일 수 있다. 다음 페이지 요청이 `422 INVALID_CURSOR`라면 첫 페이지부터 한 번 다시 수집한다. 다른 422 또는 반복된 커서 오류는 호출자에게 전달하고 화면을 가린다. 이미 받은 글은 오류만으로 지우지 않는다.

동기화가 끝난 뒤 다음 polling을 예약하고, SSE 조회와 polling을 합쳐 불필요하게 중첩하지 않는 편이 좋다. 새 동기화가 시작되면 이전 요청을 abort하고 epoch도 바꾸므로, abort를 무시한 오래된 응답도 적용할 수 없다. 현재 요청의 401/403은 저장 내용을 지우며, 이전 요청의 늦은 401은 새 인증의 화면을 지우지 않는다. 페어링 교체·세션 재검증 시작 때는 `resetAuthentication()`을 호출해야 한다. 이 모듈은 SSE·QR·mode 제어 요청의 수명까지 관리하지 않으므로 프론트가 해당 요청도 같은 인증 경계에서 정리해야 한다.

30초 온라인 lease가 만료되거나 PAUSED/CHATBOT이면 화면 내용을 숨기고 당일 캐시는 유지한다. heartbeat로 lease를 갱신하고 새로 인증한 state를 받아 복원한다. 오프라인 동안 자정이 지나면 재접속 여부와 관계없이 `view()` 호출에서 이전 날짜 내용을 비운다. 서버 시각 + `performance.now()`를 기준으로 경계를 계산하며 늦은 응답 때문에 투명 전환 시계가 뒤로 가지 않는다.

## 저장과 제한

캐시는 같은 origin·브라우저 프로필의 Web Storage를 사용한다. 새로고침 때 먼저 인증된 서버 응답의 전광판·날짜·기기 세대·`cacheResetToken`을 확인한 뒤 복원한다. v2 저장 형식을 사용하며 이전 강조 슬롯 방식의 v1 캐시는 읽지 않고 폐기한다. 쿠키·CSRF·인증 토큰은 이 모듈의 저장 대상이 아니다.

숨김 목록이 1000개를 넘어 서버가 일부 ID를 생략해야 할 때는 `cacheResetToken` 변경으로 이전 캐시를 비워 숨긴 글의 재등장을 막는다. 새 노트북, 다른 브라우저, 저장소 삭제 후에는 서버에서 이미 삭제한 원문을 복원할 수 없다. 저장 실패 때는 메모리로 계속 동작하고 `storageAvailable=false`로 알리지만 새로고침 복원은 보장하지 않는다. 단일 전광판 탭 기준이며 여러 탭의 저장소 쓰기 조정은 별도 구현 대상이다.

`view()`는 저장된 말풍선 전체를 반환하며 40개 순환 창도 없다. 기본 `maxCards=2000`은 브라우저 메모리·저장소 보호를 위한 조정 가능한 상한(1~10000)이다. 초과하면 접수 시각이 가장 오래된 항목을 버린다. 이는 승인이나 표시를 4개로 제한하는 제품 규칙과 관계없다. 실제 학생 수와 노트북 저장 용량을 확인해 충분히 설정하고, 애니메이션 성능 최적화는 렌더러에서 처리한다.

## 검증

Node 24에서 다음 명령으로 실행한다. 외부 패키지는 필요 없다.

```sh
node --test clients/display/bubble-cache.test.mjs
```

테스트는 5개 동시 승인, 105개 전체 페이지 수신, 모든 캐시 반환, 1시간 투명 전환, 자정 초기화, 인증 후 복원, 숨김·기기 세대, 커서 재시작, 오래된 인증 응답 차단, 저장 실패를 확인한다. 주입한 저장소로 수행하는 Node 테스트이므로 실제 브라우저의 영구 저장·용량·종료/재시작 시험을 대신하지 않는다.
