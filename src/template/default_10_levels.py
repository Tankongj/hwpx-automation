"""기본 10단계 스타일 스펙.

기획안 4.4 의 상수 테이블을 그대로 상수화한 것. 사용자가 공고 양식을 업로드하지 않은
경우의 fallback, 그리고 template_analyzer 가 매칭 실패했을 때의 기본값.

스펙은 한/글의 Ctrl+1~0 단축키 레벨(휴먼명조/HY견고딕, A4 여백) 과 1:1 매칭된다.
구조
----
``DEFAULT_STYLE_MAP``: {level(int) → :class:`StyleSpec` dataclass}
``DEFAULT_PAGE_SETUP``: :class:`PageSetup` dataclass
``V1_TYPE_STYLE_MAP`` : v1 ``md_to_hwpx`` 가 쓰는 ``{'H1': {para, char, style}, ...}``
                      (엔진 API 호환)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StyleSpec:
    """레벨별 시각 스펙 (휴먼/한글에서 Ctrl+N 로 지정되는 값)."""

    level: int
    font: str
    size: int           # pt
    indent: int         # 들여쓰기 칸 수 (한/글 기준)
    para_gap: int       # 단락 간 간격 (pt)
    symbol: str         # 자동 삽입되는 선두 기호
    hotkey: str         # 한/글 단축키


@dataclass(frozen=True)
class PageSetup:
    """A4 페이지 여백 스펙."""

    paper: str = "A4"
    margin_top_mm: float = 15.0
    margin_bottom_mm: float = 15.0
    margin_left_mm: float = 20.0
    margin_right_mm: float = 20.0
    header_mm: float = 10.0
    footer_mm: float = 10.0


@dataclass(frozen=True)
class EngineStyleIDs:
    """v1 엔진(md_to_hwpx) 이 기대하는 HWPX 스타일 ID 튜플.

    paraPrIDRef / charPrIDRef / styleIDRef. 기본 10단계 번들 템플릿 기준 값이며,
    사용자 템플릿을 쓸 때는 template_analyzer 가 이 값을 다시 계산한다.
    """

    para: str
    char: str
    style: str = "0"


# ---------------------------------------------------------------------------
# The spec itself (기획안 4.4 테이블 그대로)
# ---------------------------------------------------------------------------

DEFAULT_STYLE_MAP: dict[int, StyleSpec] = {
    1:  StyleSpec(1,  "휴먼명조", 20, 0, 0,  "Ⅰ.", "Ctrl+1"),
    2:  StyleSpec(2,  "HY견고딕", 18, 0, 0,  "1",   "Ctrl+2"),
    3:  StyleSpec(3,  "휴먼명조", 18, 1, 20, "1)",  "Ctrl+3"),
    4:  StyleSpec(4,  "휴먼명조", 16, 2, 10, "(1)", "Ctrl+4"),
    5:  StyleSpec(5,  "휴먼명조", 15, 3, 10, "①",   "Ctrl+5"),
    6:  StyleSpec(6,  "휴먼명조", 15, 4, 0,  "□",   "Ctrl+6"),
    7:  StyleSpec(7,  "휴먼명조", 15, 5, 0,  "❍",   "Ctrl+7"),
    8:  StyleSpec(8,  "휴먼명조", 15, 6, 0,  "-",   "Ctrl+8"),
    9:  StyleSpec(9,  "휴먼명조", 15, 7, 0,  "·",   "Ctrl+9"),
    10: StyleSpec(10, "중고딕",   13, 9, 0,  "*",   "Ctrl+0"),
}

DEFAULT_PAGE_SETUP: PageSetup = PageSetup()


# ---------------------------------------------------------------------------
# v1 엔진 호환 매핑
# ---------------------------------------------------------------------------

# 번들 기본 템플릿(templates/00_기본_10단계스타일.hwpx) 의 하드코딩된 ID 값과 동일.
# v1 md_to_hwpx.DEFAULT_STYLE_MAP 에서 그대로 옮겨 왔다.
V1_TYPE_STYLE_MAP: dict[str, EngineStyleIDs] = {
    "H1":    EngineStyleIDs(para="3",  char="19", style="0"),
    "H2":    EngineStyleIDs(para="3",  char="18", style="0"),
    "H3":    EngineStyleIDs(para="8",  char="20", style="7"),
    "H4":    EngineStyleIDs(para="9",  char="21", style="6"),
    "H5":    EngineStyleIDs(para="10", char="2",  style="0"),
    "L1":    EngineStyleIDs(para="11", char="2",  style="1"),
    "L2":    EngineStyleIDs(para="12", char="2",  style="2"),
    "L3":    EngineStyleIDs(para="13", char="22", style="3"),
    "L4":    EngineStyleIDs(para="14", char="22", style="4"),
    "note":  EngineStyleIDs(para="15", char="1",  style="5"),
    "body":  EngineStyleIDs(para="11", char="2",  style="0"),
    "empty": EngineStyleIDs(para="11", char="2",  style="0"),
}


def to_v1_style_dict() -> dict[str, dict[str, str]]:
    """v1 엔진(md_to_hwpx.convert) 의 ``style_map`` 파라미터 형식으로 변환."""
    return {k: {"para": v.para, "char": v.char, "style": v.style}
            for k, v in V1_TYPE_STYLE_MAP.items()}


__all__ = [
    "StyleSpec",
    "PageSetup",
    "EngineStyleIDs",
    "DEFAULT_STYLE_MAP",
    "DEFAULT_PAGE_SETUP",
    "V1_TYPE_STYLE_MAP",
    "to_v1_style_dict",
]
