"""
pdf_exporter.py
Markdown 리포트를 PDF 파일로 변환/저장하는 모듈

변환 전략 (우선순위 순):
  1. WeasyPrint (markdown → HTML → PDF, 한글 폰트 지원)
  2. fpdf2 (직접 PDF 생성, 한글 폰트 별도 등록 필요)
  3. Fallback: .txt 파일로 저장 + 경고 로그

외부 패키지가 없어도 앱 전체가 죽지 않도록 설계한다.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from config.config import Settings, get_settings

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

# 내장 한글 폰트 경로 후보 (Windows)
_FONT_CANDIDATES: list[Path] = [
    Path("C:/Windows/Fonts/malgun.ttf"),       # 맑은 고딕
    Path("C:/Windows/Fonts/NanumGothic.ttf"),  # 나눔고딕
    Path("C:/Windows/Fonts/gulim.ttc"),        # 굴림
]


class PdfExporter:
    """Markdown 리포트를 PDF 파일로 변환/저장하는 클래스.

    변환 우선순위:
        1. WeasyPrint: markdown → HTML → PDF (고품질, 한글 CSS 폰트 포함).
        2. fpdf2: 직접 PDF 셀 렌더링 (중품질, 한글 폰트 TTF 별도 등록 필요).
        3. Fallback: .txt 파일 저장 + 경고 로그.

    Example:
        >>> exporter = PdfExporter()
        >>> path = exporter.export(markdown_content, "테크윙")
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        """PdfExporter를 초기화한다.

        Args:
            settings: 애플리케이션 설정. None이면 .env에서 자동 로드.
        """
        self._settings: Settings = settings or get_settings()
        self._output_dir: Path = self._settings.output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("PdfExporter 초기화 완료 (출력 경로: %s)", self._output_dir)

    def export(self, markdown_content: str, company_name: str) -> Path:
        """Markdown 문자열을 PDF로 변환하여 파일로 저장한다.

        라이브러리 우선순위에 따라 자동으로 적합한 백엔드를 선택한다.
        어떤 라이브러리도 없을 경우 .txt 파일로 Fallback 저장한다.

        Args:
            markdown_content: PDF로 변환할 Markdown 텍스트.
            company_name: 기업명 (파일명 생성에 사용).

        Returns:
            Path: 저장된 파일의 절대 경로 (.pdf 또는 .txt).
        """
        timestamp = datetime.now(_KST).strftime("%Y%m%d_%H%M%S")
        safe_name = company_name.replace(" ", "_").replace("/", "-")
        base_name = f"{safe_name}_report_{timestamp}"

        # 1순위: WeasyPrint
        try:
            return self._export_weasyprint(markdown_content, base_name)
        except ImportError:
            logger.debug("WeasyPrint 미설치, fpdf2 시도")
        except Exception as exc:
            logger.warning("WeasyPrint 변환 실패: %s - fpdf2 시도", exc)

        # 2순위: fpdf2
        try:
            return self._export_fpdf2(markdown_content, base_name)
        except ImportError:
            logger.debug("fpdf2 미설치, Fallback(.txt) 사용")
        except Exception as exc:
            logger.warning("fpdf2 변환 실패: %s - Fallback(.txt) 사용", exc)

        # Fallback: .txt 저장
        return self._export_txt_fallback(markdown_content, base_name)

    # ── 백엔드 구현 ───────────────────────────────────────────────────────────

    def _export_weasyprint(self, markdown_content: str, base_name: str) -> Path:
        """WeasyPrint를 사용하여 PDF를 생성한다.

        markdown → HTML(내장 CSS) → PDF 변환.

        Args:
            markdown_content: Markdown 문자열.
            base_name: 저장 파일의 기본 이름 (확장자 제외).

        Returns:
            Path: 저장된 .pdf 파일 경로.

        Raises:
            ImportError: weasyprint 또는 markdown 패키지 미설치 시.
        """
        import markdown as md_lib  # type: ignore[import]
        from weasyprint import HTML, CSS  # type: ignore[import]

        html_body = md_lib.markdown(
            markdown_content,
            extensions=["tables", "fenced_code", "toc"],
        )

        html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<style>
  body {{
    font-family: 'Malgun Gothic', 'NanumGothic', 'Gulim', sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    margin: 2cm;
    color: #1a1a2e;
  }}
  h1 {{ font-size: 18pt; color: #1F4E79; border-bottom: 2px solid #1F4E79; padding-bottom: 6px; }}
  h2 {{ font-size: 14pt; color: #2E75B6; margin-top: 18px; }}
  h3 {{ font-size: 12pt; color: #2E75B6; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th {{ background: #1F4E79; color: white; padding: 6px 10px; text-align: center; }}
  td {{ border: 1px solid #D0D0D0; padding: 5px 10px; }}
  tr:nth-child(even) {{ background: #F0F4F8; }}
  blockquote {{ border-left: 4px solid #2E75B6; margin: 0; padding: 4px 12px; background: #F0F4F8; }}
  code {{ background: #F5F5F5; padding: 1px 4px; font-size: 9pt; }}
  .footer {{ margin-top: 24px; font-size: 8pt; color: #888; text-align: right; }}
</style>
</head>
<body>
{html_body}
<div class="footer">본 리포트는 자동 생성된 참고용 분석이며, 투자 추천이 아닙니다.</div>
</body>
</html>"""

        file_path = self._output_dir / f"{base_name}.pdf"
        HTML(string=html_content).write_pdf(
            str(file_path),
            stylesheets=[CSS(string="@page { size: A4; margin: 2cm; }")],
        )
        logger.info("[PdfExporter] WeasyPrint PDF 저장 완료: %s", file_path)
        return file_path

    def _export_fpdf2(self, markdown_content: str, base_name: str) -> Path:
        """fpdf2를 사용하여 PDF를 직접 생성한다.

        Markdown 마크업을 간이 파싱하여 PDF 셀로 렌더링한다.

        Args:
            markdown_content: Markdown 문자열.
            base_name: 저장 파일의 기본 이름 (확장자 제외).

        Returns:
            Path: 저장된 .pdf 파일 경로.

        Raises:
            ImportError: fpdf2 패키지 미설치 시.
        """
        from fpdf import FPDF  # type: ignore[import]

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        # 한글 폰트 등록 시도
        font_registered = False
        for font_path in _FONT_CANDIDATES:
            if font_path.exists():
                try:
                    pdf.add_font("KoreanFont", "", str(font_path), uni=True)
                    font_registered = True
                    break
                except Exception:
                    continue

        if font_registered:
            pdf.set_font("KoreanFont", size=10)
        else:
            pdf.set_font("Helvetica", size=10)
            logger.warning("[PdfExporter] 한글 폰트 미발견 - 영문 폰트로 렌더링")

        # Markdown 간이 파싱하여 PDF로 출력
        for line in markdown_content.splitlines():
            stripped = line.strip()
            if not stripped:
                pdf.ln(4)
                continue

            if stripped.startswith("# "):
                pdf.set_font_size(16)
                pdf.set_text_color(31, 78, 121)
                pdf.multi_cell(0, 8, stripped[2:])
                pdf.set_text_color(0, 0, 0)
            elif stripped.startswith("## "):
                pdf.set_font_size(13)
                pdf.set_text_color(46, 117, 182)
                pdf.multi_cell(0, 7, stripped[3:])
                pdf.set_text_color(0, 0, 0)
            elif stripped.startswith("### "):
                pdf.set_font_size(11)
                pdf.set_text_color(46, 117, 182)
                pdf.multi_cell(0, 6, stripped[4:])
                pdf.set_text_color(0, 0, 0)
            elif stripped.startswith("> "):
                pdf.set_font_size(9)
                pdf.set_fill_color(240, 244, 248)
                pdf.multi_cell(0, 5, stripped[2:], fill=True)
            elif stripped.startswith("- ") or stripped.startswith("* "):
                pdf.set_font_size(10)
                pdf.multi_cell(0, 5, f"  • {stripped[2:]}")
            elif stripped.startswith("|"):
                # 테이블 행 — 단순 텍스트로 출력
                clean = re.sub(r"\|", "  |  ", stripped).strip("|").strip()
                pdf.set_font_size(9)
                pdf.multi_cell(0, 4, clean)
            else:
                pdf.set_font_size(10)
                # 굵게(**text**) 마크업 간이 제거
                clean = re.sub(r"\*\*(.*?)\*\*", r"\1", stripped)
                clean = re.sub(r"`(.*?)`", r"\1", clean)
                pdf.multi_cell(0, 5, clean)

        file_path = self._output_dir / f"{base_name}.pdf"
        pdf.output(str(file_path))
        logger.info("[PdfExporter] fpdf2 PDF 저장 완료: %s", file_path)
        return file_path

    def _export_txt_fallback(self, markdown_content: str, base_name: str) -> Path:
        """PDF 변환 라이브러리가 없을 때 .txt 파일로 Fallback 저장한다."""
        file_path = self._output_dir / f"{base_name}.txt"
        file_path.write_text(markdown_content, encoding="utf-8")
        logger.warning(
            "[PdfExporter] PDF 변환 라이브러리(weasyprint/fpdf2) 없음 "
            "- .txt 파일로 저장: %s", file_path
        )
        return file_path


# ── 단독 실행 테스트 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from config.config import Settings as _Settings, configure_logging

    configure_logging("DEBUG")

    sample_md = """# 테크윙 (089030) 기업 분석 리포트

> 분석 기준일: 2026-08-06 | 데이터 신뢰도: 0.72

---

## 기업 개요

테크윙은 반도체 테스트 핸들러 장비를 전문으로 제조하는 기업입니다.
HBM 관련 테스터 장비 시장에서 높은 점유율을 보유하고 있습니다.

## 재무 분석

| 항목 | 값 |
|------|-----|
| 매출액 | 3200.0 억원 |
| 영업이익 | 480.0 억원 |
| 영업이익률 | 15.0% |
| PER | 19.0배 |

## 핵심 투자 포인트

**[1]** HBM 테스터 시장 독점적 공급 지위 확보
  - 신뢰도: `0.85` | 출처 수: 3건

## 리스크 요인

- 단기 수요 불확실성 및 고객사 집중 리스크

## 결론

테크윙은 HBM 사이클 수혜가 기대되는 핵심 장비 기업입니다.

*본 리포트는 자동 생성된 참고용 분석이며, 투자 추천이 아닙니다.*
"""

    print("=" * 60)
    print("[PdfExporter] 단독 테스트")
    print("=" * 60)

    exporter = PdfExporter(_Settings(dart_api_key="mock"))
    try:
        path = exporter.export(sample_md, "테크윙")
        ext = path.suffix
        if ext == ".pdf":
            print(f"[SUCCESS] PDF 파일 생성 완료: {path}")
        else:
            print(f"[FALLBACK] PDF 라이브러리 없음 - .txt 저장: {path}")
    except Exception as e:
        print(f"[ERROR] 오류 발생: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)

    print("\n[PdfExporter] 테스트 완료")
