"""
telegram_collector.py
텔레그램 공개 채널에서 증권사 리포트를 수집하는 Collector

특징:
  - 공개 채널 및 메시지 수집
  - 최근 3개월 이내 자료만 수집
  - 기업명 키워드 필터링
  - PDF 파일 첨부 시 텍스트 파싱 및 중복 제거
  - 투자의견, 목표주가, 증권사명 정규식 파싱
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Protocol
from zoneinfo import ZoneInfo

from collectors.base_collector import BaseCollector, CollectorError
from config.config import Settings
from models.report_item import InvestmentOpinion, ReportItem, ReportSource
from utils.company_matcher import is_company_match

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

# 증권사명 추출 정규식
_BROKERAGE_PATTERN = re.compile(
    r"(키움|삼성|NH투자|NH|미래에셋|한국투자|한투|KB|하나|신한|유진|대신|메리츠|한화|현대차|DB|교보|이베스트|LS|하이|상상인|IBK|BNK|신영|유안타)"
    r"(?:증권|투자증권)?"
)

# 목표주가 추출 정규식
_TARGET_PRICE_PATTERN = re.compile(
    r"(?:목표주가|목표가|TP|Target\s*Price)[\s:]*([0-9,]+)\s*원?",
    re.IGNORECASE,
)

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


# ── Telegram Adapter Protocol (Mock 주입용) ────────────────────────────────────

class TelegramAdapter(Protocol):
    """Telethon API 호출을 추상화하는 프로토콜."""

    def fetch_messages(
        self,
        channel: str,
        company_name: str,
        cutoff_date: datetime,
        max_messages: int = 2000,
    ) -> list[dict[str, Any]]:
        """채널에서 최신 메시지부터 역순으로 순회하며 cutoff_date 이내 메시지를 가져온다."""
        ...


class _NullTelegramAdapter:
    """실제 API 연동 환경 전 기본 어댑터 (Telethon 미설치 시)."""

    def fetch_messages(
        self,
        channel: str,
        company_name: str,
        cutoff_date: datetime,
        max_messages: int = 2000,
    ) -> list[dict[str, Any]]:
        logger.debug("[Telegram] Telethon API 미연동 상태 — 빈 메시지 반환")
        return []


class TelethonAdapter:
    """Telethon 클라이언트로 실제 텔레그램 공개 채널 메시지를 가져오는 어댑터.

    연결/로그인은 최초 ``fetch_messages()`` 호출 시점에 지연 수행한다(Lazy).
    최초 로그인 시 텔레그램이 본인 계정으로 보내는 인증 코드를 1회 입력해야 하며,
    성공하면 세션 파일(``session_path`` + ``.session``)에 저장되어 이후엔 재인증 없이 재사용된다.
    """

    def __init__(self, api_id: str, api_hash: str, phone: str, session_path: str) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._phone = phone
        self._session_path = session_path
        self._client: Any = None

    def _connect(self) -> Any:
        if self._client is not None:
            return self._client

        try:
            from telethon.sync import TelegramClient
        except ImportError as exc:
            raise CollectorError(
                "[Telegram] telethon 패키지가 설치되어 있지 않음 — pip install telethon"
            ) from exc

        client = TelegramClient(self._session_path, int(self._api_id), self._api_hash)
        client.start(phone=self._phone)
        logger.info("[Telegram] Telethon 로그인 성공 (session=%s)", self._session_path)
        self._client = client
        return client

    def fetch_messages(
        self,
        channel: str,
        company_name: str,
        cutoff_date: datetime,
        max_messages: int = 2000,
    ) -> list[dict[str, Any]]:
        """최신 메시지부터 역순으로 순회하며 cutoff_date 이내 메시지를 수집한다.

        건수 고정(limit) 방식 대신 발행일 기준으로 순회를 멈춘다. 메시지 날짜가
        cutoff_date보다 오래되면 그 시점에서 즉시 중단하고, 무한 순회를 막기 위해
        max_messages 건을 넘으면 cutoff에 도달하지 못했어도 중단한다.

        PDF는 메시지 텍스트에 기업명이 이미 매칭된 경우에만 다운로드한다
        (매칭되지 않는 메시지의 PDF까지 무조건 받아오는 비효율을 방지).
        """
        client = self._connect()
        messages: list[dict[str, Any]] = []
        scanned = 0

        for msg in client.iter_messages(channel):
            scanned += 1

            if scanned > max_messages:
                logger.warning(
                    "[Telegram] %s 채널 안전 상한(%d건) 도달 — lookback 기간 도달 전 스캔 중단",
                    channel, max_messages,
                )
                break

            if msg.date < cutoff_date:
                logger.debug(
                    "[Telegram] %s 채널 lookback 기간(%s) 이전 메시지 도달 — 스캔 중단 (%d건 스캔)",
                    channel, cutoff_date, scanned,
                )
                break

            if scanned % 200 == 0:
                logger.info(
                    "[Telegram] %s 채널 %d건째 스캔 중... (매칭 %d건)",
                    channel, scanned, len(messages),
                )

            text = msg.message or ""
            pdf_bytes: Optional[bytes] = None

            # 기업명이 텍스트에 정확히 매칭된 경우에만 PDF 다운로드
            if (
                is_company_match(company_name, text)
                and msg.document
                and getattr(msg.document, "mime_type", "") == "application/pdf"
            ):
                try:
                    pdf_bytes = client.download_media(msg, file=bytes)
                except Exception as exc:
                    logger.warning(
                        "[Telegram] PDF 다운로드 실패 (channel=%s, msg_id=%s): %s",
                        channel, msg.id, exc,
                    )

            messages.append({
                "text": text,
                "pdf_bytes": pdf_bytes,
                "published_at": msg.date,
                "url": f"https://t.me/{channel.lstrip('@')}/{msg.id}",
            })

        logger.info(
            "[Telegram] %s 채널 스캔 완료: %d건 스캔, %d건 수신", channel, scanned, len(messages)
        )
        return messages


# ── PDF 텍스트 추출 Helper ───────────────────────────────────────────────────

def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """PDF 바이트 데이터에서 텍스트를 추출한다.

    PyPDF2, pypdf, pdfplumber 순서로 시도하며, 라이브러리가 없으면 빈 문자열을 반환한다.
    """
    if not pdf_bytes:
        return ""

    # 1. pypdf / PyPDF2 시도
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages_text)
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("pypdf 추출 실패: %s", exc)

    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages_text)
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("PyPDF2 추출 실패: %s", exc)

    # 2. pdfplumber 시도
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_text = [page.extract_text() or "" for page in pdf.pages]
            return "\n".join(pages_text)
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("pdfplumber 추출 실패: %s", exc)

    return ""


# ── TelegramCollector ─────────────────────────────────────────────────────────

class TelegramCollector(BaseCollector):
    """텔레그램 공개 채널에서 증권사 리포트를 수집하는 Collector.

    수집 항목:
        - 공개방 메시지 및 첨부 파일 (PDF) 텍스트
        - 목표주가, 투자의견, 증권사명
        - 발행일 기준 최근 3개월 이내 자료만 선별 및 중복 제거

    Mock 지원:
        생성자의 ``adapter`` 파라미터에 ``TelegramAdapter`` 구현체를 전달할 수 있다.

    Example:
        >>> collector = TelegramCollector()
        >>> reports = collector.get_reports("테크윙", days=90)
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        adapter: Optional[TelegramAdapter] = None,
    ) -> None:
        """TelegramCollector를 초기화한다.

        Args:
            settings: 애플리케이션 설정. None이면 .env에서 자동 로드.
            adapter: Telegram API 어댑터 (테스트 시 Mock 주입용).
        """
        super().__init__(settings)
        self._api_id = self._settings.telegram_api_id
        self._api_hash = self._settings.telegram_api_hash
        self._phone = self._settings.telegram_phone
        self._use_mock = not (bool(self._api_id) and bool(self._api_hash))
        self._pdf_dir: Path = self._settings.report_pdf_dir
        self._pdf_dir.mkdir(parents=True, exist_ok=True)

        if adapter is not None:
            self._adapter: TelegramAdapter = adapter
        elif self._use_mock:
            self._adapter = _NullTelegramAdapter()
            logger.info(
                "[Telegram] API 인증 정보 미설정 - Mock 데이터를 반환합니다."
            )
        else:
            session_path = str(self._settings.cache_dir.parent / "telegram_session")
            self._adapter = TelethonAdapter(self._api_id, self._api_hash, self._phone, session_path)
            logger.info(
                "[Telegram] Telethon 어댑터 활성화 (실제 연동, 최초 호출 시 로그인 시도 — session=%s)",
                session_path,
            )
        logger.debug("TelegramCollector 준비 완료")

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    def collect(self, company_name: str, **kwargs: Any) -> list[ReportItem]:
        """기업명을 입력받아 텔레그램 증권사 리포트를 수집한다.

        Args:
            company_name: 수집 대상 기업명.
            **kwargs:
                - days (int): 수집 기간 (일). 기본값 settings.report_lookback_days (90일).

        Returns:
            list[ReportItem]: 수집 및 중복 제거된 리포트 목록.
        """
        days: int = kwargs.get("days", self._settings.report_lookback_days)
        return self.get_reports(company_name, days=days)

    def get_reports(
        self, company_name: str, days: Optional[int] = None
    ) -> list[ReportItem]:
        """최근 N일(기본 90일) 이내 기업 관련 텔레그램 리포트를 반환한다.

        검색 결과가 없을 경우 예외를 발생시키지 않고 빈 리스트[]를 반환한다.

        Args:
            company_name: 기업명.
            days: 수집 기간 (일). None이면 settings.report_lookback_days (90일) 사용.

        Returns:
            list[ReportItem]: 발행일 기준 내림차순 정렬된 리포트 목록.
        """
        lookback = days or self._settings.report_lookback_days
        cache_key = self._make_cache_key("telegram_reports", company_name, lookback)

        cached = self._load_cache(cache_key)
        if cached is not None:
            logger.info("[Telegram] 캐시에서 로드: %s (%d일)", company_name, lookback)
            return [ReportItem.model_validate(item) for item in cached]

        logger.info("[Telegram] 수집 시작: %s (최근 %d일)", company_name, lookback)

        if self._use_mock and isinstance(self._adapter, _NullTelegramAdapter):
            raw_reports = self._generate_mock_reports(company_name, lookback)
        else:
            raw_reports = self._fetch_all_channels(company_name, lookback)

        if not raw_reports:
            logger.info("[Telegram] 리포트 없음: %s", company_name)
            self._save_cache(cache_key, [])
            return []

        # 최근 3개월(lookback) 이내 데이터 필터링 및 중복 제거
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=lookback)
        filtered_reports: list[ReportItem] = []
        seen_keys: set[str] = set()

        for report in raw_reports:
            # 1. 3개월 이내 체크
            pub_date = report.published_at
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)
            if pub_date < cutoff_date:
                continue

            # 2. 중복 제거 (증권사 + 제목 유일성)
            dedup_key = f"{report.brokerage}_{report.title.strip()}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            filtered_reports.append(report)

        # 발행일 내림차순 정렬
        filtered_reports.sort(key=lambda r: r.published_at, reverse=True)

        self._save_cache(cache_key, [r.model_dump(mode="json") for r in filtered_reports])
        logger.info(
            "[Telegram] 수집 완료: %s - %d건 (최근 %d일)",
            company_name, len(filtered_reports), lookback
        )
        return filtered_reports

    # ── 내부 채널 수집 ────────────────────────────────────────────────────────

    def _fetch_all_channels(
        self, company_name: str, lookback_days: int
    ) -> list[ReportItem]:
        """설정된 채널 목록에서 메시지를 수집하여 ReportItem으로 파싱한다.

        건수 고정이 아닌 lookback_days 기간 기준으로 각 채널을 스캔한다.
        """
        channels = self._settings.telegram_channels or ["@public_research_channel"]
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        max_messages = self._settings.telegram_max_scan_messages
        reports: list[ReportItem] = []

        for channel in channels:
            self._wait()
            try:
                messages = self._request_with_retry(
                    self._adapter.fetch_messages,
                    channel=channel,
                    company_name=company_name,
                    cutoff_date=cutoff_date,
                    max_messages=max_messages,
                )
                for msg in messages:
                    parsed = self._parse_message(msg, company_name)
                    if parsed:
                        reports.append(parsed)
            except Exception as exc:
                logger.warning("[Telegram] 채널 수집 중 오류 무시 (%s): %s", channel, exc)

        return reports

    def _parse_message(
        self, message: dict[str, Any], company_name: str
    ) -> Optional[ReportItem]:
        """텔레그램 메시지/파일 딕셔너리를 ReportItem 모델로 파싱한다."""
        text: str = message.get("text", "") or ""
        pdf_bytes: Optional[bytes] = message.get("pdf_bytes")

        # PDF 파일이 포함되어 있으면 텍스트 추출 시도
        pdf_text = extract_text_from_pdf_bytes(pdf_bytes) if pdf_bytes else ""
        full_text = f"{text}\n{pdf_text}".strip()

        if not full_text or not is_company_match(company_name, text):
            return None

        # 발행 시각
        pub_at = message.get("published_at")
        if not isinstance(pub_at, datetime):
            pub_at = datetime.now(_KST)

        # 증권사 파싱
        brokerage_match = _BROKERAGE_PATTERN.search(full_text)
        brokerage = f"{brokerage_match.group(1)}증권" if brokerage_match else "텔레그램 수집"

        # 목표주가 파싱
        tp_match = _TARGET_PRICE_PATTERN.search(full_text)
        target_price: Optional[float] = None
        if tp_match:
            try:
                target_price = float(tp_match.group(1).replace(",", ""))
            except ValueError:
                pass

        # 투자의견 파싱
        opinion = InvestmentOpinion.NOT_RATED
        for key, op in _OPINION_MAP.items():
            if re.search(r"\b" + re.escape(key) + r"\b", full_text, re.IGNORECASE):
                opinion = op
                break

        # 제목 및 요약 생성
        first_line = text.split("\n")[0].strip() if text else f"{company_name} 분석 리포트"
        title = first_line[:100]
        summary = full_text[:300]

        # 투자 포인트 간단 추출
        investment_points: list[str] = []
        for line in full_text.split("\n"):
            line_str = line.strip()
            if any(kw in line_str for kw in ["포인트", "수혜", "성장", "모멘텀", "실적", "전망"]):
                if len(line_str) > 10 and line_str not in investment_points:
                    investment_points.append(line_str[:150])
            if len(investment_points) >= 3:
                break

        report_id = hashlib.sha256(
            f"{brokerage}_{title}_{pub_at.isoformat()}".encode()
        ).hexdigest()[:16]

        pdf_path = self._save_pdf(pdf_bytes, f"tg_{report_id}") if pdf_bytes else None

        return ReportItem(
            report_id=f"tg_{report_id}",
            source=ReportSource.TELEGRAM,
            brokerage=brokerage,
            title=title,
            summary=summary,
            investment_points=investment_points,
            opinion=opinion,
            target_price=target_price,
            company_name=company_name,
            published_at=pub_at,
            url=message.get("url"),
            pdf_path=pdf_path,
            relevance_score=0.8,
            is_within_3_months=True,
        )

    def _save_pdf(self, pdf_bytes: bytes, file_stem: str) -> Optional[str]:
        """PDF 바이트를 로컬 파일로 저장하고 경로를 반환한다.

        저장 실패는 리포트 수집 자체를 막지 않도록 예외를 삼키고 None을 반환한다.

        Args:
            pdf_bytes: 저장할 PDF 바이너리.
            file_stem: 확장자를 제외한 파일명 (report_id 기반).

        Returns:
            저장된 파일의 절대 경로 문자열. 실패 시 None.
        """
        path = self._pdf_dir / f"{file_stem}.pdf"
        try:
            path.write_bytes(pdf_bytes)
            return str(path)
        except OSError as exc:
            logger.warning("[Telegram] PDF 저장 실패: %s — %s", path, exc)
            return None

    # ── Mock 데이터 생성 ─────────────────────────────────────────────────────

    def _generate_mock_reports(
        self, company_name: str, days: int
    ) -> list[ReportItem]:
        """개발 및 테스트용 Mock 텔레그램 리포트 데이터를 생성한다."""
        if "존재하지않는" in company_name or "none" in company_name.lower():
            logger.info("[Telegram] Mock 리포트 없음: %s", company_name)
            return []

        now = datetime.now(_KST)
        mock_list = [
            {
                "brokerage": "키움증권",
                "title": f"{company_name} - HBM 테스트 장비 공급 본격화",
                "summary": f"{company_name}은 HBM 퀄 테스트를 통과하고 주요 메모리 업체향 공급을 확대 중임. 목표주가 38,000원 상향 제시.",
                "opinion": InvestmentOpinion.BUY,
                "target_price": 38000.0,
                "days_ago": 10,
                "points": ["HBM 퀄 테스트 완료 및 양산 공급", "2025년 영업이익 전년比 +120% 전망"],
            },
            {
                "brokerage": "삼성증권",
                "title": f"{company_name} (089030) - 신규 장비 모멘텀 유효",
                "summary": f"{company_name}의 신규 핸들러 장비 도입으로 비메모리 영역 진출 기대감 고조.",
                "opinion": InvestmentOpinion.BUY,
                "target_price": 35000.0,
                "days_ago": 35,
                "points": ["비메모리 핸들러 장비 매출 반영 시작", "고객사 다변화로 이익 체질 개선"],
            },
            {
                "brokerage": "NH투자증권",
                "title": f"{company_name} 3분기 실적 리뷰 및 향후 전망",
                "summary": f"3분기 실적은 컨센서스 부합. 4분기부터 체질 개선 본격화될 것으로 예상.",
                "opinion": InvestmentOpinion.HOLD,
                "target_price": 30000.0,
                "days_ago": 70,
                "points": ["단기 주가 상승에 따른 밸류에이션 부담", "중장기 성장성은 유효"],
            },
        ]

        reports: list[ReportItem] = []
        for i, item in enumerate(mock_list):
            days_ago: int = item["days_ago"]  # type: ignore[assignment]
            if days_ago > days:
                continue

            pub_at = now - timedelta(days=days_ago)
            rep_id = hashlib.sha256(
                f"mock_tg_{i}_{company_name}".encode()
            ).hexdigest()[:16]

            reports.append(
                ReportItem(
                    report_id=f"tg_{rep_id}",
                    source=ReportSource.TELEGRAM,
                    brokerage=item["brokerage"],  # type: ignore[arg-type]
                    title=item["title"],  # type: ignore[arg-type]
                    summary=item["summary"],  # type: ignore[arg-type]
                    investment_points=item["points"],  # type: ignore[arg-type]
                    opinion=item["opinion"],  # type: ignore[arg-type]
                    target_price=item["target_price"],  # type: ignore[arg-type]
                    company_name=company_name,
                    published_at=pub_at,
                    url=f"https://t.me/public_research/{1000 + i}",
                    relevance_score=0.85,
                    is_within_3_months=(days_ago <= 90),
                )
            )

        logger.info("[Telegram] Mock 리포트 %d건 생성 완료", len(reports))
        return reports


