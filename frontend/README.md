# 무드월 React 화면

2026-11-05 [새 시안](../moodwall-design/README.md)의 학생·전광판 디자인을 적용했다. 로컬 구름 16종·낮/밤 색·모바일 다크 모드를 사용하며 Gemini 검수 정책과 15초 재생을 우선한다. [디자인 연결 기준](../docs/design-integration.md)과 [선택적 감정 모델 연결](../docs/gemini-emotion-integration.md)을 따른다.

팀의 [원본 HTML 시안](../prototypes/README.md)을 참고해 학생·전광판·운영자 화면을 React로 만들고 실제 v2 백엔드 API에 연결했다. 원본 HTML은 보존한다. 감정 배경은 준비된 CSS/SVG 테마를 선택하는 방식이며 이미지 생성 모델을 실행하지 않는다.

| 주소 | 용도 |
| --- | --- |
| `/`, `/student` | 학생의 기분 작성·접수 완료 |
| `/join#ticket=…` | 전광판 QR로 입장하는 학생 화면 |
| `/display` | HDMI로 전광판에 연결한 노트북의 브라우저 화면 |
| `/operator` | 운영자 로그인·장치 등록 코드·접수/송출 제어·글 숨김 |

전광판은 준비가 끝난 순서로 한 글씩 서버가 정한 시간(`durationMs`, 기본 15초·대기열이 쌓이면 최소 8초) 동안 표시한다. 새 글은 대기하며 현재 글을 덮지 않는다. 최초 표시 시작 승인 +1시간 이후 서버 원문은 사라지고, 노트북이 받은 내용은 투명 말풍선으로 KST 자정까지 유지한다. 학생에게는 접수 확인 후 메시지를 보냈어요! 카드만 보여주고 정상 내부 단계를 노출하지 않는다.

## 준비

Node.js와 npm, 백엔드를 실행할 Docker 환경이 필요하다. Node 버전은 [package.json](package.json)의 `engines`를 따르고 의존성은 [package-lock.json](package-lock.json)으로 고정한다. 먼저 [백엔드 실행 가이드](../backend/README.md)에 따라 로컬 `.env` 생성·DB·migration·전광판 초기화·운영자 계정을 준비한다. 비밀번호와 `.env`는 저장소에 올리지 않는다.

아래 명령은 `compose.yaml`이 있는 **저장소 최상위 폴더**에서 실행한다. 실행 방법은 다음 둘 중 하나를 선택한다.

## 빌드된 화면과 API를 같은 서버에서 실행

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
docker compose -f compose.yaml -f compose.frontend.yaml up -d --build api worker
```

`frontend/dist`가 먼저 있어야 한다. [Compose 추가 설정](../compose.frontend.yaml)은 이 폴더만 API 컨테이너에 읽기 전용으로 연결하며 저장소 전체를 공개하지 않는다. 최초 실행의 DB·migration·운영자 생성은 위 백엔드 가이드의 선행 단계다.

기본 로컬 설정 `MOODWALL_PUBLIC_ORIGIN=http://localhost:8000`에서는 다음 주소를 연다.

- 운영자: <http://localhost:8000/operator>
- 전광판 노트북: <http://localhost:8000/display>
- 학생: 전광판 QR이 가리키는 `/join#ticket=…`

화면과 API가 같은 origin을 사용한다. API의 `MOODWALL_FRONTEND_DIST` 설정이 없으면 기존 API만 실행하며 UI가 제공되지 않는다. Python API를 직접 실행한다면 이 설정에 빌드한 `frontend/dist`의 절대 경로를 지정한다.

코드를 바꾸면 `npm --prefix frontend run build`를 다시 실행하고 위 Compose 명령으로 적용한다. 프론트 자산만 바뀐 경우 브라우저도 새로고침한다. DB 볼륨 삭제는 재시작에 필요하지 않다.

## React를 수정하며 Vite 개발 서버 실행

```powershell
npm --prefix frontend ci
$env:MOODWALL_PUBLIC_ORIGIN = 'http://127.0.0.1:5173'
docker compose up -d --build api worker
npm --prefix frontend run dev
```

<http://127.0.0.1:5173/operator>와 <http://127.0.0.1:5173/display>를 연다. Vite가 `/api` 요청을 `http://127.0.0.1:8000`으로 전달하므로 React는 상대 `/api/…` 주소를 사용한다. API가 다른 개발 주소에서 실행된다면 Vite를 실행하는 셸의 `MOODWALL_API_TARGET`을 지정한다.

**백엔드의 `MOODWALL_PUBLIC_ORIGIN`은 브라우저 주소와 scheme·host·port까지 같아야 한다.** `localhost:8000`, `localhost:5173`, `127.0.0.1:5173`은 서로 다른 origin이다. 이 값이 어긋나면 로그인·장치 등록·글 전송이 Origin 검사로 거절될 수 있다. CSRF 검사를 끄거나 CORS를 전체 허용하는 방법으로 우회하지 않는다. Vite 프록시는 브라우저의 Origin을 보존한다.

