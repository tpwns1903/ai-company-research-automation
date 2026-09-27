"""
research_engine.py
리서치 엔진 — 다중 출처 교차 검증, 공통 투자포인트 추출, Confidence Score 계산
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from models.financial_metrics import FinancialMetrics
from models.news_item import NewsItem, SentimentType
from models.report_item import InvestmentOpinion, ReportItem, ReportSource
from models.research_result import (
    ConsensusOpinion,
    InvestmentPoint,
    ResearchResult,
    SentimentSummary,
)
from processors.text_processor import TextProcessor

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")


class ResearchEngine:
    """수집된 재무, 뉴스, 증권사 리포트 데이터를 통합 분석하는 리서치 엔진.

    주요 기능:
        - 교차 검증을 통한 공통 투자 포인트 추출
        - 개별 투자 포인트 및 전체 분석 Confidence Score 계산
        - 증권사 컨센서스(목표주가, 투자의견 분포) 집계
        - 뉴스 감성 분포 및 주요 키워드 집계
        - 일부 데이터 누락 시에도 안전하게 이용 가능한 데이터 기반 분석 수행 (Empty Data Fault Tolerance)
    """

    def __init__(self, text_processor: Optional[TextProcessor] = None) -> None:
        """ResearchEngine을 초기화한다.

        Args:
            text_processor: 텍스트 파싱용 TextProcessor. None이면 자동 생성.
        """
        self._text_processor = text_processor or TextProcessor()
        logger.debug("ResearchEngine 초기화 완료")

    def analyze(
        self,
        company_name: str,
        financial_metrics: Optional[FinancialMetrics] = None,
        reports: Optional[list[ReportItem]] = None,
        news_items: Optional[list[NewsItem]] = None,
    ) -> ResearchResult:
        """수집된 데이터를 통합하여 ResearchResult 모델을 생성한다.

        Args:
            company_name: 분석 대상 기업명.
            financial_metrics: 정규화된 재무 데이터.
            reports: 수집된 증권사 리포트 목록.
            news_items: 수집된 뉴스 기사 목록.

        Returns:
            ResearchResult: LLM 및 최종 출력을 위한 통합 리서치 객체.
        """
        reports_list = reports or []
        news_list = news_items or []
        quality_notes: list[str] = []

        # 데이터 부재 관련 품질 노트 생성
        if not reports_list:
            quality_notes.append("증권사 리포트 데이터가 수집되지 않았습니다 (0건).")
        elif len(reports_list) < 3:
            quality_notes.append(f"증권사 리포트 수가 적습니다 ({len(reports_list)}건).")

        if not news_list:
            quality_notes.append("뉴스 기사 데이터가 수집되지 않았습니다 (0건).")
        elif len(news_list) < 5:
            quality_notes.append(f"뉴스 기사 수집 수가 적습니다 ({len(news_list)}건).")

        if not financial_metrics:
            quality_notes.append("DART 재무 지표 데이터가 누락되었습니다.")

        # 1. 증권사 컨센서스 집계
        consensus = self._build_consensus(reports_list)

        # 2. 뉴스 감성 및 키워드 집계
        sentiment_summary = self._build_sentiment_summary(news_list)

        # 3. 공통 투자 포인트 추출 및 Confidence Score 계산
        investment_points = self._extract_investment_points(reports_list, news_list)

        # 4. 공통 리스크 요인 추출
        risk_factors = self._extract_risk_factors(reports_list, news_list)

        # 5. 전체 Confidence Score 계산
        overall_score = self._calculate_overall_confidence_score(
            reports_count=len(reports_list),
            news_count=len(news_list),
            has_financials=financial_metrics is not None,
            investment_points=investment_points,
        )

        stock_code = financial_metrics.stock_code if financial_metrics else None

        logger.info(
            "[ResearchEngine] 분석 완료: %s - 종합 신뢰도: %.2f (포인트: %d개, 노트: %d개)",
            company_name, overall_score, len(investment_points), len(quality_notes)
        )

        return ResearchResult(
            company_name=company_name,
            stock_code=stock_code,
            analysis_date=datetime.now(_KST),
            investment_points=investment_points,
            risk_factors=risk_factors,
            consensus=consensus,
            sentiment_summary=sentiment_summary,
            financial_metrics=financial_metrics,
            source_reports=reports_list,
            source_news=news_list,
            overall_confidence_score=overall_score,
            data_quality_notes=quality_notes,
        )

    # ── 내부 분석 구현 ────────────────────────────────────────────────────────

    def _extract_investment_points(
        self, reports: list[ReportItem], news_items: list[NewsItem]
    ) -> list[InvestmentPoint]:
        """증권사 리포트와 뉴스 데이터를 분석하여 교차 확인된 공통 투자 포인트를 추출한다.

        Confidence Score 공식 (보정안):
            Confidence Score = min(1.0, (0.3 + 0.25 * log2(1 + source_count)))
                               * recency_weight * direction_consistency

            - 언급 출처 수가 1개여도 0.55 기본점을 부여하되, 여러 출처에서 언급될수록 로그적으로 상승
            - 최신성 가중치: 최근 14일 1.0, 30일 0.9, 60일 0.7, 90일 0.5
            - 방향 일치도: 긍정/매수 일치 시 1.0, 중립/혼조 0.6, 부정적 0.2
        """
        raw_candidates: list[dict[str, Any]] = []

        # 리포트 투자포인트 수집
        for r in reports:
            for pt in r.investment_points:
                if len(pt.strip()) >= 5:
                    raw_candidates.append(
                        {
                            "point": pt.strip(),
                            "source_name": r.brokerage,
                            "published_at": r.published_at,
                            "is_positive": r.opinion in (InvestmentOpinion.BUY, InvestmentOpinion.STRONG_BUY),
                        }
                    )

        # 뉴스 제목 중 실적/계약/성장 관련 수집
        for n in news_items:
            if n.sentiment == SentimentType.POSITIVE and n.keywords:
                raw_candidates.append(
                    {
                        "point": n.title,
                        "source_name": n.source,
                        "published_at": n.published_at,
                        "is_positive": True,
                    }
                )

        if not raw_candidates:
            return []

        # 클러스터링 및 중복 합체 (간단한 키워드 유무 기준 그룹화)
        clusters: list[list[dict[str, Any]]] = []
        for cand in raw_candidates:
            matched = False
            for cluster in clusters:
                rep = cluster[0]["point"]
                # 단어 겹침 비율이 높은 경우 같은 그룹으로 통합
                words_a = set(self._text_processor.extract_keywords(cand["point"], top_n=5))
                words_b = set(self._text_processor.extract_keywords(rep, top_n=5))
                if words_a and words_b and len(words_a & words_b) >= 2:
                    cluster.append(cand)
                    matched = True
                    break
            if not matched:
                clusters.append([cand])

        results: list[InvestmentPoint] = []
        now = datetime.now(timezone.utc)

        for cluster in clusters:
            best_point_text = max(cluster, key=lambda x: len(x["point"]))["point"]
            source_names = list({item["source_name"] for item in cluster})
            source_count = len(source_names)

            # 1. 최신성 가중치 계산 (가장 최근 발행일 기준)
            newest_date = max(item["published_at"] for item in cluster)
            if newest_date.tzinfo is None:
                newest_date = newest_date.replace(tzinfo=timezone.utc)
            days_diff = max(0, (now - newest_date).days)

            if days_diff <= 14:
                recency_weight = 1.0
            elif days_diff <= 30:
                recency_weight = 0.9
            elif days_diff <= 60:
                recency_weight = 0.75
            elif days_diff <= 90:
                recency_weight = 0.6
            else:
                recency_weight = 0.4

            # 2. 방향 일치도 계산
            pos_count = sum(1 for item in cluster if item["is_positive"])
            direction_consistency = round(pos_count / len(cluster), 2)
            if direction_consistency < 0.3:
                direction_consistency = 0.3  # 최소 바운드

            # 3. Confidence Score 계산 공식 적용
            # Source_Factor = min(1.0, 0.35 + 0.25 * math.log2(1 + source_count))
            source_factor = min(1.0, 0.35 + 0.25 * math.log2(1 + source_count))
            confidence_score = round(source_factor * recency_weight * direction_consistency, 2)
            confidence_score = max(0.0, min(1.0, confidence_score))

            results.append(
                InvestmentPoint(
                    point=best_point_text,
                    source_count=source_count,
                    source_names=source_names,
                    recency_weight=recency_weight,
                    direction_consistency=direction_consistency,
                    confidence_score=confidence_score,
                )
            )

        # 신뢰도 내림차순 정렬 후 상위 5개 반환
        results.sort(key=lambda p: p.confidence_score, reverse=True)
        return results[:5]

    def _extract_risk_factors(
        self, reports: list[ReportItem], news_items: list[NewsItem]
    ) -> list[str]:
        """리포트의 risk_factors 및 부정이 강한 뉴스에서 리스크 요인을 추출한다."""
        risks: list[str] = []

        for r in reports:
            for rf in r.risk_factors:
                rf_str = rf.strip()
                if rf_str and rf_str not in risks:
                    risks.append(rf_str)

        for n in news_items:
            if n.sentiment == SentimentType.NEGATIVE and n.sentiment_score and n.sentiment_score <= -0.4:
                title = n.title.strip()
                if title and title not in risks:
                    risks.append(f"[뉴스] {title}")

        return risks[:5]

    def _build_consensus(self, reports: list[ReportItem]) -> Optional[ConsensusOpinion]:
        """증권사 리포트들로부터 투자의견 및 목표주가 컨센서스를 계산한다.

        같은 증권사가 여러 번 낸 리포트(텔레그램 재배포, 목표가 변경 전후 등)를
        모두 세면 한 증권사 의견이 과대 반영되고 변경 전 목표가가 섞인다.
        그래서 증권사별로 가장 최근 리포트의 투자의견과 목표주가 하나씩만 집계한다.
        """
        if not reports:
            return None

        latest_opinion: dict[str, InvestmentOpinion] = {}
        latest_target: dict[str, float] = {}
        for r in sorted(reports, key=lambda x: x.published_at):  # 오래된 것 → 최신 순으로 덮어쓰기
            broker = (r.brokerage or "미상").strip()
            if r.opinion != InvestmentOpinion.NOT_RATED:
                latest_opinion[broker] = r.opinion
            if r.target_price and r.target_price > 0:
                latest_target[broker] = r.target_price

        buy_count = 0
        hold_count = 0
        sell_count = 0
        for opinion in latest_opinion.values():
            if opinion in (InvestmentOpinion.BUY, InvestmentOpinion.STRONG_BUY):
                buy_count += 1
            elif opinion in (InvestmentOpinion.HOLD, InvestmentOpinion.NEUTRAL):
                hold_count += 1
            elif opinion in (InvestmentOpinion.SELL, InvestmentOpinion.UNDERPERFORM):
                sell_count += 1

        target_prices: list[float] = list(latest_target.values())

        total_reports = len(reports)
        if buy_count >= hold_count and buy_count >= sell_count:
            dominant = InvestmentOpinion.BUY
        elif hold_count >= sell_count:
            dominant = InvestmentOpinion.HOLD
        else:
            dominant = InvestmentOpinion.SELL

        avg_tp = round(sum(target_prices) / len(target_prices), 0) if target_prices else None
        max_tp = max(target_prices) if target_prices else None
        min_tp = min(target_prices) if target_prices else None

        return ConsensusOpinion(
            dominant_opinion=dominant,
            buy_count=buy_count,
            hold_count=hold_count,
            sell_count=sell_count,
            total_report_count=total_reports,
            brokerage_count=len(set(latest_opinion) | set(latest_target)),
            avg_target_price=avg_tp,
            max_target_price=max_tp,
            min_target_price=min_tp,
        )

    def _build_sentiment_summary(self, news_items: list[NewsItem]) -> Optional[SentimentSummary]:
        """수집된 뉴스 기사들로부터 감성 분포 및 주요 키워드를 요약한다."""
        if not news_items:
            return None

        pos_count = 0
        neg_count = 0
        neu_count = 0
        scores: list[float] = []
        all_keywords: list[str] = []

        for n in news_items:
            if n.sentiment == SentimentType.POSITIVE:
                pos_count += 1
            elif n.sentiment == SentimentType.NEGATIVE:
                neg_count += 1
            else:
                neu_count += 1

            if n.sentiment_score is not None:
                scores.append(n.sentiment_score)

            all_keywords.extend(n.keywords)

        avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0

        if pos_count > neg_count and pos_count >= neu_count:
            overall = SentimentType.POSITIVE
        elif neg_count > pos_count and neg_count >= neu_count:
            overall = SentimentType.NEGATIVE
        else:
            overall = SentimentType.NEUTRAL

        # 상위 키워드 10개 추출
        kw_counts: dict[str, int] = {}
        for kw in all_keywords:
            kw_counts[kw] = kw_counts.get(kw, 0) + 1
        top_kws = [k for k, _ in sorted(kw_counts.items(), key=lambda x: x[1], reverse=True)[:10]]

        return SentimentSummary(
            overall_sentiment=overall,
            positive_count=pos_count,
            negative_count=neg_count,
            neutral_count=neu_count,
            avg_sentiment_score=avg_score,
            top_keywords=top_kws,
        )

    def _calculate_overall_confidence_score(
        self,
        reports_count: int,
        news_count: int,
        has_financials: bool,
        investment_points: list[InvestmentPoint],
    ) -> float:
        """전체 분석 결과의 종합 신뢰도 점수(0.0 ~ 1.0)를 계산한다."""
        # 1. 데이터 소스 충실도 점수 (최대 0.5)
        source_score = 0.0
        if has_financials:
            source_score += 0.2
        source_score += min(0.15, reports_count * 0.05)
        source_score += min(0.15, news_count * 0.03)

        # 2. 투자 포인트 신뢰도 점수 (최대 0.5)
        if investment_points:
            pt_avg = sum(pt.confidence_score for pt in investment_points) / len(investment_points)
            pt_score = pt_avg * 0.5
        else:
            pt_score = 0.1

        overall = round(source_score + pt_score, 2)
        return max(0.1, min(1.0, overall))


# ── 단독 실행 테스트 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from datetime import date, timedelta
    from config.config import configure_logging

    configure_logging("DEBUG")

    print("=" * 60)
    print("[ResearchEngine] 단독 테스트")
    print("=" * 60)

    now = datetime.now(_KST)

    # 1. 샘플 뉴스 데이터 생성
    news_samples = [
        NewsItem(
            news_id="n1",
            source="한국경제",
            title="테크윙, 3분기 HBM 테스터 실적 호조로 영업이익 급증",
            published_at=now - timedelta(days=2),
            company_name="테크윙",
            sentiment=SentimentType.POSITIVE,
            sentiment_score=0.8,
            keywords=["HBM", "테스터", "실적"],
        ),
        NewsItem(
            news_id="n2",
            source="매일경제",
            title="테크윙 글로벌 반도체 고객사 다변화 성과",
            published_at=now - timedelta(days=5),
            company_name="테크윙",
            sentiment=SentimentType.POSITIVE,
            sentiment_score=0.7,
            keywords=["고객사", "반도체"],
        ),
    ]

    # 2. 샘플 증권사 리포트 데이터 생성
    report_samples = [
        ReportItem(
            report_id="r1",
            source=ReportSource.NAVER_FINANCE,
            brokerage="키움증권",
            title="테크윙 - HBM 테스터 시장 점유율 독점적 지위",
            company_name="테크윙",
            opinion=InvestmentOpinion.BUY,
            target_price=38000.0,
            published_at=now - timedelta(days=7),
            investment_points=["HBM 테스터 시장 점유율 급상승", "2025년 영업이익 사상 최대 전망"],
        ),
        ReportItem(
            report_id="r2",
            source=ReportSource.TELEGRAM,
            brokerage="삼성증권",
            title="테크윙 - 신규 핸들러 라인업 추가",
            company_name="테크윙",
            opinion=InvestmentOpinion.BUY,
            target_price=35000.0,
            published_at=now - timedelta(days=12),
            investment_points=["비메모리 테스트 핸들러 신규 수주", "고객사 다변화 성과"],
        ),
    ]

    # 3. 샘플 재무 데이터 생성
    fin_sample = FinancialMetrics(
        company_name="테크윙",
        stock_code="089030",
        fiscal_year=2024,
        base_date=date(2024, 12, 31),
        revenue=3200.0,
        operating_income=480.0,
        net_income=380.0,
        operating_margin=15.0,
        roe=12.3,
        per=19.0,
    )

    engine = ResearchEngine()

    print("\n[테스트 1] 전체 데이터 포함 시 분석")
    try:
        res = engine.analyze(
            company_name="테크윙",
            financial_metrics=fin_sample,
            reports=report_samples,
            news_items=news_samples,
        )

        print("[SUCCESS] 분석 성공!")
        print(f"  - 기업명: {res.company_name} ({res.stock_code})")
        print(f"  - 종합 신뢰도: {res.overall_confidence_score}")
        print(f"  - 컨센서스: {res.consensus.dominant_opinion.value} (평균 목표가: {res.consensus.avg_target_price:,.0f}원)")
        print(f"  - 감성 요약: {res.sentiment_summary.overall_sentiment.value} (긍정 {res.sentiment_summary.positive_count}건)")
        print("  - 추출된 핵심 투자포인트:")
        for idx, pt in enumerate(res.investment_points, 1):
            print(f"    [{idx}] {pt.point} (신뢰도: {pt.confidence_score}, 출처수: {pt.source_count})")
    except Exception as e:
        print(f"[ERROR] 오류 발생: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)

    print("\n[테스트 2] 리포트/뉴스 0건(데이터 일부 누락) 예외 없는 처리 테스트")
    try:
        res_empty = engine.analyze(
            company_name="테크윙",
            financial_metrics=fin_sample,
            reports=[],
            news_items=[],
        )
        print("[SUCCESS] 데이터 부재 처리 성공!")
        print(f"  - 종합 신뢰도: {res_empty.overall_confidence_score}")
        print(f"  - 품질 주의사항(data_quality_notes): {res_empty.data_quality_notes}")
    except Exception as e:
        print(f"[ERROR] 예외 발생하면 안 됨: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n[ResearchEngine] 테스트 완료")
