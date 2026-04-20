"""HWPX → HTML 렌더러 (미리보기 용).

기획안 4.8 "미리보기 탭" 용. QTextBrowser 에 바로 꽂아서 볼 수 있는 HTML 문자열을
돌려준다. 완전 재현이 아니라 **계층/서식 대략 감 잡기** 가 목표.

접근
----
1. ``Contents/header.xml`` 에서 fontface, charPr, paraPr, style 수집
2. ``Contents/section0.xml`` 의 각 ``<hp:p>`` 를 순회
3. paraPrIDRef / charPrIDRef 로 CSS 스타일 구성 (font, size, bold, indent)
4. 표는 ``<table>`` 로, 단락은 ``<p>`` 로 렌더링

참고: v1 ``scripts/visualize_hwpx.py`` 의 접근을 v2 로 단순화/모듈화.

QTextBrowser 주의점
------------------
QTextBrowser 는 HTML 4 서브셋만 지원. 지원하는 것:
- ``<p>``, ``<b>``, ``<i>``, ``<u>``, ``<font>``, ``<span style="...">``
- ``<table>``, ``<tr>``, ``<td>``, ``<th>`` (단, cellspacing/border 제한적)
- inline CSS: color, font-family, font-size, font-weight, margin-left
"""
from __future__ import annotations

import html
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

from lxml import etree


PathLike = Union[str, Path]

NS_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
NS_HH = "http://www.hancom.co.kr/hwpml/2011/head"


# ---------------------------------------------------------------------------
# Header parse
# ---------------------------------------------------------------------------

@dataclass
class _CharStyle:
    font: str = ""
    size_pt: float = 10.0
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color: str = "#000000"


@dataclass
class _ParaStyle:
    indent_em: float = 0.0
    margin_top_px: int = 0
    margin_bottom_px: int = 0


@dataclass
class _HeaderData:
    fontfaces: dict[str, str] = field(default_factory=dict)        # id → name
    char_prs: dict[str, _CharStyle] = field(default_factory=dict)  # id → style
    para_prs: dict[str, _ParaStyle] = field(default_factory=dict)
    styles: dict[str, dict] = field(default_factory=dict)          # id → {name, charPrIDRef, paraPrIDRef}


def _parse_header(header_bytes: bytes) -> _HeaderData:
    root = etree.fromstring(header_bytes)
    data = _HeaderData()

    for ff in root.iter(f"{{{NS_HH}}}fontface"):
        fid = ff.get("id", "")
        name = ff.get("name", "")
        if fid:
            data.fontfaces[fid] = name

    for cp in root.iter(f"{{{NS_HH}}}charPr"):
        cid = cp.get("id", "")
        if not cid:
            continue
        style = _CharStyle()
        height_raw = cp.get("height", "0")
        try:
            style.size_pt = int(height_raw) / 100.0
        except ValueError:
            style.size_pt = 10.0

        for child in cp.iter():
            tag = etree.QName(child.tag).localname if isinstance(child.tag, str) else ""
            if tag == "fontRef":
                hid = child.get("hangul", "")
                style.font = data.fontfaces.get(hid, "")
            elif tag == "bold":
                style.bold = True
            elif tag == "italic":
                style.italic = True
            elif tag == "underline":
                style.underline = True

        data.char_prs[cid] = style

    for pp in root.iter(f"{{{NS_HH}}}paraPr"):
        pid = pp.get("id", "")
        if not pid:
            continue
        ps = _ParaStyle()
        # indent: <hh:margin><hh:left/right/indent ...>
        for child in pp.iter():
            tag = etree.QName(child.tag).localname if isinstance(child.tag, str) else ""
            if tag == "indent":
                val = child.get("value", "0")
                try:
                    # HWPUNIT → 대략 em 환산 (1em ≈ 2000 units)
                    ps.indent_em = int(val) / 2000.0
                except ValueError:
                    pass
        data.para_prs[pid] = ps

    for st in root.iter(f"{{{NS_HH}}}style"):
        sid = st.get("id", "")
        if sid:
            data.styles[sid] = {
                "name": st.get("name", ""),
                "charPrIDRef": st.get("charPrIDRef", ""),
                "paraPrIDRef": st.get("paraPrIDRef", ""),
            }

    return data


# ---------------------------------------------------------------------------
# Section render
# ---------------------------------------------------------------------------

def _char_style_css(style: _CharStyle) -> str:
    parts: list[str] = []
    if style.font:
        # 한글 폰트가 시스템에 없을 수 있어 fallback 체인
        parts.append(f"font-family: '{style.font}', '맑은 고딕', sans-serif")
    if style.size_pt:
        parts.append(f"font-size: {style.size_pt:.1f}pt")
    if style.bold:
        parts.append("font-weight: bold")
    if style.italic:
        parts.append("font-style: italic")
    if style.underline:
        parts.append("text-decoration: underline")
    if style.color and style.color != "#000000":
        parts.append(f"color: {style.color}")
    return "; ".join(parts)


