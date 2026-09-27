# 기업 분석 자동화 프로젝트 — 최종 명세서

## 0. 프로젝트 개요
기업명을 입력하면 DART, 증권사 리포트(텔레그램/네이버 금융), 뉴스, 실적 데이터를 자동 수집 →
Python으로 정형/비정형 데이터 처리 → LLM으로 투자자 관점 분석 → PDF/Excel/웹 리포트를 자동 생성하는 시스템.

- 리포트 원천: 텔레그램 공개방, 네이버 금융 (최근 3개월 이내 자료만 수집)
- 개발 방식: 단계별로 하나씩 구현, 명세서/개발 규칙 문서를 먼저 고정한 뒤 진행

## 1. 전체 파이프라인

```
[사용자 입력]
    기업명 입력 (예: 테크윙)
        ↓
[1단계. 데이터 수집 - Collector]
    - DART API        : 사업보고서, 재무제표, 주요 사업 내용
    - PyKRX            : 시세/투자지표
    - 증권사 리포트    : 텔레그램 공개방 + 네이버 금융 (최근 3개월)
                         → 목표주가, 투자의견, 투자포인트
    - 뉴스 API         : 최근 기사, 산업 이슈, 이벤트
    - 실적 데이터      : 매출, 영업이익, EPS
        ↓
[2단계. 데이터 처리 - Processor]
    텍스트 분석
      - 기업명 추출
      - 산업 키워드 추출
      - 긍정/부정 뉴스 분류
      - 투자 키워드 생성
    재무 데이터 분석
      - 매출 성장률(YoY)
      - 영업이익률 변화
      - ROE / PER / PBR
      - FinancialMetrics 객체로 정규화
        ↓
[3단계. 리서치 엔진 - Research Engine]
    - 여러 출처(뉴스/리포트/재무) 비교
    - 공통 투자포인트 추출
    - Confidence Score 계산
        ↓
[4단계. AI 분석 - LLM Report Generator]
    입력: 정규화된 JSON (재무 지표 + 투자포인트 + 뉴스 요약)
    프롬프트: "아래 데이터를 기반으로 투자자 관점에서 기업 분석" (별도 파일로 분리)
    출력: Markdown 리포트
        ↓
[5단계. 출력 - Output]
    PDF / Excel / Streamlit 웹페이지
```

## 2. 폴더 구조

```
project/
├── config/
│   ├── config.py
│   └── .env
├── collectors/
│   ├── base_collector.py
│   ├── dart_collector.py
│   ├── krx_collector.py
│   ├── news_collector.py
│   ├── telegram_collector.py
│   └── naver_finance_collector.py
├── processors/
│   ├── text_processor.py
│   └── financial_processor.py
├── models/
│   ├── financial_metrics.py
│   ├── news_item.py
│   ├── report_item.py
│   └── research_result.py
├── research/
│   └── research_engine.py
├── llm/
│   ├── prompts/
│   │   └── analysis_prompt.py
│   └── report_generator.py
├── output/
│   ├── pdf_exporter.py
│   ├── excel_exporter.py
│   └── markdown_exporter.py
├── cache/
├── ui/
│   └── streamlit_app.py
├── tests/
├── DEVELOPMENT_RULES.md
├── SPEC.md
├── requirements.txt
└── main.py
```

## 3. 모듈 간 의존성 및 데이터 스키마

```
collectors → processors → research_engine → llm/report_generator → output
   ↑                              ↑
 models (공통 스키마, 모든 계층이 참조)
```

모든 Collector는 원시 데이터가 아니라 정해진 Pydantic 모델을 리턴한다.

| Collector/Processor | 반환 타입 |
|---|---|
| `DartCollector.get_financials()` | `FinancialMetrics` |
| `KrxCollector.get_price_data()` | `PriceMetrics` |
| `NewsCollector.get_recent_news()` | `list[NewsItem]` |
| `TelegramCollector.get_reports()` | `list[ReportItem]` |
| `NaverFinanceCollector.get_reports()` | `list[ReportItem]` |
| `FinancialProcessor.process()` | `FinancialMetrics` (정규화 완료) |
| `TextProcessor.analyze()` | `dict` (키워드/감성 결과) |
| `ResearchEngine.analyze()` | `ResearchResult` |
| `ReportGenerator.generate()` | `str` (Markdown) |

## 4. 데이터 수집 범위 및 제약사항
- 텔레그램/네이버 금융: 최근 3개월 이내 리포트만 수집
- 텔레그램/네이버 금융 요청은 rate limit 및 재시도 로직 필수
- 크롤링은 각 서비스 약관/robots.txt 범위 내에서, 내부 분석용으로만 사용
- 동일 요청은 로컬 캐시(24시간) 후 재사용
- Confidence Score = 언급 출처 수 × 최신성 가중치 × 방향 일치도 (필요시 조정)

---
*최종 업데이트: 2026-07-29*
