"""HWPX 템플릿 → 스타일맵 추출.

기획안 4.4 의 분석 로직. ``Contents/header.xml`` 에서 charPr/paraPr/style 을,
``Contents/section0.xml`` 에서 페이지 설정(``pagePr``, ``margin``) 을 읽어 **레벨 → HWPX
스타일 ID** 매핑 테이블을 만든다. v2 의 HWPX 생성기(``src.hwpx.md_to_hwpx.convert``) 가
바로 받아 쓸 수 있는 형식으로 반환한다.

매칭 전략 (plan 4.4)
--------------------
1. **스타일 이름 휴리스틱** : "제목1", "□ 4칸" 같은 한/글 관습 이름 → 직접 매핑
2. **폰트 크기** : 20pt → L1, 18pt → L2/L3, ... (charPr ``height`` 기반)
3. **Gemini 보조** : W3 에서 추가 (여기서는 훅만 남김)
4. **fallback** : :data:`src.template.default_10_levels.V1_TYPE_STYLE_MAP` 으로 대체

HWPX 단위 변환
--------------
- charPr ``height``  : 1/100 pt  (1800 = 18.00pt)
- 페이지/여백        : HWPUNIT (1/7200 inch ≈ 1/283.465 mm)
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from lxml import etree

from ..utils.logger import get_logger
from .default_10_levels import (
    DEFAULT_PAGE_SETUP,
    DEFAULT_STYLE_MAP,
    EngineStyleIDs,
    PageSetup,
    V1_TYPE_STYLE_MAP,
)


_log = get_logger("template.analyzer")


NS_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
NS_HH = "http://www.hancom.co.kr/hwpml/2011/head"

HWPUNIT_PER_MM = 283.4646  # 1/7200 inch == 25.4/7200 mm per unit → 7200/25.4 ≈ 283.46 units/mm


PathLike = Union[str, Path]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StyleMap:
    """템플릿에서 추출한 레벨별 매핑.

    ``level_to_ids`` 는 :func:`src.hwpx.md_to_hwpx.convert` 가 쓰는 ``EngineStyleIDs`` 형식.
    ``level_to_v1_type`` 은 v1 paragraph dict 의 'type' 문자열 (H1/L1/note/...).
    """

    template_path: str
    level_to_ids: dict[int, EngineStyleIDs] = field(default_factory=dict)
    # 보조 정보 (디버깅/UI 용)
    level_to_name: dict[int, str] = field(default_factory=dict)
    level_to_font_size_pt: dict[int, float] = field(default_factory=dict)
    page_setup: PageSetup = field(default_factory=lambda: DEFAULT_PAGE_SETUP)
    fallback_used_levels: set[int] = field(default_factory=set)

    # ---- 편의 메서드 ----

    def to_engine_style_dict(self) -> dict[str, dict[str, str]]:
        """``md_to_hwpx.convert(style_map=...)`` 인자 형식으로 변환.

        v1 type 문자열(H1/H2/.../note/body) 키를 쓴다. 누락 레벨은 하드코딩 fallback
        (:data:`V1_TYPE_STYLE_MAP`) 으로 채운다.
        """
        from .default_10_levels import V1_TYPE_STYLE_MAP as _FALLBACK
        from ..parser.ir_schema import LEVEL_TO_V1_TYPE

        result: dict[str, dict[str, str]] = {}
        for level, ids in self.level_to_ids.items():
            v1_type = LEVEL_TO_V1_TYPE.get(level)
            if v1_type is None or v1_type in ("title",):
                continue
            result[v1_type] = {"para": ids.para, "char": ids.char, "style": ids.style}

        # 누락된 키는 번들 기본값으로 채움
        for key, fallback in _FALLBACK.items():
            result.setdefault(key, {"para": fallback.para, "char": fallback.char, "style": fallback.style})

        return result


# ---------------------------------------------------------------------------
# Name heuristics
# ---------------------------------------------------------------------------

# v1 정성제안서 서식 기준 스타일 이름 → 레벨 매핑.
# 다른 템플릿도 이 관습을 많이 따르므로 1차 매핑으로 유효.
NAME_TO_LEVEL: dict[str, int] = {
    "제목1":     1,
    "제목 1":    1,
    "Heading 1": 1,
    "제목2":     2,
    "제목 2":    2,
    "Heading 2": 2,
    "제목3":     3,
    "제목 3":    3,
    "Heading 3": 3,
    "본문1":     4,        # v1 템플릿 관습: 본문1 = (1) 단락 제목
    "제목4":     4,
    "본문2":     5,        # 관습적으로 ①
    "제목5":     5,
    "□ 4칸":     6,
    "❍ 5칸":     7,
    "- 6칸":     8,
    "· 7칸":     9,
    "* 9칸":     10,
    "바탕글":    0,        # body
}


# 폰트 크기(pt) → 후보 레벨 목록.
# 동일 크기 여러 레벨이 있을 때 name heuristic 이 먼저 결정해야 한다.
SIZE_CANDIDATE_LEVELS: dict[float, list[int]] = {
    20.0: [1],
    18.0: [2, 3],
    16.0: [4],
    15.0: [5, 6, 7, 8, 9],
    13.0: [10],
}


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------

@dataclass
class _HeaderInfo:
    fontfaces: dict[str, str] = field(default_factory=dict)      # id → name
    charprs: dict[str, dict] = field(default_factory=dict)       # id → {height_pt, font_name, ...}
    paraprs: dict[str, dict] = field(default_factory=dict)       # id → {indent, ...}
    styles: list[dict] = field(default_factory=list)             # {id, name, charPrIDRef, paraPrIDRef}


def _parse_header(header_bytes: bytes) -> _HeaderInfo:
    root = etree.fromstring(header_bytes)
    info = _HeaderInfo()

    for ff in root.iter(f"{{{NS_HH}}}fontface"):
        fid = ff.get("id", "")
        name = ff.get("name", "")
        if fid:
            info.fontfaces[fid] = name

    for cp in root.iter(f"{{{NS_HH}}}charPr"):
        cid = cp.get("id", "")
        if not cid:
            continue
        height_raw = cp.get("height", "0")
        try:
            height_pt = int(height_raw) / 100.0
        except ValueError:
            height_pt = 0.0

        font_hangul_id = ""
        font_hangul_name = ""
        font_ref = cp.find(f"{{{NS_HH}}}fontRef")
        if font_ref is not None:
            font_hangul_id = font_ref.get("hangul", "")
            font_hangul_name = info.fontfaces.get(font_hangul_id, "")

        info.charprs[cid] = {
            "height_pt": height_pt,
            "font_hangul_id": font_hangul_id,
            "font_hangul_name": font_hangul_name,
        }

    for pp in root.iter(f"{{{NS_HH}}}paraPr"):
        pid = pp.get("id", "")
        if not pid:
            continue
        # 들여쓰기는 margin 하위 요소들을 본다. 최소한 attributes 만 저장.
        info.paraprs[pid] = {"attrib": dict(pp.attrib)}

    for st in root.iter(f"{{{NS_HH}}}style"):
        info.styles.append(
            {
                "id": st.get("id", ""),
                "name": st.get("name", ""),
                "charPrIDRef": st.get("charPrIDRef", ""),
                "paraPrIDRef": st.get("paraPrIDRef", ""),
            }
        )

    return info


def _parse_page_setup(section_bytes: bytes) -> PageSetup:
    try:
        root = etree.fromstring(section_bytes)
    except etree.XMLSyntaxError as exc:
        _log.warning("section0.xml 파싱 실패 (%s). 기본 PageSetup 사용.", exc)
        return DEFAULT_PAGE_SETUP

    page_pr = None
    margin_el = None
    for el in root.iter():
        tag = etree.QName(el.tag).localname if isinstance(el.tag, str) else ""
        if tag == "pagePr" and page_pr is None:
            page_pr = el
        elif tag == "margin" and margin_el is None:
            margin_el = el
        if page_pr is not None and margin_el is not None:
            break

    if page_pr is None:
        return DEFAULT_PAGE_SETUP

    def mm(attr: str, default_mm: float) -> float:
        if margin_el is None:
            return default_mm
        raw = margin_el.get(attr, "")
        if not raw:
            return default_mm
        try:
            return round(int(raw) / HWPUNIT_PER_MM, 2)
        except ValueError:
            return default_mm

    # paper detect — width ~= 59528 (A4 210mm)
    paper = "A4"
    width = page_pr.get("width", "")
    if width and width.isdigit():
        w_mm = int(width) / HWPUNIT_PER_MM
        if abs(w_mm - 210.0) > 5:  # ±5mm 이상이면 비 A4
            paper = f"{w_mm:.0f}mm"

    return PageSetup(
        paper=paper,
        margin_top_mm=mm("top", DEFAULT_PAGE_SETUP.margin_top_mm),
        margin_bottom_mm=mm("bottom", DEFAULT_PAGE_SETUP.margin_bottom_mm),
        margin_left_mm=mm("left", DEFAULT_PAGE_SETUP.margin_left_mm),
        margin_right_mm=mm("right", DEFAULT_PAGE_SETUP.margin_right_mm),
        header_mm=mm("header", DEFAULT_PAGE_SETUP.header_mm),
        footer_mm=mm("footer", DEFAULT_PAGE_SETUP.footer_mm),
    )


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _match_level_by_name(info: _HeaderInfo) -> dict[int, dict]:
    """style.name 휴리스틱으로 level → style dict 매핑."""
    result: dict[int, dict] = {}
    for style in info.styles:
        level = NAME_TO_LEVEL.get(style["name"].strip())
        if level is None:
            continue
        if level in result:
            continue
        result[level] = style
    return result


def _match_level_by_size(info: _HeaderInfo, excluded_levels: set[int]) -> dict[int, str]:
    """이름 매칭이 실패한 레벨을 폰트 크기로 보완. 반환은 level → charPr ID."""
    # 레벨별 candidate charPr id 를 size 로 찾는다
    result: dict[int, str] = {}
    # charPr 를 size 별로 모음
    size_to_charprs: dict[float, list[str]] = {}
    for cid, attrs in info.charprs.items():
        size_to_charprs.setdefault(attrs["height_pt"], []).append(cid)

    for size, candidate_levels in SIZE_CANDIDATE_LEVELS.items():
        cids = size_to_charprs.get(size, [])
        for level in candidate_levels:
            if level in excluded_levels:
                continue
            if not cids:
                break
            result[level] = cids[0]  # 첫 번째 사용 (heuristic)
            cids = cids[1:]  # 다음 레벨엔 다른 것 사용 시도
    return result


def _build_style_map(
    info: _HeaderInfo,
    page_setup: PageSetup,
    template_path: PathLike,
) -> StyleMap:
    sm = StyleMap(template_path=str(template_path), page_setup=page_setup)

    # 1. Name heuristic
    by_name = _match_level_by_name(info)
    for level, style in by_name.items():
        cid = style["charPrIDRef"]
        pid = style["paraPrIDRef"]
        style_id = style["id"]
        sm.level_to_ids[level] = EngineStyleIDs(para=pid, char=cid, style=style_id)
        sm.level_to_name[level] = style["name"]
        cp = info.charprs.get(cid)
        if cp:
            sm.level_to_font_size_pt[level] = cp["height_pt"]

    # 2. Size heuristic for remaining levels (1~10)
    missing = {lv for lv in range(1, 11) if lv not in sm.level_to_ids}
    if missing:
        by_size = _match_level_by_size(info, excluded_levels=set(sm.level_to_ids.keys()))
        for level, cid in by_size.items():
            if level not in missing:
                continue
            # paraPr 는 찾기 어려우니 fallback 의 것을 사용
            fallback = _fallback_for_level(level)
            sm.level_to_ids[level] = EngineStyleIDs(
                para=fallback.para, char=cid, style=fallback.style
            )
            sm.fallback_used_levels.add(level)
            cp = info.charprs.get(cid)
            if cp:
                sm.level_to_font_size_pt[level] = cp["height_pt"]

    # 3. Still missing → hardcoded fallback
    for level in range(1, 11):
        if level not in sm.level_to_ids:
            fb = _fallback_for_level(level)
            sm.level_to_ids[level] = fb
            sm.fallback_used_levels.add(level)
            if level in DEFAULT_STYLE_MAP:
                sm.level_to_font_size_pt[level] = float(DEFAULT_STYLE_MAP[level].size)

    # body (level 0) 도 반드시 채움
    if 0 not in sm.level_to_ids:
        sm.level_to_ids[0] = EngineStyleIDs(
            para=V1_TYPE_STYLE_MAP["body"].para,
            char=V1_TYPE_STYLE_MAP["body"].char,
            style=V1_TYPE_STYLE_MAP["body"].style,
        )

    return sm


def _fallback_for_level(level: int) -> EngineStyleIDs:
    """V1_TYPE_STYLE_MAP 에서 해당 레벨의 기본 ID 를 꺼낸다."""
    from ..parser.ir_schema import LEVEL_TO_V1_TYPE

    t = LEVEL_TO_V1_TYPE.get(level)
    if t and t in V1_TYPE_STYLE_MAP:
        return V1_TYPE_STYLE_MAP[t]
    return V1_TYPE_STYLE_MAP["body"]


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def analyze(hwpx_path: PathLike) -> StyleMap:
    """HWPX 템플릿 파일에서 :class:`StyleMap` 을 추출."""
    path = Path(hwpx_path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    if path.suffix.lower() != ".hwpx":
        _log.warning("확장자가 .hwpx 가 아닙니다: %s", path)

    with zipfile.ZipFile(path, "r") as z:
        try:
            hdr = z.read("Contents/header.xml")
        except KeyError as exc:
            raise ValueError("HWPX 에 Contents/header.xml 이 없습니다") from exc
        try:
            sec = z.read("Contents/section0.xml")
        except KeyError:
            sec = b""

    info = _parse_header(hdr)
    page_setup = _parse_page_setup(sec) if sec else DEFAULT_PAGE_SETUP
    sm = _build_style_map(info, page_setup, path)

    if sm.fallback_used_levels:
        _log.info(
            "템플릿 스타일 추출: level %s 는 fallback 사용 (%s)",
            sorted(sm.fallback_used_levels),
            path.name,
        )
    return sm


__all__ = [
    "NAME_TO_LEVEL",
    "SIZE_CANDIDATE_LEVELS",
    "StyleMap",
    "analyze",
]
