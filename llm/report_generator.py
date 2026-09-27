"""
report_generator.py
LLM 기반 Markdown 리포트 생성기

ResearchResult(JSON)를 받아 OpenAI API(또는 호환 LLM)를 통해
투자자 관점의 Markdown 리포트를 생성한다.

특징:
  - API 키는 config.py/Settings에서만 로드 (하드코딩 금지)
  - 재시도 로직 내장 (max_retry_attempts 설정 준수)
  - LLM API 실패 시 앱 전체가 죽지 않도록 에러 메시지 반환
  - 증권사 리포트 0건이어도 재무/뉴스로 리포트 생성 + 사실 명시
  - API 키 미설정 시 Fallback: 프롬프트 기반 로컬 리포트 자동 생성
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from config.config import Settings, get_settings
from llm.prompts.analysis_prompt import (
    SYSTEM_PROMPT,
    build_analysis_prompt,
)
from models.research_result import ResearchResult
from utils.formatters import format_confidence_10, format_won_uk

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")


class ReportGenerator:
    """ResearchResult를 입력받아 LLM으로 Markdown 리포트를 생성하는 클래스.

    입력 : ResearchResult (재무 지표 + 투자포인트 + Confidence Score + 뉴스 요약)
    출력 : Markdown 형식의 기업 분석 리포트 문자열

    LLM 백엔드:
        - OpenAI API (기본): openai 패키지 사용. config.openai_api_key 필요.
        - API 키 미설정 시: 수집된 데이터를 구조화하여 Fallback Markdown 리포트 반환.

    재시도 로직:
        - LLM 호출 실패 시 settings.max_retry_attempts 횟수만큼 재시도.
        - 재시도 간 settings.retry_delay_seconds × attempt 초 대기 (지수 백오프).
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        """ReportGenerator를 초기화한다.

        Args:
            settings: 애플리케이션 설정. None이면 .env에서 자동 로드.
        """
        self._settings: Settings = settings or get_settings()
        self._api_key: str = self._settings.openai_api_key
        self._model: str = self._settings.llm_model
        self._max_tokens: int = self._settings.llm_max_tokens
        self._temperature: float = self._settings.llm_temperature

        self._use_llm: bool = bool(self._api_key)
        self._client: Optional[Any] = None

        if self._use_llm:
            self._client = self._init_openai_client()
        else:
            logger.info(
                "[ReportGenerator] OPENAI_API_KEY 미설정 - Fallback 리포트 생성 모드로 동작합니다."
            )

        logger.debug(
            "ReportGenerator 초기화 완료 (모델: %s, LLM사용: %s)",
            self._model, self._use_llm,
        )

    def _init_openai_client(self) -> Optional[Any]:
        """OpenAI 클라이언트를 초기화한다."""
        try:
            from openai import OpenAI  # type: ignore[import]
            client = OpenAI(api_key=self._api_key)
            logger.debug("OpenAI 클라이언트 초기화 완료")
            return client
        except ImportError:
            logger.warning(
                "openai 패키지가 설치되지 않았습니다. 'pip install openai'를 실행하세요. "
                "Fallback 모드로 동작합니다."
            )
            self._use_llm = False
            return None

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    def generate(self, research_result: ResearchResult) -> str:
        """ResearchResult를 기반으로 Markdown 리포트를 생성한다.

        LLM API를 사용할 수 없는 경우에도 Fallback 리포트를 반환하며,
        앱 전체가 종료되지 않도록 예외를 내부에서 처리한다.

        Args:
            research_result: 리서치 엔진의 종합 분석 결과.

        Returns:
            str: Markdown 형식의 기업 분석 리포트. 실패 시 에러 내용이 포함된 Markdown 반환.
        """
        company_name = research_result.company_name
        logger.info("[ReportGenerator] 리포트 생성 시작: %s", company_name)

        result_dict = research_result.model_dump(mode="json")
        user_prompt = build_analysis_prompt(result_dict)

        if self._use_llm and self._client:
            try:
                report_md = self._call_llm_with_retry(SYSTEM_PROMPT, user_prompt)
                logger.info("[ReportGenerator] LLM 리포트 생성 완료: %s", company_name)
                return self._wrap_with_header(report_md, research_result)
            except Exception as exc:
                logger.error(
                    "[ReportGenerator] LLM 리포트 생성 최종 실패: %s - %s", company_name, exc
                )
                # 앱 전체가 죽지 않도록 에러 메시지를 Markdown으로 반환
                fallback = self._generate_fallback_report(research_result)
                return fallback + f"\n\n> **[시스템 오류]** LLM API 호출 실패: `{exc}`\n"
        else:
            # API 키 없음 → Fallback
            report_md = self._generate_fallback_report(research_result)
            logger.info("[ReportGenerator] Fallback 리포트 생성 완료: %s", company_name)
            return report_md

    def save(self, markdown: str, company_name: str) -> Path:
        """생성된 Markdown 리포트를 파일로 저장한다.

        Args:
            markdown: 저장할 Markdown 문자열.
            company_name: 기업명 (파일명에 사용).

        Returns:
            Path: 저장된 파일 경로.
        """
        output_dir: Path = self._settings.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(_KST).strftime("%Y%m%d_%H%M%S")
        safe_name = company_name.replace(" ", "_").replace("/", "-")
        file_path = output_dir / f"{safe_name}_{timestamp}.md"

        file_path.write_text(markdown, encoding="utf-8")
        logger.info("[ReportGenerator] 리포트 저장 완료: %s", file_path)
        return file_path

    # ── LLM 호출 (재시도 포함) ────────────────────────────────────────────────

    def _call_llm_with_retry(self, system_prompt: str, user_prompt: str) -> str:
        """재시도 로직(지수 백오프)을 적용하여 LLM API를 호출한다.

        Args:
            system_prompt: 시스템 역할 지시 프롬프트.
            user_prompt: 분석 데이터가 포함된 사용자 프롬프트.

        Returns:
            str: LLM 응답 텍스트.

        Raises:
            RuntimeError: 최대 재시도 횟수 초과 시.
        """
        max_attempts = self._settings.max_retry_attempts
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                result = self._call_llm(system_prompt, user_prompt)
                if attempt > 1:
                    logger.info("[ReportGenerator] LLM 재시도 성공 (%d회)", attempt)
                return result

            except Exception as exc:
                last_exc = exc
                wait = self._settings.retry_delay_seconds * attempt
                logger.warning(
                    "[ReportGenerator] LLM 호출 실패 (%d/%d): %s - %.1f초 후 재시도",
                    attempt, max_attempts, exc, wait,
                )
                if attempt < max_attempts:
                    time.sleep(wait)

        raise RuntimeError(
            f"LLM API 최대 재시도({max_attempts}회) 초과"
        ) from last_exc

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """OpenAI Chat Completions API를 호출하여 응답 텍스트를 반환한다.

        Args:
            system_prompt: 시스템 역할 지시 프롬프트.
            user_prompt: 분석 데이터가 포함된 사용자 프롬프트.

        Returns:
            str: LLM 응답 텍스트.

        Raises:
            Exception: API 호출 실패 시 (재시도 로직이 처리).
        """
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("LLM이 빈 응답을 반환했습니다.")
        return content.strip()

    def _count_tokens(self, text: str) -> int:
        """텍스트의 토큰 수를 추정한다.

        tiktoken 패키지가 설치된 경우 정확한 값을, 아닌 경우 글자 수 기반 추정값을 반환한다.

        Args:
            text: 토큰 수를 계산할 텍스트.

        Returns:
            int: 토큰 수 (추정치).
        """
        try:
            import tiktoken  # type: ignore[import]
            enc = tiktoken.encoding_for_model(self._model)
            return len(enc.encode(text))
        except Exception:
            # tiktoken 미설치 시 간이 추정 (한글 1자 ≈ 2토큰, 영문 1자 ≈ 0.3토큰)
            return len(text) // 2

    # ── Fallback 리포트 생성 (LLM 없이) ──────────────────────────────────────

    def _generate_fallback_report(self, result: ResearchResult) -> str:
        """LLM 없이 수집 데이터만으로 구조화된 Markdown 리포트를 생성한다.

        Args:
            result: 리서치 엔진 분석 결과.

        Returns:
            str: Fallback Markdown 리포트.
        """
        now = datetime.now(_KST).strftime("%Y-%m-%d %H:%M")
        company = result.company_name
        ticker = f"({result.stock_code})" if result.stock_code else ""

        lines: list[str] = [
            f"# {company} {ticker} 기업 분석 리포트",
            f"\n> 분석 기준일: {now} | 데이터 신뢰도: {format_confidence_10(result.overall_confidence_score)}",
            "> **[Fallback 모드]** LLM API 미연동 - 수집 데이터 기반 구조화 리포트입니다.\n",
        ]

        if result.data_quality_notes:
            lines.append("> **데이터 품질 주의사항**")
            for note in result.data_quality_notes:
                lines.append(f"> - {note}")
            lines.append("")

        # 기업 개요 (간단)
        lines.append("---\n")
        lines.append("## 기업 개요\n")
        if result.financial_metrics:
            fm = result.financial_metrics
            lines.append(
                f"{company}은 {fm.fiscal_year or 'N/A'}년 기준 "
                f"매출 {format_won_uk(fm.revenue)}, 영업이익 {format_won_uk(fm.operating_income)}을 기록한 기업입니다."
            )
        else:
            lines.append(f"{company}에 대한 재무 데이터가 수집되지 않았습니다.")
        lines.append("")

        # 재무 분석
        lines.append("---\n")
        lines.append("## 재무 분석\n")
        if result.financial_metrics:
            fm = result.financial_metrics
            lines.append("| 항목 | 값 |")
            lines.append("|------|-----|")
            _row = lambda k, v, u="": f"| {k} | {v}{u} |" if v is not None else f"| {k} | N/A |"
            lines.append(_row("매출액", format_won_uk(fm.revenue)))
            lines.append(_row("영업이익", format_won_uk(fm.operating_income)))
            lines.append(_row("영업이익률", fm.operating_margin, "%"))
            lines.append(_row("ROE", fm.roe, "%"))
            lines.append(_row("PER", fm.per, "배"))
            lines.append(_row("시가총액", format_won_uk(fm.market_cap)))
            if hasattr(fm, "revenue_yoy"):
                lines.append(_row("매출 성장률(YoY)", fm.revenue_yoy, "%"))
        else:
            lines.append("재무 지표 데이터 없음\n")
        lines.append("")

        # 증권사 컨센서스
        lines.append("---\n")
        lines.append("## 증권사 컨센서스\n")
        if result.consensus and result.source_reports:
            c = result.consensus
            lines.append(f"- 대표 의견: **{c.dominant_opinion.value}**")
            lines.append(f"- 의견 분포: 매수 {c.buy_count}건 / 중립 {c.hold_count}건 / 매도 {c.sell_count}건")
            if c.avg_target_price:
                lines.append(f"- 평균 목표주가: **{c.avg_target_price:,.0f}원**")
        else:
            lines.append("> **참고 가능한 증권사 리포트 없음** — 재무 데이터 및 뉴스 기반으로만 분석합니다.")
        lines.append("")

        # 핵심 투자 포인트
        lines.append("---\n")
        lines.append("## 핵심 투자 포인트\n")
        if result.investment_points:
            for i, pt in enumerate(result.investment_points[:3], 1):
                lines.append(f"**[{i}]** {pt.point}")
                lines.append(
                    f"  - 신뢰도: `{format_confidence_10(pt.confidence_score)}` | "
                    f"출처: {', '.join(pt.source_names) if pt.source_names else '미상'}"
                )
                lines.append("")
        else:
            lines.append("교차 확인된 투자 포인트 없음 (데이터 부족)\n")

        # 리스크 요인
        lines.append("---\n")
        lines.append("## 리스크 요인\n")
        if result.risk_factors:
            for rf in result.risk_factors[:3]:
                lines.append(f"- {rf}")
        else:
            lines.append("명시된 리스크 요인 없음\n")
        lines.append("")

        # 뉴스 감성
        lines.append("---\n")
        lines.append("## 뉴스 감성 요약\n")
        if result.sentiment_summary:
            s = result.sentiment_summary
            label = {"positive": "긍정", "negative": "부정", "neutral": "중립"}.get(
                s.overall_sentiment.value, s.overall_sentiment.value
            )
            lines.append(f"- 전체 감성: **{label}**")
            lines.append(f"- 기사 분포: 긍정 {s.positive_count}건 / 부정 {s.negative_count}건 / 중립 {s.neutral_count}건")
            if s.top_keywords:
                lines.append(f"- 주요 키워드: {', '.join(s.top_keywords[:8])}")
        else:
            lines.append("뉴스 감성 데이터 없음\n")

        if result.source_news:
            lines.append("\n### 최근 주요 뉴스\n")
            for n in result.source_news[:5]:
                icon = {"positive": "[+]", "negative": "[-]", "neutral": "[=]"}.get(
                    n.sentiment.value if n.sentiment else "neutral", "[=]"
                )
                pub = n.published_at.strftime("%Y-%m-%d") if n.published_at else ""
                lines.append(f"- {icon} **{n.title}** ({n.source}, {pub})")
        lines.append("")

        # 결론
        lines.append("---\n")
        lines.append("## 결론 및 투자자 관점 요약\n")
        confidence = result.overall_confidence_score
        if confidence >= 0.7:
            data_quality = "다양한 출처에서 충분한 데이터가 수집되었으며 분석 신뢰도가 높습니다."
        elif confidence >= 0.4:
            data_quality = "일부 데이터가 제한적이므로 추가적인 자료 수집 후 재검토를 권장합니다."
        else:
            data_quality = "수집된 데이터가 제한적이어서 분석 신뢰도가 낮습니다. 직접 확인이 필요합니다."

        lines.append(
            f"{company}에 대한 분석 데이터 신뢰도는 **{format_confidence_10(confidence)}**이며, "
            f"{data_quality}"
        )
        lines.append("\n*본 리포트는 자동 수집된 데이터 기반의 참고용 분석이며, 투자 추천이 아닙니다.*")

        return "\n".join(lines)

    def _wrap_with_header(self, markdown: str, result: ResearchResult) -> str:
        """LLM이 생성한 Markdown 본문에 파일 헤더를 추가한다."""
        now = datetime.now(_KST).strftime("%Y-%m-%d %H:%M")
        company = result.company_name
        ticker = f"({result.stock_code})" if result.stock_code else ""
        header = (
            f"# {company} {ticker} 기업 분석 리포트\n\n"
            f"> 분석 기준일: {now} | "
            f"데이터 신뢰도: {format_confidence_10(result.overall_confidence_score)} | "
            f"생성 엔진: {self._model}\n\n"
        )
        return header + markdown


