"""
main.py
기업 분석 자동화 파이프라인 — 메인 진입점

실행 순서:
  1단계. 데이터 수집 (DartCollector, KrxCollector, NewsCollector,
                        TelegramCollector, NaverFinanceCollector)
  2단계. 데이터 처리 (FinancialProcessor, TextProcessor)
  3단계. 리서치 엔진 (ResearchEngine)
  4단계. AI 리포트 생성 (ReportGenerator)
  5단계. 파일 출력 (MarkdownExporter, ExcelExporter, PdfExporter)
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

from config.config import configure_logging, get_settings, Settings

logger = logging.getLogger(__name__)


def run_pipeline(
    company_name: str,
    settings: Optional[Settings] = None,
    *,
    export_excel: bool = True,
    export_pdf: bool = True,
) -> dict:
    """전체 기업 분석 파이프라인을 실행한다.

    Args:
        company_name: 분석 대상 기업명 (예: '테크윙').
        settings: 애플리케이션 설정. None이면 .env에서 자동 로드.
        export_excel: Excel 리포트 출력 여부 (기본 True).
        export_pdf: PDF 리포트 출력 여부 (기본 True).

    Returns:
        dict: 결과 요약 딕셔너리
            - research_result: ResearchResult 객체
            - markdown_path: 저장된 Markdown 파일 경로 (str)
            - excel_path: 저장된 Excel 파일 경로 (str 또는 None)
            - pdf_path: 저장된 PDF 파일 경로 (str 또는 None)
            - report_markdown: 생성된 Markdown 문자열
    """
    cfg = settings or get_settings()
    result: dict = {
        "research_result": None,
        "markdown_path": None,
        "excel_path": None,
        "pdf_path": None,
        "report_markdown": None,
    }

    logger.info("=" * 50)
    logger.info("기업 분석 시작: %s", company_name)
    logger.info("=" * 50)

    # ── 1단계: 데이터 수집 ────────────────────────────────────────────────────
    logger.info("[1단계] 데이터 수집 시작")

    from collectors.dart_collector import DartCollector
    from collectors.krx_collector import KrxCollector
    from collectors.news_collector import NewsCollector
    from collectors.telegram_collector import TelegramCollector
    from collectors.naver_finance_collector import NaverFinanceCollector

    dart_collector = DartCollector(settings=cfg)
    krx_collector = KrxCollector(settings=cfg)
    news_collector = NewsCollector(settings=cfg)
    telegram_collector = TelegramCollector(settings=cfg)
    naver_collector = NaverFinanceCollector(settings=cfg)

    # 재무 데이터 수집 (DART)
    raw_financials = None
    try:
        raw_financials = dart_collector.get_financials(company_name)
        logger.info("[1단계] DART 재무 수집 완료: 매출 %s 억원", raw_financials.revenue)
    except Exception as exc:
        logger.warning("[1단계] DART 수집 실패 (계속 진행): %s", exc)

    # 주가 데이터 수집 (KRX)
    price_data = None
    try:
        price_data = krx_collector.get_price_data(company_name)
        logger.info("[1단계] KRX 시세 수집 완료: 종가 %s원", price_data.close_price)
    except Exception as exc:
        logger.warning("[1단계] KRX 수집 실패 (계속 진행): %s", exc)

    # 뉴스 수집
    news_items = []
    try:
        news_items = news_collector.get_recent_news(company_name)
        logger.info("[1단계] 뉴스 수집 완료: %d건", len(news_items))
    except Exception as exc:
        logger.warning("[1단계] 뉴스 수집 실패 (계속 진행): %s", exc)

    # 증권사 리포트 수집 (텔레그램 + 네이버금융)
    reports = []
    try:
        tg_reports = telegram_collector.get_reports(company_name)
        reports.extend(tg_reports)
        logger.info("[1단계] 텔레그램 리포트 수집: %d건", len(tg_reports))
    except Exception as exc:
        logger.warning("[1단계] 텔레그램 수집 실패 (계속 진행): %s", exc)

    try:
        naver_reports = naver_collector.get_reports(company_name)
        reports.extend(naver_reports)
        logger.info("[1단계] 네이버금융 리포트 수집: %d건", len(naver_reports))
    except Exception as exc:
        logger.warning("[1단계] 네이버금융 수집 실패 (계속 진행): %s", exc)

    logger.info(
        "[1단계] 수집 완료 — 재무: %s, 뉴스: %d건, 리포트: %d건",
        "OK" if raw_financials else "없음", len(news_items), len(reports),
    )

    # ── 2단계: 데이터 처리 ────────────────────────────────────────────────────
    logger.info("[2단계] 데이터 처리 시작")

    from processors.financial_processor import FinancialProcessor

    financial_processor = FinancialProcessor()
    processed_financials = None

    if raw_financials:
        try:
            processed_financials = financial_processor.process(
                current=raw_financials,
                price_metrics=price_data,
            )
            logger.info(
                "[2단계] 재무 처리 완료: YoY 매출 성장률 %s%%",
                processed_financials.revenue_yoy,
            )
        except Exception as exc:
            logger.warning("[2단계] 재무 처리 실패 (계속 진행): %s", exc)
            processed_financials = raw_financials

    logger.info("[2단계] 처리 완료")

    # ── 3단계: 리서치 엔진 ───────────────────────────────────────────────────
    logger.info("[3단계] 리서치 엔진 분석 시작")

    from research.research_engine import ResearchEngine

    engine = ResearchEngine()
    try:
        research_result = engine.analyze(
            company_name=company_name,
            financial_metrics=processed_financials,
            reports=reports,
            news_items=news_items,
        )
        result["research_result"] = research_result
        logger.info(
            "[3단계] 리서치 엔진 완료: 신뢰도 %.2f, 투자포인트 %d개",
            research_result.overall_confidence_score,
            len(research_result.investment_points),
        )
    except Exception as exc:
        logger.error("[3단계] 리서치 엔진 오류: %s", exc)
        raise RuntimeError(f"리서치 엔진 실패: {exc}") from exc

    # ── 4단계: LLM 리포트 생성 ───────────────────────────────────────────────
    logger.info("[4단계] LLM 리포트 생성 시작")

    from llm.report_generator import ReportGenerator

    generator = ReportGenerator(settings=cfg)
    try:
        report_markdown = generator.generate(research_result)
        result["report_markdown"] = report_markdown
        logger.info("[4단계] 리포트 생성 완료 (%d chars)", len(report_markdown))
    except Exception as exc:
        logger.error("[4단계] 리포트 생성 오류: %s", exc)
        report_markdown = f"# 리포트 생성 실패\n\n오류: `{exc}`"
        result["report_markdown"] = report_markdown

    # ── 5단계: 파일 출력 ─────────────────────────────────────────────────────
    logger.info("[5단계] 파일 출력 시작")

    from output.markdown_exporter import MarkdownExporter
    from output.excel_exporter import ExcelExporter
    from output.pdf_exporter import PdfExporter

    # Markdown 저장 (항상)
    try:
        md_exporter = MarkdownExporter(settings=cfg)
        md_path = md_exporter.export(report_markdown, company_name)
        result["markdown_path"] = str(md_path)
        logger.info("[5단계] Markdown 저장: %s", md_path)
    except Exception as exc:
        logger.error("[5단계] Markdown 저장 실패: %s", exc, exc_info=True)

    # Excel 저장 (선택)
    if export_excel:
        try:
            xl_exporter = ExcelExporter(settings=cfg)
            xl_path = xl_exporter.export(research_result)
            result["excel_path"] = str(xl_path)
            logger.info("[5단계] Excel 저장: %s", xl_path)
        except ImportError:
            logger.warning("[5단계] openpyxl 미설치 - Excel 저장 건너뜀")
        except Exception as exc:
            logger.error("[5단계] Excel 저장 실패: %s", exc, exc_info=True)

    # PDF 저장 (선택)
    if export_pdf:
        try:
            pdf_exporter = PdfExporter(settings=cfg)
            pdf_path = pdf_exporter.export(report_markdown, company_name)
            result["pdf_path"] = str(pdf_path)
            logger.info("[5단계] PDF 저장: %s", pdf_path)
        except Exception as exc:
            logger.error("[5단계] PDF 저장 실패: %s", exc, exc_info=True)

    logger.info("=" * 50)
    logger.info("기업 분석 완료: %s", company_name)
    logger.info("  - Markdown : %s", result["markdown_path"] or "저장 실패")
    logger.info("  - Excel    : %s", result["excel_path"] or "없음/실패")
    logger.info("  - PDF      : %s", result["pdf_path"] or "없음/실패")
    logger.info("=" * 50)

    return result


def main() -> None:
    """CLI 진입점. 기업명을 입력받아 전체 파이프라인을 실행한다."""
    cfg = get_settings()
    configure_logging(cfg.log_level)

    # CLI 인수 우선, 없으면 대화형 입력
    if len(sys.argv) > 1:
        company_name = " ".join(sys.argv[1:]).strip()
    else:
        try:
            company_name = input("분석할 기업명을 입력하세요: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n입력 취소됨.")
            return

    if not company_name:
        logger.error("기업명이 입력되지 않았습니다.")
        sys.exit(1)

    try:
        result = run_pipeline(company_name)
        print("\n[완료] 분석 결과:")
        print(f"  Markdown : {result['markdown_path']}")
        print(f"  Excel    : {result['excel_path'] or '없음'}")
        print(f"  PDF      : {result['pdf_path'] or '없음'}")
    except Exception as exc:
        logger.error("파이프라인 실행 오류: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
