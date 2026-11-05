# React 화면과 API를 같은 주소로 실행하기

화면은 `/join`(QR 입장), `/student`(작성), `/display`(전광판), `/operator`(운영) 주소를 사용한다. QR의 `joinUrl`은 `MOODWALL_PUBLIC_ORIGIN`의 `/join#ticket=...`을 가리킨다. 브라우저에서 보이는 주소와 이 설정이 일치해야 한다. 개발 서버와 실제 배포 서버는 다음 두 가지 방법으로 실행한다.

## 개발: Vite 프록시

백엔드는 8000번 포트에서 실행하고 React 개발 서버는 5173번 포트에서 실행한다. 백엔드에 `MOODWALL_PUBLIC_ORIGIN=http://localhost:5173`을 설정한 다음 브라우저로 `http://localhost:5173`에 접속한다. Vite는 `/api` 요청만 `http://127.0.0.1:8000`으로 전달한다. 프록시는 브라우저의 `Origin`을 보존해야 한다(`changeOrigin: false`). API는 기존 Origin, 쿠키, CSRF 검사를 그대로 사용한다.

`localhost`와 `127.0.0.1`을 화면 URL에서 섞지 않는다. 기기에서 접속할 경우에는 노트북의 접근 가능한 주소를 개발 서버 주소와 `MOODWALL_PUBLIC_ORIGIN`에 동일하게 적용해야 한다. 운영 공개 주소는 HTTPS로 구성한다. 기존 Compose 기본 포트는 로컬 주소에만 열려 있으므로 휴대폰에서 `localhost:8000`으로 접속할 수 없다.

## 빌드한 화면: FastAPI에서 함께 제공

저장소 최상위 폴더에서 다음 명령으로 React를 빌드한다.

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
```

Docker Compose를 쓰는 경우 기존 DB 초기화·마이그레이션·운영 계정 준비를 먼저 완료하고, 기존 `MOODWALL_PUBLIC_ORIGIN`을 화면을 열 주소로 설정한다. 로컬 기본 주소는 `http://localhost:8000`이다. 선택 구성을 추가하면 API 컨테이너가 빌드 폴더를 읽기 전용으로 제공한다.

```powershell
docker compose -f compose.yaml -f compose.frontend.yaml up -d api worker
```

기존 이미지의 빌드 방식과 DB 볼륨은 바뀌지 않는다. 화면이 변경되면 다시 빌드한 뒤 API 컨테이너를 재시작한다. `dist` 폴더가 없거나 빌드가 불완전하면 시작 시 명확히 실패한다. 화면을 제공하지 않는 API 배포는 이 구성을 추가하지 않으면 된다.

Python API를 직접 실행할 때는 `MOODWALL_FRONTEND_DIST`에 빌드된 `frontend/dist`의 절대 경로를 지정하면 같은 방식으로 제공한다. 예를 들어 현재 작업 폴더에서는 `C:\mood project\moodwall\frontend\dist`이다. API 작업자는 Linux에서 실행하는 기존 배포 지침을 따른다.

## 라우팅과 보안 범위

화면 URL과 `/assets`만 정적으로 제공한다. `/api/없는주소`를 React HTML로 바꾸지 않으며 기존 JSON 404와 `/docs`가 유지된다. 저장소, `.env`, 원본 TypeScript 파일은 제공하지 않는다. HTML은 캐시하지 않고 외부 스크립트를 허용하지 않는 CSP를 사용한다. 감정 배경의 위치 스타일과 QR 이미지에는 필요한 인라인 스타일·data 이미지만 허용한다.

화면 파일을 여는 것과 API 권한은 별개다. 전광판은 페어링, 학생은 최근 QR 입장, 운영 화면은 운영자 로그인이 필요하다. 실제 API 연결 확인에는 DB·마이그레이션·작업자·전광판 모드와 접수 재개가 필요하다. 가짜 분석기는 개발 연결 확인용이며 AI의 게시 기준이 확정됐다는 뜻이 아니다.
