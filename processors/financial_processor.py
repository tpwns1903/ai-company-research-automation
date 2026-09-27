"""
financial_processor.py
재무 데이터 정규화 프로세서 — 성장률/수익성/밸류에이션 지표 계산 및 단위 정규화
"""

from __future__ import annotations

import logging
from typing import Optional

from models.financial_metrics import FinancialMetrics, PriceMetrics

logger = logging.getLogger(__name__)


class FinancialProcessor:
    """수집된 재무 원시 데이터를 정규화하고 파생 지표를 계산하는 Processor.

    계산 항목:
        - 매출액 / 영업이익 YoY 성장률 (%)
        - 영업이익률 및 순이익률 (%)
        - ROE, ROA, 부채비율 (%)
        - PriceMetrics 병합을 통한 PER, PBR, EPS, BPS, 시가총액 정규화
    """

    def __init__(self) -> None:
        """FinancialProcessor를 초기화한다."""
        logger.debug("FinancialProcessor 초기화 완료")

    def process(
        self,
        current: FinancialMetrics,
        previous: Optional[FinancialMetrics] = None,
        price_metrics: Optional[PriceMetrics] = None,
    ) -> FinancialMetrics:
        """현재 기간 재무 데이터, 전년도 데이터, 시세 데이터를 통합하여 파생 지표를 계산한다.

        Args:
            current: 현재 회계연도 재무 지표.
            previous: 전년도 회계연도 재무 지표 (YoY 계산용, 옵션).
            price_metrics: 최근 시장 시세 지표 (PER/PBR/시가총액 병합용, 옵션).

        Returns:
            FinancialMetrics: 파생 지표가 통합 계산된 정규화된 재무 모델.
        """
        data = current.model_dump()

        # 1. 수익성 지표 계산 (영업이익률, 순이익률, ROE, ROA)
        revenue = data.get("revenue")
        op_income = data.get("operating_income")
        net_income = data.get("net_income")
        total_assets = data.get("total_assets")
        total_equity = data.get("total_equity")

        if revenue and revenue > 0:
            if op_income is not None:
                data["operating_margin"] = self.calculate_operating_margin(op_income, revenue)
            if net_income is not None:
                data["net_margin"] = round((net_income / revenue) * 100, 2)

        if total_equity and total_equity > 0 and net_income is not None:
            data["roe"] = round((net_income / total_equity) * 100, 2)

        if total_assets and total_assets > 0 and net_income is not None:
            data["roa"] = round((net_income / total_assets) * 100, 2)

        # 2. YoY 성장률 계산 (전년도 데이터 존재 시)
        if previous:
            if current.revenue is not None and previous.revenue is not None:
                try:
                    data["revenue_yoy"] = self.calculate_growth_rate(current.revenue, previous.revenue)
                except ValueError as exc:
                    logger.debug("매출 YoY 계산 불가: %s", exc)

            if current.operating_income is not None and previous.operating_income is not None:
                try:
                    data["operating_income_yoy"] = self.calculate_growth_rate(
                        current.operating_income, previous.operating_income
                    )
                except ValueError as exc:
                    logger.debug("영업이익 YoY 계산 불가: %s", exc)

        # 3. 주가 및 밸류에이션 데이터 병합 (PriceMetrics 존재 시)
        if price_metrics:
            if price_metrics.close_price is not None:
                data["closing_price"] = price_metrics.close_price
            if price_metrics.market_cap is not None:
                data["market_cap"] = price_metrics.market_cap
            if price_metrics.per is not None:
                data["per"] = price_metrics.per
            if price_metrics.pbr is not None:
                data["pbr"] = price_metrics.pbr
            if price_metrics.eps is not None:
                data["eps"] = price_metrics.eps
            if price_metrics.bps is not None:
                data["bps"] = price_metrics.bps
            if price_metrics.dividend_yield is not None:
                data["dividend_yield"] = price_metrics.dividend_yield

        logger.info(
            "[FinancialProcessor] 정규화 완료: %s (%d) - 매출: %s, YoY: %s%%",
            data.get("company_name"),
            data.get("fiscal_year"),
            data.get("revenue"),
            data.get("revenue_yoy", "N/A"),
        )

        return FinancialMetrics.model_validate(data)

    def calculate_growth_rate(
        self, current_value: float, previous_value: float
    ) -> float:
        """YoY 성장률(%)을 계산한다.

        Args:
            current_value: 당기 실적.
            previous_value: 전기(전년도) 실적.

        Returns:
            float: 백분율 성장률 (소수점 2자리).

        Raises:
            ValueError: previous_value가 0인 경우.
        """
        if previous_value == 0:
            raise ValueError("전기(전년도) 값이 0이므로 성장률을 계산할 수 없습니다.")

        # 전기가 음수에서 당기 양수 전환 등 특수 상황 처리
        if previous_value < 0:
            rate = ((current_value - previous_value) / abs(previous_value)) * 100
        else:
            rate = ((current_value - previous_value) / previous_value) * 100

        return round(rate, 2)

    def calculate_operating_margin(
        self, operating_income: float, revenue: float
    ) -> float:
        """영업이익률(%)을 계산한다.

        Args:
            operating_income: 영업이익 (억 원).
            revenue: 매출액 (억 원).

        Returns:
            float: 영업이익률 (소수점 2자리).

        Raises:
            ValueError: 매출액이 0 이하인 경우.
        """
        if revenue <= 0:
            raise ValueError("매출액은 0보다 커야 합니다.")
        return round((operating_income / revenue) * 100, 2)

    def normalize_units(
        self, value: float, source_unit: str = "원", target_unit: str = "억원"
    ) -> float:
        """금액 단위를 목표 단위(기본: 억 원)로 변환한다.

        Args:
            value: 변환할 원본 수치.
            source_unit: 원본 단위 ('원', '천원', '백만원', '억원').
            target_unit: 목표 단위 ('원', '천원', '백만원', '억원').

        Returns:
            float: 단위 변환된 금액.
        """
        unit_factors = {
            "원": 1.0,
            "천원": 1_000.0,
            "백만원": 1_000_000.0,
            "억원": 100_000_000.0,
        }

        src_factor = unit_factors.get(source_unit, 1.0)
        tgt_factor = unit_factors.get(target_unit, 100_000_000.0)

        won_value = value * src_factor
        converted = won_value / tgt_factor
        return round(converted, 4)


