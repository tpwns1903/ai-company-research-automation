"""
financial_metrics.py
재무 지표 데이터 스키마 (DART API 및 PyKRX 수집 결과 정규화)
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class FinancialMetrics(BaseModel):
    """기업 재무 지표 모델.

    DART 사업보고서 및 PyKRX 시세 데이터를 통합한 정규화된 재무 스키마.
    모든 금액 단위는 억 원(KRW 100M).
    """

    # 기업 식별
    company_name: str = Field(..., description="기업명")
    stock_code: str = Field(..., description="종목 코드 (예: 000660)")
    fiscal_year: int = Field(..., description="회계연도 (예: 2024)")
    base_date: date = Field(..., description="데이터 기준일")

    # 손익계산서
    revenue: Optional[float] = Field(None, description="매출액 (억 원)")
    operating_income: Optional[float] = Field(None, description="영업이익 (억 원)")
    net_income: Optional[float] = Field(None, description="당기순이익 (억 원)")
    ebitda: Optional[float] = Field(None, description="EBITDA (억 원)")

    # 성장률
    revenue_yoy: Optional[float] = Field(None, description="매출 YoY 성장률 (%)")
    operating_income_yoy: Optional[float] = Field(None, description="영업이익 YoY 성장률 (%)")

    # 수익성 지표
    operating_margin: Optional[float] = Field(None, description="영업이익률 (%)")
    net_margin: Optional[float] = Field(None, description="순이익률 (%)")
    roe: Optional[float] = Field(None, description="자기자본이익률 ROE (%)")
    roa: Optional[float] = Field(None, description="총자산이익률 ROA (%)")

    # 밸류에이션 지표
    per: Optional[float] = Field(None, description="주가수익비율 PER (배)")
    pbr: Optional[float] = Field(None, description="주가순자산비율 PBR (배)")
    eps: Optional[float] = Field(None, description="주당순이익 EPS (원)")
    bps: Optional[float] = Field(None, description="주당순자산 BPS (원)")
    dividend_yield: Optional[float] = Field(None, description="배당수익률 (%)")

    # 재무건전성
    debt_to_equity: Optional[float] = Field(None, description="부채비율 (%)")
    current_ratio: Optional[float] = Field(None, description="유동비율 (%)")
    total_assets: Optional[float] = Field(None, description="총자산 (억 원)")
    total_equity: Optional[float] = Field(None, description="자본총계 (억 원)")
    total_liabilities: Optional[float] = Field(None, description="부채총계 (억 원)")

    # 시장 데이터
    market_cap: Optional[float] = Field(None, description="시가총액 (억 원)")
    closing_price: Optional[float] = Field(None, description="종가 (원)")
    week_52_high: Optional[float] = Field(None, description="52주 최고가 (원)")
    week_52_low: Optional[float] = Field(None, description="52주 최저가 (원)")

    class Config:
        """Pydantic 모델 설정."""
        json_schema_extra = {
            "example": {
                "company_name": "테크윙",
                "stock_code": "089030",
                "fiscal_year": 2024,
                "base_date": "2024-12-31",
                "revenue": 3200.0,
                "operating_income": 480.0,
                "operating_margin": 15.0,
                "per": 18.5,
                "roe": 12.3,
            }
        }


class PriceMetrics(BaseModel):
    """주가/시세 데이터 모델 (PyKRX 수집 결과).

    일별 시세 및 투자 지표를 담는 스키마.
    """

    company_name: str = Field(..., description="기업명")
    stock_code: str = Field(..., description="종목 코드")
    trade_date: date = Field(..., description="거래일")

    open_price: Optional[float] = Field(None, description="시가 (원)")
    high_price: Optional[float] = Field(None, description="고가 (원)")
    low_price: Optional[float] = Field(None, description="저가 (원)")
    close_price: Optional[float] = Field(None, description="종가 (원)")
    volume: Optional[int] = Field(None, description="거래량 (주)")
    trading_value: Optional[float] = Field(None, description="거래대금 (억 원)")

    change_rate: Optional[float] = Field(None, description="등락률 (%)")
    market_cap: Optional[float] = Field(None, description="시가총액 (억 원)")
    foreign_ratio: Optional[float] = Field(None, description="외국인 보유 비율 (%)")
    per: Optional[float] = Field(None, description="PER (배)")
    pbr: Optional[float] = Field(None, description="PBR (배)")
    eps: Optional[float] = Field(None, description="EPS (원)")
    bps: Optional[float] = Field(None, description="BPS (원)")
    dividend_yield: Optional[float] = Field(None, description="배당수익률 (%)")

    class Config:
        """Pydantic 모델 설정."""
        json_schema_extra = {
            "example": {
                "company_name": "테크윙",
                "stock_code": "089030",
                "trade_date": "2024-12-31",
                "close_price": 25000.0,
                "change_rate": 1.2,
            }
        }
