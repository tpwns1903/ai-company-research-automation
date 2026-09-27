"""
base_collector.py
모든 Collector의 추상 기반 클래스 — 캐싱, 재시도, Rate Limit 공통 구현
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from config.config import Settings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CollectorError(Exception):
    """Collector 공통 예외 클래스."""


class RateLimitError(CollectorError):
    """API Rate Limit 초과 예외."""


class ApiResponseError(CollectorError):
    """API 응답 오류 예외 (HTTP 4xx/5xx 등)."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"[HTTP {status_code}] {message}")


class BaseCollector(ABC):
    """모든 데이터 수집기(Collector)의 추상 기반 클래스.

    제공하는 공통 기능:
        - 로컬 디스크 캐시 (JSON, TTL=24h)
        - 요청 간 딜레이 (Rate Limit 대응)
        - 지수 백오프 기반 재시도 로직
        - Mock 주입 지원 (생성자에서 http_session 대체 가능)

    Subclass 구현 필수:
        - ``collect(company_name, **kwargs)``
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        """BaseCollector를 초기화한다.

        Args:
            settings: 애플리케이션 설정 객체.
                      None이면 .env 파일에서 자동 로드한다.
        """
        self._settings: Settings = settings or get_settings()
        self._cache_dir: Path = self._settings.cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("%s 초기화 완료 (cache_dir=%s)", self.__class__.__name__, self._cache_dir)

    # ── 추상 메서드 ───────────────────────────────────────────────────────────

    @abstractmethod
    def collect(self, company_name: str, **kwargs: Any) -> Any:
        """데이터를 수집하여 정규화된 Pydantic 모델로 반환한다.

        Args:
            company_name: 수집 대상 기업명.
            **kwargs: 수집기별 추가 파라미터.

        Returns:
            Pydantic 모델 인스턴스 또는 그 목록.

        Raises:
            CollectorError: 수집 실패 시.
        """

    # ── 캐시 ─────────────────────────────────────────────────────────────────

    def _make_cache_key(self, *args: Any) -> str:
        """요청 파라미터를 기반으로 캐시 키를 생성한다 (SHA-256).

        Args:
            *args: 캐시 키를 구성할 직렬화 가능한 값들.

        Returns:
            16자리 16진수 해시 문자열.
        """
        raw = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _get_cache_path(self, cache_key: str) -> Path:
        """캐시 파일의 절대 경로를 반환한다.

        Args:
            cache_key: ``_make_cache_key()``로 생성된 키.

        Returns:
            캐시 파일 Path 객체.
        """
        return self._cache_dir / f"{self.__class__.__name__}_{cache_key}.json"

    def _is_empty_payload(self, payload: Any) -> bool:
        """payload가 '데이터 없음'에 해당하는지 판별한다.

        Args:
            payload: 캐시에 저장하려는 데이터.

        Returns:
            None이거나 빈 list/dict이면 True.
        """
        if payload is None:
            return True
        if isinstance(payload, (list, dict)):
            return len(payload) == 0
        return False

    def _load_cache(self, cache_key: str) -> Optional[Any]:
        """유효한(TTL 이내) 캐시 데이터를 반환한다.

        빈 결과([]/{}/None)로 저장된 캐시는 ``cache_ttl_empty_seconds``(기본 1시간)를,
        실제 데이터가 있는 캐시는 ``cache_ttl_seconds``(기본 24시간)를 TTL로 사용한다.
        (TTL은 저장 시점에 wrapper에 기록되므로 여기서는 그 값을 그대로 읽는다.)

        Args:
            cache_key: 조회할 캐시 키.

        Returns:
            캐시된 payload(dict | list). 캐시 없거나 만료됐으면 None.
        """
        path = self._get_cache_path(cache_key)
        if not path.exists():
            logger.debug("캐시 미존재: %s", cache_key)
            return None

        try:
            wrapper: dict = json.loads(path.read_text(encoding="utf-8"))
            cached_at = datetime.fromisoformat(wrapper["cached_at"])
            # timezone-aware 비교를 위해 UTC 변환
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - cached_at).total_seconds()

            # 이전 버전 캐시 파일과의 호환을 위해 ttl_seconds가 없으면 기본 TTL 사용
            ttl = wrapper.get("ttl_seconds", self._settings.cache_ttl_seconds)

            if age > ttl:
                logger.debug(
                    "캐시 만료: %s (경과 %.0f초 / TTL %ds)",
                    cache_key, age, ttl,
                )
                return None

            logger.debug("캐시 히트: %s (경과 %.0f초 / TTL %ds)", cache_key, age, ttl)
            return wrapper["payload"]

        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("캐시 파일 손상으로 무시: %s — %s", path, exc)
            return None

    def _save_cache(self, cache_key: str, payload: Any) -> None:
        """데이터를 캐시 파일로 저장한다.

        payload는 JSON 직렬화 가능해야 한다 (Pydantic 모델은 .model_dump() 후 전달).

        빈 결과([]/{}/None)는 일시적 오류(Rate limit, 타임아웃 등)로 인한 것일 수 있으므로
        ``cache_ttl_empty_seconds``(기본 1시간)만 캐싱하고, 실제 데이터가 있는 결과는
        ``cache_ttl_seconds``(기본 24시간)를 그대로 유지한다.

        Args:
            cache_key: 저장할 캐시 키.
            payload: JSON 직렬화 가능한 데이터 (dict | list | str 등).
        """
        path = self._get_cache_path(cache_key)
        ttl = (
            self._settings.cache_ttl_empty_seconds
            if self._is_empty_payload(payload)
            else self._settings.cache_ttl_seconds
        )
        try:
            wrapper = {
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "collector": self.__class__.__name__,
                "ttl_seconds": ttl,
                "payload": payload,
            }
            path.write_text(
                json.dumps(wrapper, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            logger.debug("캐시 저장: %s → %s (TTL %ds)", cache_key, path.name, ttl)
        except OSError as exc:
            # 캐시 저장 실패는 치명적이지 않으므로 경고만 기록
            logger.warning("캐시 저장 실패: %s — %s", cache_key, exc)

    def _invalidate_cache(self, cache_key: str) -> None:
        """특정 캐시 항목을 강제 삭제한다.

        Args:
            cache_key: 삭제할 캐시 키.
        """
        path = self._get_cache_path(cache_key)
        if path.exists():
            path.unlink()
            logger.debug("캐시 삭제: %s", cache_key)

    # ── Rate Limit / 재시도 ───────────────────────────────────────────────────

    def _wait(self, seconds: Optional[float] = None) -> None:
        """지정된 시간(초) 동안 대기한다 (Rate Limit 준수).

        Args:
            seconds: 대기 시간. None이면 settings.request_delay_seconds 사용.
        """
        delay = seconds if seconds is not None else self._settings.request_delay_seconds
        if delay > 0:
            logger.debug("Rate limit 대기: %.1f초", delay)
            time.sleep(delay)

    def _request_with_retry(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """재시도 로직(지수 백오프)을 적용하여 함수를 실행한다.

        최대 ``settings.max_retry_attempts``회 재시도하며,
        매 실패마다 ``settings.retry_delay_seconds * attempt``초 대기한다.

        Args:
            func: 실행할 호출 가능 객체.
            *args: func에 전달할 위치 인자.
            **kwargs: func에 전달할 키워드 인자.

        Returns:
            func의 반환값.

        Raises:
            CollectorError: 최대 재시도 횟수 초과 시 마지막 예외를 래핑.
        """
        last_exc: Exception | None = None
        max_attempts = self._settings.max_retry_attempts

        for attempt in range(1, max_attempts + 1):
            try:
                result = func(*args, **kwargs)
                if attempt > 1:
                    logger.info("[%s] 재시도 성공 (시도 %d회)", self.__class__.__name__, attempt)
                return result

            except RateLimitError as exc:
                last_exc = exc
                wait = self._settings.retry_delay_seconds * attempt * 2  # 더 길게 대기
                logger.warning(
                    "[%s] Rate limit 감지 (시도 %d/%d) — %.1f초 후 재시도",
                    self.__class__.__name__, attempt, max_attempts, wait,
                )
                if attempt < max_attempts:
                    time.sleep(wait)

            except (ApiResponseError, OSError, Exception) as exc:
                last_exc = exc
                wait = self._settings.retry_delay_seconds * attempt
                logger.warning(
                    "[%s] 요청 실패 (시도 %d/%d): %s — %.1f초 후 재시도",
                    self.__class__.__name__, attempt, max_attempts, exc, wait,
                )
                if attempt < max_attempts:
                    time.sleep(wait)

        raise CollectorError(
            f"{self.__class__.__name__}: 최대 재시도 횟수({max_attempts}) 초과"
        ) from last_exc

    # ── 유틸리티 ─────────────────────────────────────────────────────────────

    def _yyyymmdd(self, d: Any) -> str:
        """날짜 객체를 'YYYYMMDD' 문자열로 변환한다.

        Args:
            d: datetime.date 또는 datetime.datetime 인스턴스.

        Returns:
            'YYYYMMDD' 형식 문자열.
        """
        return d.strftime("%Y%m%d")

    def _won_to_uk(self, won: Optional[float]) -> Optional[float]:
        """원(KRW) 단위 값을 억 원 단위로 변환한다.

        Args:
            won: 원 단위 금액.

        Returns:
            억 원 단위 금액 (소수점 2자리). 입력이 None이면 None.
        """
        if won is None:
            return None
        return round(won / 1e8, 2)
