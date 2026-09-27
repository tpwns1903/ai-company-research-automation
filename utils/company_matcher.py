"""
company_matcher.py
텔레그램/네이버 등 여러 수집기에서 공통으로 사용하는 기업명 매칭 유틸리티.

단순 부분 문자열 포함(in) 매칭은 두 가지 문제를 일으킨다:
  1. 계열사명 혼동 — "한화오션"을 검색했는데 "한화에어로스페이스"를 다루는
     리포트가 본문 중간에 "한화오션"을 언급(그룹/섹터 비교 등)하기만 해도
     매칭되어 버린다.
  2. 다종목 데일리 브리핑 오매칭 — 수십 개 종목을 한 번에 나열하는 메시지는
     특정 기업에 대한 리포트가 아닌데도, 그 중 한 줄에 검색어가 등장하면
     매칭되어 버린다.

이를 막기 위해 본문 "전체"에서 검색어를 찾는 대신, 메시지 맨 앞에 오는
"진짜 대상 기업" 표기(canonical subject)를 추출해 그것과 정확히 일치하는
경우에만 매칭한다. canonical subject를 찾을 수 없는 메시지(다종목 브리핑 등)는
특정 기업 리포트로 보지 않는다.
"""

from __future__ import annotations

import re
from typing import Optional

# "[한화오션]" / "[한화에어로스페이스/방산/항공우주]" — 채널이 붙이는 대괄호 헤더 태그
_BRACKET_SUBJECT_PATTERN = re.compile(r"^\s*\[([^\]\n]+)\]")

# "한화오션 (042660)" / "  HD현대중공업 (329180)  ..." — "회사명 (종목코드)" 헤더
_NAME_CODE_SUBJECT_PATTERN = re.compile(r"^\s*([가-힣A-Za-z0-9&\.]+)\s*\((\d{6})\)")

_CODE_IN_NAME_PATTERN = re.compile(r"\(\d{6}\)")


def extract_canonical_subject(text: str) -> tuple[Optional[str], Optional[str]]:
    """메시지 맨 앞부분에서 '진짜 대상 기업명'과 종목코드를 추출한다.

    지원 형식:
        - "[한화오션]", "[한화에어로스페이스/방산/항공우주]" (대괄호 헤더 스타일)
        - "한화오션 (042660)" (회사명+종목코드 헤더 스타일)

    두 형식 모두 발견되지 않으면 (None, None)을 반환한다 — 여러 종목을
    한꺼번에 나열하는 데일리 브리핑성 메시지가 대표적인 경우다.

    Args:
        text: 메시지 원문(캡션) 텍스트.

    Returns:
        (기업명 또는 None, 종목코드 또는 None) 튜플.
    """
    if not text:
        return None, None

    m = _BRACKET_SUBJECT_PATTERN.match(text)
    if m:
        name = m.group(1).split("/")[0].strip()
        code_match = _CODE_IN_NAME_PATTERN.search(name)
        code = code_match.group(0)[1:-1] if code_match else None
        name = _CODE_IN_NAME_PATTERN.sub("", name).strip()
        return (name or None), code

    m = _NAME_CODE_SUBJECT_PATTERN.match(text)
    if m:
        return m.group(1).strip(), m.group(2)

    return None, None


def is_company_match(
    company_name: str, text: str, stock_code: Optional[str] = None
) -> bool:
    """텍스트가 실제로 company_name에 대한 리포트인지 판별한다.

    본문 전체에서 회사명이 등장하는지(부분 문자열 포함)를 보는 대신, 메시지
    맨 앞의 "진짜 대상 기업" 표기(canonical subject)와 정확히 일치하는 경우에만
    매칭한다. canonical subject를 찾을 수 없으면(다종목 브리핑 등) 매칭하지 않는다.

    Args:
        company_name: 검색 대상 기업명 (예: "한화오션").
        text: 메시지 원문(캡션) 텍스트.
        stock_code: 검색 대상 기업의 종목코드 (알고 있는 경우 선택 전달).
            종목코드가 주어지고 텍스트에서도 종목코드를 추출할 수 있으면
            종목코드 일치 여부를 우선 사용한다 (기업명보다 모호함이 적다).

    Returns:
        해당 기업에 대한 리포트로 판단되면 True.
    """
    subject_name, subject_code = extract_canonical_subject(text)

    if subject_name is None:
        return False

    if stock_code and subject_code:
        return subject_code == stock_code

    return subject_name == company_name
