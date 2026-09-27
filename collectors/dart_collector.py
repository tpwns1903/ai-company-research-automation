"""
dart_collector.py
DART OpenAPI 데이터 수집기 — 사업보고서, 재무제표 수집

DART API 문서: https://opendart.fss.or.kr/guide/main.do
주요 엔드포인트:
  - /api/corpCode.xml           : 전체 기업 고유번호 목록 (ZIP) — 기업명 검색은 이걸 받아 로컬 매칭
  - /api/company.json          : 기업 개황 조회 (corp_code 필수, corp_name 검색 미지원)
  - /api/fnlttSinglAcnt.json  : 단일 회사 주요 계정 (손익계산서/재무상태표 핵심)
  - /api/fnlttSinglAcntAll.json: 단일 회사 전체 재무제표
"""

from __future__ import annotations

import io
import logging
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime
from typing import Any, Optional, Protocol

import requests

from collectors.base_collector import (
    ApiResponseError,
    BaseCollector,
    CollectorError,
    RateLimitError,
)
from config.config import Settings, get_settings
from models.financial_metrics import FinancialMetrics

logger = logging.getLogger(__name__)

# ── DART API 상수 ─────────────────────────────────────────────────────────────
_DART_BASE_URL = "https://opendart.fss.or.kr/api"
_CORP_CODE_URL = f"{_DART_BASE_URL}/corpCode.xml"
_REPORT_CODE_ANNUAL = "11011"      # 사업보고서 (연간)
_REPORT_CODE_HALF = "11012"        # 반기보고서
_REPORT_CODE_Q1 = "11013"          # 1분기 보고서
_REPORT_CODE_Q3 = "11014"          # 3분기 보고서

# 재무제표 구분
_FS_DIV_CONSOLIDATED = "CFS"       # 연결재무제표
_FS_DIV_SEPARATE = "OFS"           # 별도재무제표

# 주요 계정명 매핑 (DART 계정명 → FinancialMetrics 필드)
_ACCOUNT_MAP: dict[str, str] = {
    "매출액": "revenue",
    "수익(매출액)": "revenue",
    "영업이익": "operating_income",
    "영업이익(손실)": "operating_income",
    "당기순이익": "net_income",
    "당기순이익(손실)": "net_income",
    "자산총계": "total_assets",
    "자본총계": "total_equity",
    "부채총계": "_total_liabilities",   # 부채비율 계산용 임시 필드
}


# ── Mock 프로토콜 (테스트 주입용) ─────────────────────────────────────────────

class HttpClient(Protocol):
    """HTTP GET 요청을 수행하는 클라이언트 프로토콜.

    실제 환경에서는 requests.Session, 테스트 환경에서는 MockHttpClient로 대체한다.
    """

    def get(self, url: str, params: dict[str, Any], timeout: int) -> Any:
        """GET 요청을 수행하고 응답을 반환한다."""
        ...