# ── Any 임포트 (하위 타입힌트용) ─────────────────────────────────────────────

from typing import Any


# ── 단독 실행 테스트 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from datetime import date, timedelta
    from zoneinfo import ZoneInfo

    from config.config import Settings as _Settings, configure_logging
    from models.financial_metrics import FinancialMetrics
    from models.news_item import NewsItem, SentimentType
    from models.report_item import InvestmentOpinion, ReportItem, ReportSource
    from models.research_result import (
        ConsensusOpinion,
        InvestmentPoint,
        ResearchResult,
        SentimentSummary,
    )

    configure_logging("DEBUG")
    _kst = ZoneInfo("Asia/Seoul")
    _now = datetime.now(_kst)

    print("=" * 60)
    print("[ReportGenerator] 단독 테스트")
    print("=" * 60)

    # 샘플 ResearchResult 생성
    sample_result = ResearchResult(
        company_name="테크윙",
        stock_code="089030",
        analysis_date=_now,
        overall_confidence_score=0.72,
        data_quality_notes=["증권사 리포트 수가 적습니다 (2건)."],
        financial_metrics=FinancialMetrics(
            company_name="테크윙",
            stock_code="089030",
            fiscal_year=2024,
            base_date=date(2024, 12, 31),
            revenue=3200.0,
            operating_income=480.0,
            net_income=380.0,
            operating_margin=15.0,
            roe=12.3,
            roa=7.6,
            per=19.0,
            pbr=2.2,
            market_cap=26000.0,
        ),
        consensus=ConsensusOpinion(
            dominant_opinion=InvestmentOpinion.BUY,
            buy_count=2,
            hold_count=0,
            sell_count=0,
            total_report_count=2,
            avg_target_price=36500.0,
            max_target_price=38000.0,
            min_target_price=35000.0,
        ),
        sentiment_summary=SentimentSummary(
            overall_sentiment=SentimentType.POSITIVE,
            positive_count=3,
            negative_count=1,
            neutral_count=1,
            avg_sentiment_score=0.55,
            top_keywords=["HBM", "테스터", "실적", "수주", "성장"],
        ),
        investment_points=[
            InvestmentPoint(
                point="HBM 테스터 시장에서 독점적 공급 지위 확보",
                source_count=3,
                source_names=["키움증권", "삼성증권", "한국경제"],
                recency_weight=1.0,
                direction_consistency=1.0,
                confidence_score=0.85,
            ),
            InvestmentPoint(
                point="2025년 영업이익 사상 최대 전망",
                source_count=2,
                source_names=["키움증권", "매일경제"],
                recency_weight=0.9,
                direction_consistency=1.0,
                confidence_score=0.72,
            ),
        ],
        risk_factors=["단기적 수요 불확실성 및 고객사 집중 리스크", "글로벌 반도체 업황 변동성"],
        source_reports=[
            ReportItem(
                report_id="r1",
                source=ReportSource.NAVER_FINANCE,
                brokerage="키움증권",
                title="테크윙 - HBM 사이클 최대 수혜주",
                company_name="테크윙",
                opinion=InvestmentOpinion.BUY,
                target_price=38000.0,
                published_at=_now - timedelta(days=7),
                investment_points=["HBM 테스터 시장 점유율 급상승", "2025년 사상 최대 실적 전망"],
            ),
        ],
        source_news=[
            NewsItem(
                news_id="n1",
                source="한국경제",
                title="테크윙, 3분기 영업이익 급증 어닝서프라이즈",
                published_at=_now - timedelta(days=2),
                company_name="테크윙",
                sentiment=SentimentType.POSITIVE,
                sentiment_score=0.8,
                keywords=["HBM", "실적"],
            ),
            NewsItem(
                news_id="n2",
                source="서울경제",
                title="반도체 테스터 업황 둔화 우려 일부 존재",
                published_at=_now - timedelta(days=15),
                company_name="테크윙",
                sentiment=SentimentType.NEGATIVE,
                sentiment_score=-0.5,
                keywords=["업황", "우려"],
            ),
        ],
    )

    # ── 테스트 1: Fallback 리포트 (API 키 없이) ────────────────────────────────
    print("\n[테스트 1] Fallback 리포트 생성 (API 키 없이)\n")
    settings_no_key = _Settings(dart_api_key="mock", openai_api_key="")
    generator = ReportGenerator(settings=settings_no_key)

    try:
        report_md = generator.generate(sample_result)
        print(f"[SUCCESS] Fallback 리포트 생성 완료 ({len(report_md)} chars)")
        print("\n" + "-" * 60)
        print(report_md[:1200])
        print("..." if len(report_md) > 1200 else "")
    except Exception as e:
        print(f"[ERROR] Fallback 리포트 생성 실패: {e}", file=sys.stderr)
        sys.exit(1)

    # ── 테스트 2: 저장 기능 ────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("[테스트 2] Markdown 파일 저장 테스트\n")
    try:
        saved_path = generator.save(report_md, "테크윙")
        print(f"[SUCCESS] 파일 저장 완료: {saved_path}")
    except Exception as e:
        print(f"[ERROR] 파일 저장 실패: {e}", file=sys.stderr)
        sys.exit(1)

    # ── 테스트 3: 리포트 0건 처리 확인 ────────────────────────────────────────
    print("\n" + "=" * 60)
    print("[테스트 3] 리포트 0건 + 뉴스 0건 -> 빈 데이터 Fallback\n")
    empty_result = ResearchResult(
        company_name="테크윙",
        overall_confidence_score=0.2,
        data_quality_notes=["증권사 리포트 없음", "뉴스 없음"],
        financial_metrics=sample_result.financial_metrics,
    )
    try:
        empty_report = generator.generate(empty_result)
        print(f"[SUCCESS] 빈 데이터 Fallback 처리 완료 ({len(empty_report)} chars)")
        # 증권사 리포트 없음 문구 확인
        assert "참고 가능한 증권사 리포트 없음" in empty_report, "필수 문구 누락!"
        print("[CHECK] '참고 가능한 증권사 리포트 없음' 문구 포함 확인 완료")
    except AssertionError as e:
        print(f"[ERROR] 필수 문구 누락: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] 예외 발생: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n[ReportGenerator] 테스트 완료")
