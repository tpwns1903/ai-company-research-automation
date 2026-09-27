"""
research_result.py
리서치 엔진 분석 결과 스키마 (ResearchEngine 최종 출력 모델)
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from models.financial_metrics import FinancialMetrics
from models.news_item import NewsItem, SentimentType
from models.report_item import InvestmentOpinion, ReportItem


class InvestmentPoint(BaseModel):
    """공통 투자 포인트 단건 모델.

    여러 출처에서 공통으로 언급된 투자 포인트와 그 신뢰도를 담는다.
    """

    point: str = Field(..., description="투자 포인트 요약 문장")
    source_count: int = Field(..., ge=1, description="해당 포인트를 언급한 출처 수")
    source_names: list[str] = Field(
        default_factory=list, description="출처 목록 (증권사명 또는 뉴스 매체명)"
    )
    recency_weight: float = Field(
        ..., ge=0.0, le=1.0, description="최신성 가중치 (최근일수록 1.0에 가까움)"
    )
    direction_consistency: float = Field(
        ..., ge=0.0, le=1.0, description="방향 일치도 (모든 출처가 동일 방향이면 1.0)"
    )
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0,
        description="신뢰도 점수 = source_count × recency_weight × direction_consistency"
    )


class ConsensusOpinion(BaseModel):
    """증권사 컨센서스 요약 모델.

    복수 증권사 리포트로부터 집계한 컨센서스 투자의견 및 목표주가.
    """

    dominant_opinion: InvestmentOpinion = Field(..., description="가장 많은 증권사의 투자의견")
    buy_count: int = Field(default=0, description="매수(BUY/STRONG_BUY) 의견 수")
    hold_count: int = Field(default=0, description="보유(HOLD/NEUTRAL) 의견 수")
    sell_count: int = Field(default=0, description="매도(SELL/UNDERPERFORM) 의견 수")
    total_report_count: int = Field(default=0, description="분석에 사용된 리포트 총 수")
    brokerage_count: int = Field(
        default=0, description="의견·목표주가 집계에 쓴 증권사 수 (증권사별 최신 리포트 1건씩)"
    )
    avg_target_price: Optional[float] = Field(None, description="평균 목표주가 (원)")
    max_target_price: Optional[float] = Field(None, description="최고 목표주가 (원)")
    min_target_price: Optional[float] = Field(None, description="최저 목표주가 (원)")


class SentimentSummary(BaseModel):
    """뉴스 감성 집계 모델.

    수집된 뉴스 기사들의 감성 분석 결과 요약.
    """

    overall_sentiment: SentimentType = Field(..., description="전체 감성 방향")
    positive_count: int = Field(default=0, description="긍정 기사 수")
    negative_count: int = Field(default=0, description="부정 기사 수")
    neutral_count: int = Field(default=0, description="중립 기사 수")
    avg_sentiment_score: Optional[float] = Field(
        None, ge=-1.0, le=1.0, description="평균 감성 점수"
    )
    top_keywords: list[str] = Field(
        default_factory=list, description="가장 많이 등장한 투자 키워드 (상위 10개)"
    )


class ResearchResult(BaseModel):
    """리서치 엔진 최종 분석 결과 모델.

    ResearchEngine.analyze()의 출력물로, LLM 리포트 생성기에 전달되는 통합 분석 결과.
    수집된 재무 지표, 뉴스, 증권사 리포트를 종합한 최종 결과물.
    """

    # 기업 식별
    company_name: str = Field(..., description="분석 대상 기업명")
    stock_code: Optional[str] = Field(None, description="종목 코드")
    analysis_date: datetime = Field(
        default_factory=datetime.now, description="분석 실행 시각"
    )

    # 핵심 투자 포인트 (여러 출처 교차 검증 결과)
    investment_points: list[InvestmentPoint] = Field(
        default_factory=list, description="공통 투자 포인트 목록 (신뢰도 순 정렬)"
    )
    risk_factors: list[str] = Field(
        default_factory=list, description="공통 리스크 요인 목록"
    )

    # 컨센서스 및 감성
    consensus: Optional[ConsensusOpinion] = Field(None, description="증권사 컨센서스")
    sentiment_summary: Optional[SentimentSummary] = Field(None, description="뉴스 감성 요약")

    # 재무 지표 (최신 기준)
    financial_metrics: Optional[FinancialMetrics] = Field(
        None, description="정규화된 최신 재무 지표"
    )

    # 원본 데이터 참조 (LLM 컨텍스트 구성용)
    source_reports: list[ReportItem] = Field(
        default_factory=list, description="분석에 사용된 증권사 리포트 원본"
    )
    source_news: list[NewsItem] = Field(
        default_factory=list, description="분석에 사용된 뉴스 기사 원본"
    )

    # 전체 신뢰도
    overall_confidence_score: float = Field(
        ..., ge=0.0, le=1.0,
        description="전체 분석 신뢰도 (수집 데이터 양/질 기반 종합 점수)"
    )
    data_quality_notes: list[str] = Field(
        default_factory=list,
        description="데이터 품질 관련 주의사항 (예: '리포트 수 부족', '3개월 초과 데이터 포함')"
    )

    class Config:
        """Pydantic 모델 설정."""
        json_schema_extra = {
            "example": {
                "company_name": "테크윙",
                "stock_code": "089030",
                "overall_confidence_score": 0.82,
                "investment_points": [
                    {
                        "point": "HBM 테스터 시장 점유율 지속 확대",
                        "source_count": 4,
                        "confidence_score": 0.91,
                    }
                ],
            }
        }
