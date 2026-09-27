"""
excel_exporter.py
리서치 결과를 Excel 파일로 내보내는 모듈

openpyxl 기반으로 시트를 구성한다:
  - Sheet1 (요약)       : 기업 개요, 컨센서스, 신뢰도
  - Sheet2 (재무지표)   : FinancialMetrics 전체 필드
  - Sheet3 (투자포인트) : InvestmentPoint 목록 + Confidence Score
  - Sheet4 (뉴스)       : source_news 목록
  - Sheet5 (리포트)     : source_reports 목록
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from config.config import Settings, get_settings
from models.research_result import ResearchResult
from utils.formatters import format_won_uk

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

# 헤더 배경색 (RGB hex)
_HEADER_FILL = "1F4E79"   # 진청색
_HEADER_FONT = "FFFFFF"   # 흰색
_ACCENT_FILL = "D6E4F0"   # 연한 청색 (홀수행)


class ExcelExporter:
    """리서치 결과(ResearchResult)를 다중 시트 Excel 파일로 내보내는 클래스.

    openpyxl 패키지가 설치되어 있어야 한다 (pip install openpyxl).
    패키지 미설치 시 ImportError를 발생시킨다.

    Example:
        >>> exporter = ExcelExporter()
        >>> path = exporter.export(research_result)
        >>> print(path)
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        """ExcelExporter를 초기화한다.

        Args:
            settings: 애플리케이션 설정. None이면 .env에서 자동 로드.
        """
        self._settings: Settings = settings or get_settings()
        self._output_dir: Path = self._settings.output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("ExcelExporter 초기화 완료 (출력 경로: %s)", self._output_dir)

    def export(self, research_result: ResearchResult) -> Path:
        """리서치 결과를 다중 시트 Excel 파일로 저장한다.

        Args:
            research_result: 내보낼 리서치 분석 결과.

        Returns:
            Path: 저장된 .xlsx 파일의 절대 경로.

        Raises:
            ImportError: openpyxl 패키지가 설치되지 않은 경우.
        """
        try:
            import openpyxl
            from openpyxl.styles import Alignment, Font, PatternFill
            from openpyxl.utils import get_column_letter
        except ImportError as exc:
            raise ImportError(
                "openpyxl이 설치되지 않았습니다. 'pip install openpyxl'을 실행하세요."
            ) from exc

        wb = openpyxl.Workbook()
        wb.remove(wb.active)  # 기본 시트 제거

        # 시트별 작성
        self._write_summary_sheet(wb, research_result)
        self._write_financial_sheet(wb, research_result)
        self._write_investment_points_sheet(wb, research_result)
        self._write_news_sheet(wb, research_result)
        self._write_reports_sheet(wb, research_result)

        # 파일 저장
        timestamp = datetime.now(_KST).strftime("%Y%m%d_%H%M%S")
        safe_name = research_result.company_name.replace(" ", "_").replace("/", "-")
        file_path = self._output_dir / f"{safe_name}_report_{timestamp}.xlsx"
        wb.save(str(file_path))

        logger.info(
            "[ExcelExporter] Excel 저장 완료: %s (%d 시트)",
            file_path, len(wb.sheetnames)
        )
        return file_path

    # ── 시트 작성 메서드 ──────────────────────────────────────────────────────

    def _write_summary_sheet(self, wb: Any, r: ResearchResult) -> None:
        """요약 시트 작성."""
        from openpyxl.styles import Alignment, Font, PatternFill

        ws = wb.create_sheet("요약")
        ws.column_dimensions["A"].width = 28
        ws.column_dimensions["B"].width = 40

        rows: list[tuple[str, Any]] = [
            ("분석 기업", r.company_name),
            ("종목 코드", r.stock_code or "N/A"),
            ("분석 기준일", r.analysis_date.strftime("%Y-%m-%d %H:%M") if r.analysis_date else "N/A"),
            ("전체 신뢰도 점수", f"{r.overall_confidence_score:.2f} / 1.00"),
            ("", ""),
        ]

        # 컨센서스
        if r.consensus:
            c = r.consensus
            rows += [
                ("컨센서스 투자의견", c.dominant_opinion.value),
                ("매수 / 중립 / 매도", f"{c.buy_count} / {c.hold_count} / {c.sell_count}"),
                ("집계 증권사 수 (증권사별 최신 리포트)", c.brokerage_count),
                ("총 리포트 수", c.total_report_count),
                ("평균 목표주가", f"{c.avg_target_price:,.0f}원" if c.avg_target_price else "N/A"),
                ("목표주가 범위", f"{c.min_target_price:,.0f} ~ {c.max_target_price:,.0f}원"
                 if c.min_target_price and c.max_target_price else "N/A"),
                ("", ""),
            ]
        else:
            rows.append(("증권사 컨센서스", "수집된 리포트 없음"))
            rows.append(("", ""))

        # 뉴스 감성
        if r.sentiment_summary:
            s = r.sentiment_summary
            rows += [
                ("뉴스 전체 감성", s.overall_sentiment.value),
                ("긍정 / 부정 / 중립 기사 수",
                 f"{s.positive_count} / {s.negative_count} / {s.neutral_count}"),
                ("주요 키워드", ", ".join(s.top_keywords[:8])),
                ("", ""),
            ]

        # 데이터 품질 주의사항
        if r.data_quality_notes:
            rows.append(("데이터 품질 주의사항", " | ".join(r.data_quality_notes)))

        self._write_kv_rows(ws, rows)

    def _write_financial_sheet(self, wb: Any, r: ResearchResult) -> None:
        """재무지표 시트 작성."""
        ws = wb.create_sheet("재무지표")
        ws.column_dimensions["A"].width = 26
        ws.column_dimensions["B"].width = 20

        fm = r.financial_metrics
        if not fm:
            ws.cell(1, 1, "재무 데이터 없음")
            return

        rows: list[tuple[str, Any]] = [
            ("기업명", fm.company_name),
            ("종목 코드", fm.stock_code or "N/A"),
            ("회계연도", fm.fiscal_year or "N/A"),
            ("기준일", str(fm.base_date) if fm.base_date else "N/A"),
            ("", ""),
            ("매출액", format_won_uk(fm.revenue)),
            ("영업이익", format_won_uk(fm.operating_income)),
            ("당기순이익", format_won_uk(fm.net_income)),
            ("자산총계", format_won_uk(fm.total_assets)),
            ("자본총계", format_won_uk(fm.total_equity)),
            ("부채총계", format_won_uk(fm.total_liabilities)),
            ("", ""),
            ("매출 성장률 YoY (%)", fm.revenue_yoy),
            ("영업이익 성장률 YoY (%)", fm.operating_income_yoy),
            ("영업이익률 (%)", fm.operating_margin),
            ("순이익률 (%)", fm.net_margin),
            ("ROE (%)", fm.roe),
            ("ROA (%)", fm.roa),
            ("부채비율 (%)", fm.debt_to_equity),
            ("", ""),
            ("PER (배)", fm.per),
            ("PBR (배)", fm.pbr),
            ("EPS (원)", fm.eps),
            ("BPS (원)", fm.bps),
            ("배당수익률 (%)", fm.dividend_yield),
            ("시가총액", format_won_uk(fm.market_cap)),
            ("현재주가 (원)", fm.closing_price),
        ]
        self._write_kv_rows(ws, rows)

    def _write_investment_points_sheet(self, wb: Any, r: ResearchResult) -> None:
        """투자포인트 시트 작성."""
        from openpyxl.styles import Font, PatternFill, Alignment

        ws = wb.create_sheet("투자포인트")
        headers = ["순위", "투자 포인트", "신뢰도 점수", "출처 수", "출처 목록", "최신성 가중치", "방향 일치도"]
        col_widths = [8, 60, 14, 10, 40, 16, 14]
        self._write_header_row(ws, headers, col_widths)

        if not r.investment_points:
            ws.cell(2, 1, "추출된 투자 포인트 없음")
            return

        for i, pt in enumerate(r.investment_points, 1):
            row = [
                i,
                pt.point,
                f"{pt.confidence_score:.2f}",
                pt.source_count,
                ", ".join(pt.source_names),
                f"{pt.recency_weight:.2f}",
                f"{pt.direction_consistency:.2f}",
            ]
            for col, val in enumerate(row, 1):
                cell = ws.cell(i + 1, col, val)
                if i % 2 == 0:
                    from openpyxl.styles import PatternFill
                    cell.fill = PatternFill("solid", fgColor=_ACCENT_FILL)

    def _write_news_sheet(self, wb: Any, r: ResearchResult) -> None:
        """뉴스 기사 시트 작성."""
        ws = wb.create_sheet("뉴스")
        headers = ["번호", "제목", "출처", "발행일", "감성", "감성 점수", "주요 키워드", "관련도"]
        col_widths = [6, 60, 16, 14, 10, 12, 30, 10]
        self._write_header_row(ws, headers, col_widths)

        if not r.source_news:
            ws.cell(2, 1, "수집된 뉴스 없음")
            return

        for i, n in enumerate(r.source_news, 1):
            pub = n.published_at.strftime("%Y-%m-%d") if n.published_at else "N/A"
            row = [
                i,
                n.title,
                n.source,
                pub,
                n.sentiment.value if n.sentiment else "N/A",
                f"{n.sentiment_score:.2f}" if n.sentiment_score is not None else "N/A",
                ", ".join(n.keywords[:5]),
                f"{n.relevance_score:.2f}" if n.relevance_score is not None else "N/A",
            ]
            for col, val in enumerate(row, 1):
                cell = ws.cell(i + 1, col, val)
                if i % 2 == 0:
                    from openpyxl.styles import PatternFill
                    cell.fill = PatternFill("solid", fgColor=_ACCENT_FILL)

    def _write_reports_sheet(self, wb: Any, r: ResearchResult) -> None:
        """증권사 리포트 시트 작성."""
        ws = wb.create_sheet("증권사리포트")
        headers = ["번호", "증권사", "제목", "투자의견", "목표주가(원)", "발행일", "출처", "요약"]
        col_widths = [6, 16, 55, 14, 14, 14, 16, 50]
        self._write_header_row(ws, headers, col_widths)

        if not r.source_reports:
            ws.cell(2, 1, "수집된 증권사 리포트 없음")
            return

        for i, rpt in enumerate(r.source_reports, 1):
            pub = rpt.published_at.strftime("%Y-%m-%d") if rpt.published_at else "N/A"
            tp = f"{rpt.target_price:,.0f}" if rpt.target_price else "N/A"
            row = [
                i,
                rpt.brokerage,
                rpt.title,
                rpt.opinion.value,
                tp,
                pub,
                rpt.source.value,
                (rpt.summary or "")[:100],
            ]
            for col, val in enumerate(row, 1):
                cell = ws.cell(i + 1, col, val)
                cell.alignment = __import__(
                    "openpyxl.styles", fromlist=["Alignment"]
                ).Alignment(wrap_text=True)
                if i % 2 == 0:
                    from openpyxl.styles import PatternFill
                    cell.fill = PatternFill("solid", fgColor=_ACCENT_FILL)

    # ── 공통 유틸 ─────────────────────────────────────────────────────────────

    def _write_kv_rows(self, ws: Any, rows: list[tuple[str, Any]]) -> None:
        """키-값 쌍 행 목록을 시트에 작성한다."""
        from openpyxl.styles import Alignment, Font, PatternFill

        for row_idx, (key, value) in enumerate(rows, 1):
            if not key:
                continue
            key_cell = ws.cell(row_idx, 1, key)
            key_cell.font = Font(bold=True)
            key_cell.alignment = Alignment(vertical="center")

            val_cell = ws.cell(row_idx, 2, value if value is not None else "N/A")
            val_cell.alignment = Alignment(wrap_text=True, vertical="center")

    def _write_header_row(
        self, ws: Any, headers: list[str], col_widths: list[int]
    ) -> None:
        """헤더 행을 스타일과 함께 시트에 작성한다."""
        from openpyxl.styles import Alignment, Font, PatternFill

        for col, (header, width) in enumerate(zip(headers, col_widths), 1):
            cell = ws.cell(1, col, header)
            cell.font = Font(bold=True, color=_HEADER_FONT)
            cell.fill = PatternFill("solid", fgColor=_HEADER_FILL)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.column_dimensions[
                __import__("openpyxl.utils", fromlist=["get_column_letter"]).get_column_letter(col)
            ].width = width
        ws.row_dimensions[1].height = 20


