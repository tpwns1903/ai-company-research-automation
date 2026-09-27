"""
formatters.py
화면/문서 표기용 공통 포맷터 — UI, Excel, LLM 리포트에서 공통으로 사용한다.

내부 계산(Confidence Score 산정, 억 원 단위 저장 등)은 그대로 유지하고,
사람이 읽는 화면/문서에 표시할 때만 이 모듈의 함수를 통해 변환한다.
"""

from __future__ import annotations

from typing import Optional

_UK_PER_JO = 10_000  # 1조 = 10,000억


def format_won_uk(value_uk: Optional[float]) -> str:
    """억 원 단위 금액을 한국식 조/억 표기 문자열로 변환한다.

    1조 미만이면 "조" 단위는 생략한다.

    Args:
        value_uk: 억 원 단위 금액 (예: 59685.12).

    Returns:
        "5조 9,685억원", "9,685억원" 형식의 문자열. 값이 None이면 "N/A".

    Examples:
        >>> format_won_uk(59685.12)
        '5조 9,685억원'
        >>> format_won_uk(7469.68)
        '7,470억원'
        >>> format_won_uk(50000.0)
        '5조원'
    """
    if value_uk is None:
        return "N/A"

    rounded = round(value_uk)
    sign = "-" if rounded < 0 else ""
    rounded = abs(rounded)

    jo, uk = divmod(rounded, _UK_PER_JO)

    if jo and uk:
        return f"{sign}{jo}조 {uk:,}억원"
    if jo:
        return f"{sign}{jo}조원"
    return f"{sign}{uk:,}억원"


def format_confidence_10(score: Optional[float]) -> str:
    """0.0~1.0 스케일 신뢰도 점수를 10점 만점 표기 문자열로 변환한다.

    내부 Confidence Score 산정 로직은 0~1 스케일을 그대로 사용하며,
    이 함수는 화면 표시 시점에만 ×10 하여 보여준다.

    Args:
        score: 0.0 ~ 1.0 사이의 신뢰도 점수.

    Returns:
        "8.5/10.0" 형식의 문자열. 값이 None이면 "N/A".
    """
    if score is None:
        return "N/A"
    return f"{score * 10:.1f}/10.0"
