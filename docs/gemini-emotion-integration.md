# Gemini 검수 후 감정별 구름 색 연결

Gemini가 공개를 허용한 글만 기존 로컬 학생 감정 모델로 분류해 구름 색을 선택한다. 공개 여부는 [게시 정책](content-policy.md)과 Gemini 검수 결과가 결정한다. 감정 분류는 추가 Gemini 호출이나 자동 경고를 만들지 않으며, 서버의 **15초 재생**을 변경하지 않는다.

## 실행

기존 `models/student-v0/manifest.json`과 같은 폴더의 모델 4개 파일을 준비한다. [학생 모델 안내](student-model-integration.md)의 기존 bundle을 그대로 사용할 수 있다. Gemini 키 설정 도구에서 감정 모델을 함께 선택한다.

```powershell
python scripts/setup_gemini.py --emotion-model ./models/student-v0 --start
```

이 명령은 [Gemini 설정](../compose.gemini.yaml)에 [감정 모델 설정](../compose.gemini-emotion.yaml)을 추가한다. API와 worker는 같은 bundle을 읽기 전용으로 마운트하고 `MOODWALL_EMOTION_MANIFEST_PATH=/opt/student-model/manifest.json`을 사용한다. 호스트 bundle 경로는 `MOODWALL_EMOTION_BUNDLE_DIR`로 보존된다. API는 파일 무결성만 검증하고 모델을 적재하지 않으며, worker만 CPU 모델 의존성을 포함한 `student-model` 이미지를 사용한다.

Compose 없이 실행할 때에는 API와 worker 양쪽에 같은 bundle의 manifest를 `MOODWALL_EMOTION_MANIFEST_PATH`로 지정하고 worker에 [모델 의존성](../backend/requirements-model.lock)을 설치한다. 경로를 지정하지 않으면 기존 `NEUTRAL` 기본 동작을 유지한다. 경로를 명시했는데 bundle이 없거나 잘못되었으면 시작에 실패하므로 감정 분석이 연결된 것처럼 조용히 진행하지 않는다.

## 색상으로 전달하는 감정

| 로컬 분류 | 전달 값 |
|---|---|
| 축하 | `CELEBRATION` |
| 기쁨 | `JOY` |
| 응원 | `SUPPORT` |
| 설렘 | `EXCITEMENT` |
| 피곤 | `TIRED` |
| 슬픔 | `SADNESS` |
| 분노 | `ANGER` |
| 부적절 또는 판단이 분산된 결과 | `NEUTRAL` |

8번째 부적절 분류가 가장 높거나, 가장 높은 확률이 `0.5` 이하이면 중립을 사용한다. 이 수치는 보정된 감정 확신도나 검수 기준이 아니라 애매한 분류의 색을 중립으로 표시하기 위한 기준이다. 실행 중 모델 오류나 잘못된 출력도 중립으로 표시한다. 감정 점수로 학생에게 벌점을 주거나 Gemini의 `ALLOW`, `BLOCK`, `UNCERTAIN`, `SUPPORT` 결정을 바꾸지 않는다. 비공개 글에는 감정 모델을 실행하지 않는다.

## 로딩과 무결성

[연결 모듈](../backend/inference/local_emotion.py)은 manifest와 4개 artifact의 SHA-256, 정확한 8개 label 순서, ELECTRA 구조, 추가 sidecar 파일의 부재를 확인한다. 검증된 manifest·artifact·분류 매핑·연결 코드·고정 의존성의 해시가 Gemini 배포 식별자에 포함된다. API와 worker가 다른 bundle을 쓰면 동일한 배포로 취급하지 않는다. API 설정을 만들 때 검증 결과를 캐시하며 요청마다 가중치 전체를 다시 읽지 않는다. worker는 적재 직전에 재검증하고 모델을 한 번만 로드한 뒤 합성 문구로 준비 상태를 확인한다. 인터넷 다운로드와 remote code 실행은 사용하지 않는다.

기존 학생 모델의 단독 검수 분석기는 여전히 production에서 사용할 수 없다. 이 연결은 이미 Gemini가 승인한 문장의 색 선택만 허용한다.

모델 로딩과 초기 확인은 worker의 시작 제한 안에서 수행한다. 2026년 11월 5일, 기존 511 MB bundle을 Windows Docker에 읽기 전용으로 마운트한 네트워크 차단 검사에서 첫 시작은 272.128초, 이후 한 문장의 감정 추론은 0.187초였다. 시작 측정에는 검증 2회와 모델 라이브러리 초기 import·모델 적재가 포함되며 디스크 캐시와 VM 부하에 따라 달라진다. 감정 모델을 선택하는 Compose override만 worker의 초기 기동 제한을 360초로 설정하고, API에는 최초 파일 검증을 위한 healthcheck `start_period=180s`를 둔다. 다른 프로필의 기본 시작 제한 180초는 유지한다.

같은 환경의 두 번째 실행은 경량 모듈 import 1.288초, 최초 파일 검증 15.786초, worker 재검증·모델 적재·초기 확인 112.639초, 감정 추론 0.477초로 총 130.189초였다. 합성 문구는 `JOY`와 `MODEL`로 전달되었다. 두 검사 모두 Gemini 호출 없이 기존 로컬 bundle만 사용했다.

입력마다 실행하는 Gemini HTTP는 최대 8초, 감정 분류를 포함한 전체 검수 wrapper는 최대 12초이며 기존 15초 추론 lease와 supervisor가 남은 작업을 제한한다. 구름 재생은 15초다. 감정 분류만 독립적으로 1초 안에 강제 종료한다고 보장하지 않는다. 별도 추론 thread를 남기지 않으므로 감정 모델 자체가 멈추면 전체 검수가 시간 초과로 비공개 종료될 수 있다. 일반적인 추론 오류는 중립으로 처리한다.

## 확인 방법

`backend/tests/test_local_emotion.py`는 합성 bundle과 전송 대역으로 감정 매핑, 중립 처리, Gemini 결정 보존, ALLOW 뒤 호출, 변조 차단, 캐시, 단독 v0 production 금지를 확인한다. 실제 bundle을 사용하는 검사는 외부 네트워크 없이 합성 문구로 실행할 수 있다. 이 확인은 실제 Gemini 호출이나 학생 감정 분류 정확도 평가를 대신하지 않는다.
