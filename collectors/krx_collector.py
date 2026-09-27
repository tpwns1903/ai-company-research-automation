"""
krx_collector.py
PyKRX를 통한 주가/시세 및 투자지표 수집기

PyKRX 문서: https://github.com/sharebook-kr/pykrx
주요 기능:
  - 일별 OHLCV (시가/고가/저가/종가/거래량)
  - 시가총액, 외국인 보유 비율
  - PER, PBR, EPS, BPS, 배당수익률

pykrx 1.2.8부터 종목 목록·펀더멘털·시가총액 조회에 KRX 데이터 포털 로그인
(KRX_ID / KRX_PW 환경변수)이 필요하다. 로그인 없이 빈 결과가 오면
네이버 금융 종목 API로 PER/PBR/EPS/BPS/배당수익률/시가총액을 보충한다.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional, Protocol

import requests

from collectors.base_collector import BaseCollector, CollectorError
from config.config import Settings
from models.financial_metrics import FinancialMetrics, PriceMetrics

logger = logging.getLogger(__name__)

_NAVER_STOCK_API = "https://m.stock.naver.com/api/stock/{code}/integration"


def _parse_naver_number(text: Optional[str]) -> Optional[float]:
    """'12.85배', '22,292원', '0.58%' 같은 문자열에서 숫자를 꺼낸다. 'N/A'·'-'는 None."""
    if not text:
        return None
    match = re.search(r"-?[\d,]+(?:\.\d+)?", text)
    if not match:
        return None
    return float(match.group().replace(",", ""))


def _parse_naver_market_cap_uk(text: Optional[str]) -> Optional[float]:
    """'1,674조 9,588억' 형식의 시가총액을 억 원 단위 숫자로 바꾼다."""
    if not text:
        return None
    jo = re.search(r"([\d,]+)\s*조", text)
    uk = re.search(r"([\d,]+)\s*억", text)
    if not jo and not uk:
        return None
    total = 0.0
    if jo:
        total += float(jo.group(1).replace(",", "")) * 10_000
    if uk:
        total += float(uk.group(1).replace(",", ""))
    return total


def fetch_naver_valuation(stock_code: str, timeout: float = 10.0) -> dict[str, Optional[float]]:
    """네이버 금융 종목 API에서 밸류에이션 지표를 조회한다 (로그인 불필요).

    Args:
        stock_code: 6자리 종목 코드.
        timeout: 요청 타임아웃 (초).

    Returns:
        dict: per, pbr, eps, bps, dividend_yield (%), market_cap (억 원). 값이 없으면 None.

    Raises:
        requests.RequestException: 네트워크/HTTP 오류.
    """
    resp = requests.get(
        _NAVER_STOCK_API.format(code=stock_code),
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=timeout,
    )
    resp.raise_for_status()
    infos = {item.get("code"): item.get("value") for item in resp.json().get("totalInfos", [])}
    return {
        "per": _parse_naver_number(infos.get("per")),
        "pbr": _parse_naver_number(infos.get("pbr")),
        "eps": _parse_naver_number(infos.get("eps")),
        "bps": _parse_naver_number(infos.get("bps")),
        "dividend_yield": _parse_naver_number(infos.get("dividendYieldRatio")),
        "market_cap": _parse_naver_market_cap_uk(infos.get("marketValue")),
    }


# ── PyKRX 어댑터 프로토콜 (Mock 주입용) ──────────────────────────────────────

class KrxAdapter(Protocol):
    """PyKRX 호출을 추상화하는 프로토콜.

    실제 환경에서는 ``_PykrxAdapter``,
    테스트 환경에서는 ``MockKrxAdapter``를 주입한다.
    """

    def get_market_ticker_list(self, date_str: str, market: str) -> list[str]:
        """전체 종목 코드 목록을 반환한다."""
        ...

    def get_market_ticker_name(self, ticker: str) -> str:
        """종목 코드로 기업명을 반환한다."""
        ...

    def get_market_ohlcv_by_date(
        self, from_date: str, to_date: str, ticker: str
    ) -> Any:
        """기간별 OHLCV 데이터프레임을 반환한다."""
        ...

    def get_market_fundamental_by_date(
        self, from_date: str, to_date: str, ticker: str
    ) -> Any:
        """기간별 펀더멘털(PER/PBR/EPS 등) 데이터프레임을 반환한다."""
        ...

    def get_market_cap_by_date(
        self, from_date: str, to_date: str, ticker: str
    ) -> Any:
        """기간별 시가총액 데이터프레임을 반환한다."""
        ...

    def get_market_trading_volume_by_date(
        self, from_date: str, to_date: str, ticker: str, etf: bool, etn: bool,
        elw: bool, freq: str
    ) -> Any:
        """기간별 외국인 거래 데이터프레임을 반환한다."""
        ...


class _PykrxAdapter:
    """실제 pykrx 라이브러리를 KrxAdapter 프로토콜에 맞게 감싸는 어댑터."""

    def __init__(self, krx_id: str = "", krx_pw: str = "") -> None:
        # pykrx는 import 시점에 os.getenv("KRX_ID"/"KRX_PW")로 로그인 정보를 읽으므로
        # .env(Settings)에 있는 값을 import 전에 환경변수로 넘긴다. 이미 설정된 값은 유지.
        if krx_id and krx_pw:
            os.environ.setdefault("KRX_ID", krx_id)
            os.environ.setdefault("KRX_PW", krx_pw)
        try:
            from pykrx import stock as _stock  # type: ignore[import]
            self._stock = _stock
        except ImportError as exc:
            raise ImportError(
                "pykrx가 설치되지 않았습니다. 'pip install pykrx'를 실행하세요."
            ) from exc

    def get_market_ticker_list(self, date_str: str, market: str = "ALL") -> list[str]:
        return list(self._stock.get_market_ticker_list(date_str, market=market))

    def get_market_ticker_name(self, ticker: str) -> str:
        return self._stock.get_market_ticker_name(ticker)

    def get_market_ohlcv_by_date(
        self, from_date: str, to_date: str, ticker: str
    ) -> Any:
        return self._stock.get_market_ohlcv_by_date(from_date, to_date, ticker)

    def get_market_fundamental_by_date(
        self, from_date: str, to_date: str, ticker: str
    ) -> Any:
        return self._stock.get_market_fundamental_by_date(from_date, to_date, ticker)

    def get_market_cap_by_date(
        self, from_date: str, to_date: str, ticker: str
    ) -> Any:
        return self._stock.get_market_cap_by_date(from_date, to_date, ticker)

    def get_market_trading_volume_by_date(
        self, from_date: str, to_date: str, ticker: str,
        etf: bool = False, etn: bool = False, elw: bool = False, freq: str = "d"
    ) -> Any:
        return self._stock.get_market_trading_volume_by_date(
            from_date, to_date, ticker, etf=etf, etn=etn, elw=elw, freq=freq
        )


# ── KrxCollector ─────────────────────────────────────────────────────────────

class KrxCollector(BaseCollector):
    """PyKRX를 통해 한국거래소 주가 및 투자지표 데이터를 수집하는 Collector.

    수집 항목:
        - OHLCV (시가/고가/저가/종가/거래량)
        - 시가총액, 외국인 보유 비율
        - PER, PBR, EPS, BPS, 배당수익률
        - 52주 최고가/최저가

    Mock 지원:
        생성자의 ``krx_adapter`` 파라미터에 ``KrxAdapter`` 프로토콜을 구현한
        Mock 객체를 주입하면 실제 PyKRX 없이 테스트 가능하다.

    Example:
        >>> collector = KrxCollector()
        >>> price = collector.get_price_data("삼성전자")
        >>> print(price.close_price)
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        krx_adapter: Optional[KrxAdapter] = None,
        valuation_fetcher: Optional[Callable[[str], dict[str, Optional[float]]]] = None,
    ) -> None:
        """KrxCollector를 초기화한다.

        Args:
            settings: 애플리케이션 설정. None이면 .env에서 자동 로드.
            krx_adapter: PyKRX 어댑터 (테스트 시 Mock 주입용).
            valuation_fetcher: pykrx 지표가 비었을 때 쓰는 보충 조회 함수
                (기본: 네이버 금융, 테스트 시 Mock 주입용).
        """
        super().__init__(settings)
        self._krx: KrxAdapter = krx_adapter or _PykrxAdapter(
            self._settings.krx_id, self._settings.krx_pw
        )
        self._valuation_fetcher = valuation_fetcher or fetch_naver_valuation
        self._ticker_cache: dict[str, str] = {}  # company_name → ticker (인메모리 캐시)
        logger.debug("KrxCollector 준비 완료")

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    def collect(self, company_name: str, **kwargs: Any) -> PriceMetrics:
        """기업명을 입력받아 최근 주가 데이터를 수집한다.

        Args:
            company_name: 수집 대상 기업명.
            **kwargs:
                - trade_date (date): 조회 기준일. 미지정 시 전 거래일.

        Returns:
            PriceMetrics: 정규화된 주가 데이터.

        Raises:
            CollectorError: 종목 코드 조회 실패 또는 데이터 없음.
        """
        trade_date: Optional[date] = kwargs.get("trade_date")
        return self.get_price_data(company_name, trade_date=trade_date)

    def get_price_data(
        self,
        company_name: str,
        trade_date: Optional[date] = None,
        stock_code: Optional[str] = None,
    ) -> PriceMetrics:
        """특정 날짜의 주가 및 투자지표를 반환한다.

        Args:
            company_name: 기업명.
            trade_date: 조회 기준일. None이면 전 영업일.
            stock_code: 종목 코드를 이미 알고 있으면 지정 (예: DART 조회 결과).
                지정하면 KRX 종목 목록 조회(로그인 필요)를 건너뛴다.

        Returns:
            PriceMetrics: 정규화된 주가 데이터.

        Raises:
            CollectorError: 데이터 조회 실패.
        """
        target_date = trade_date or (date.today() - timedelta(days=1))
        cache_key = self._make_cache_key("krx_price", company_name, str(target_date))

        cached = self._load_cache(cache_key)
        if cached is not None:
            logger.info("[KRX] 캐시에서 로드: %s (%s)", company_name, target_date)
            return PriceMetrics.model_validate(cached)

        logger.info("[KRX] API 수집 시작: %s (%s)", company_name, target_date)

        # 종목 코드 조회
        if stock_code:
            self._ticker_cache[company_name] = stock_code
        ticker = self._request_with_retry(self.get_stock_code, company_name)

        # 주가/펀더멘털/시가총액 병렬 수집
        date_str = self._yyyymmdd(target_date)
        # 10 영업일 범위로 조회 (휴장일 대응)
        from_date_str = self._yyyymmdd(target_date - timedelta(days=14))

        self._wait()
        ohlcv_df = self._request_with_retry(
            self._krx.get_market_ohlcv_by_date, from_date_str, date_str, ticker
        )

        self._wait()
        fundamental_df = self._request_with_retry(
            self._krx.get_market_fundamental_by_date, from_date_str, date_str, ticker
        )

        self._wait()
        cap_df = self._request_with_retry(
            self._krx.get_market_cap_by_date, from_date_str, date_str, ticker
        )

        # 데이터프레임에서 최근 거래일 데이터 추출
        metrics = self._parse_market_data(
            company_name=company_name,
            ticker=ticker,
            ohlcv_df=ohlcv_df,
            fundamental_df=fundamental_df,
            cap_df=cap_df,
            target_date=target_date,
        )
        metrics = self._fill_missing_valuation(metrics)

        self._save_cache(cache_key, metrics.model_dump(mode="json"))
        logger.info(
            "[KRX] 수집 완료: %s — 종가 %.0f원 (거래일: %s)",
            company_name, metrics.close_price or 0, metrics.trade_date,
        )
        return metrics

    def get_financial_fundamentals(
        self,
        company_name: str,
        trade_date: Optional[date] = None,
    ) -> dict[str, Optional[float]]:
        """주가 기반 밸류에이션 지표(PER/PBR/EPS/BPS/배당수익률)를 반환한다.

        FinancialMetrics에 주가 지표를 병합할 때 활용한다.

        Args:
            company_name: 기업명.
            trade_date: 기준일. None이면 전 영업일.

        Returns:
            dict: 필드명 → 값 매핑.
        """
        metrics = self.get_price_data(company_name, trade_date=trade_date)
        return {
            "per": metrics.per,
            "pbr": metrics.pbr,
            "eps": metrics.eps,
            "bps": metrics.bps,
            "dividend_yield": metrics.dividend_yield,
            "market_cap": metrics.market_cap,
            "closing_price": metrics.close_price,
        }

    def get_stock_code(self, company_name: str) -> str:
        """기업명으로 KRX 종목 코드를 조회한다.

        인메모리 캐시를 우선 확인하고, 없으면 PyKRX에서 전체 종목 목록을
        조회하여 이름을 대조한다.

        Args:
            company_name: 검색할 기업명 (정확히 일치).

        Returns:
            6자리 종목 코드 문자열.

        Raises:
            CollectorError: 종목을 찾을 수 없는 경우.
        """
        if company_name in self._ticker_cache:
            return self._ticker_cache[company_name]

        today_str = self._yyyymmdd(date.today())
        try:
            tickers = self._krx.get_market_ticker_list(today_str, market="ALL")
        except Exception as exc:
            raise CollectorError(f"[KRX] 종목 목록 조회 실패: {exc}") from exc

        for ticker in tickers:
            try:
                name = self._krx.get_market_ticker_name(ticker)
            except Exception:
                continue
            if name == company_name:
                self._ticker_cache[company_name] = ticker
                logger.debug("[KRX] 종목 코드 조회 성공: %s → %s", company_name, ticker)
                return ticker

        raise CollectorError(f"[KRX] 종목을 찾을 수 없음: '{company_name}'")

    # ── 내부 파싱 ─────────────────────────────────────────────────────────────

    def _fill_missing_valuation(self, metrics: PriceMetrics) -> PriceMetrics:
        """pykrx에서 비어 온 밸류에이션 지표를 보충 조회 함수(네이버 금융)로 채운다."""
        fields = ("per", "pbr", "eps", "bps", "dividend_yield", "market_cap")
        missing = [f for f in fields if getattr(metrics, f) is None]
        if not missing:
            return metrics

        try:
            self._wait()
            fetched = self._valuation_fetcher(metrics.stock_code)
        except Exception as exc:
            logger.warning(
                "[KRX] 밸류에이션 보충 조회 실패 (%s): %s — %s 비어 있음",
                metrics.stock_code, exc, ", ".join(missing),
            )
            return metrics

        filled = {f: fetched[f] for f in missing if fetched.get(f) is not None}
        if filled:
            logger.info(
                "[KRX] pykrx에 없는 지표를 네이버 금융으로 보충: %s", ", ".join(filled)
            )
        return metrics.model_copy(update=filled)

    def _parse_market_data(
        self,
        company_name: str,
        ticker: str,
        ohlcv_df: Any,
        fundamental_df: Any,
        cap_df: Any,
        target_date: date,
    ) -> PriceMetrics:
        """수집된 데이터프레임에서 최근 거래일 데이터를 파싱한다.

        Args:
            company_name: 기업명.
            ticker: 종목 코드.
            ohlcv_df: OHLCV 데이터프레임 (pykrx 반환).
            fundamental_df: 펀더멘털 데이터프레임 (pykrx 반환).
            cap_df: 시가총액 데이터프레임 (pykrx 반환).
            target_date: 요청 기준일.

        Returns:
            PriceMetrics: 정규화된 주가 모델.

        Raises:
            CollectorError: 데이터프레임이 비어있는 경우.
        """
        if ohlcv_df is None or ohlcv_df.empty:
            raise CollectorError(
                f"[KRX] OHLCV 데이터 없음: {company_name} ({target_date}). "
                "휴장일이거나 상장폐지된 종목일 수 있습니다."
            )

        # 가장 최근 거래일 행 선택
        last_row = ohlcv_df.iloc[-1]
        actual_date: date = last_row.name.date() if hasattr(last_row.name, "date") else target_date

        # OHLCV
        open_price = float(last_row.get("시가", last_row.get("Open", 0)) or 0) or None
        high_price = float(last_row.get("고가", last_row.get("High", 0)) or 0) or None
        low_price = float(last_row.get("저가", last_row.get("Low", 0)) or 0) or None
        close_price = float(last_row.get("종가", last_row.get("Close", 0)) or 0) or None
        volume = int(last_row.get("거래량", last_row.get("Volume", 0)) or 0) or None
        change_rate = float(last_row.get("등락률", last_row.get("Change", 0)) or 0) or None

        # 펀더멘털 지표 (PER, PBR, EPS, BPS, DIV)
        per, pbr, eps, bps, div = None, None, None, None, None
        if fundamental_df is not None and not fundamental_df.empty:
            last_fun = fundamental_df.iloc[-1]
            per = float(last_fun.get("PER", 0) or 0) or None
            pbr = float(last_fun.get("PBR", 0) or 0) or None
            eps = float(last_fun.get("EPS", 0) or 0) or None
            bps = float(last_fun.get("BPS", 0) or 0) or None
            div = float(last_fun.get("DIV", last_fun.get("DVD", 0)) or 0) or None

        # 시가총액 (억 원)
        market_cap_uk: Optional[float] = None
        if cap_df is not None and not cap_df.empty:
            last_cap = cap_df.iloc[-1]
            raw_cap = float(last_cap.get("시가총액", last_cap.get("Mktcap", 0)) or 0)
            market_cap_uk = self._won_to_uk(raw_cap) if raw_cap else None

        # 외국인 보유 비율
        foreign_ratio: Optional[float] = None
        if cap_df is not None and not cap_df.empty:
            last_cap = cap_df.iloc[-1]
            raw_ratio = last_cap.get("외국인한도소진률", last_cap.get("Foreignratio"))
            if raw_ratio is not None:
                try:
                    foreign_ratio = float(raw_ratio)
                except (ValueError, TypeError):
                    pass

        # 거래대금 (억 원)
        trading_value_uk: Optional[float] = None
        if volume and close_price:
            trading_value_uk = self._won_to_uk(volume * close_price)

        return PriceMetrics(
            company_name=company_name,
            stock_code=ticker,
            trade_date=actual_date,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
            volume=volume,
            trading_value=trading_value_uk,
            change_rate=change_rate,
            market_cap=market_cap_uk,
            foreign_ratio=foreign_ratio,
            per=per,
            pbr=pbr,
            eps=eps,
            bps=bps,
            dividend_yield=div,
        )


