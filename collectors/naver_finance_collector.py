"""
naver_finance_collector.py
네이버 금융에서 증권사 리포트를 수집하는 Collector

특징:
  - 네이버 금융 리서치 종목분석 리포트 페이지 크롤링
  - 최근 3개월 이내 자료만 수집
  - 증권사명, 리포트 제목, 발행일, 목표주가, 투자의견 파싱
  - BeautifulSoup4 및 HTTP 백엔드 (Mock 지원)
  - 검색 결과 없을 시 예외 없이 빈 리스트([]) 반환
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Protocol
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from collectors.base_collector import (
    ApiResponseError,
    BaseCollector,
    CollectorError,
    RateLimitError,
)
from config.config import Settings
from models.report_item import InvestmentOpinion, ReportItem, ReportSource

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

# 네이버 금융 리서치 URL
_NAVER_RESEARCH_COMPANY_URL = "https://finance.naver.com/research/company_list.naver"

# 투자의견 매핑
_OPINION_MAP: dict[str, InvestmentOpinion] = {
    "BUY": InvestmentOpinion.BUY,
    "매수": InvestmentOpinion.BUY,
    "STRONG BUY": InvestmentOpinion.STRONG_BUY,
    "강력매수": InvestmentOpinion.STRONG_BUY,
    "HOLD": InvestmentOpinion.HOLD,
    "보유": InvestmentOpinion.HOLD,
    "NEUTRAL": InvestmentOpinion.NEUTRAL,
    "중립": InvestmentOpinion.NEUTRAL,
    "UNDERPERFORM": InvestmentOpinion.UNDERPERFORM,
    "시장하회": InvestmentOpinion.UNDERPERFORM,
    "SELL": InvestmentOpinion.SELL,
    "매도": InvestmentOpinion.SELL,
}

# 목표주가 추출 정규식
_TARGET_PRICE_PATTERN = re.compile(
    r"(?:목표주가|목표가|TP|Target\s*Price)[\s:]*([0-9,]+)\s*원?",
    re.IGNORECASE,
)


# ── HTTP Client Protocol (Mock 주입용) ────────────────────────────────────────

class NaverHttpClient(Protocol):
    """네이버 금융 HTTP 요청 프로토콜."""

    def get(self, url: str, headers: dict[str, str], params: dict[str, Any], timeout: int) -> Any:
        """GET 요청을 수행한다."""
        ...


class _RequestsNaverClient:
    """requests.Session을 NaverHttpClient 프로토콜에 맞게 감싼 어댑터."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            }
        )

    def get(
        self,
        url: str,
        headers: Optional[dict[str, str]] = None,
        params: Optional[dict[str, Any]] = None,
        timeout: int = 10,
    ) -> Any:
        """HTTP GET 요청을 수행한다.

        Raises:
            RateLimitError: 429 응답 시.
            ApiResponseError: 4xx/5xx 응답 시.
            CollectorError: 네트워크 실패 시.
        """
        try:
            req_headers = headers or {}
            resp = self._session.get(url, headers=req_headers, params=params, timeout=timeout)
        except requests.ConnectionError as exc:
            raise CollectorError(f"네이버 금융 연결 실패: {exc}") from exc
        except requests.Timeout as exc:
            raise CollectorError(f"네이버 금융 응답 타임아웃: {exc}") from exc

        if resp.status_code == 429:
            raise RateLimitError("네이버 금융 Rate Limit 초과")
        if resp.status_code >= 400:
            raise ApiResponseError(resp.status_code, resp.text[:200])

        return resp


# ── NaverFinanceCollector ─────────────────────────────────────────────────────

