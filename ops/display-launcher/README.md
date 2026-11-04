# 직원용 전광판 시작 아이콘

Windows 직원 계정에 전광판 전용 시작 아이콘을 설치한다. 최초 웹주소 설정과 전광판 등록은 개발자 또는 관리자가 한 번 수행하고, 직원은 이후 아이콘으로 화면을 연다. 이 실행기는 이미 실행 중인 Moodwall 서버의 `/display` 화면을 연다. API·DB·AI의 실행과 배포는 [백엔드 가이드](../../backend/README.md)의 별도 준비다.

현재 실제 서비스 웹주소는 미정이다. 주소를 정한 뒤 대상 전광판 노트북에서 설치한다. 개발 PC에 임의 주소의 바로가기를 설치하거나 실제 장치 전원 예약을 적용한 상태는 아니다.

## 직원의 매일 사용 방법

1. 노트북과 전광판을 켜고 평소 사용하는 Windows 직원 계정으로 로그인한다.
2. 인터넷이 연결되면 바탕화면의 **무드월 전광판 시작** 아이콘을 더블클릭한다.
3. 전체화면이 열리고 참여 QR이 나타나는지 확인한다. 연결 안내가 나오면 인터넷을 확인한 뒤 안내에 따라 다시 시도한다.
4. 끝날 때는 전광판 창을 `Alt+F4`로 닫고 장치를 끈다. 다른 업무용 브라우저 창을 모두 종료할 필요는 없다.

직원은 Git·Python·Node·Docker 명령이나 운영자 비밀번호를 입력하지 않는다. 전광판 등록 코드가 다시 필요하다는 화면이 나오면 관리자에게 요청한다. 웹주소를 직원이 매일 다시 입력하는 방식도 아니다.

매일 화면 운영 시간은 한국 시간 06:00~23:00이다. 이 아이콘이 오전 6시에 노트북 전원을 켜거나 오후 11시에 자동 종료시키는 것은 아니다. 운영 시간에 따른 접수 정책은 별도로 결정한다.

## 개발자 또는 관리자의 최초 설치

대상 노트북의 **직원이 실제 사용하는 Windows 계정**으로 설치한다. Edge 또는 Chrome이 설치되어 있어야 한다. 설치 폴더 전체를 복사하거나 이 저장소를 받은 뒤 개발자가 다음 명령으로 실제 서비스 주소를 지정한다. `Install.cmd`를 쓸 때도 첫 인자로 실제 주소를 전달한다. 주소 없이 더블클릭하면 설치하지 않고 사용법만 표시한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\ops\display-launcher\Install.ps1" -DisplayUrl "<실제 HTTPS 서비스 주소>"
```

`<실제 HTTPS 서비스 주소>`를 배포한 주소로 바꾼다. 루트 주소와 `/display` 주소를 받으며 다른 화면 경로·사용자 정보·쿼리·fragment를 넣지 않는다. URL에는 등록 코드·QR ticket·토큰·비밀번호를 넣지 않는다. 일반 서비스는 HTTPS를 사용하고, HTTP는 같은 PC의 loopback 개발 확인에만 사용한다.

이 폴더만 전달받았다면 압축을 먼저 풀고, `Install.ps1`이 있는 폴더에서 다음 명령을 사용한다. 압축파일 안에서 직접 실행하지 않는다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\Install.ps1" -DisplayUrl "<실제 HTTPS 서비스 주소>"
```