class _RequestsSession:
    """requests.Session을 HttpClient 프로토콜에 맞게 감싸는 어댑터."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    def get(self, url: str, params: dict[str, Any], timeout: int = 10) -> Any:
        """HTTP GET 요청을 수행한다.

        Args:
            url: 요청 URL.
            params: 쿼리 파라미터.
            timeout: 타임아웃 (초).

        Returns:
            requests.Response 객체.

        Raises:
            ApiResponseError: HTTP 4xx/5xx 응답.
            CollectorError: 연결 오류.
        """
        try:
            resp = self._session.get(url, params=params, timeout=timeout)
        except requests.ConnectionError as exc:
            raise CollectorError(f"DART 연결 실패: {exc}") from exc
        except requests.Timeout as exc:
            raise CollectorError(f"DART 응답 타임아웃: {exc}") from exc

        if resp.status_code == 429:
            raise RateLimitError("DART API Rate Limit 초과")
        if resp.status_code >= 400:
            raise ApiResponseError(resp.status_code, resp.text[:200])

        return resp


# ── DartCollector ─────────────────────────────────────────────────────────────

class DartCollector(BaseCollector):
    """DART OpenAPI를 통해 기업 재무 데이터를 수집하는 Collector.

    수집 항목:
        - 기업 기본 정보 (회사명, 법인 코드)
        - 단일 회사 주요 계정 (매출, 영업이익, 순이익, 자산, 자본)
        - 위 데이터를 ``FinancialMetrics`` 모델로 정규화

    Mock 지원:
        생성자의 ``http_client`` 파라미터에 ``HttpClient`` 프로토콜을 구현한
        Mock 객체를 주입하면 실제 API 호출 없이 테스트 가능하다.

        >>> from unittest.mock import MagicMock
        >>> mock_client = MagicMock()
        >>> collector = DartCollector(http_client=mock_client)

    Example:
        >>> collector = DartCollector()
        >>> metrics = collector.get_financials("테크윙", fiscal_year=2023)
        >>> print(metrics.revenue)
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        http_client: Optional[HttpClient] = None,
    ) -> None:
        """DartCollector를 초기화한다.

        Args:
            settings: 애플리케이션 설정. None이면 .env에서 자동 로드.
            http_client: HTTP 클라이언트 (테스트 시 Mock 주입용).
        """
        super().__init__(settings)
        self._client: HttpClient = http_client or _RequestsSession()
        self._api_key: str = self._settings.dart_api_key
        logger.debug("DartCollector 준비 완료 (api_key 마스킹: %s****)", self._api_key[:4])

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    def collect(self, company_name: str, **kwargs: Any) -> FinancialMetrics:
        """기업명을 입력받아 최신 재무 지표를 수집한다.

        ``get_financials()``의 단순 래퍼 역할을 한다.

        Args:
            company_name: 수집 대상 기업명.
            **kwargs:
                - fiscal_year (int): 회계연도. 미지정 시 직전 연도.

        Returns:
            FinancialMetrics: 정규화된 재무 지표.

        Raises:
            CollectorError: 기업 코드 조회 실패 또는 재무 데이터 수집 실패.
        """
        fiscal_year: int = kwargs.get("fiscal_year", datetime.now().year - 1)
        return self.get_financials(company_name, fiscal_year=fiscal_year)

    def get_financials(
        self,
        company_name: str,
        fiscal_year: Optional[int] = None,
        prefer_consolidated: bool = True,
    ) -> FinancialMetrics:
        """기업의 연간 재무 지표를 반환한다.

        캐시 우선 조회 → 캐시 미스 시 DART API 호출 순서로 동작한다.

        Args:
            company_name: 기업명.
            fiscal_year: 회계연도. None이면 직전 연도 사용.
            prefer_consolidated: True면 연결재무제표, False면 별도재무제표 우선.

        Returns:
            FinancialMetrics: 정규화된 재무 지표 모델.

        Raises:
            CollectorError: 기업 미발견 또는 DART API 오류.
        """
        year = fiscal_year or (datetime.now().year - 1)
        fs_div = _FS_DIV_CONSOLIDATED if prefer_consolidated else _FS_DIV_SEPARATE

        cache_key = self._make_cache_key("dart_financials", company_name, year, fs_div)
        cached = self._load_cache(cache_key)
        if cached is not None:
            logger.info("[DART] 캐시에서 로드: %s (%d)", company_name, year)
            return FinancialMetrics.model_validate(cached)

        logger.info("[DART] API 수집 시작: %s (%d)", company_name, year)

        # 1) 기업 코드 조회
        corp_code = self._request_with_retry(self._search_company_code, company_name)

        # 2) 재무 데이터 수집
        self._wait()
        raw_accounts = self._request_with_retry(
            self._fetch_financial_accounts,
            corp_code=corp_code,
            fiscal_year=year,
            fs_div=fs_div,
            report_code=_REPORT_CODE_ANNUAL,
        )

        # 3) 별도재무제표 폴백 (연결이 없는 경우)
        if not raw_accounts and prefer_consolidated:
            logger.info("[DART] 연결재무제표 없음 → 별도재무제표로 폴백: %s", company_name)
            self._wait()
            raw_accounts = self._request_with_retry(
                self._fetch_financial_accounts,
                corp_code=corp_code,
                fiscal_year=year,
                fs_div=_FS_DIV_SEPARATE,
                report_code=_REPORT_CODE_ANNUAL,
            )

        if not raw_accounts:
            raise CollectorError(
                f"[DART] 재무 데이터를 찾을 수 없음: {company_name} ({year})"
            )

        # 4) 정규화
        stock_code = self._fetch_stock_code(corp_code)
        metrics = self._parse_financial_accounts(
            raw_accounts=raw_accounts,
            company_name=company_name,
            stock_code=stock_code,
            fiscal_year=year,
        )

        # 5) 캐시 저장
        self._save_cache(cache_key, metrics.model_dump(mode="json"))
        logger.info("[DART] 수집 완료: %s — 매출 %.0f억원", company_name, metrics.revenue or 0)
        return metrics

    # ── 내부 구현 ─────────────────────────────────────────────────────────────

    def _search_company_code(self, company_name: str) -> str:
        """기업명으로 DART 고유 법인 코드를 조회한다.

        DART ``company.json``은 corp_code가 필수 파라미터이며 corp_name으로
        검색하는 기능을 제공하지 않는다. 따라서 전체 기업 목록(corpCode.xml)을
        받아 로컬에서 기업명을 매칭하는 방식으로 corp_code를 구한다.

        Args:
            company_name: 검색할 기업명 (부분 일치 허용).

        Returns:
            8자리 DART 법인 고유 코드.

        Raises:
            CollectorError: 기업을 찾지 못한 경우.
        """
        corp_map = self._load_corp_code_map()

        corp_code = corp_map.get(company_name)
        if not corp_code:
            # 정확히 일치하는 이름이 없으면 부분 일치로 재시도 (가장 짧은 이름을 우선)
            candidates = [name for name in corp_map if company_name in name]
            if candidates:
                best = min(candidates, key=len)
                corp_code = corp_map[best]
                logger.debug(
                    "[DART][법인코드조회] 부분 일치로 매칭: '%s' → '%s' (corp_code=%s)",
                    company_name, best, corp_code,
                )

        if not corp_code:
            logger.error(
                "[DART][법인코드조회] 실패 — '%s'를 corpCode.xml 목록(%d개 기업)에서 찾을 수 없음",
                company_name, len(corp_map),
            )
            raise CollectorError(f"[DART][법인코드조회] 기업을 찾을 수 없음: '{company_name}'")

        logger.info("[DART][법인코드조회] 성공: %s → corp_code=%s", company_name, corp_code)
        return corp_code

    def _load_corp_code_map(self) -> dict[str, str]:
        """{기업명: corp_code} 매핑을 캐시 우선으로 반환한다.

        corpCode.xml은 전체 상장/비상장 기업(약 10만 건 이상)을 담고 있어
        매 요청마다 새로 받으면 비효율적이므로 BaseCollector의 캐시를 그대로 사용한다.

        Returns:
            {기업명: corp_code} 매핑 딕셔너리.
        """
        cache_key = self._make_cache_key("dart_corp_code_map")
        cached = self._load_cache(cache_key)
        if cached is not None:
            logger.debug("[DART][법인코드조회] corp_code 맵 캐시에서 로드: %d개 기업", len(cached))
            return cached

        corp_map = self._request_with_retry(self._fetch_corp_code_map)
        self._save_cache(cache_key, corp_map)
        return corp_map

    def _fetch_corp_code_map(self) -> dict[str, str]:
        """DART ``/api/corpCode.xml``을 다운로드하여 {기업명: corp_code} 매핑을 생성한다.

        응답은 CORPCODE.xml을 담은 ZIP 바이너리다. 인증키 오류 등으로 실패하면
        ZIP 대신 JSON 오류 응답(status/message)이 오므로 이를 구분해 로그로 남긴다.

        Returns:
            {기업명: corp_code} 매핑 딕셔너리.

        Raises:
            ApiResponseError: DART가 오류 응답(JSON)을 반환한 경우.
            CollectorError: 응답을 ZIP/JSON 어느 쪽으로도 해석할 수 없는 경우.
        """
        params = {"crtfc_key": self._api_key}
        resp = self._client.get(_CORP_CODE_URL, params=params, timeout=20)

        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                xml_bytes = zf.read("CORPCODE.xml")
        except zipfile.BadZipFile:
            try:
                data = resp.json()
            except ValueError:
                raise CollectorError(
                    "[DART][법인코드조회] corpCode.xml 응답을 ZIP/JSON 어느 쪽으로도 해석할 수 없음"
                )
            status, message = data.get("status", "?"), data.get("message", "")
            logger.error(
                "[DART][법인코드조회] 응답 status=%s, message=%s (corpCode.xml)",
                status, message,
            )
            raise ApiResponseError(0, f"DART corpCode.xml 오류: status={status}, msg={message}")

        root = ET.fromstring(xml_bytes)
        corp_map: dict[str, str] = {}
        for elem in root.findall(".//list"):
            corp_name = (elem.findtext("corp_name") or "").strip()
            corp_code = (elem.findtext("corp_code") or "").strip()
            stock_code = (elem.findtext("stock_code") or "").strip()
            if not corp_name or not corp_code:
                continue
            # 동명 기업이 여러 건이면 상장사(종목코드 있음)를 우선한다
            if corp_name not in corp_map or stock_code:
                corp_map[corp_name] = corp_code

        logger.info("[DART][법인코드조회] corpCode.xml 파싱 완료: 총 %d개 기업", len(corp_map))
        return corp_map

    def _fetch_stock_code(self, corp_code: str) -> str:
        """DART 법인 코드로 주식 종목 코드를 조회한다.

        Args:
            corp_code: DART 고유 법인 코드.

        Returns:
            6자리 종목 코드. 미상장이면 빈 문자열.
        """
        url = f"{_DART_BASE_URL}/company.json"
        params = {"crtfc_key": self._api_key, "corp_code": corp_code}
        try:
            resp = self._client.get(url, params=params, timeout=10)
            data: dict = resp.json()
            status, message = data.get("status", ""), data.get("message", "")
            logger.debug("[DART][기업개황조회] 응답 status=%s, message=%s", status, message)

            if status != "000":
                logger.warning(
                    "[DART][기업개황조회] 종목코드 조회 실패: corp_code=%s, status=%s, message=%s",
                    corp_code, status, message,
                )
                return ""
            return data.get("stock_code", "").strip() or ""
        except CollectorError as exc:
            logger.warning("[DART][기업개황조회] 요청 실패: corp_code=%s — %s", corp_code, exc)
            return ""

    def _fetch_financial_accounts(
        self,
        corp_code: str,
        fiscal_year: int,
        fs_div: str,
        report_code: str,
    ) -> list[dict[str, Any]]:
        """DART 단일 회사 주요 계정 데이터를 원본 형태로 수집한다.

        ``/api/fnlttSinglAcnt.json`` 엔드포인트를 사용한다.

        Args:
            corp_code: DART 법인 고유 코드.
            fiscal_year: 회계연도.
            fs_div: 재무제표 구분 ('CFS'=연결 / 'OFS'=별도).
            report_code: 보고서 코드 (11011=사업보고서).

        Returns:
            계정 항목 딕셔너리의 리스트. 데이터 없으면 빈 리스트.
        """
        url = f"{_DART_BASE_URL}/fnlttSinglAcnt.json"
        params = {
            "crtfc_key": self._api_key,
            "corp_code": corp_code,
            "bsns_year": str(fiscal_year),
            "reprt_code": report_code,
        }

        resp = self._client.get(url, params=params, timeout=15)
        data: dict = resp.json()
        status = data.get("status", "")
        message = data.get("message", "")
        logger.info(
            "[DART][재무제표조회] 응답 status=%s, message=%s (corp_code=%s, year=%d, fs_div=%s)",
            status, message, corp_code, fiscal_year, fs_div,
        )

        if status == "013":  # 조회된 데이터 없음
            logger.warning(
                "[DART][재무제표조회] 조회된 데이터 없음: corp_code=%s year=%d fs_div=%s",
                corp_code, fiscal_year, fs_div,
            )
            return []
        if status != "000":
            logger.error(
                "[DART][재무제표조회] 오류 응답: corp_code=%s year=%d fs_div=%s — status=%s, message=%s",
                corp_code, fiscal_year, fs_div, status, message,
            )
            raise ApiResponseError(0, f"DART fnlttSinglAcnt 오류: status={status}, msg={message}")

        items: list[dict] = data.get("list", [])
        # fs_div 필터링
        return [item for item in items if item.get("fs_div") == fs_div]

    def _parse_financial_accounts(
        self,
        raw_accounts: list[dict[str, Any]],
        company_name: str,
        stock_code: str,
        fiscal_year: int,
    ) -> FinancialMetrics:
        """DART API 응답 계정 목록을 FinancialMetrics 모델로 변환한다.

        Args:
            raw_accounts: DART API로부터 받은 계정 항목 리스트.
            company_name: 기업명.
            stock_code: 종목 코드.
            fiscal_year: 회계연도.

        Returns:
            FinancialMetrics: 정규화된 재무 지표. 금액 단위는 억 원.
        """
        # 계정명 → 금액 매핑 (당기 기준 'thstrm_amount')
        account_values: dict[str, float] = {}
        for item in raw_accounts:
            account_nm: str = item.get("account_nm", "").strip()
            raw_amount: str = item.get("thstrm_amount", "").replace(",", "").strip()

            if account_nm in _ACCOUNT_MAP and raw_amount:
                try:
                    account_values[_ACCOUNT_MAP[account_nm]] = float(raw_amount)
                except ValueError:
                    logger.warning("[DART] 금액 파싱 실패: %s = '%s'", account_nm, raw_amount)

        # 원 → 억 원 변환
        def to_uk(field: str) -> Optional[float]:
            return self._won_to_uk(account_values.get(field))

        revenue = to_uk("revenue")
        operating_income = to_uk("operating_income")
        net_income = to_uk("net_income")
        total_assets = to_uk("total_assets")
        total_equity = to_uk("total_equity")
        total_liabilities = to_uk("_total_liabilities")

        # 수익성 지표 계산
        operating_margin: Optional[float] = None
        net_margin: Optional[float] = None
        roe: Optional[float] = None
        roa: Optional[float] = None
        debt_to_equity: Optional[float] = None

        if revenue and revenue != 0:
            if operating_income is not None:
                operating_margin = round(operating_income / revenue * 100, 2)
            if net_income is not None:
                net_margin = round(net_income / revenue * 100, 2)

        if total_equity and total_equity != 0:
            if net_income is not None:
                roe = round(net_income / total_equity * 100, 2)
            if total_liabilities is not None:
                debt_to_equity = round(total_liabilities / total_equity * 100, 2)

        if total_assets and total_assets != 0 and net_income is not None:
            roa = round(net_income / total_assets * 100, 2)

        return FinancialMetrics(
            company_name=company_name,
            stock_code=stock_code or "N/A",
            fiscal_year=fiscal_year,
            base_date=date(fiscal_year, 12, 31),
            revenue=revenue,
            operating_income=operating_income,
            net_income=net_income,
            total_assets=total_assets,
            total_equity=total_equity,
            total_liabilities=total_liabilities,
            operating_margin=operating_margin,
            net_margin=net_margin,
            roe=roe,
            roa=roa,
            debt_to_equity=debt_to_equity,
        )