class NaverFinanceCollector(BaseCollector):
    """네이버 금융 리서치 페이지에서 증권사 리포트를 수집하는 Collector.

    수집 항목:
        - 최근 3개월 이내 증권사 종목분석 리포트
        - 증권사명, 리포트 제목, 작성일자, PDF 링크
        - 목표주가 및 투자의견 파싱

    주의:
        - 크롤링은 서비스 약관 및 robots.txt 범위 내 내부 분석용으로만 사용한다.
        - 요청 간 딜레이 및 재시도 로직을 적용한다.

    Example:
        >>> collector = NaverFinanceCollector()
        >>> reports = collector.get_reports("테크윙", days=90)
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        http_client: Optional[NaverHttpClient] = None,
    ) -> None:
        """NaverFinanceCollector를 초기화한다.

        Args:
            settings: 애플리케이션 설정. None이면 .env에서 자동 로드.
            http_client: HTTP 클라이언트 (테스트 시 Mock 주입용).
        """
        super().__init__(settings)
        self._client: NaverHttpClient = http_client or _RequestsNaverClient()
        self._pdf_dir: Path = self._settings.report_pdf_dir
        self._pdf_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("NaverFinanceCollector 준비 완료")

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    def collect(self, company_name: str, **kwargs: Any) -> list[ReportItem]:
        """기업명을 입력받아 네이버 금융 증권사 리포트를 수집한다.

        Args:
            company_name: 수집 대상 기업명.
            **kwargs:
                - days (int): 수집 기간 (일). 기본값 settings.report_lookback_days (90일).

        Returns:
            list[ReportItem]: 수집된 리포트 목록.
        """
        days: int = kwargs.get("days", self._settings.report_lookback_days)
        return self.get_reports(company_name, days=days)

    def get_reports(
        self, company_name: str, days: Optional[int] = None
    ) -> list[ReportItem]:
        """최근 N일(기본 90일) 이내 기업 관련 네이버 금융 리포트를 반환한다.

        검색 결과가 없을 경우 예외를 발생시키지 않고 빈 리스트[]를 반환한다.

        Args:
            company_name: 기업명.
            days: 수집 기간 (일). None이면 settings.report_lookback_days (90일) 사용.

        Returns:
            list[ReportItem]: 발행일 기준 내림차순 정렬된 리포트 목록.
        """
        lookback = days or self._settings.report_lookback_days
        cache_key = self._make_cache_key("naver_finance_reports", company_name, lookback)

        cached = self._load_cache(cache_key)
        if cached is not None:
            logger.info("[NaverFinance] 캐시에서 로드: %s (%d일)", company_name, lookback)
            return [ReportItem.model_validate(item) for item in cached]

        logger.info("[NaverFinance] 수집 시작: %s (최근 %d일)", company_name, lookback)

        try:
            raw_reports = self._fetch_reports_from_naver(company_name, lookback)
        except Exception as exc:
            logger.warning("[NaverFinance] 수집 중 예외 발생 -> 빈 리스트 반환: %s", exc)
            raw_reports = []

        if not raw_reports:
            logger.info("[NaverFinance] 리포트 없음: %s", company_name)
            self._save_cache(cache_key, [])
            return []

        # 3개월 이내 데이터 필터링 및 중복 제거
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=lookback)
        filtered_reports: list[ReportItem] = []
        seen_keys: set[str] = set()

        for report in raw_reports:
            pub_date = report.published_at
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)
            if pub_date < cutoff_date:
                continue

            dedup_key = f"{report.brokerage}_{report.title.strip()}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            filtered_reports.append(report)

        filtered_reports.sort(key=lambda r: r.published_at, reverse=True)

        self._save_cache(cache_key, [r.model_dump(mode="json") for r in filtered_reports])
        logger.info(
            "[NaverFinance] 수집 완료: %s - %d건 (최근 %d일)",
            company_name, len(filtered_reports), lookback
        )
        return filtered_reports

    # ── 내부 크롤링 / 파싱 ───────────────────────────────────────────────────

    def _fetch_reports_from_naver(
        self, company_name: str, lookback_days: int
    ) -> list[ReportItem]:
        """네이버 금융 리서치 목록 페이지 크롤링."""
        params = {
            "searchType": "itemCode",
            "itemCode": "",
            "searchType": "search",
            "searchWord": company_name,
            "page": "1",
        }

        self._wait()
        resp = self._request_with_retry(
            self._client.get,
            url=_NAVER_RESEARCH_COMPANY_URL,
            params=params,
            timeout=10,
        )

        # 네이버 금융 페이지 EUC-KR / CP949 인코딩 처리
        html_text = resp.content.decode("euc-kr", errors="replace")
        soup = BeautifulSoup(html_text, "html.parser")

        # 테이블 행 추출
        table = soup.find("table", class_="type_1")
        if not table:
            logger.debug("[NaverFinance] 리포트 테이블을 찾을 수 없음: %s", company_name)
            return []

        rows = table.find_all("tr")
        reports: list[ReportItem] = []

        for row in rows:
            parsed = self._parse_report_row(row, company_name)
            if parsed:
                reports.append(parsed)

        return reports

    def _parse_report_row(
        self, row: Any, company_name: str
    ) -> Optional[ReportItem]:
        """HTML 테이블 <tr> 행을 파싱하여 ReportItem으로 변환한다."""
        tds = row.find_all("td")
        if len(tds) < 5:
            return None

        # [0] 종목명, [1] 제목 & 링크, [2] 증권사, [3] PDF 링크, [4] 작성일
        item_code_td = tds[0].text.strip()
        title_td = tds[1]
        brokerage_td = tds[2].text.strip()
        pdf_td = tds[3]
        date_td = tds[4].text.strip()

        title_a = title_td.find("a")
        if not title_a:
            return None

        title = title_a.text.strip()
        if not title:
            return None

        # 검색된 기업명 매칭 확인 — item_code_td는 네이버가 분류한 정확한 종목명 필드이므로
        # 부분 문자열 포함(in) 대신 정확히 일치하는 경우에만 매칭한다.
        # (부분 일치로 하면 "한화" 검색 시 "한화오션"/"한화솔루션" 등 계열사 행까지
        #  잘못 매칭되는 문제가 생긴다.)
        if item_code_td != company_name:
            return None

        # 작성일 파싱 (YY.MM.DD 또는 YYYY.MM.DD)
        try:
            date_parts = date_td.split(".")
            if len(date_parts[0]) == 2:
                year = 2000 + int(date_parts[0])
            else:
                year = int(date_parts[0])
            month = int(date_parts[1])
            day = int(date_parts[2])
            pub_at = datetime(year, month, day, 9, 0, tzinfo=_KST)
        except Exception:
            pub_at = datetime.now(_KST)

        # PDF 링크
        pdf_a = pdf_td.find("a")
        pdf_url = pdf_a.get("href") if pdf_a else None

        # 투자의견 & 목표주가 제목 텍스트 파싱
        opinion = InvestmentOpinion.NOT_RATED
        for key, op in _OPINION_MAP.items():
            if re.search(r"\b" + re.escape(key) + r"\b", title, re.IGNORECASE):
                opinion = op
                break

        tp_match = _TARGET_PRICE_PATTERN.search(title)
        target_price: Optional[float] = None
        if tp_match:
            try:
                target_price = float(tp_match.group(1).replace(",", ""))
            except ValueError:
                pass

        report_id = hashlib.sha256(
            f"naver_{brokerage_td}_{title}_{date_td}".encode()
        ).hexdigest()[:16]

        pdf_path = self._download_pdf(pdf_url, f"naver_{report_id}") if pdf_url else None

        return ReportItem(
            report_id=f"naver_{report_id}",
            source=ReportSource.NAVER_FINANCE,
            brokerage=brokerage_td or "네이버금융",
            title=title,
            summary=f"[{brokerage_td}] {title} (발행일: {date_td})",
            investment_points=[title],
            opinion=opinion,
            target_price=target_price,
            company_name=company_name,
            published_at=pub_at,
            url=pdf_url,
            pdf_path=pdf_path,
            relevance_score=0.9,
            is_within_3_months=True,
        )

    def _download_pdf(self, pdf_url: str, file_stem: str) -> Optional[str]:
        """리포트 원문 PDF를 다운로드하여 로컬 파일로 저장한다.

        다운로드 실패는 리포트 수집 자체를 막지 않도록 예외를 삼키고 None을 반환한다.

        Args:
            pdf_url: PDF 원문 URL.
            file_stem: 확장자를 제외한 파일명 (report_id 기반).

        Returns:
            저장된 파일의 절대 경로 문자열. 실패 시 None.
        """
        try:
            resp = self._client.get(pdf_url, headers=None, params=None, timeout=15)
        except Exception as exc:
            logger.warning("[NaverFinance] PDF 다운로드 실패: %s — %s", pdf_url, exc)
            return None

        path = self._pdf_dir / f"{file_stem}.pdf"
        try:
            path.write_bytes(resp.content)
            return str(path)
        except OSError as exc:
            logger.warning("[NaverFinance] PDF 저장 실패: %s — %s", path, exc)
            return None


# ── 단독 실행 테스트 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock

    from config.config import Settings as _Settings, configure_logging

    configure_logging("DEBUG")

    print("=" * 60)
    print("[NaverFinanceCollector] 테스트 (Mock HTTP 클라이언트)")
    print("=" * 60)

    # 최근 날짜 계산 (테스트용)
    now = datetime.now(_KST)
    date_1 = (now - timedelta(days=10)).strftime("%y.%m.%d")
    date_2 = (now - timedelta(days=40)).strftime("%y.%m.%d")

    mock_html = f"""
    <html>
    <body>
    <table class="type_1">
        <tr>
            <td>테크윙</td>
            <td><a href="/research/company_read.naver?nid=100">테크윙 - HBM 테스터 수요 급증 (BUY, 목표주가 36,000원)</a></td>
            <td>키움증권</td>
            <td><a href="https://ssl.pstatic.net/imgstock/upload/research/company/123.pdf">PDF</a></td>
            <td>{date_1}</td>
        </tr>
        <tr>
            <td>테크윙</td>
            <td><a href="/research/company_read.naver?nid=101">테크윙 - 신규 핸들러 공급 계약 모멘텀</a></td>
            <td>삼성증권</td>
            <td><a href="https://ssl.pstatic.net/imgstock/upload/research/company/124.pdf">PDF</a></td>
            <td>{date_2}</td>
        </tr>
    </table>
    </body>
    </html>
    """.encode("euc-kr")

    mock_client = MagicMock(spec=NaverHttpClient)
    mock_resp = MagicMock()
    mock_resp.content = mock_html
    mock_resp.status_code = 200
    mock_client.get.return_value = mock_resp

    mock_settings = _Settings(
        dart_api_key="mock",
        cache_ttl_seconds=0,
    )

    collector = NaverFinanceCollector(settings=mock_settings, http_client=mock_client)

    print("\n[테스트 1] Mock HTML 기반 네이버 금융 리포트 수집 (최근 90일)")
    try:
        reports = collector.get_reports("테크윙", days=90)
        print(f"  -> 수집된 리포트 수: {len(reports)}건\n")
        for idx, r in enumerate(reports, 1):
            print(f"  [{idx}] [{r.brokerage}] {r.title}")
            print(f"       발행일: {r.published_at.strftime('%Y-%m-%d')} | 투자의견: {r.opinion.value}")
            print(f"       목표주가: {r.target_price:,.0f}원" if r.target_price else "       목표주가: N/A")
            print(f"       PDF URL: {r.url}")
            print()
    except Exception as e:
        print(f"\n[ERROR] 오류: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)

    # 2. 리포트 없을 때 테스트 (빈 리스트 반환 확인)
    print("=" * 60)
    print("[테스트 2] 리포트 없는 기업 조회 시 빈 리스트 반환 테스트")
    print("=" * 60)

    empty_resp = MagicMock()
    empty_resp.content = "<html><body><table class='type_1'></table></body></html>".encode("euc-kr")
    empty_resp.status_code = 200
    mock_client.get.return_value = empty_resp

    try:
        empty_reports = collector.get_reports("없는기업명999", days=90)
        print(f"  -> 반환된 리포트 수: {len(empty_reports)}건 (예외 없이 빈 리스트 반환 확인 완료)")
    except Exception as e:
        print(f"\n[ERROR] 오류 발생하면 안 됨: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n[NaverFinanceCollector] 테스트 완료")
