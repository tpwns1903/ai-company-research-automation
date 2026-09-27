"""
news_item.py
뉴스 기사 데이터 스키마 (뉴스 API 수집 결과 정규화)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


class SentimentType(str, Enum):
    """뉴스 감성 분류."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class NewsCategory(str, Enum):
    """뉴스 카테고리 분류."""

    EARNINGS = "earnings"          # 실적
    BUSINESS = "business"          # 사업/제품
    MANAGEMENT = "management"      # 경영/인사
    INDUSTRY = "industry"          # 산업/시장
    REGULATORY = "regulatory"      # 규제/정책
    MACRO = "macro"                # 거시경제
    OTHER = "other"                # 기타


class NewsItem(BaseModel):
    """뉴스 기사 모델.

    뉴스 API로부터 수집한 기사 단건을 표현하는 스키마.
    감성 분석 결과 및 투자 관련 키워드가 포함된다.
    """

    # 기사 식별
    news_id: str = Field(..., description="뉴스 고유 ID (URL 해시 등)")
    source: str = Field(..., description="출처 언론사 (예: 한국경제)")
    url: Optional[str] = Field(None, description="기사 원문 URL")

    # 기사 내용
    title: str = Field(..., description="기사 제목")
    summary: Optional[str] = Field(None, description="기사 요약 (200자 이내)")
    content: Optional[str] = Field(None, description="기사 본문 (크롤링 시 수집)")

    # 메타데이터
    published_at: datetime = Field(..., description="기사 발행 시각 (KST)")
    collected_at: datetime = Field(default_factory=datetime.now, description="수집 시각")

    # 분류/분석 결과
    company_name: Optional[str] = Field(None, description="연관 기업명")
    stock_code: Optional[str] = Field(None, description="연관 종목 코드")
    category: NewsCategory = Field(default=NewsCategory.OTHER, description="뉴스 카테고리")
    sentiment: Optional[SentimentType] = Field(None, description="감성 분류 결과")
    sentiment_score: Optional[float] = Field(
        None, ge=-1.0, le=1.0, description="감성 점수 (-1.0 ~ 1.0)"
    )
    keywords: list[str] = Field(default_factory=list, description="추출된 투자 키워드")
    relevance_score: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="기업 관련도 점수 (0.0 ~ 1.0)"
    )

    class Config:
        """Pydantic 모델 설정."""
        json_schema_extra = {
            "example": {
                "news_id": "abc123",
                "source": "한국경제",
                "title": "테크윙, HBM 테스터 수요 급증으로 실적 개선 기대",
                "published_at": "2024-12-01T09:00:00",
                "company_name": "테크윙",
                "stock_code": "089030",
                "category": "earnings",
                "sentiment": "positive",
                "sentiment_score": 0.75,
                "keywords": ["HBM", "테스터", "실적 개선"],
            }
        }
