# 설계 검증 재현

저장소 루트에서 실행한다. 서버 구현·모델 성능·HTTP 동시성을 검증하는 도구는 아니다. Python 검사는 API 구조·예시·인증 메타데이터·SQL 문법·문서 링크·AI 입출력/최종화 계약·HTTP DTO 경계와 [전체 분기표](../backend-completeness-tree.md)의 API 연결을 확인한다. Markdown의 AI Python 예시를 실행하므로 검토한 저장소 코드에만 사용한다.

앱과 분리된 가상환경 예시다. 아래 경로는 저장소의 바깥이므로 앱 의존성이나 학습 환경을 바꾸지 않는다.

```powershell
python -m venv ..\.moodwall-design-venv
..\.moodwall-design-venv\Scripts\python -m pip install -r docs/checks/requirements.txt
..\.moodwall-design-venv\Scripts\python docs/checks/verify_contracts.py
```

SQL 검사는 **새 격리 PostgreSQL 16 데이터베이스**에만 스키마를 먼저 적용한 뒤 실행한다. 운영 DB에서 실행하지 않는다. 연결 정보는 환경 설정으로 주입하며 비밀번호를 명령 이력이나 저장소에 적지 않는다.

```powershell
psql -v ON_ERROR_STOP=1 -f docs/schema.sql
psql -v ON_ERROR_STOP=1 -f docs/checks/schema-invariants.sql
```

두 번째 스크립트는 34개의 잘못된 입력이 지정한 제약 이름으로 거절되는지와 rolling 경계·숨김·유효 규칙/감사 필드의 8개 단언을 확인하고 전체 fixture를 롤백한다. 단순히 어떤 오류든 발생하면 통과시키지 않는다. 처리 임대의 최종화 조건·잠금 경합·위반 증가·HTTP 명령 재전송·큐 복구는 DB 제약만으로 증명되지 않으므로 [출시 시험](../verification.md)과 별도로 실행한다.
