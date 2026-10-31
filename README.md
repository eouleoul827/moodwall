# slm

학생 멘션을 8개 라벨로 분류하는 KcELECTRA(`beomi/KcELECTRA-base-v2022`) 파인튜닝 모델과 로컬 테스트용 HTTP 래퍼다. 백엔드 worker 연결은 `backend/inference/student_model.py`가 담당하며, 이 폴더의 API는 모델 단독 확인과 CPU 측정에 쓴다.

라벨은 축하·기쁨·응원·설렘·피곤·슬픔·화남·부적절이다. 부적절은 백엔드 계약에서 감정이 아니라 공개 적합성 점수로 변환된다.

```
slm/
  train/moodwall_train_v0.ipynb   Colab(T4) 학습
  data/seed_data.csv              시드 문장 300개
  api/app.py                      로컬 HTTP 래퍼
  api/check_pc.py                 로딩·지연 측정
  api/start_slm.bat               Windows 상시 실행
  api/requirements.txt
```

가중치(`moodwall_model/`, 약 500MB)는 Git에 넣지 않고 드라이브로 전달한다.

## 학습

Colab에서 노트북을 열고 런타임을 T4 GPU로 바꾼 뒤 순서대로 실행한다. 3번 셀에서 `seed_data.csv`를 업로드하면 9번 셀이 `moodwall_model.zip`과 `test_set_v0.csv`를 드라이브에 저장한다. `test_set_v0.csv`는 이후 버전 비교에 고정해서 쓴다.

## 실행

`api/`의 파일과 압축을 푼 `moodwall_model/`을 한 디렉터리에 두고 Python 3.11 이상에서 실행한다.

```
python -m venv venv
venv\Scripts\activate            # macOS: source venv/bin/activate
pip install -r requirements.txt
python check_pc.py
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Intel macOS는 PyTorch 최신 빌드가 없으므로 `requirements.txt` 대신 다음 조합을 설치한다.

```
pip install "torch==2.2.2" "numpy<2" "transformers==4.44.2" fastapi uvicorn
```

Windows 상시 운영은 `start_slm.bat`을 작업 스케줄러의 "시작할 때" 트리거로 등록한다. 기본 설정의 3일 실행 제한은 해제한다.

## HTTP

`127.0.0.1`에만 바인딩한다. 원문은 로그에 남기지 않고 라벨·점수·처리 시간만 `slm.log`에 기록한다.

```
GET  /health    -> {"status": "ok"}
POST /classify  {"text": "합격 축하해!"}
             -> {"label": "축하", "score": 0.95, "inappropriate_score": 0.01, "ms": 45}
```

빈 문자열은 400이다. 30자를 넘는 입력은 잘라서 분류한다.

## 측정

Intel MacBook Pro, 2 threads 기준 로딩 1.0초, 문장당 평균 51ms, 최대 131ms다.

## 데이터

- `seed_data.csv`: 팀 작성 문장 300개. 실명은 `OO`로 가렸다.
- 부적절 문장 40개는 [Smilegate UnSmile](https://github.com/smilegate-ai/korean_unsmile_dataset) train v1.0에서 학습 시점에 직접 받는다. 저장소에 재배포하지 않으며 사용 조건은 확인 중이다.
- AI Hub 감성 대화 말뭉치는 이용 신청 상태다.

## 한계

v0은 시드 문장만으로 학습한 파이프라인 확인용이다. 안전성 평가셋이 없으므로 학생 공개 운영에 쓰지 않는다.
