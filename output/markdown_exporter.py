"""
markdown_exporter.py
Markdown 리포트 파일 저장 모듈
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from config.config import Settings, get_settings

logger = logging.getLogger(__name__)


class MarkdownExporter:
    """생성된 Markdown 리포트를 파일로 저장하는 클래스."""

    def __init__(self, settings: Settings | None = None) -> None:
        """MarkdownExporter를 초기화한다.

        Args:
            settings: 애플리케이션 설정. None이면 기본값을 로드한다.
        """
        self._settings: Settings = settings or get_settings()
        self._output_dir: Path = self._settings.output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("MarkdownExporter 초기화 완료")

    def export(self, markdown_content: str, company_name: str) -> Path:
        """Markdown 내용을 .md 파일로 저장한다.

        Args:
            markdown_content: 저장할 Markdown 텍스트.
            company_name: 기업명 (파일명 생성에 사용).

        Returns:
            Path: 저장된 .md 파일의 절대 경로.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{company_name}_report_{timestamp}.md"
        output_path = self._output_dir / filename

        output_path.write_text(markdown_content, encoding="utf-8")
        logger.info("Markdown 리포트 저장 완료: %s", output_path)
        return output_path