빌드된 기본 화면으로 돌아갈 때는 이 셸에서 `$env:MOODWALL_PUBLIC_ORIGIN = 'http://localhost:8000'`로 맞추고 앞의 Compose 추가 설정을 함께 적용한다. 다른 터미널이나 `.env`의 실제 값도 브라우저 주소와 일치하는지 확인한다. `npm run preview`는 빌드 미리보기 도구이며 별도로 API 프록시를 제공하는 실행 방법이 아니다.

## 처음 화면을 연결하는 순서

1. `/operator`에 준비한 운영자 계정으로 로그인한다. 기본 비밀번호는 없다.
2. 등록 코드를 발급하고 노트북의 `/display/setup`에 입력한다. 코드는 5분짜리 일회용이며 다른 학생에게 공유하지 않는다. 직원 설정을 마친 뒤 `/display`를 연다. 공개 송출에는 등록 폼이나 오류 안내가 나오지 않는다.
3. 운영자 화면에서 **무드월 재개**와 **접수 재개**를 각각 실행한다. 초기 실행·장치 등록만으로 중단이 해제되지 않는다.
4. 전광판에 나온 실제 QR로 입장한다. 처음에는 익명 쿠키를 만들고 이후 같은 브라우저 세션을 재사용한다. QR ticket·CSRF·비밀번호를 localStorage에 저장하지 않는다.
5. 30자 이내 글을 보내고 접수 완료 카드와 전광판 재생을 확인한다. 이미 보낸 글의 응답이 유실되면 같은 키로 결과를 확인하며 새 글을 자동 작성하지 않는다.

원문이 사라진 투명 말풍선은 같은 전용 브라우저의 직원 설정(`/display/setup`)에서 해당 글을 고르고 운영자 숨김 화면으로 이동한다. 운영자 로그인 후 **숨김 버튼을 직접 눌러야** 적용된다. 링크에 담긴 UUID만으로 글 내용이나 권한이 복구되거나 자동으로 숨김 처리되는 것은 아니다.

노트북 화면은 표시 중인 탭으로 유지한다. 탭을 숨기거나 노트북이 절전되면 정상적인 연속 15초 재생을 보장할 수 없어 컨트롤러가 중단·복구를 처리한다. 두 탭에서 같은 전광판을 동시에 재생하면 한 소유자만 허용된다.

로컬 `localhost`나 `127.0.0.1` QR은 다른 휴대폰에서 이 PC를 가리키지 않는다. 실제 휴대폰·LTE 참여에는 접속 가능한 도메인·HTTPS·배포와 일치한 `PUBLIC_ORIGIN`이 필요하다. 이 로컬 실행으로 클라우드·도메인·학교망 설정이 완료되는 것은 아니다.

## 코드 위치와 검증 범위

| 코드 | 역할 |
| --- | --- |
| [학생 화면](src/student/StudentApp.jsx) | 작성·QR 입장·서버 한도·완료 카드·수정 안내·동일 의도 복구 |
| [전광판 화면](src/display/DisplayApp.jsx) | 공개 QR·감정 테마·첫 화면 렌더·말풍선·조용한 기본 배경 |
| [직원 설정](src/display/DisplaySetupApp.jsx) | 장치 등록·연결 확인·읽기 전용 말풍선 관리 목록 |
| [운영자 화면](src/operator/OperatorApp.jsx) | 로그인·등록 코드·중단/재개·일반/투명 말풍선 숨김 |
| [전광판 런타임](../clients/display/playback-browser.mjs) | 페이지 동기화·서버 지정 재생 시간·소유권·재연결·당일 캐시 |
| [학생 전송 모듈](../clients/student/submission-client.mjs) | 영수증 조회·동일 멱등 키 전송·최소 UI 결과 |

```powershell
npm --prefix frontend test
npm --prefix frontend run build
```

최신 수정과 검증 범위는 [전광판 복구 구현](../docs/display-recovery-implementation.md)과 [구현 상태](../docs/implementation-status.md)를 확인한다. 컴포넌트/API 모의 응답 검사와 Chrome의 합성 응답 화면 검사는 학교 HDMI 패널의 동작 검증과 다르다. 실제 휴대폰·HDMI 현장 시험과 장시간 송출·재부팅 복구는 별도다.

개발용 `fake` AI는 정상/오류 파이프라인용이다. 실제 학생 모델 v0는 평가된 게시 허용 경계가 없어 `UNCERTAIN`을 반환하고 production 로딩이 차단된다. 이 화면 연결에서 보호를 해제하지 않았다. [모델 연결 가이드](../docs/student-model-integration.md)와 [전체 구현 상태](../docs/implementation-status.md)를 함께 확인한다.