# ── 단독 실행 테스트 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from datetime import date
    from config.config import configure_logging

    configure_logging("DEBUG")

    print("=" * 60)
    print("[FinancialProcessor] 단독 테스트")
    print("=" * 60)

    # 1. 2023년 및 2024년 테스트용 재무 데이터 준비
    metrics_2023 = FinancialMetrics(
        company_name="테크윙",
        stock_code="089030",
        fiscal_year=2023,
        base_date=date(2023, 12, 31),
        revenue=2400.0,           # 2400억
        operating_income=300.0,   # 300억
        net_income=220.0,         # 220억
        total_assets=4500.0,
        total_equity=2800.0,
    )

    metrics_2024 = FinancialMetrics(
        company_name="테크윙",
        stock_code="089030",
        fiscal_year=2024,
        base_date=date(2024, 12, 31),
        revenue=3200.0,           # 3200억 (매출 성장)
        operating_income=480.0,   # 480억 (이익 성장)
        net_income=380.0,
        total_assets=5000.0,
        total_equity=3100.0,
    )

    price_data = PriceMetrics(
        company_name="테크윙",
        stock_code="089030",
        trade_date=date(2024, 12, 30),
        close_price=25500.0,
        market_cap=26000.0,       # 2.6조
        per=19.0,
        pbr=2.2,
        eps=1380.0,
    )

    processor = FinancialProcessor()

    try:
        processed = processor.process(
            current=metrics_2024,
            previous=metrics_2023,
            price_metrics=price_data,
        )

        print("[SUCCESS] 정규화 결과:")
        print(f"  - 기업명        : {processed.company_name} ({processed.stock_code})")
        print(f"  - 회계연도      : {processed.fiscal_year}년")
        print(f"  - 매출액        : {processed.revenue} 억원 (YoY: {processed.revenue_yoy}%)")
        print(f"  - 영업이익      : {processed.operating_income} 억원 (YoY: {processed.operating_income_yoy}%)")
        print(f"  - 영업이익률    : {processed.operating_margin}%")
        print(f"  - ROE / ROA     : {processed.roe}% / {processed.roa}%")
        print(f"  - 시가총액 / PER: {processed.market_cap} 억원 / {processed.per}배")
    except Exception as e:
        print(f"[ERROR] 처리 중 오류 발생: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n[FinancialProcessor] 테스트 완료")
