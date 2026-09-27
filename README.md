# AI Company Research Automation

**AI 기반 기업 리서치 자동화 파이프라인**

기업명을 입력하면 DART 공시·재무제표, 한국거래소 시세, 증권사 리포트(텔레그램 공개 채널·네이버 금융), 뉴스를 자동으로 수집하고,
Python으로 정리한 데이터를 LLM에 넘겨 **투자자 관점의 기업 분석 리포트**(Markdown / Excel / PDF / 웹 대시보드)를 만들어 줍니다.

## 파이프라인

```
기업명 입력 (예: 테크윙)
   │
   ▼
1. 수집 (collectors/)     DART · PyKRX · 텔레그램 · 네이버 금융 · 뉴스
   │                       (최근 3개월 리포트, 24시간 로컬 캐시, 재시도/rate limit)
   ▼
2. 처리 (processors/)     재무: 매출 성장률, 영업이익률, ROE/PER/PBR
   │                       텍스트: 키워드 추출, 긍정/부정 뉴스 분류
   ▼
3. 리서치 엔진 (research/) 출처 간 교차 비교 → 공통 투자포인트 + Confidence Score
   │
   ▼
4. LLM 리포트 (llm/)      정규화된 JSON → 투자자 관점 Markdown 리포트
   │                       (API 키가 없으면 규칙 기반 fallback 리포트 생성)
   ▼
5. 출력 (output/, ui/)    Markdown · Excel · PDF · Streamlit 대시보드
```

## 폴더 구조

```
.
├── main.py                 # CLI 진입점
├── config/config.py        # pydantic-settings 기반 환경변수 로드
├── collectors/             # DART, KRX, 뉴스, 텔레그램, 네이버 금융 수집기
├── processors/             # 재무/텍스트 처리
├── models/                 # Pydantic 공통 스키마 (FinancialMetrics, NewsItem, ReportItem, ResearchResult)
├── research/               # 리서치 엔진 (투자포인트 추출, 신뢰도 점수)
├── llm/                    # 리포트 생성기와 프롬프트
├── output/                 # Markdown / Excel / PDF 내보내기
├── ui/streamlit_app.py     # 웹 대시보드
├── utils/                  # 기업명 매칭, 숫자 포맷터
├── SPEC.md                 # 상세 명세서
└── DEVELOPMENT_RULES.md    # 개발 규칙
```

모든 Collector는 원시 데이터가 아니라 정해진 Pydantic 모델을 반환합니다. 모듈별 반환 타입은 [SPEC.md](SPEC.md)에 정리되어 있습니다.

## 설치

Python 3.10 이상이 필요합니다.

```bash
pip install -r requirements.txt
cp .env.example .env    # 이후 .env에 본인 API 키 입력
```

### 환경변수 (`.env`)

| 변수 | 필수 | 설명 |
|---|---|---|
| `DART_API_KEY` | ✅ | [OpenDART](https://opendart.fss.or.kr/) 인증키 |
| `OPENAI_API_KEY` | | LLM 리포트 생성. 없으면 fallback 리포트 생성 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | | 네이버 뉴스 검색 API. 없으면 Google News RSS만 사용 |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` / `TELEGRAM_PHONE` | | [my.telegram.org](https://my.telegram.org/apps)에서 발급. 없으면 텔레그램 수집 생략 |
| `TELEGRAM_CHANNELS` | | 수집할 공개 채널 목록 (JSON 배열) |

나머지 설정값(수집 기간, 캐시 TTL, LLM 모델 등)은 [.env.example](.env.example)을 참고하세요.

> `.env`, 텔레그램 세션 파일(`*.session`), 캐시, 생성된 리포트는 `.gitignore`로 저장소에서 제외됩니다.

## 사용법

```bash
# CLI
python main.py 삼성전자

# 웹 대시보드
streamlit run ui/streamlit_app.py
```

결과 리포트는 `output/reports/`에 `{기업명}_report_{시각}` 이름으로 저장됩니다.

## 대시보드

<!--
대시보드 스크린샷 추가 방법:
1. Streamlit 화면을 캡처해 docs/images/dashboard.png 로 저장
2. 아래 줄의 주석 표시를 지우고 커밋
![Streamlit 대시보드](docs/images/dashboard.png)
-->

`streamlit run ui/streamlit_app.py`로 실행합니다. 사이드바에 기업명을 입력하면 재무 지표, 증권사 컨센서스, 핵심 투자포인트·리스크, 뉴스 감성 요약을 탭으로 보여 주고, 리포트 파일(Markdown / Excel / PDF)을 내려받을 수 있습니다.

## 샘플 리포트

아래는 `python main.py 삼성전자`로 2026-09-03에 실제 생성한 리포트의 앞부분입니다. 전체 파일은 [docs/sample_report_samsung_electronics.md](docs/sample_report_samsung_electronics.md)에 있습니다.

> 이 실행에서는 LLM API 호출이 실패해 **Fallback 모드**(LLM 없이 수집 데이터를 구조화한 리포트)로 생성됐습니다. DART 재무, 증권사 리포트, 뉴스 수집과 신뢰도 계산 결과는 LLM 연동 여부와 관계없이 동일합니다.

<details>
<summary><b>삼성전자 (005930) 기업 분석 리포트 — 펼쳐 보기</b></summary>

> 분석 기준일: 2026-09-03 16:04 | 데이터 신뢰도: 7.7/10.0

**재무 분석**

| 항목 | 값 |
|------|-----|
| 매출액 | 333조 6,059억원 |
| 영업이익 | 43조 6,011억원 |
| 영업이익률 | 13.07% |
| ROE | 10.36% |

**증권사 컨센서스**

- 대표 의견: **BUY**
- 의견 분포: 매수 2건 / 중립 0건 / 매도 0건
- 평균 목표주가: **410,000원**

**핵심 투자 포인트**

1. HBM 가격 인상 주도로 DRAM ASP 상승이 지속될 전망이며, 매출의 절반 이상이 LTA(장기공급계약)로 묶여 실적 변동성이 낮아짐.
   — 신뢰도 `6.0/10.0` | 출처: 삼성증권
2. 중국의 HBM 자립도가 예상보다 낮아 현지 가속기 업체들의 HBM3급 자급 시점이 2년 뒤로 지연됨에 따라 삼성전자의 HBM 수요 강세가 지속될 전망임.
   — 신뢰도 `6.0/10.0` | 출처: 삼성증권

**뉴스 감성 요약**

- 전체 감성: **중립** (긍정 3건 / 부정 0건 / 중립 47건)
- 주요 키워드: 출시, 매출, 상승, 확대, 투자, 기술

</details>

## 주의사항

- 크롤링은 각 서비스 약관과 robots.txt 범위 안에서 개인 분석용으로만 사용합니다.
- 생성된 리포트는 참고 자료이며 투자 권유가 아닙니다.