# ── 단독 실행 테스트 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from datetime import date
    from unittest.mock import MagicMock

    import pandas as pd

    from config.config import Settings as _Settings, configure_logging

    configure_logging("DEBUG")

    print("=" * 60)
    print("[KrxCollector] Mock 테스트")
    print("=" * 60)

    def _make_mock_adapter() -> MagicMock:
        """PyKRX API를 흉내내는 Mock 어댑터를 생성한다."""
        adapter = MagicMock(spec=KrxAdapter)

        # 종목 코드 목록
        adapter.get_market_ticker_list.return_value = ["089030", "005930"]

        # 종목명 조회
        def _get_name(ticker: str) -> str:
            return {"089030": "테크윙", "005930": "삼성전자"}.get(ticker, "")
        adapter.get_market_ticker_name.side_effect = _get_name

        # OHLCV 데이터프레임 Mock
        idx = pd.to_datetime(["2024-12-27", "2024-12-30"])
        ohlcv_df = pd.DataFrame({
            "시가": [24500.0, 25000.0],
            "고가": [25200.0, 25800.0],
            "저가": [24300.0, 24800.0],
            "종가": [25000.0, 25500.0],
            "거래량": [1_200_000, 980_000],
            "등락률": [1.5, 2.0],
        }, index=idx)
        adapter.get_market_ohlcv_by_date.return_value = ohlcv_df

        # 펀더멘털 데이터프레임 Mock
        fundamental_df = pd.DataFrame({
            "PER": [18.5, 19.0],
            "PBR": [2.1, 2.2],
            "EPS": [1350.0, 1380.0],
        }, index=idx)
        adapter.get_market_fundamental_by_date.return_value = fundamental_df

        # 시가총액 데이터프레임 Mock
        cap_df = pd.DataFrame({
            "시가총액": [2_500_000_000_000, 2_600_000_000_000],  # 2.5조, 2.6조
            "외국인한도소진률": [35.2, 35.5],
        }, index=idx)
        adapter.get_market_cap_by_date.return_value = cap_df

        return adapter

    mock_settings = _Settings(
        dart_api_key="mock",
        cache_ttl_seconds=0,
    )

    mock_adapter = _make_mock_adapter()
    collector = KrxCollector(settings=mock_settings, krx_adapter=mock_adapter)

    try:
        price = collector.get_price_data("테크윙", trade_date=date(2024, 12, 30))
        print(f"\n[SUCCESS] 수집 성공!")
        print(f"  기업명       : {price.company_name}")
        print(f"  종목코드     : {price.stock_code}")
        print(f"  거래일       : {price.trade_date}")
        print(f"  종가         : {price.close_price:,.0f} 원")
        print(f"  시가         : {price.open_price:,.0f} 원")
        print(f"  등락률       : {price.change_rate}%")
        print(f"  시가총액     : {price.market_cap:,.0f} 억원")
        print(f"  PER / PBR    : {price.per} / {price.pbr}")
        print(f"  거래량       : {price.volume:,} 주")
        print(f"  외국인비율   : {price.foreign_ratio}%")
    except Exception as e:
        print(f"\n[ERROR] 오류: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)

    print("\n[KrxCollector] Mock 테스트 완료")
