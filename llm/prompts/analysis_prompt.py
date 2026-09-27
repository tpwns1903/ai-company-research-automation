"""
analysis_prompt.py
LLM 분석 프롬프트 템플릿 정의 모듈

ResearchResult 데이터를 받아 LLM에 전달할 최종 프롬프트를 조립한다.
프롬프트는 이 파일에서만 관리되며, report_generator.py는 이 함수를 호출하기만 한다.
"""

from __future__ import annotations

from typing import Any, Optional

from utils.formatters import format_confidence_10, format_won_uk

# 프롬프트에 넣는 뉴스 건수. 최신순 상위 몇 건은 행사·홍보 기사일 때가 많아
# 넉넉히 넣고, 투자 판단과 관련된 기사만 고르도록 LLM에 지시한다.
_NEWS_FOR_PROMPT = 15

# ── 시스템 프롬프트 ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """당신은 10년 이상의 경력을 가진 전문 주식 애널리스트입니다.
제공된 재무 데이터, 증권사 리포트, 뉴스 요약을 바탕으로
투자자 관점에서 균형 잡힌 기업 분석 리포트를 Markdown 형식으로 작성합니다.

작성 원칙:
- 제공된 사실 데이터만 사용하고, 데이터에 없는 내용은 추론하지 않습니다.
- 긍정적 투자 포인트와 리스크 요인을 반드시 균형 있게 다룹니다.
- 투자 추천(매수/매도/보유)은 하지 않으며, 분석 결과와 관점만 제공합니다.
  증권사 의견을 옮길 때는 "○○증권은 …로 판단" 처럼 출처를 밝히고,
  "매수 기회", "비중 확대 기회" 같은 권유 표현을 리포트 자신의 판단처럼 쓰지 않습니다.
- 증권사 리포트가 없을 경우, 재무 데이터와 뉴스만으로 분석하고 해당 사실을 리포트에 명시합니다.
- 숫자는 반드시 단위(억원, %, 원 등)와 함께 표기합니다.

데이터 일관성 규칙:
- 신뢰도 점수는 항상 입력 데이터에 적힌 그대로 "X.X/10.0" 형식으로 표기합니다. 0~1 척도로 바꾸지 않습니다.
- 목표주가와 투자의견 수치는 '증권사 컨센서스' 섹션의 값만 사용합니다.
  뉴스 제목에 다른 목표주가가 나오더라도 인용하지 않습니다 (수집되지 않은 증권사 리포트라 검증할 수 없음).
- 컨센서스가 증권사 1~2곳 기준이면 그 사실을 밸류에이션 문단에 밝힙니다.

뉴스 사용 규칙:
- 뉴스는 기업가치에 영향을 주는 내용만 사용합니다:
  실적·가이던스, 업황·제품 가격, 수주·공급 계약, 설비 투자, 경쟁 구도, 규제·소송,
  주주환원·자본 정책, 지배구조, 파업 등 생산·실적 차질이 기사에 명시된 노사 분쟁 등.
- 다음은 투자 판단과 무관하므로 기업 개요와 리스크 요인에 쓰지 않습니다:
  박람회·전시·행사 참가, 제품 홍보·마케팅, 사회공헌·봉사, 사내 행사·타운홀,
  육아휴직·복지·성과급 등 일반 근무 여건, 생산 차질 언급이 없는 노조 집회·임금 협상,
  특정 개인 투자자의 매매, 다른 기업의 주가 움직임.
- '[뉴스]'로 시작하는 리스크 항목은 키워드로 자동 추출되어 위 기준에 맞지 않을 수 있습니다.
  기업가치와 관련 있는 것만 리스크로 쓰고, 나머지는 버립니다.
"""

# ── 리포트 구조 명세 (LLM 지시) ──────────────────────────────────────────────

_REPORT_STRUCTURE_INSTRUCTION = """
---

