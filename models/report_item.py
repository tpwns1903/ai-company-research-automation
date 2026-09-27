"""
report_item.py
증권사 리포트 데이터 스키마 (텔레그램 / 네이버 금융 수집 결과 정규화)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class InvestmentOpinion(str, Enum):
    """투자 의견 분류."""

    BUY = "BUY"                  # 매수
    STRONG_BUY = "STRONG_BUY"    # 강력 매수
    HOLD = "HOLD"                # 보유
    NEUTRAL = "NEUTRAL"          # 중립
    UNDERPERFORM = "UNDERPERFORM"  # 시장 하회
    SELL = "SELL"                # 매도
    NOT_RATED = "NOT_RATED"      # 투자의견 없음


class ReportSource(str, Enum):
    """리포트 수집 출처."""

    TELEGRAM = "telegram"
    NAVER_FINANCE = "naver_finance"
    OTHER = "other"


class ReportItem(BaseModel):
    """증권사 리포트 단건 모델.

    텔레그램 공개방 또는 네이버 금융에서 수집한 증권사 리포트를 표현하는 스키마.
    최근 3개월 이내 자료만 수집 대상으로 한다.
    """

    # 리포트 식별
    report_id: str = Field(..., description="리포트 고유 ID (URL 해시 등)")
    source: ReportSource = Field(..., description="수집 출처 (telegram / naver_finance)")
    brokerage: str = Field(..., description="증권사명 (예: 키움증권)")

    # 리포트 내용
    title: str = Field(..., description="리포트 제목")
    analyst: Optional[str] = Field(None, description="애널리스트 이름")
    summary: Optional[str] = Field(None, description="리포트 요약 (300자 이내)")
    investment_points: list[str] = Field(
        default_factory=list, description="주요 투자 포인트 목록"
    )
    risk_factors: list[str] = Field(
        default_factory=list, description="주요 리스크 요인 목록"
    )

    # 투자의견 및 목표주가
    opinion: InvestmentOpinion = Field(
        default=InvestmentOpinion.NOT_RATED, description="투자 의견"
    )
    target_price: Optional[float] = Field(None, description="목표주가 (원)")
    previous_target_price: Optional[float] = Field(None, description="이전 목표주가 (원)")
    target_price_change_rate: Optional[float] = Field(
        None, description="목표주가 변동률 (%)"
    )

    # 실적 전망 (리포트 내 추정치)
    estimated_revenue: Optional[float] = Field(None, description="추정 매출액 (억 원)")
    estimated_operating_income: Optional[float] = Field(
        None, description="추정 영업이익 (억 원)"
    )
    estimated_eps: Optional[float] = Field(None, description="추정 EPS (원)")

    # 메타데이터
    company_name: str = Field(..., description="분석 대상 기업명")
    stock_code: Optional[str] = Field(None, description="종목 코드")
    published_at: datetime = Field(..., description="리포트 발행 시각 (KST)")
    collected_at: datetime = Field(default_factory=datetime.now, description="수집 시각")
    url: Optional[str] = Field(None, description="원문 URL")
    pdf_path: Optional[str] = Field(
        None, description="다운로드하여 로컬에 저장한 원문 PDF 파일 경로 (없으면 None)"
    )

    # 품질 지표
    relevance_score: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="관련도 점수 (0.0 ~ 1.0)"
    )
    is_within_3_months: bool = Field(
        default=True, description="최근 3개월 이내 리포트 여부"
    )

    class Config:
        """Pydantic 모델 설정."""
        json_schema_extra = {
            "example": {
                "report_id": "rep_xyz789",
                "source": "naver_finance",
                "brokerage": "키움증권",
                "title": "테크윙 - HBM 사이클 최대 수혜주",
                "analyst": "홍길동",
                "opinion": "BUY",
                "target_price": 35000.0,
                "company_name": "테크윙",
                "stock_code": "089030",
                "published_at": "2024-11-15T08:30:00",
                "investment_points": ["HBM 테스터 점유율 확대", "고객사 다변화"],
            }
        }