설치 전 실제 변경 없이 확인하려면 `-PlanOnly`를 추가한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\ops\display-launcher\Install.ps1" -DisplayUrl "<실제 HTTPS 서비스 주소>" -PlanOnly
```

계획 확인은 설치·네트워크 연결·브라우저 실행을 하지 않는다. 실제 설치는 사용자별 로컬 앱 데이터 폴더와 바탕화면 바로가기를 사용한다. 관리자 권한이나 시스템 전체 실행 정책 변경은 요구하지 않으며 `Bypass`는 이 실행 프로세스에만 지정한다. 학교의 관리 정책이 실행을 제한하는 경우에는 기기 담당자가 허용 여부를 확인한다.

설치 후 개발자가 시작 아이콘을 열고 다음을 확인한다.

1. 배포된 웹주소의 `/display` 화면이 정상적으로 열린다.
2. 별도의 운영자 화면에서 등록 코드를 발급해 전광판에 입력한다. 운영자 로그인은 전광판 전용 브라우저에 상시 남기지 않는다.
3. 필요한 접수·송출 재개를 운영자 화면에서 수행한다. 시작 아이콘은 관리자의 일시정지 설정을 임의 해제하지 않는다.
4. 전광판 창을 닫았다가 아이콘으로 다시 열어 장치 연결이 유지되는지 확인한다.
5. 전광판을 복제 화면 또는 주 화면으로 설정하고 QR·새 메시지·15초 재생을 실제 패널에서 확인한다. 다른 모니터에서 열리면 `Win+Shift+방향키`로 창을 이동한 뒤 전체화면을 확인한다.

## 장치 연결과 복구

전용 브라우저 프로필에 쿠키를 유지한다. Edge·Chrome의 프로필은 서로 다른 폴더를 사용하며 일반 업무용 프로필과 섞지 않는다. 프로필 폴더를 지우거나 다른 Windows 계정으로 실행하면 전광판을 다시 등록해야 할 수 있다.

현재 백엔드의 장치 자격은 발급 후 30일이다. 만료·관리자 해지·쿠키 삭제 때에는 관리자가 새 등록 코드를 발급한다. 아이콘 설치가 자격을 영구 연장하거나 등록을 우회하지 않는다.

노트북 브라우저를 닫으면 서버의 온라인 허가가 만료된 후 새 접수는 기존 `DISPLAY_OFFLINE` 규칙으로 막힐 수 있다. 전광판 패널만 끄고 브라우저를 계속 실행하면 서버에서 실제 패널 전원은 알 수 없다. [화면 운영 정책](../../docs/display-laptop.md)을 따른다.

초기 인터넷·서버 연결, 이미 열린 전용 창, 미등록·만료 장치는 각각 복구 안내를 사용한다. 브라우저가 열렸다는 사실만으로 실제 패널 출력이나 서비스 준비가 끝났다고 판단하지 않는다. 다른 브라우저 프로세스를 강제 종료하는 방식으로 중복 실행을 정리하지 않는다.

## 검증과 근거

스크립트의 URL 검증·공백 경로·설치 계획·프로필 분리·실행 인자 등을 브라우저를 열지 않는 테스트로 확인한다. 실제 설치와 바탕화면 아이콘 실행, 학교 관리 정책, 재부팅·화면 배치·HDMI는 대상 노트북에서 별도로 확인해야 한다.

저장소 최상위에서 다음 명령으로 Windows PowerShell 5.1 검사를 실행한다. 현재 검사 항목은 68개이며, 실제 바탕화면 바로가기·브라우저·외부 네트워크 연결을 만들지 않는다.

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ".\ops\display-launcher\Test-Launcher.ps1"
```

영속 프로필 선택은 [Chromium 사용자 데이터 폴더](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/user_data_dir.md)를 따른다. Edge 키오스크는 InPrivate 세션을 사용하므로 현재의 쿠키 기반 장치 연결에는 일반 전체화면을 사용한다. [Microsoft Edge 키오스크 문서](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-configure-kiosk-mode)

시작 프로그램 등록은 기본 설치에 포함하지 않는다. 추후 요청해 추가하더라도 Windows 로그인 후 실행이며 장치 전원 켜기나 자동 로그인 기능과 구분한다. [Microsoft 시작 프로그램 안내](https://support.microsoft.com/en-gb/windows/experience/startup-boot/configure-startup-applications-in-windows)