위 데이터를 바탕으로 아래 **정확한 구조**의 Markdown 리포트를 작성해주세요.
각 섹션의 헤더(##)를 반드시 그대로 사용하세요.

---

## 기업 개요
(주요 사업 영역, 산업 내 포지셔닝, 실적·업황 관점의 최근 사업 현황을 3~5줄로 요약.
 행사·홍보·사내 소식은 쓰지 않음)

## 재무 분석
### 성장성
(매출액/영업이익 성장률(YoY) 중심으로 1~2줄)
### 수익성
(영업이익률, ROE, ROA 중심으로 1~2줄)
### 밸류에이션
(PER, PBR, 시가총액, 컨센서스 목표주가 대비 현재가 등 1~2줄)

## 핵심 투자 포인트
(제공된 투자 포인트 중 상위 3가지를 각 2~3줄로 설명. 신뢰도 점수가 높은 순으로,
 제목 옆에 신뢰도를 "(신뢰도: X.X/10.0)" 형식으로 표기)

## 리스크 요인
(기업가치에 영향을 주는 리스크 2~3가지를 각 1~2줄로 설명. 투자 판단과 무관한 뉴스는 제외)

## 결론 및 투자자 관점 요약
(종합 분석 요약 3~5줄. 데이터 신뢰도("X.X/10.0" 형식), 모니터링 포인트 포함)
"""


# ── 프롬프트 조립 함수 ────────────────────────────────────────────────────────

def build_analysis_prompt(research_result_dict: dict[str, Any]) -> str:
    """ResearchResult 딕셔너리를 받아 LLM에 전달할 최종 분석 프롬프트를 조립한다.

    Args:
        research_result_dict: ResearchResult.model_dump(mode='json') 결과.

    Returns:
        str: LLM에 전달할 완성된 User 프롬프트 문자열.
    """
    company_name: str = research_result_dict.get("company_name", "알 수 없음")
    stock_code: Optional[str] = research_result_dict.get("stock_code")
    analysis_date: str = str(research_result_dict.get("analysis_date", ""))[:10]
    overall_confidence: float = research_result_dict.get("overall_confidence_score", 0.0)
    quality_notes: list[str] = research_result_dict.get("data_quality_notes", [])

    sections: list[str] = []

    # ── 섹션 1: 분석 메타 정보 ────────────────────────────────────────────────
    ticker_str = f"({stock_code})" if stock_code else ""
    sections.append(f"# 기업 분석 요청\n")
    sections.append(
        f"**분석 대상**: {company_name} {ticker_str}  \n"
        f"**분석 기준일**: {analysis_date}  \n"
        f"**전체 데이터 신뢰도**: {format_confidence_10(overall_confidence)}  \n"
    )

    if quality_notes:
        notes_str = "\n".join(f"- {n}" for n in quality_notes)
        sections.append(f"\n> **데이터 품질 주의사항**\n{notes_str}\n")

    sections.append("---\n")

    # ── 섹션 2: 재무 지표 ─────────────────────────────────────────────────────
    fin = research_result_dict.get("financial_metrics")
    if fin:
        sections.append(_format_financial_metrics(fin))
    else:
        sections.append("## 재무 지표\n재무 지표 데이터 없음\n")
    sections.append("---\n")

    # ── 섹션 3: 증권사 컨센서스 ──────────────────────────────────────────────
    consensus = research_result_dict.get("consensus")
    has_reports = research_result_dict.get("source_reports") or []
    sections.append(_format_consensus(consensus, len(has_reports)))
    sections.append("---\n")

    # ── 섹션 4: 핵심 투자 포인트 ─────────────────────────────────────────────
    inv_points = research_result_dict.get("investment_points", [])
    sections.append(_format_investment_points(inv_points))
    sections.append("---\n")

    # ── 섹션 5: 리스크 요인 ───────────────────────────────────────────────────
    risks = research_result_dict.get("risk_factors", [])
    sections.append(_format_risk_factors(risks))
    sections.append("---\n")

    # ── 섹션 6: 뉴스 감성 요약 + 최근 뉴스 ──────────────────────────────────
    sentiment = research_result_dict.get("sentiment_summary")
    news_list = research_result_dict.get("source_news", [])
    sections.append(_format_news_section(sentiment, news_list))
    sections.append("---\n")

    # ── 리포트 구조 지시 ─────────────────────────────────────────────────────
    sections.append(_REPORT_STRUCTURE_INSTRUCTION)

    return "\n".join(sections)


# ── 섹션별 포매터 ─────────────────────────────────────────────────────────────

def _format_financial_metrics(fin: dict[str, Any]) -> str:
    """재무 지표 딕셔너리를 Markdown 섹션 문자열로 변환한다."""
    lines = ["## 재무 지표 (최근 기준)\n"]

    def _v(key: str, unit: str = "", fmt: str = ".1f") -> str:
        val = fin.get(key)
        if val is None:
            return "N/A"
        try:
            return f"{val:{fmt}}{unit}"
        except (TypeError, ValueError):
            return str(val)

    lines.append(f"| 항목 | 값 |")
    lines.append(f"|------|-----|")
    lines.append(f"| 회계연도 | {fin.get('fiscal_year', 'N/A')} |")
    lines.append(f"| 매출액 | {format_won_uk(fin.get('revenue'))} |")
    lines.append(f"| 영업이익 | {format_won_uk(fin.get('operating_income'))} |")
    lines.append(f"| 당기순이익 | {format_won_uk(fin.get('net_income'))} |")
    lines.append(f"| 매출 성장률(YoY) | {_v('revenue_yoy', '%')} |")
    lines.append(f"| 영업이익 성장률(YoY) | {_v('operating_income_yoy', '%')} |")
    lines.append(f"| 영업이익률 | {_v('operating_margin', '%')} |")
    lines.append(f"| ROE | {_v('roe', '%')} |")
    lines.append(f"| ROA | {_v('roa', '%')} |")
    lines.append(f"| PER | {_v('per', '배', '.2f')} |")
    lines.append(f"| PBR | {_v('pbr', '배', '.2f')} |")
    lines.append(f"| 현재 주가 | {_v('closing_price', '원', ',.0f')} |")
    lines.append(f"| 시가총액 | {format_won_uk(fin.get('market_cap'))} |")
    lines.append(f"| 부채비율 | {_v('debt_to_equity', '%')} |")
    lines.append("")
    return "\n".join(lines)


def _format_consensus(consensus: Optional[dict[str, Any]], report_count: int) -> str:
    """증권사 컨센서스 딕셔너리를 Markdown 섹션 문자열로 변환한다."""
    lines = ["## 증권사 컨센서스\n"]

    if not consensus or report_count == 0:
        lines.append("> **참고 가능한 증권사 리포트 없음** — 재무 데이터 및 뉴스 기반으로만 분석합니다.\n")
        return "\n".join(lines)

    opinion = consensus.get("dominant_opinion", "N/A")
    buy = consensus.get("buy_count", 0)
    hold = consensus.get("hold_count", 0)
    sell = consensus.get("sell_count", 0)
    brokers = consensus.get("brokerage_count", 0)
    avg_tp = consensus.get("avg_target_price")
    max_tp = consensus.get("max_target_price")
    min_tp = consensus.get("min_target_price")

    lines.append(f"- **집계 기준**: 증권사 {brokers}곳의 최신 리포트 (수집 리포트 총 {report_count}건)")
    lines.append(f"- **대표 의견**: {opinion}")
    lines.append(f"- **의견 분포**: 매수 {buy}곳 / 중립 {hold}곳 / 매도 {sell}곳")
    if avg_tp:
        lines.append(f"- **평균 목표주가**: {avg_tp:,.0f}원")
    if max_tp and min_tp and max_tp != min_tp:
        lines.append(f"- **목표주가 범위**: {min_tp:,.0f}원 ~ {max_tp:,.0f}원")
    lines.append("")
    return "\n".join(lines)


def _format_investment_points(points: list[dict[str, Any]]) -> str:
    """투자 포인트 목록을 Markdown 섹션 문자열로 변환한다."""
    lines = ["## 주요 투자 포인트 (다중 출처 교차 검증)\n"]

    if not points:
        lines.append("교차 확인된 투자 포인트 없음 (데이터 부족)\n")
        return "\n".join(lines)

    for i, pt in enumerate(points[:5], 1):
        point_text = pt.get("point", "")
        confidence = pt.get("confidence_score", 0.0)
        sources = pt.get("source_names", [])
        src_count = pt.get("source_count", len(sources))
        src_str = ", ".join(sources) if sources else "미상"
        lines.append(f"**[{i}]** {point_text}")
        lines.append(
            f"  - 신뢰도: {format_confidence_10(confidence)} | 출처 수: {src_count}건 ({src_str})"
        )
        lines.append("")

    return "\n".join(lines)


def _format_risk_factors(risks: list[str]) -> str:
    """리스크 요인 목록을 Markdown 섹션 문자열로 변환한다."""
    lines = ["## 리스크 요인 후보 ('[뉴스]' 항목은 키워드 자동 추출 — 관련 있는 것만 사용)\n"]

    if not risks:
        lines.append("명시된 리스크 요인 없음\n")
        return "\n".join(lines)

    for risk in risks[:5]:
        lines.append(f"- {risk}")
    lines.append("")
    return "\n".join(lines)


def _format_news_section(
    sentiment: Optional[dict[str, Any]], news_list: list[dict[str, Any]]
) -> str:
    """뉴스 감성 요약과 최근 뉴스 목록을 Markdown 섹션 문자열로 변환한다."""
    lines = ["## 뉴스 감성 요약\n"]

    if sentiment:
        overall = sentiment.get("overall_sentiment", "neutral")
        pos = sentiment.get("positive_count", 0)
        neg = sentiment.get("negative_count", 0)
        neu = sentiment.get("neutral_count", 0)
        avg_score = sentiment.get("avg_sentiment_score")
        top_kws = sentiment.get("top_keywords", [])

        sentiment_label = {"positive": "긍정", "negative": "부정", "neutral": "중립"}.get(
            overall, overall
        )
        lines.append(f"- **전체 감성**: {sentiment_label}")
        lines.append(f"- **기사 분포**: 긍정 {pos}건 / 부정 {neg}건 / 중립 {neu}건")
        if avg_score is not None:
            lines.append(f"- **평균 감성 점수**: {avg_score:+.2f} (-1.0 ~ +1.0)")
        if top_kws:
            lines.append(f"- **주요 키워드**: {', '.join(top_kws[:8])}")
        lines.append("")
    else:
        lines.append("뉴스 감성 데이터 없음\n")

    if news_list:
        lines.append(
            f"### 최근 뉴스 (최신순 {min(len(news_list), _NEWS_FOR_PROMPT)}건, "
            "관련도 필터링 전 — 투자 판단과 관련된 기사만 사용)\n"
        )
        for news in news_list[:_NEWS_FOR_PROMPT]:
            title = news.get("title", "")
            source = news.get("source", "")
            pub_at = str(news.get("published_at", ""))[:10]
            sentiment_val = news.get("sentiment", "neutral")
            sentiment_icon = {"positive": "[+]", "negative": "[-]", "neutral": "[=]"}.get(
                sentiment_val, "[=]"
            )
            lines.append(f"- {sentiment_icon} **{title}** ({source}, {pub_at})")
        lines.append("")

    return "\n".join(lines)
