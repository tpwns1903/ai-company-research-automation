"""
streamlit_app.py
기업 분석 자동화 파이프라인 - Streamlit 웹 대시보드 UI
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import streamlit as st

# 프로젝트 루트를 sys.path에 추가하여 모듈 임포트가 가능하게 함
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config import configure_logging, get_settings
from llm.report_generator import resolve_llm_backend
from main import run_pipeline
from utils.formatters import format_confidence_10, format_won_uk

logger = logging.getLogger(__name__)

# 페이지 설정
st.set_page_config(
    page_title="기업 리서치 자동화 대시보드",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


def init_app() -> None:
    """앱 초기화 및 설정 로드."""
    if "settings" not in st.session_state:
        st.session_state.settings = get_settings()
        configure_logging(st.session_state.settings.log_level)


def render_sidebar() -> tuple[str, bool, bool]:
    """사이드바 렌더링 및 입력값 반환."""
    with st.sidebar:
        st.header("⚙️ 분석 설정")
        
        company_name = st.text_input("🏢 기업명 입력", placeholder="예: 테크윙, 삼성전자")
        
        st.subheader("파일 출력 옵션")
        export_excel = st.checkbox("Excel 리포트 생성", value=True)
        export_pdf = st.checkbox("PDF 리포트 생성", value=True)
        
        st.markdown("---")
        backend = resolve_llm_backend(st.session_state.settings)
        if backend.provider:
            st.caption(f"LLM: `{backend.provider} / {backend.model}`")
        else:
            st.caption(f"LLM: Fallback 모드 ({backend.unavailable_reason})")
        st.caption(f"캐시 기간: `{st.session_state.settings.cache_ttl_seconds}`초")
        
    return company_name.strip(), export_excel, export_pdf


def render_financials(fm: Any) -> None:
    """재무 지표를 렌더링한다."""
    if not fm:
        st.info("재무 데이터가 없습니다.")
        return
        
    st.markdown(f"**회계연도**: {fm.fiscal_year or 'N/A'} (기준일: {fm.base_date or 'N/A'})")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("매출액", format_won_uk(fm.revenue),
                  f"{fm.revenue_yoy:+.1f}% YoY" if fm.revenue_yoy else None)
    with col2:
        st.metric("영업이익", format_won_uk(fm.operating_income),
                  f"{fm.operating_income_yoy:+.1f}% YoY" if fm.operating_income_yoy else None)
    with col3:
        st.metric("당기순이익", format_won_uk(fm.net_income))
    with col4:
        st.metric("영업이익률", f"{fm.operating_margin:.1f}%" if fm.operating_margin else "N/A")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("ROE", f"{fm.roe:.1f}%" if fm.roe else "N/A")
    with col2:
        st.metric("PER", f"{fm.per:.1f}배" if fm.per else "N/A")
    with col3:
        st.metric("PBR", f"{fm.pbr:.1f}배" if fm.pbr else "N/A")
    with col4:
        st.metric("시가총액", format_won_uk(fm.market_cap))


def render_report_pdf_button(rpt: Any) -> None:
    """개별 증권사 리포트의 원문 PDF 다운로드 버튼(또는 원문 링크)을 렌더링한다."""
    if rpt.pdf_path and Path(rpt.pdf_path).exists():
        with open(rpt.pdf_path, "rb") as f:
            st.download_button(
                label="📄 원문 PDF 다운로드",
                data=f.read(),
                file_name=Path(rpt.pdf_path).name,
                mime="application/pdf",
                key=f"pdf_dl_{rpt.report_id}",
            )
    elif rpt.url:
        st.markdown(f"[🔗 원문 링크 열기]({rpt.url})")
    else:
        st.caption("원문 PDF/링크 없음")


def render_consensus_and_points(r: Any) -> None:
    """컨센서스 및 투자 포인트를 렌더링한다."""
    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("📊 증권사 컨센서스")
        if r.consensus and r.source_reports:
            c = r.consensus
            st.metric("투자의견", c.dominant_opinion.value)
            st.write(f"**매수/중립/매도**: {c.buy_count} / {c.hold_count} / {c.sell_count} (증권사 {c.brokerage_count}곳 기준, 수집 리포트 {c.total_report_count}건)")
            if c.avg_target_price:
                st.write(f"**평균 목표주가**: {c.avg_target_price:,.0f}원")
                if c.min_target_price and c.max_target_price:
                    st.caption(f"범위: {c.min_target_price:,.0f} ~ {c.max_target_price:,.0f}원")

            st.markdown("---")
            st.caption("개별 리포트 원문")
            for rpt in r.source_reports[:10]:
                pub = rpt.published_at.strftime("%Y-%m-%d") if rpt.published_at else "N/A"
                st.markdown(f"**[{rpt.brokerage}] {rpt.title}**")
                st.caption(f"{pub} | {rpt.opinion.value} | 출처: {rpt.source.value}")
                render_report_pdf_button(rpt)
                st.markdown("&nbsp;", unsafe_allow_html=True)
        else:
            st.info("수집된 증권사 리포트가 없습니다.")

    with col2:
        st.subheader("💡 핵심 투자 포인트")
        if r.investment_points:
            for i, pt in enumerate(r.investment_points[:5], 1):
                st.markdown(f"**{i}. {pt.point}**")
                st.caption(f"신뢰도: {format_confidence_10(pt.confidence_score)} | 출처 수: {pt.source_count}건 ({', '.join(pt.source_names)})")
        else:
            st.info("추출된 투자 포인트가 없습니다.")
            
        st.subheader("⚠️ 리스크 요인")
        if r.risk_factors:
            for rf in r.risk_factors[:3]:
                st.markdown(f"- {rf}")
        else:
            st.info("명시된 리스크 요인이 없습니다.")


def render_news_sentiment(r: Any) -> None:
    """뉴스 감성 및 목록을 렌더링한다."""
    st.subheader("📰 뉴스 감성 요약")
    if r.sentiment_summary:
        s = r.sentiment_summary
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("전체 감성", s.overall_sentiment.value)
        with col2:
            st.metric("긍정 기사", f"{s.positive_count}건")
        with col3:
            st.metric("부정 기사", f"{s.negative_count}건")
        with col4:
            st.metric("중립 기사", f"{s.neutral_count}건")
            
        if s.top_keywords:
            st.write(f"**주요 키워드**: {', '.join(s.top_keywords[:10])}")
    else:
        st.info("뉴스 감성 데이터가 없습니다.")
        
    st.markdown("---")
    st.subheader("최근 주요 뉴스")
    if r.source_news:
        for n in r.source_news[:10]:
            icon = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(
                n.sentiment.value if n.sentiment else "neutral", "⚪"
            )
            pub = n.published_at.strftime("%Y-%m-%d") if n.published_at else "N/A"
            st.markdown(f"{icon} **[{n.title}]({n.url})** - {n.source} ({pub})")
    else:
        st.info("수집된 뉴스가 없습니다.")


def render_report_view(report_md: str) -> None:
    """Markdown 리포트를 렌더링한다."""
    if report_md:
        st.markdown(report_md)
    else:
        st.error("리포트가 생성되지 않았습니다.")


def get_download_button(file_path: str | None, label: str, file_name: str, mime: str) -> None:
    """파일 다운로드 버튼을 렌더링한다."""
    if not file_path:
        st.button(f"📄 {label} 다운로드 (실패)", disabled=True)
        return
        
    path = Path(file_path)
    if path.exists():
        with open(path, "rb") as f:
            st.download_button(
                label=f"📄 {label} 다운로드",
                data=f.read(),
                file_name=path.name,
                mime=mime,
            )
    else:
        st.button(f"📄 {label} 다운로드 (파일 없음)", disabled=True)


def main() -> None:
    init_app()
    
    st.title("📈 AI 기반 기업 리서치 자동화")
    st.markdown(
        "기업명을 입력하면 DART 재무 데이터, 증권사 리포트, 뉴스 기사를 자동으로 수집 및 분석하여 "
        "투자자 관점의 리포트를 생성합니다."
    )
    
    company_name, export_excel, export_pdf = render_sidebar()
    
    if st.sidebar.button("🚀 리서치 시작", type="primary", use_container_width=True):
        if not company_name:
            st.error("기업명을 입력해주세요.")
            return
            
        # 세션 상태 초기화
        st.session_state.result = None
        
        with st.spinner(f"'{company_name}' 데이터를 수집하고 분석하는 중입니다. (약 1~2분 소요)"):
            try:
                result = run_pipeline(
                    company_name=company_name,
                    settings=st.session_state.settings,
                    export_excel=export_excel,
                    export_pdf=export_pdf,
                )
                st.session_state.result = result
                st.success("✅ 분석이 완료되었습니다!")
            except Exception as e:
                st.error(f"❌ 분석 중 오류가 발생했습니다: {e}")
                logger.exception("파이프라인 실행 중 예외 발생")
                return

    # 결과가 있으면 렌더링
    if "result" in st.session_state and st.session_state.result:
        result = st.session_state.result
        r = result.get("research_result")
        
        if not r:
            st.error("분석 결과를 불러오지 못했습니다.")
            return
            
        # 메타 정보
        st.markdown(
            f"### 분석 대상: {r.company_name} "
            f"{'(' + r.stock_code + ')' if r.stock_code else ''} | "
            f"신뢰도 점수: **{format_confidence_10(r.overall_confidence_score)}**"
        )
        
        if r.data_quality_notes:
            with st.expander("⚠️ 데이터 품질 주의사항", expanded=True):
                for note in r.data_quality_notes:
                    st.warning(note)

        # 탭 구성
        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "📝 AI 리포트", "💰 재무 지표", "💡 투자 포인트", "📰 뉴스 분석", "💾 다운로드"
        ])
        
        with tab1:
            render_report_view(result.get("report_markdown", ""))
            
        with tab2:
            render_financials(r.financial_metrics)
            
        with tab3:
            render_consensus_and_points(r)
            
        with tab4:
            render_news_sentiment(r)
            
        with tab5:
            st.subheader("파일 다운로드")
            st.markdown("생성된 리포트 파일을 다운로드할 수 있습니다.")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                get_download_button(
                    result.get("markdown_path"), 
                    "Markdown 리포트", 
                    "report.md", 
                    "text/markdown"
                )
            with col2:
                get_download_button(
                    result.get("excel_path"), 
                    "Excel 리포트", 
                    "report.xlsx", 
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            with col3:
                get_download_button(
                    result.get("pdf_path"), 
                    "PDF 리포트", 
                    "report.pdf", 
                    "application/pdf"
                )


if __name__ == "__main__":
    main()