# ── 단독 실행 테스트 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock

    from config.config import configure_logging

    configure_logging("DEBUG")

    # ── Mock 모드 (API 키 없이 동작 확인) ────────────────────────────────────
    print("=" * 60)
    print("[DartCollector] Mock 테스트")
    print("=" * 60)

    def _make_mock_client() -> MagicMock:
        """DART API 응답을 흉내내는 Mock HTTP 클라이언트를 생성한다."""
        client = MagicMock()

        # corpCode.xml 응답 (ZIP 바이너리 안에 CORPCODE.xml)
        import zipfile as _zipfile
        from io import BytesIO as _BytesIO

        corp_code_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <result>
            <list>
                <corp_code>00113067</corp_code>
                <corp_name>테크윙</corp_name>
                <stock_code>089030</stock_code>
                <modify_date>20240101</modify_date>
            </list>
        </result>""".encode("utf-8")

        _zip_buf = _BytesIO()
        with _zipfile.ZipFile(_zip_buf, "w") as _zf:
            _zf.writestr("CORPCODE.xml", corp_code_xml)

        corp_code_resp = MagicMock()
        corp_code_resp.content = _zip_buf.getvalue()

        # 기업 개황 응답 (종목코드 조회용)
        company_resp = MagicMock()
        company_resp.json.return_value = {
            "status": "000",
            "corp_code": "00113067",
            "stock_code": "089030",
            "corp_name": "테크윙",
        }

        # 재무제표 응답
        financial_resp = MagicMock()
        financial_resp.json.return_value = {
            "status": "000",
            "list": [
                {
                    "fs_div": "CFS",
                    "account_nm": "매출액",
                    "thstrm_amount": "320,000,000,000",  # 3200억
                },
                {
                    "fs_div": "CFS",
                    "account_nm": "영업이익",
                    "thstrm_amount": "48,000,000,000",   # 480억
                },
                {
                    "fs_div": "CFS",
                    "account_nm": "당기순이익",
                    "thstrm_amount": "38,000,000,000",   # 380억
                },
                {
                    "fs_div": "CFS",
                    "account_nm": "자산총계",
                    "thstrm_amount": "500,000,000,000",  # 5000억
                },
                {
                    "fs_div": "CFS",
                    "account_nm": "자본총계",
                    "thstrm_amount": "310,000,000,000",  # 3100억
                },
                {
                    "fs_div": "CFS",
                    "account_nm": "부채총계",
                    "thstrm_amount": "190,000,000,000",  # 1900억
                },
            ],
        }

        # get() 호출 순서:
        #   1번째: _fetch_corp_code_map (corpCode.xml 다운로드 → 기업명 -> corp_code 매핑)
        #   2번째: _fetch_financial_accounts (재무제표)
        #   3번째: _fetch_stock_code (corp_code -> stock_code)
        client.get.side_effect = [corp_code_resp, financial_resp, company_resp]
        return client

    from config.config import Settings as _Settings

    mock_settings = _Settings(
        dart_api_key="mock_key_0000",
        cache_ttl_seconds=0,  # 캐시 비활성화
    )

    mock_client = _make_mock_client()
    collector = DartCollector(settings=mock_settings, http_client=mock_client)

    try:
        metrics = collector.get_financials("테크윙", fiscal_year=2023)
        print(f"\n[SUCCESS] 수집 성공!")
        print(f"  기업명       : {metrics.company_name}")
        print(f"  종목코드     : {metrics.stock_code}")
        print(f"  회계연도     : {metrics.fiscal_year}")
        print(f"  매출액       : {metrics.revenue} 억원")
        print(f"  영업이익     : {metrics.operating_income} 억원")
        print(f"  영업이익률   : {metrics.operating_margin}%")
        print(f"  당기순이익   : {metrics.net_income} 억원")
        print(f"  ROE          : {metrics.roe}%")
        print(f"  부채비율     : {metrics.debt_to_equity}%")
    except Exception as e:
        print(f"\n[ERROR] 오류: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n[DartCollector] Mock 테스트 완료")

    # ── 실제 DART API 테스트 (삼성전자) ──────────────────────────────────────
    print("\n" + "=" * 60)
    print("[DartCollector] 실제 DART API 테스트 (삼성전자)")
    print("=" * 60)

    try:
        real_settings = get_settings()
        real_collector = DartCollector(settings=real_settings)
        # 위 Mock 테스트에서 저장된 corp_code 맵 캐시(테크윙 1건짜리)를 그대로 재사용하면
        # 실제 corpCode.xml을 받지 않고 항상 "찾을 수 없음"으로 실패하므로 무효화한다.
        real_collector._invalidate_cache(real_collector._make_cache_key("dart_corp_code_map"))
        real_metrics = real_collector.get_financials("삼성전자", fiscal_year=datetime.now().year - 1)
        print(f"\n[SUCCESS] 실제 DART API 데이터 수집 성공!")
        print(f"  기업명       : {real_metrics.company_name}")
        print(f"  종목코드     : {real_metrics.stock_code}")
        print(f"  회계연도     : {real_metrics.fiscal_year}")
        print(f"  매출액       : {real_metrics.revenue} 억원")
        print(f"  영업이익     : {real_metrics.operating_income} 억원")
        print(f"  영업이익률   : {real_metrics.operating_margin}%")
        print(f"  당기순이익   : {real_metrics.net_income} 억원")
        print(f"  ROE          : {real_metrics.roe}%")
        print(f"  부채비율     : {real_metrics.debt_to_equity}%")
    except Exception as e:
        print(f"\n[ERROR] 실제 API 테스트 오류: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)

    print("\n[DartCollector] 실제 API 테스트 완료")