# ── 단독 실행 테스트 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from datetime import date, timedelta
    from config.config import Settings as _Settings, configure_logging
    from models.financial_metrics import FinancialMetrics
    from models.news_item import NewsItem, SentimentType
    from models.report_item import InvestmentOpinion, ReportItem, ReportSource
    from models.research_result import (
        ConsensusOpinion, InvestmentPoint, ResearchResult, SentimentSummary,
    )

    configure_logging("DEBUG")
    _kst = ZoneInfo("Asia/Seoul")
    _now = datetime.now(_kst)

    sample = ResearchResult(
        company_name="테크윙",
        stock_code="089030",
        analysis_date=_now,
        overall_confidence_score=0.75,
        financial_metrics=FinancialMetrics(
            company_name="테크윙", stock_code="089030",
            fiscal_year=2024, base_date=date(2024, 12, 31),
            revenue=3200.0, operating_income=480.0, net_income=380.0,
            operating_margin=15.0, roe=12.3, per=19.0, pbr=2.2,
            market_cap=26000.0,
        ),
        consensus=ConsensusOpinion(
            dominant_opinion=InvestmentOpinion.BUY,
            buy_count=2, hold_count=0, sell_count=0,
            total_report_count=2, avg_target_price=36500.0,
            max_target_price=38000.0, min_target_price=35000.0,
        ),
        sentiment_summary=SentimentSummary(
            overall_sentiment=SentimentType.POSITIVE,
            positive_count=3, negative_count=1, neutral_count=1,
            avg_sentiment_score=0.55,
            top_keywords=["HBM", "테스터", "실적"],
        ),
        investment_points=[
            InvestmentPoint(
                point="HBM 테스터 시장 독점 공급 지위",
                source_count=3, source_names=["키움증권", "삼성증권", "한국경제"],
                recency_weight=1.0, direction_consistency=1.0, confidence_score=0.85,
            ),
        ],
        risk_factors=["단기 수요 불확실성"],
        source_news=[
            NewsItem(
                news_id="n1", source="한국경제",
                title="테크윙, 3분기 어닝서프라이즈",
                published_at=_now - timedelta(days=2),
                company_name="테크윙",
                sentiment=SentimentType.POSITIVE, sentiment_score=0.8,
                keywords=["HBM", "실적"],
            ),
        ],
        source_reports=[
            ReportItem(
                report_id="r1", source=ReportSource.NAVER_FINANCE,
                brokerage="키움증권",
                title="테크윙 - HBM 사이클 최대 수혜",
                company_name="테크윙",
                opinion=InvestmentOpinion.BUY, target_price=38000.0,
                published_at=_now - timedelta(days=7),
                investment_points=["HBM 점유율 급상승"],
            ),
        ],
    )

    print("=" * 60)
    print("[ExcelExporter] 단독 테스트")
    print("=" * 60)

    exporter = ExcelExporter(_Settings(dart_api_key="mock"))
    try:
        path = exporter.export(sample)
        print(f"[SUCCESS] Excel 파일 생성 완료: {path}")
    except ImportError as e:
        print(f"[SKIP] openpyxl 미설치로 테스트 건너뜀: {e}")
    except Exception as e:
        print(f"[ERROR] 오류 발생: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)

    print("\n[ExcelExporter] 테스트 완료")