def _para_style_css(style: _ParaStyle) -> str:
    parts: list[str] = []
    if style.indent_em > 0:
        parts.append(f"margin-left: {style.indent_em:.1f}em")
    if style.margin_top_px:
        parts.append(f"margin-top: {style.margin_top_px}px")
    if style.margin_bottom_px:
        parts.append(f"margin-bottom: {style.margin_bottom_px}px")
    return "; ".join(parts)


def _extract_text(run_elem) -> str:
    """``<hp:run>`` 에서 모든 ``<hp:t>`` 텍스트 합치기."""
    parts: list[str] = []
    for t in run_elem.iter(f"{{{NS_HP}}}t"):
        if t.text:
            parts.append(t.text)
    return "".join(parts)


def _render_paragraph(p_elem, header: _HeaderData) -> str:
    para_id = p_elem.get("paraPrIDRef", "")
    para_style = header.para_prs.get(para_id, _ParaStyle())
    para_css = _para_style_css(para_style)

    # 텍스트 run 들을 <span> 으로 래핑
    pieces: list[str] = []
    for run in p_elem.iterchildren(f"{{{NS_HP}}}run"):
        char_id = run.get("charPrIDRef", "")
        char_style = header.char_prs.get(char_id, _CharStyle())
        text = _extract_text(run)
        if not text:
            continue
        safe = html.escape(text).replace("\t", "&nbsp;&nbsp;&nbsp;&nbsp;")
        css = _char_style_css(char_style)
        if css:
            pieces.append(f'<span style="{css}">{safe}</span>')
        else:
            pieces.append(safe)

    if not pieces:
        # 빈 단락도 공간을 유지하도록
        return "<p style=\"margin:0\">&nbsp;</p>"

    style_attr = f' style="margin:0; {para_css}"' if para_css else ' style="margin:0"'
    return f"<p{style_attr}>{''.join(pieces)}</p>"


def _render_table(tbl_elem, header: _HeaderData) -> str:
    rows_html: list[str] = []
    for tr in tbl_elem.iterchildren(f"{{{NS_HP}}}tr"):
        cells_html: list[str] = []
        for tc in tr.iterchildren(f"{{{NS_HP}}}tc"):
            cell_paras: list[str] = []
            # cell 안의 subList 또는 직접 p 처리
            for sub in tc.iter(f"{{{NS_HP}}}p"):
                cell_paras.append(_render_paragraph(sub, header))
            cell_html = "".join(cell_paras) or "&nbsp;"
            cells_html.append(f'<td style="border:1px solid #888; padding:4px; vertical-align:top">{cell_html}</td>')
        rows_html.append(f"<tr>{''.join(cells_html)}</tr>")

    return (
        '<table cellspacing="0" cellpadding="0" '
        'style="border-collapse: collapse; margin: 6px 0">'
        + "".join(rows_html) + "</table>"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_hwpx_to_html(hwpx_path: PathLike, *, max_chars: int = 500_000) -> str:
    """HWPX 파일 → HTML 문자열. QTextBrowser 에 setHtml() 로 바로 표시 가능.

    Parameters
    ----------
    max_chars : 반환 HTML 길이 상한 (아주 큰 문서 대응). 넘으면 자르고 안내 문구 추가.
    """
    path = Path(hwpx_path)
    if not path.exists():
        raise FileNotFoundError(str(path))

    with zipfile.ZipFile(path, "r") as z:
        try:
            hdr = z.read("Contents/header.xml")
        except KeyError as exc:
            raise ValueError("HWPX 에 Contents/header.xml 이 없음") from exc
        # 여러 섹션 대응
        sec_names = sorted(
            n for n in z.namelist()
            if n.startswith("Contents/section") and n.endswith(".xml")
        )
        if not sec_names:
            raise ValueError("HWPX 에 Contents/section*.xml 섹션이 없음 (손상된 파일?)")
        sections = [z.read(n) for n in sec_names]

    header = _parse_header(hdr)

    body_parts: list[str] = []
    for sec_bytes in sections:
        sec_root = etree.fromstring(sec_bytes)
        for child in sec_root:
            tag = etree.QName(child.tag).localname if isinstance(child.tag, str) else ""
            if tag == "p":
                # 표 포함 단락이면 표 먼저 렌더
                tbls = list(child.iter(f"{{{NS_HP}}}tbl"))
                if tbls:
                    for tbl in tbls:
                        body_parts.append(_render_table(tbl, header))
                else:
                    body_parts.append(_render_paragraph(child, header))

    body = "\n".join(body_parts)
    if len(body) > max_chars:
        body = body[:max_chars] + (
            f'\n<p style="color:#999; font-style: italic">'
            f'… (미리보기가 {max_chars:,} 문자에서 잘렸습니다) </p>'
        )

    title = html.escape(path.name)
    return (
        '<html><head><meta charset="utf-8">'
        '<style>'
        'body { font-family: "맑은 고딕", sans-serif; line-height: 1.5; '
        'background-color: #fafafa; padding: 16px; color: #222; }'
        'p { margin: 0.2em 0; }'
        '</style></head><body>'
        f'<h2 style="color:#555; border-bottom:1px solid #ccc; padding-bottom:4px">{title}</h2>'
        f'{body}'
        '</body></html>'
    )


__all__ = ["render_hwpx_to_html"]