# ── 단독 실행 테스트 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock

    from config.config import Settings as _Settings, configure_logging

    configure_logging("DEBUG")

    print("=" * 60)
    print("[TelegramCollector] 테스트 (Mock 모드)")
    print("=" * 60)

    # 1. Mock 데이터 생성 테스트
    mock_settings = _Settings(
        dart_api_key="mock",
        telegram_api_id="",
        telegram_api_hash="",
        cache_ttl_seconds=0,
    )

    collector = TelegramCollector(settings=mock_settings)

    print("\n[테스트 1] Mock 리포트 수집 (최근 90일)")
    try:
        reports = collector.get_reports("테크윙", days=90)
        print(f"  -> 수집된 리포트 수: {len(reports)}건\n")
        for idx, r in enumerate(reports, 1):
            print(f"  [{idx}] [{r.brokerage}] {r.title}")
            print(f"       발행일: {r.published_at.strftime('%Y-%m-%d')} | 투자의견: {r.opinion.value}")
            print(f"       목표주가: {r.target_price:,.0f}원" if r.target_price else "       목표주가: N/A")
            print(f"       투자포인트: {r.investment_points}")
            print()
    except Exception as e:
        print(f"\n[ERROR] 오류: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)

    # 2. 리포트 없을 때 테스트 (빈 리스트 반환 확인)
    print("=" * 60)
    print("[테스트 2] 없는 기업 조회 시 빈 리스트 반환 테스트")
    print("=" * 60)

    try:
        empty_reports = collector.get_reports("존재하지않는회사12345", days=90)
        print(f"  -> 반환된 리포트 수: {len(empty_reports)}건 (예외 없이 빈 리스트 반환 확인 완료)")
    except Exception as e:
        print(f"\n[ERROR] 오류 발생하면 안 됨: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n[TelegramCollector] 테스트 완료")
