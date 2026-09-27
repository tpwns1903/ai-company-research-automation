# DEVELOPMENT RULES

> 이 문서는 프로젝트의 모든 개발 규칙을 정의한다.  
> 코드 작성 전 반드시 숙지하고, 모든 PR/커밋은 이 규칙을 준수해야 한다.

---

## 1. 언어 및 스타일

| 항목 | 규칙 |
|------|------|
| Python 버전 | **3.12** |
| 코드 스타일 | **PEP8** 준수 (자동 포매터: `black`, 린터: `ruff`) |
| 최대 줄 길이 | 100자 |
| 문자열 따옴표 | 큰따옴표(`"`) 우선 |
| Import 순서 | `isort` 기준 (stdlib → third-party → local) |

---

## 2. 설계 원칙

- **객체지향 설계**: 모든 모듈은 클래스 기반으로 작성한다.
- **단일 책임 원칙(SRP)**: 하나의 함수/클래스는 하나의 역할만 수행한다.
- **계층 분리**: 아래 계층 구조를 엄격히 준수한다.

```
Collector → Processor → Research Engine → LLM → Output
               ↑
           models/ (공통 스키마, 모든 계층 참조 가능)
```

- 상위 계층은 하위 계층을 직접 호출하지 않는다.  
  예) `LLM`은 `Collector`를 직접 호출하면 안 된다.

---

## 3. 타입 및 문서화

- **Type Hint 필수**: 모든 함수의 인자와 반환값에 타입 힌트를 작성한다.

  ```python
  # ✅ 올바른 예
  def get_news(company_name: str, days: int = 30) -> list[NewsItem]:
      ...

  # ❌ 잘못된 예
  def get_news(company_name, days=30):
      ...
  ```

- **Docstring 필수**: 모든 클래스와 함수에 Google 스타일 Docstring을 작성한다.

  ```python
  def calculate_score(source_count: int, recency: float) -> float:
      """신뢰도 점수를 계산한다.

      Args:
          source_count: 해당 포인트를 언급한 출처 수.
          recency: 최신성 가중치 (0.0 ~ 1.0).

      Returns:
          계산된 신뢰도 점수 (0.0 ~ 1.0).

      Raises:
          ValueError: source_count가 0 이하인 경우.
      """
  ```

---

## 4. 로깅

- **`print()` 사용 금지**: 모든 출력은 `logging` 모듈로 대체한다.
- 모듈별로 `logger = logging.getLogger(__name__)` 패턴을 사용한다.
- 로그 레벨 기준:

  | 레벨 | 사용 상황 |
  |------|-----------|
  | `DEBUG` | 개발 중 상세 추적 정보 |
  | `INFO` | 정상 작업 흐름 주요 이벤트 |
  | `WARNING` | 예상치 못한 상황이나 주의 필요 |
  | `ERROR` | 오류 발생 (작업 실패) |
  | `CRITICAL` | 시스템 전체에 영향을 주는 치명적 오류 |

---

## 5. 설정 및 비밀값 관리

- **하드코딩 금지**: API 키, URL, 임계값 등은 모두 `config/config.py`의 `Settings`를 통해 관리한다.
- `.env` 파일에서 환경변수를 로드하며, `.env`는 절대 Git에 커밋하지 않는다 (`.gitignore` 필수).
- 새로운 설정 항목 추가 시 `.env.example`도 반드시 업데이트한다.

  ```python
  # ✅ 올바른 예
  from config.config import get_settings
  settings = get_settings()
  api_key = settings.dart_api_key

  # ❌ 잘못된 예
  api_key = "abc123xyz"
  ```

---

## 6. 데이터 스키마

- **Pydantic 모델 필수**: 모든 Collector와 Processor의 입출력은 `models/`의 Pydantic 모델을 사용한다.
- 원시 데이터(dict, raw JSON)를 상위 계층으로 직접 전달하지 않는다.
- 새로운 데이터 구조가 필요한 경우 `models/`에 스키마를 먼저 정의한다.

  ```python
  # ✅ 올바른 예
  def get_recent_news(company_name: str) -> list[NewsItem]:
      ...

  # ❌ 잘못된 예
  def get_recent_news(company_name: str) -> list[dict]:
      ...
  ```

---

## 7. 캐싱

- 동일한 외부 API 요청은 **로컬 캐시(기본 24시간)** 후 재사용한다.
- 캐시 파일은 `cache/` 디렉토리에 저장한다.
- 캐시 키는 요청 파라미터의 해시값으로 생성한다.
- `CACHE_TTL_SECONDS` 설정으로 유효 기간을 조절한다.

---

## 8. 외부 요청 (Rate Limit / 재시도)

텔레그램 및 네이버 금융 등 외부 수집 시 아래를 필수 적용한다.

- 요청 간 최소 딜레이: `settings.request_delay_seconds` (기본 1.0초)
- 실패 시 재시도: `settings.max_retry_attempts`회까지 `settings.retry_delay_seconds` 간격으로 재시도
- 3개월 초과 데이터는 수집하지 않는다 (`settings.report_lookback_days` 기준)

---

## 9. 테스트

- 각 모듈은 **실제 API 호출 없이** mock 테스트가 가능한 구조로 작성한다.
- 외부 의존성은 생성자 주입(Constructor Injection) 방식으로 분리한다.
- 테스트 파일은 `tests/` 디렉토리에 위치하며, `test_<모듈명>.py` 형식을 따른다.
- 새로운 Collector/Processor 추가 시 반드시 대응하는 mock 테스트를 함께 작성한다.

---

## 10. 파일/모듈 명명 규칙

| 대상 | 규칙 | 예시 |
|------|------|------|
| 파일명 | `snake_case` | `dart_collector.py` |
| 클래스명 | `PascalCase` | `DartCollector` |
| 함수/변수명 | `snake_case` | `get_financials()` |
| 상수 | `UPPER_SNAKE_CASE` | `DEFAULT_TIMEOUT` |
| 비공개 멤버 | 언더스코어 접두사 | `_parse_response()` |

---

## 11. Git 규칙

- 브랜치 전략: `main`(안정), `dev`(개발), `feature/<기능명>` (기능 개발)
- 커밋 메시지: `[타입] 요약` 형식
  - 타입: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`
  - 예시: `[feat] DartCollector.get_financials() 구현`
- `.gitignore`에 반드시 포함: `.env`, `cache/`, `__pycache__/`, `*.pyc`, `.venv/`

---

*최종 업데이트: 2026-07-29*
