"""
news_collector.py
뉴스 API 데이터 수집기 — 최신 기사, 산업 이슈, 이벤트 수집

지원 백엔드:
  1. 네이버 뉴스 검색 API (메인) : https://openapi.naver.com/v1/search/news.json
  2. Google News RSS (Fallback) : https://news.google.com/rss/search

뉴스 관련 투자 키워드 분류 및 카테고리 태깅도 포함한다.
"""

from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol
from zoneinfo import ZoneInfo
from urllib.parse import quote

import requests

from collectors.base_collector import (
    BaseCollector,
)
from config.config import Settings
from models.news_item import NewsCategory, NewsItem, SentimentType

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

# ── 투자 키워드 사전 ──────────────────────────────────────────────────────────
_INVESTMENT_KEYWORDS: dict[str, list[str]] = {
    "실적": ["매출", "영업이익", "순이익", "실적", "분기", "연간", "어닝"],
    "성장": ["성장", "확대", "증가", "상승", "급증", "최대", "신기록"],
    "계약": ["계약", "수주", "공급", "MOU", "협약", "체결"],
    "제품": ["출시", "개발", "양산", "출하", "신제품", "기술"],
    "인수합병": ["인수", "합병", "M&A", "지분", "투자", "조인트벤처"],
    "리스크": ["하락", "감소", "부진", "우려", "리콜", "제재", "소송", "적자"],
}

_CATEGORY_KEYWORDS: dict[NewsCategory, list[str]] = {
    NewsCategory.EARNINGS: [
        "실적", "영업이익", "매출", "순이익", "EPS", "어닝", "분기보고",
    ],
    NewsCategory.BUSINESS: [
        "계약", "수주", "공급", "출시", "양산", "MOU", "협약", "파트너",
    ],
    NewsCategory.MANAGEMENT: [
        "대표이사", "CEO", "임원", "이사회", "지배구조", "사퇴", "선임",
    ],
    NewsCategory.INDUSTRY: [
        "반도체", "배터리", "바이오", "IT", "시장", "산업", "섹터", "경쟁",
    ],
    NewsCategory.REGULATORY: [
        "규제", "법안", "허가", "승인", "금지", "정책", "정부", "공정위",
    ],
    NewsCategory.MACRO: [
        "금리", "환율", "인플레이션", "GDP", "연준", "기준금리", "경기",
    ],
}

_POSITIVE_WORDS: set[str] = {
    "성장", "증가", "상승", "개선", "호조", "최대", "신기록", "흑자",
    "계약", "수주", "확대", "돌파", "급등", "기대", "전망", "긍정",
}
_NEGATIVE_WORDS: set[str] = {
    "감소", "하락", "악화", "부진", "우려", "적자", "손실", "리스크",
    "제재", "소송", "급락", "위기", "불안", "경고", "취소", "철수",
}


# ── HTTP 클라이언트 프로토콜 (Mock 주입용) ────────────────────────────────────

class NewsHttpClient(Protocol):
    """뉴스 API HTTP 요청 프로토콜."""

    def get(self, url: str, headers: dict[str, str], params: dict[str, Any], timeout: int) -> Any:
        """GET 요청을 수행한다."""
        ...


