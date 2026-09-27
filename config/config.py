"""
config.py
전역 설정 및 API 키 관리 모듈

.env 파일에서 환경변수를 로드하여 애플리케이션 전체에서 사용하는
설정 객체(Settings)를 제공한다. python-dotenv 기반.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 프로젝트 루트 경로
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
ENV_FILE: Path = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """애플리케이션 전역 설정.

    .env 파일 또는 환경변수에서 자동으로 값을 로드한다.
    모든 API 키/시크릿은 이 클래스를 통해서만 접근해야 한다.
    """

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── API 키 ─────────────────────────────────────────────────
    dart_api_key: str = Field(..., description="DART OpenAPI 인증 키")
    openai_api_key: str = Field(default="", description="OpenAI API 키 (LLM 리포트 생성)")
    gemini_api_key: str = Field(default="", description="Google Gemini API 키 (LLM 리포트 생성)")
    naver_client_id: str = Field(default="", description="네이버 검색 API Client ID")
    naver_client_secret: str = Field(default="", description="네이버 검색 API Client Secret")
    news_api_key: str = Field(default="", description="뉴스 API 키 (더 더이상 사용 안함, 유지됨)")
    telegram_api_id: str = Field(default="", description="Telegram API ID")
    telegram_api_hash: str = Field(default="", description="Telegram API Hash")
    telegram_phone: str = Field(default="", description="Telegram 계정 전화번호")
    krx_id: str = Field(default="", description="KRX 데이터 포털 ID (pykrx 1.2.8+ 펀더멘털 조회용)")
    krx_pw: str = Field(default="", description="KRX 데이터 포털 비밀번호")

    # ── 수집 설정 ───────────────────────────────────────────────
    report_lookback_days: int = Field(
        default=90, description="증권사 리포트 수집 기간 (기본 90일 = 3개월)"
    )
    news_lookback_days: int = Field(
        default=30, description="뉴스 수집 기간 (기본 30일)"
    )
    request_delay_seconds: float = Field(
        default=1.0, description="외부 요청 간 기본 딜레이 (초)"
    )
    telegram_max_scan_messages: int = Field(
        default=2000,
        description="텔레그램 채널당 lookback 기간 스캔 시 최대 메시지 수 (무한 순회 방지 안전 상한선)",
    )
    max_retry_attempts: int = Field(
        default=3, description="요청 실패 시 최대 재시도 횟수"
    )
    retry_delay_seconds: float = Field(
        default=2.0, description="재시도 간 대기 시간 (초)"
    )

    # ── 캐시 설정 ───────────────────────────────────────────────
    cache_dir: Path = Field(
        default=PROJECT_ROOT / "cache",
        description="로컬 캐시 저장 디렉토리",
    )
    cache_ttl_seconds: int = Field(
        default=86400, description="캐시 유효 기간 (기본 24시간 = 86400초)"
    )
    cache_ttl_empty_seconds: int = Field(
        default=3600,
        description="빈 결과([]/{}/None) 캐시 유효 기간 (기본 1시간 = 3600초). "
        "일시적 오류로 빈 결과가 반환된 경우 오래 캐싱되어 재조회를 막는 것을 방지한다.",
    )

    # ── 출력 설정 ───────────────────────────────────────────────
    output_dir: Path = Field(
        default=PROJECT_ROOT / "output" / "reports",
        description="생성된 리포트 저장 디렉토리",
    )
    report_pdf_dir: Path = Field(
        default=PROJECT_ROOT / "output" / "report_pdfs",
        description="텔레그램/네이버에서 수집한 증권사 리포트 원문 PDF 저장 디렉토리",
    )
    log_level: str = Field(default="INFO", description="로그 레벨 (DEBUG/INFO/WARNING/ERROR)")

    # ── LLM 설정 ───────────────────────────────────────────────
    llm_provider: str = Field(
        default="auto",
        description="LLM 제공자 (auto / openai / gemini). "
        "auto는 OPENAI_API_KEY → GEMINI_API_KEY 순으로 설정된 키를 사용한다.",
    )
    llm_model: str = Field(
        default="", description="사용할 LLM 모델명 (비우면 제공자별 기본 모델)"
    )
    llm_reasoning_effort: str = Field(
        default="",
        description="추론 모델의 추론 강도 (low / medium / high). 비우면 전송하지 않는다.",
    )
    llm_max_tokens: int = Field(
        default=8192,
        description="LLM 최대 토큰 수 (추론 모델은 내부 추론 토큰도 이 한도를 함께 쓴다)",
    )
    llm_temperature: float = Field(
        default=0.3, description="LLM 온도 파라미터 (낮을수록 일관성 높음)"
    )

    # ── 텔레그램 채널 ───────────────────────────────────────────
    telegram_channels: list[str] = Field(
        default_factory=lambda: [],
        description="수집 대상 텔레그램 공개 채널 목록 (예: ['@channel1'])",
    )


_PLACEHOLDER_MARKERS = ("your_", "_here", "<", "changeme")


def is_configured_key(value: str | None) -> bool:
    """API 키가 실제 값으로 설정되었는지 확인한다.

    빈 문자열이나 .env.example의 예시 문구(예: 'your_openai_api_key_here')는
    설정되지 않은 것으로 본다.

    Args:
        value: 확인할 API 키 문자열.

    Returns:
        bool: 실제 키로 보이면 True.
    """
    if not value or not value.strip():
        return False
    return not any(marker in value for marker in _PLACEHOLDER_MARKERS)


def get_settings() -> Settings:
    """Settings 싱글턴 인스턴스를 반환한다.

    Returns:
        Settings: 전역 설정 객체.

    Raises:
        ValidationError: 필수 환경변수(예: DART_API_KEY)가 누락된 경우.
    """
    return Settings()


def configure_logging(level: str = "INFO") -> None:
    """프로젝트 공통 로깅 설정을 초기화한다.

    Args:
        level: 로그 레벨 문자열 (DEBUG / INFO / WARNING / ERROR).
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
