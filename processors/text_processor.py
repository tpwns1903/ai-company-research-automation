"""
text_processor.py
텍스트 분석 프로세서 — 기업명 추출, 키워드 분석, 감성 분류, 산업 키워드 생성
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── 사전 데이터 ─────────────────────────────────────────────────────────────

# 긍정/부정 키워드 및 가중치
_POSITIVE_DICTIONARY: dict[str, float] = {
    "성장": 0.8, "증가": 0.6, "상승": 0.6, "개선": 0.7, "호조": 0.8,
    "최대": 0.9, "신기록": 0.9, "흑자": 0.8, "계약": 0.7, "수주": 0.8,
    "확대": 0.6, "돌파": 0.7, "급등": 0.8, "기대": 0.6, "전망": 0.5,
    "긍정": 0.6, "서프라이즈": 0.9, "수혜": 0.8, "달성": 0.7, "우상향": 0.8,
}

_NEGATIVE_DICTIONARY: dict[str, float] = {
    "감소": 0.6, "하락": 0.6, "악화": 0.8, "부진": 0.7, "우려": 0.6,
    "적자": 0.9, "손실": 0.8, "리스크": 0.7, "제재": 0.8, "소송": 0.7,
    "급락": 0.8, "위기": 0.8, "불안": 0.6, "경고": 0.7, "취소": 0.8,
    "철수": 0.7, "하향": 0.7, "둔화": 0.6, "쇼크": 0.9, "지연": 0.6,
}

# 산업별 대표 키워드
_INDUSTRY_KEYWORDS: dict[str, list[str]] = {
    "반도체/전자": ["HBM", "DRAM", "NAND", "파운드리", "클린룸", "테스터", "핸들러", "기판", "팹리스", "OSAT"],
    "2차전지/배터리": ["양극재", "음극재", "전해액", "분리막", "리튬", "니켈", "전고체", "셀", "수율"],
    "바이오/제약": ["임상", "파이프라인", "FDA", "품목허가", "바이오시밀러", "CRO", "CMO", "기술이전"],
    "IT/소프트웨어": ["AI", "LLM", "클라우드", "SaaS", "플랫폼", "알고리즘", "데이터센터", "보안"],
    "자동차/모빌리티": ["전기차", "EV", "자율주행", "SDV", "배터리팩", "부품", "완성차"],
    "조선/해운": ["LNG", "친환경선박", "수주잔고", "선가", "조선소", "해운", "운임지수"],
    "금융": ["NIM", "연체율", "배당성향", "자본적정성", "충당금", "ROE", "PBR"],
}

# 공통 불용어 (Stopwords)
_STOPWORDS: set[str] = {
    "있다", "있다.", "하반기", "상반기", "올해", "내년", "지난해", "관련", "지난",
    "대비", "기준", "이상", "이하", "위해", "통해", "경우", "따르면", "따라",
    "대한", "대해", "위한", "현재", "지속", "가능성", "분석", "보고서", "가운데",
}


class TextProcessor:
    """뉴스 기사 및 증권사 리포트 텍스트를 분석하는 Processor.

    주요 기능:
        - 감성 분류 (긍정 / 부정 / 중립 및 -1.0 ~ 1.0 점수)
        - 투자 키워드 및 빈도 분석
        - 산업 키워드 태깅
        - 텍스트 내 기업명/기관명(엔티티) 추출
    """

    def __init__(self) -> None:
        """TextProcessor를 초기화한다."""
        self._okt: Any = None
        self._init_tokenizer()
        logger.debug("TextProcessor 초기화 완료 (형태소 분석기 사용 여부: %s)", self._okt is not None)

    def _init_tokenizer(self) -> None:
        """KoNLPy Okt 형태소 분석기 초기화를 시도한다 (선택적)."""
        try:
            from konlpy.tag import Okt  # type: ignore[import]
            self._okt = Okt()
        except Exception:
            self._okt = None

    def analyze(self, text: str, company_name: Optional[str] = None) -> dict[str, Any]:
        """텍스트를 분석하여 키워드, 감성, 엔티티, 산업 분류 결과를 통합 반환한다.

        Args:
            text: 분석할 원문 텍스트.
            company_name: 관련 기업명 (옵션).

        Returns:
            dict: 분석 결과 딕셔너리
                - keywords (list[str]): 주요 키워드
                - sentiment (str): 'positive' / 'negative' / 'neutral'
                - sentiment_score (float): -1.0 ~ 1.0
                - entities (list[str]): 추출된 기업/개체명
                - industry_keywords (list[str]): 관련 산업 키워드
        """
        if not text or not text.strip():
            return {
                "keywords": [],
                "sentiment": "neutral",
                "sentiment_score": 0.0,
                "entities": [],
                "industry_keywords": [],
            }

        keywords = self.extract_keywords(text, top_n=10)
        sentiment, sentiment_score = self.classify_sentiment(text)
        entities = self.extract_company_names(text)
        industry_kws = self.extract_industry_keywords(text)

        if company_name and company_name not in entities:
            entities.insert(0, company_name)

        return {
            "keywords": keywords,
            "sentiment": sentiment,
            "sentiment_score": sentiment_score,
            "entities": entities,
            "industry_keywords": industry_kws,
        }

    def extract_keywords(self, text: str, top_n: int = 10) -> list[str]:
        """텍스트에서 주요 명사/투자 키워드를 빈도순으로 추출한다.

        Args:
            text: 분석할 텍스트.
            top_n: 최대 반환 키워드 수.

        Returns:
            list[str]: 상위 키워드 목록.
        """
        words: list[str] = []

        if self._okt:
            try:
                nouns = self._okt.nouns(text)
                words = [w for w in nouns if len(w) >= 2 and w not in _STOPWORDS]
            except Exception as exc:
                logger.debug("KoNLPy 명사 추출 오류, 정규식으로 전환: %s", exc)

        if not words:
            # 정규식 fallback: 2글자 이상 한글/영문 단어 추출
            raw_words = re.findall(r"[가-힣a-zA-Z]{2,}", text)
            words = [w for w in raw_words if w not in _STOPWORDS]

        # 빈도 계산
        counts: dict[str, int] = {}
        for w in words:
            counts[w] = counts.get(w, 0) + 1

        sorted_words = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        return [word for word, _ in sorted_words[:top_n]]

    def classify_sentiment(self, text: str) -> tuple[str, float]:
        """텍스트의 감성을 긍정(positive)/부정(negative)/중립(neutral)으로 분류한다.

        Args:
            text: 분석할 텍스트.

        Returns:
            tuple[str, float]: (감성 레이블, 점수 -1.0 ~ 1.0)
        """
        if not text:
            return "neutral", 0.0

        pos_score = 0.0
        neg_score = 0.0

        for word, weight in _POSITIVE_DICTIONARY.items():
            matches = len(re.findall(re.escape(word), text))
            pos_score += matches * weight

        for word, weight in _NEGATIVE_DICTIONARY.items():
            matches = len(re.findall(re.escape(word), text))
            neg_score += matches * weight

        total_score = pos_score + neg_score
        if total_score == 0:
            return "neutral", 0.0

        normalized_score = round((pos_score - neg_score) / (total_score + 1.0), 2)
        # 상한/하한 캡 (-1.0 ~ 1.0)
        normalized_score = max(-1.0, min(1.0, normalized_score))

        if normalized_score >= 0.15:
            sentiment = "positive"
        elif normalized_score <= -0.15:
            sentiment = "negative"
        else:
            sentiment = "neutral"

        return sentiment, normalized_score

    def extract_company_names(self, text: str) -> list[str]:
        """텍스트에서 기업명(주식회사, 대표 상장사 등) 패턴을 추출한다.

        Args:
            text: 분석할 텍스트.

        Returns:
            list[str]: 중복 제거된 기업명 목록.
        """
        found: list[str] = []

        # 1. (주)OOO 또는 OOO(주) 형태
        pattern1 = re.findall(r"(?:\(주\)|㈜)\s*([가-힣a-zA-Z0-9]{2,15})", text)
        pattern2 = re.findall(r"([가-힣a-zA-Z0-9]{2,15})\s*(?:\(주\)|㈜)", text)

        # 2. 대표 증권사/기업 패턴 (OO전자, OO바이오, OO테크, OO증권 등)
        pattern3 = re.findall(r"([가-힣A-Z]{2,8}(?:전자|바이오|테크|반도체|모빌리티|증권|중공업|제약|화학))", text)

        for name in pattern1 + pattern2 + pattern3:
            name_clean = name.strip()
            if name_clean and name_clean not in found and name_clean not in _STOPWORDS:
                found.append(name_clean)

        return found

    def extract_industry_keywords(self, text: str) -> list[str]:
        """텍스트와 매칭되는 산업 분야 키워드를 추출한다.

        Args:
            text: 분석할 텍스트.

        Returns:
            list[str]: 매칭된 산업 키워드 목록.
        """
        matched_keywords: list[str] = []
        for industry, keywords in _INDUSTRY_KEYWORDS.items():
            for kw in keywords:
                if kw in text and kw not in matched_keywords:
                    matched_keywords.append(kw)
        return matched_keywords


# ── 단독 실행 테스트 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from config.config import configure_logging

    configure_logging("DEBUG")

    print("=" * 60)
    print("[TextProcessor] 단독 테스트")
    print("=" * 60)

    sample_text = (
        "테크윙은 올해 3분기 HBM 테스터 장비 수주 급증으로 영업이익이 전년 대비 45% 증가하며 "
        "어닝 서프라이즈를 기록했다. 삼성전자 및 글로벌 OSAT 업체향 공급이 확대되고 있으며 "
        "반도체 테스터 시장 내 점유율 상승이 기대된다. 단, 글로벌 시황 불확실성과 인플레이션 우려도 존재한다."
    )

    processor = TextProcessor()

    try:
        res = processor.analyze(sample_text, company_name="테크윙")
        print("[SUCCESS] 분석 결과:")
        print(f"  - 감성 분류 : {res['sentiment']} (점수: {res['sentiment_score']})")
        print(f"  - 주요 키워드: {res['keywords']}")
        print(f"  - 산업 키워드: {res['industry_keywords']}")
        print(f"  - 개체명 목록: {res['entities']}")
    except Exception as e:
        print(f"[ERROR] 분석 중 오류 발생: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n[TextProcessor] 테스트 완료")