class _RequestsNewsClient:
    """실제 requests 라이브러리를 NewsHttpClient 프로토콜에 맞게 감싼 어댑터."""

    def __init__(self) -> None:
        self._session = requests.Session()

    def get(
        self,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any],
        timeout: int = 10,
    ) -> Any:
        try:
            resp = self._session.get(url, headers=headers, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            # 예외를 발생시키지 않고, 호출자가 처리할 수 있도록 래핑하지 않음 
            # 대신 예외를 던지되 BaseCollector의 retry에서 잡히도록 함.
            raise exc


# ── NewsCollector ─────────────────────────────────────────────────────────────

class NewsCollector(BaseCollector):
    """뉴스 기사를 수집하는 Collector.

    수집 방식:
        - 1순위: 네이버 뉴스 검색 API (openapi.naver.com)
        - 2순위: Google News RSS (news.google.com/rss/search) (네이버 실패 시 또는 보완)
    """

    _NAVER_API_URL = "https://openapi.naver.com/v1/search/news.json"
    _GOOGLE_RSS_URL = "https://news.google.com/rss/search"

    def __init__(
        self,
        settings: Optional[Settings] = None,
        http_client: Optional[NewsHttpClient] = None,
    ) -> None:
        """NewsCollector를 초기화한다."""
        super().__init__(settings)
        self._client: NewsHttpClient = http_client or _RequestsNewsClient()
        self._naver_client_id: str = self._settings.naver_client_id
        self._naver_client_secret: str = self._settings.naver_client_secret
        
        self._use_naver = bool(self._naver_client_id and self._naver_client_secret)
        if not self._use_naver:
            logger.warning("[News] NAVER_CLIENT_ID 또는 NAVER_CLIENT_SECRET 미설정 — Google News RSS만 사용합니다.")
        
        logger.debug("NewsCollector 준비 완료")

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    def collect(self, company_name: str, **kwargs: Any) -> list[NewsItem]:
        days: int = kwargs.get("days", self._settings.news_lookback_days)
        return self.get_recent_news(company_name, days=days)

    def get_recent_news(
        self,
        company_name: str,
        days: Optional[int] = None,
        max_articles: int = 50,
    ) -> list[NewsItem]:
        """최근 N일 이내 기업 관련 뉴스 기사를 반환한다."""
        lookback = days or self._settings.news_lookback_days
        cache_key = self._make_cache_key("news", company_name, lookback)
        
        cached = self._load_cache(cache_key)
        if cached is not None:
            logger.info("[News] 캐시에서 로드: %s (%d일)", company_name, lookback)
            return [NewsItem.model_validate(item) for item in cached]

        logger.info("[News] API 수집 시작: %s (최근 %d일)", company_name, lookback)

        articles = []
        since_date = datetime.now(_KST) - timedelta(days=lookback)

        # 1. 네이버 API 수집 시도
        if self._use_naver:
            try:
                raw_naver = self._request_with_retry(
                    self._fetch_naver,
                    query=company_name,
                    page_size=min(max_articles, 100)
                )
                
                for raw in raw_naver:
                    item = self._parse_naver_article(raw, company_name)
                    if item.published_at >= since_date:
                        articles.append(item)
                
                logger.info("[News] 네이버 뉴스 수집 성공: %d건", len(articles))
            except Exception as e:
                logger.warning("[News] 네이버 뉴스 수집 실패 (Google RSS로 넘어감): %s", e)
        
        # 2. Google RSS 폴백 (네이버 실패했거나 기사가 너무 적을 때)
        if not articles or len(articles) < 5:
            try:
                raw_google = self._request_with_retry(
                    self._fetch_google_rss,
                    query=company_name,
                )
                
                for raw in raw_google:
                    item = self._parse_google_article(raw, company_name)
                    if item.published_at >= since_date:
                        # 중복 제거 (제목 기준 간단히)
                        if not any(a.title == item.title for a in articles):
                            articles.append(item)
                            
                logger.info("[News] Google News RSS 수집/보완 성공: 총 %d건", len(articles))
            except Exception as e:
                logger.warning("[News] Google News RSS 수집 실패: %s", e)

        # 예외를 던지지 않고 빈 리스트 반환
        if not articles:
            logger.warning("[News] 수집된 뉴스가 없습니다: %s", company_name)
            return []

        # 관련도 점수로 1차 정렬 후 상위 자르고 최신순 정렬
        articles.sort(key=lambda a: a.relevance_score, reverse=True)
        articles = articles[:max_articles]
        articles.sort(key=lambda a: a.published_at, reverse=True)

        self._save_cache(cache_key, [a.model_dump(mode="json") for a in articles])
        logger.info("[News] 수집 완료: %s — 최종 %d건 (최근 %d일)", company_name, len(articles), lookback)
        return articles

    # ── 내부 구현 — API 수집 ──────────────────────────────────────────────────

    def _fetch_naver(
        self,
        query: str,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        headers = {
            "X-Naver-Client-Id": self._naver_client_id,
            "X-Naver-Client-Secret": self._naver_client_secret,
        }
        params: dict[str, Any] = {
            "query": query,
            "display": page_size,
            "start": 1,
            "sort": "date",  # 최신순
        }

        resp = self._client.get(self._NAVER_API_URL, headers=headers, params=params, timeout=10)
        data = resp.json()
        return data.get("items", [])

    def _fetch_google_rss(
        self,
        query: str,
    ) -> list[dict[str, Any]]:
        # Google News RSS (한국어, 한국 지역)
        params: dict[str, Any] = {
            "q": query,
            "hl": "ko",
            "gl": "KR",
            "ceid": "KR:ko",
        }
        resp = self._client.get(self._GOOGLE_RSS_URL, headers={}, params=params, timeout=10)
        
        # Parse XML
        root = ET.fromstring(resp.content)
        items = []
        for item in root.findall(".//item"):
            items.append({
                "title": item.findtext("title") or "",
                "link": item.findtext("link") or "",
                "description": item.findtext("description") or "",
                "pubDate": item.findtext("pubDate") or "",
                "source": item.find("source").text if item.find("source") is not None else "Google News",
            })
        return items

    def _clean_html(self, raw_html: str) -> str:
        """간단한 HTML 태그 제거 및 HTML 엔티티 디코딩"""
        import html
        clean = re.sub(r'<.*?>', '', raw_html)
        return html.unescape(clean)

    def _parse_naver_article(self, raw: dict[str, Any], company_name: str) -> NewsItem:
        title = self._clean_html(raw.get("title", ""))
        description = self._clean_html(raw.get("description", ""))
        url = raw.get("link", "")
        # 네이버 API는 source를 주지 않는 경우가 많음. 대신 originallink나 link를 사용하거나 '네이버 뉴스'로 표기
        source_name = "네이버 뉴스"

        # pubDate 파싱: Thu, 06 Aug 2026 23:25:00 +0900
        published_str = raw.get("pubDate", "")
        try:
            from email.utils import parsedate_to_datetime
            published_kst = parsedate_to_datetime(published_str).astimezone(_KST)
        except Exception:
            published_kst = datetime.now(_KST)

        return self._create_news_item(title, description, url, source_name, published_kst, company_name)

    def _parse_google_article(self, raw: dict[str, Any], company_name: str) -> NewsItem:
        title = self._clean_html(raw.get("title", ""))
        # 구글 뉴스는 타이틀 뒤에 ' - 출처' 형태가 붙는 경우가 많음
        if " - " in title:
            title = " - ".join(title.split(" - ")[:-1])
            
        description = self._clean_html(raw.get("description", ""))
        url = raw.get("link", "")
        source_name = raw.get("source", "Google News")

        published_str = raw.get("pubDate", "")
        try:
            from email.utils import parsedate_to_datetime
            published_kst = parsedate_to_datetime(published_str).astimezone(_KST)
        except Exception:
            published_kst = datetime.now(_KST)

        return self._create_news_item(title, description, url, source_name, published_kst, company_name)

    def _create_news_item(self, title: str, description: str, url: str, source_name: str, published_kst: datetime, company_name: str) -> NewsItem:
        news_id = hashlib.sha256(url.encode()).hexdigest()[:12] if url else hashlib.sha256(title.encode()).hexdigest()[:12]

        full_text = f"{title} {description}"
        category = self._classify_category(full_text)
        sentiment, sentiment_score = self._analyze_sentiment(full_text)
        keywords = self._extract_keywords(full_text)
        relevance = self._calc_relevance(full_text, company_name)

        summary = description[:200] if description else None

        return NewsItem(
            news_id=news_id,
            source=source_name,
            url=url or None,
            title=title,
            summary=summary,
            content=None,
            published_at=published_kst,
            company_name=company_name,
            category=category,
            sentiment=sentiment,
            sentiment_score=sentiment_score,
            keywords=keywords,
            relevance_score=relevance,
        )

    # ── 내부 구현 — 텍스트 분석 ───────────────────────────────────────────────

    def _classify_category(self, text: str) -> NewsCategory:
        scores: dict[NewsCategory, int] = {}
        for category, keywords in _CATEGORY_KEYWORDS.items():
            count = sum(1 for kw in keywords if kw in text)
            if count > 0:
                scores[category] = count

        if not scores:
            return NewsCategory.OTHER
        return max(scores, key=lambda c: scores[c])

    def _analyze_sentiment(self, text: str) -> tuple[SentimentType, float]:
        pos_count = sum(1 for w in _POSITIVE_WORDS if w in text)
        neg_count = sum(1 for w in _NEGATIVE_WORDS if w in text)
        total = pos_count + neg_count

        if total == 0:
            return SentimentType.NEUTRAL, 0.0

        raw_score = (pos_count - neg_count) / total
        score = round(max(-1.0, min(1.0, raw_score)), 3)

        if score > 0.1:
            return SentimentType.POSITIVE, score
        if score < -0.1:
            return SentimentType.NEGATIVE, score
        return SentimentType.NEUTRAL, score

    def _extract_keywords(self, text: str, top_n: int = 5) -> list[str]:
        found: list[str] = []
        for group_keywords in _INVESTMENT_KEYWORDS.values():
            for kw in group_keywords:
                if kw in text and kw not in found:
                    found.append(kw)
        return found[:top_n]

    def _calc_relevance(self, text: str, company_name: str) -> float:
        exact_count = len(re.findall(re.escape(company_name), text))
        if exact_count >= 3:
            return 1.0
        if exact_count == 2:
            return 0.85
        if exact_count == 1:
            return 0.7
        if len(company_name) >= 2 and company_name[:2] in text:
            return 0.4
        return 0.1

# ── 단독 실행 테스트 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock

    from config.config import Settings as _Settings, configure_logging

    configure_logging("DEBUG")

    print("=" * 60)
    print("[NewsCollector] 테스트")
    print("=" * 60)

    # ── 1. Google News RSS Mock 테스트 ───────────────────────────────
    mock_settings = _Settings(
        dart_api_key="mock",
        naver_client_id="",
        naver_client_secret="",
        cache_ttl_seconds=0,
    )

    mock_http = MagicMock(spec=NewsHttpClient)
    mock_resp = MagicMock()
    mock_resp.content = '''<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
    <channel>
        <item>
            <title>테크윙, HBM 시장 확대로 수혜 기대 - 한국경제</title>
            <link>https://example.com/1</link>
            <description>테크윙이 HBM 시장 확대로 큰 수혜를 입을 것으로 예상된다.</description>
            <pubDate>Thu, 06 Aug 2026 09:00:00 +0900</pubDate>
            <source>한국경제</source>
        </item>
        <item>
            <title>일부 반도체 업황 우려... 테크윙 주가 주춤 - 매일경제</title>
            <link>https://example.com/2</link>
            <description>전반적인 반도체 업황 우려 속에 테크윙 주가가 주춤하고 있다.</description>
            <pubDate>Wed, 05 Aug 2026 15:00:00 +0900</pubDate>
            <source>매일경제</source>
        </item>
    </channel>
    </rss>
    '''.encode("utf-8")
    mock_http.get.return_value = mock_resp

    collector = NewsCollector(settings=mock_settings, http_client=mock_http)

    print("\n[테스트 1] Google News RSS (Mock) 데이터 수집")
    try:
        articles = collector.get_recent_news("테크윙", days=30)
        print(f"  → 수집된 기사 수: {len(articles)}건\n")
        for idx, article in enumerate(articles, 1):
            print(f"  [{idx}] {article.title}")
            print(f"       출처: {article.source} | 발행: {article.published_at.strftime('%Y-%m-%d')}")
            print(f"       카테고리: {article.category.value} | 감성: {article.sentiment.value if article.sentiment else 'N/A'} ({article.sentiment_score})")
            print(f"       키워드: {article.keywords}")
            print(f"       관련도: {article.relevance_score}")
            print()
    except Exception as e:
        print(f"\n[ERROR] 오류: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)

    print("\n[NewsCollector] 테스트 완료")
