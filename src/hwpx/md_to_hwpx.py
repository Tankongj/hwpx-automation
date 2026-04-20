"""Markdown/IR → HWPX 변환 엔진.

v1(Tankongj/hwpx-proposal-automation) 의 `scripts/md_to_hwpx.py` 를 v2 로 포팅.
53 이상의 iteration 으로 검증된 XML 조작(Style Remapper, Font Remapper, Bullet
Dedup, linesegarray 캐시 제거, secPr 보존 등) 을 **그대로 유지**하면서,

새 공개 API
-----------
- :func:`convert_markdown(template, md, output, ...)` — 마크다운 본문을 바로 변환
- :func:`convert(blocks, template, output, ...)` — IR :class:`Block` 리스트를 변환
- :func:`parse_markdown(md_text) -> list[dict]` — 내부 파서 (W2 regex_parser 이전 임시 사용)

기존 CLI 동작(`python -m src.hwpx.md_to_hwpx --template ... --md ... --output ...`) 은
그대로 유지한다.

이 모듈이 끝나고 나면 :mod:`src.hwpx.fix_namespaces` 로 네임스페이스 후처리를 반드시
돌려야 한다 (CLI 는 자동으로 실행).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import zipfile
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

from lxml import etree

# Force UTF-8 stdout on Windows
if sys.stdout and sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except (AttributeError, ValueError):
        pass


NS_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"

PathLike = Union[str, Path]


# ============================================================
# Default style map — Ctrl+1~0 mapping
# (v1 hardcoded IDs; will be superseded by template_analyzer output in W2)
# ============================================================
DEFAULT_STYLE_MAP: dict[str, dict[str, str]] = {
    "H1":    {"para": "3",  "char": "19", "style": "0"},   # Ⅰ  Ctrl+1
    "H2":    {"para": "3",  "char": "18", "style": "0"},   # 1  Ctrl+2
    "H3":    {"para": "8",  "char": "20", "style": "7"},   # 1) Ctrl+3
    "H4":    {"para": "9",  "char": "21", "style": "6"},   # (1) Ctrl+4
    "H5":    {"para": "10", "char": "2",  "style": "0"},   # ①  Ctrl+5
    "L1":    {"para": "11", "char": "2",  "style": "1"},   # □  Ctrl+6
    "L2":    {"para": "12", "char": "2",  "style": "2"},   # ❍  Ctrl+7
    "L3":    {"para": "13", "char": "22", "style": "3"},   # -  Ctrl+8
    "L4":    {"para": "14", "char": "22", "style": "4"},   # ·  Ctrl+9
    "note":  {"para": "15", "char": "1",  "style": "5"},   # *  Ctrl+0
    "body":  {"para": "11", "char": "2",  "style": "0"},
    "empty": {"para": "11", "char": "2",  "style": "0"},
}


def load_config(config_path: Optional[PathLike]) -> dict[str, dict[str, str]]:
    """YAML config 에서 style 정의를 덮어쓴다. PyYAML 미설치 시 기본 사용."""
    if config_path is None:
        return dict(DEFAULT_STYLE_MAP)

    try:
        import yaml  # type: ignore
    except ImportError:
        print("WARNING: PyYAML not installed. Using default styles.", file=sys.stderr)
        return dict(DEFAULT_STYLE_MAP)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    style_map: dict[str, dict[str, str]] = dict(DEFAULT_STYLE_MAP)
    for key, vals in (config.get("styles") or {}).items():
        style_map[key] = {
            "para": str(vals.get("paraPrIDRef", "0")),
            "char": str(vals.get("charPrIDRef", "0")),
            "style": str(vals.get("styleIDRef", "0")),
        }
    return style_map


# ============================================================
# Paragraph builders
# ============================================================

def create_paragraph(
    text: str,
    para_ref: str,
    char_ref: str,
    style_ref: str = "0",
    page_break: bool = False,
    ns_map: Optional[dict] = None,
):
    """HWPX ``<hp:p>`` 단락 요소 생성."""
    if ns_map:
        p = etree.Element(f"{{{NS_HP}}}p", nsmap=ns_map)
    else:
        p = etree.Element(f"{{{NS_HP}}}p")
    p.set("paraPrIDRef", str(para_ref))
    p.set("styleIDRef", str(style_ref))
    p.set("pageBreak", "1" if page_break else "0")
    p.set("columnBreak", "0")
    p.set("merged", "0")

    run = etree.SubElement(p, f"{{{NS_HP}}}run")
    run.set("charPrIDRef", str(char_ref))
    t = etree.SubElement(run, f"{{{NS_HP}}}t")
    t.text = text
    return p


def remove_lineseg(elem) -> None:
    """모든 ``linesegarray`` 제거. deepcopy 이후 glyph 깨짐 방지용 필수 단계.

    See: docs/style-system.md rule #2
    """
    for ls in list(elem.iter(f"{{{NS_HP}}}linesegarray")):
        parent = ls.getparent()
        if parent is not None:
            parent.remove(ls)


# paraPr IDs whose <hh:heading type="NUMBER"> causes double-numbering against
# NumberingTracker text prefixes. Default 10단계 스펙의 H3/H4/H5.
AUTO_NUMBER_PARAPR_IDS: set[str] = {"8", "9", "10"}


def disable_heading_auto_numbering(
    header_bytes: bytes | str,
    target_ids: Optional[Iterable[str]] = None,
) -> bytes:
    """``header.xml`` 의 지정된 paraPr 들에서 ``heading type="NUMBER"`` → ``"NONE"`` 으로 패치."""
    ids_to_patch = set(target_ids) if target_ids is not None else AUTO_NUMBER_PARAPR_IDS
    if isinstance(header_bytes, str):
        root = etree.fromstring(header_bytes.encode("utf-8"))
    else:
        root = etree.fromstring(header_bytes)
    patched = 0
    for parapr in root.iter():
        tag = parapr.tag.split("}")[-1] if "}" in parapr.tag else parapr.tag
        if tag != "paraPr":
            continue
        if parapr.get("id", "") not in ids_to_patch:
            continue
        for heading in parapr.iter():
            htag = heading.tag.split("}")[-1] if "}" in heading.tag else heading.tag
            if htag == "heading" and heading.get("type") == "NUMBER":
                heading.set("type", "NONE")
                patched += 1
    if patched:
        print(f"  -> Disabled auto-numbering on {patched} paraPr heading(s)")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


# ============================================================
# Front-page dynamic content update (reference document mode)
# ============================================================

def update_cover_text(front_paras, title: str, ns: str) -> None:
    """커버 제목 교체. [0] 단락은 비우고 [3] 단락의 2번째 run 을 title 로."""
    if len(front_paras) < 4:
        return

    p0 = front_paras[0]
    for t in p0.iter(f"{{{ns}}}t"):
        t.text = ""

    p3 = front_paras[3]
    runs_with_text: list[tuple] = []
    for run in p3.iter(f"{{{ns}}}run"):
        for t in run.iter(f"{{{ns}}}t"):
            if t.text and t.text.strip():
                runs_with_text.append((run, t))

    if len(runs_with_text) >= 2:
        runs_with_text[0][1].text = ""
        runs_with_text[1][1].text = title
    if len(runs_with_text) >= 3:
        runs_with_text[2][1].text = "- 정성제안서 -"


def update_toc(front_paras, paragraphs: list[dict], ns: str) -> None:
    """TOC 셀(18번 단락 안 표) 의 항목을 본문 제목들로 다시 빌드한다."""
    if len(front_paras) < 19:
        return

    p18 = front_paras[18]
    tbls = list(p18.iter(f"{{{ns}}}tbl"))
    if not tbls:
        return
    tbl = tbls[0]
    trs = list(tbl.iter(f"{{{ns}}}tr"))
    if len(trs) < 3:
        return
    tr2 = trs[2]
    tcs = list(tr2.iter(f"{{{ns}}}tc"))
    if not tcs:
        return
    tc0 = tcs[0]

    sub_lists = tc0.findall(f"{{{ns}}}subList")
    if not sub_lists:
        return
    sub_list = sub_lists[0]

    cell_paras = sub_list.findall(f"{{{ns}}}p")
    if len(cell_paras) < 2:
        return

    h1_style = h2_style = h3_style = None
    for cp in cell_paras:
        pp = cp.get("paraPrIDRef", "")
        if pp in ("16", "27") and h1_style is None:
            h1_style = cp
        elif pp in ("22", "26") and h2_style is None:
            h2_style = cp
        elif pp in ("433", "434", "25") and h3_style is None:
            h3_style = cp
    if h1_style is None:
        h1_style = cell_paras[1] if len(cell_paras) > 1 else cell_paras[0]
    if h2_style is None:
        h2_style = h1_style
    if h3_style is None:
        h3_style = h2_style

    headings = [
        {"type": p["type"], "text": p["text"]}
        for p in paragraphs
        if p["type"] in ("H1", "H2", "H3")
    ]
    if not headings:
        return

    for p in cell_paras:
        sub_list.remove(p)

    empty_p = deepcopy(cell_paras[0])
    for t in empty_p.iter(f"{{{ns}}}t"):
        t.text = ""
    remove_lineseg(empty_p)
    sub_list.append(empty_p)

    for heading in headings:
        htype = heading["type"]
        text = heading["text"]
        template = {"H1": h1_style, "H2": h2_style}.get(htype, h3_style)

        new_p = deepcopy(template)
        remove_lineseg(new_p)

        for t_elem in new_p.iter(f"{{{ns}}}t"):
            for tab in list(t_elem):
                if tab.tag.endswith("}tab"):
                    t_elem.remove(tab)

        runs = list(new_p.iter(f"{{{ns}}}run"))
        if runs:
            first_t = None
            for t in runs[0].iter(f"{{{ns}}}t"):
                first_t = t
                break
            if first_t is not None:
                if htype == "H1":
                    parts = text.split(". ", 1)
                    if len(parts) == 2:
                        first_t.text = parts[0]
                        if len(runs) > 1:
                            for t2 in runs[1].iter(f"{{{ns}}}t"):
                                t2.text = f". {parts[1]}"
                                break
                        else:
                            first_t.text = text
                    else:
                        first_t.text = text
                    for run in runs:
                        run.set("charPrIDRef", "28")
                elif htype == "H3":
                    first_t.text = f" {text}"
                    for run in runs:
                        run.set("charPrIDRef", "16")
                else:
                    first_t.text = text

                for ri, run in enumerate(runs):
                    if htype == "H1" and ri <= 1:
                        continue
                    if ri == 0:
                        continue
                    for t in run.iter(f"{{{ns}}}t"):
                        for child in list(t):
                            t.remove(child)
                        t.text = ""

            last_run = runs[-1] if runs else None
            if last_run is not None:
                last_t = None
                for t in last_run.iter(f"{{{ns}}}t"):
                    last_t = t
                    break
                if last_t is not None:
                    tab_elem = etree.SubElement(last_t, f"{{{ns}}}tab")
                    tab_elem.set("width", "30000")
                    tab_elem.set("leader", "3")
                    tab_elem.set("type", "2")
                    tab_elem.tail = ""

        sub_list.append(new_p)


def update_summary_table(
    front_paras,
    paragraphs: list[dict],
    eval_mapping: Optional[list[dict]],
    ns: str,
) -> None:
    """조견표 셀 업데이트. '상생' 포함 row 는 행 자체 삭제."""
    if len(front_paras) < 22:
        return

    toc_para = None
    for idx in (20, 21):
        if idx < len(front_paras):
            tbls = list(front_paras[idx].iter(f"{{{ns}}}tbl"))
            if tbls:
                toc_para = front_paras[idx]
                break
    if toc_para is None:
        return

    tbls = list(toc_para.iter(f"{{{ns}}}tbl"))
    if not tbls:
        return
    tbl = tbls[0]
    trs = list(tbl.iter(f"{{{ns}}}tr"))

    for tr in reversed(trs):
        tcs = list(tr.iter(f"{{{ns}}}tc"))
        row_text = ""
        for tc in tcs:
            for t in tc.iter(f"{{{ns}}}t"):
                if t.text:
                    row_text += t.text
        if "상생" in row_text:
            tbl.remove(tr)
            trs.remove(tr)

    if not eval_mapping:
        return

    for mapping in eval_mapping:
        row_idx = mapping.get("row", -1)
        cell_values = mapping.get("cells", [])
        if row_idx < 0 or row_idx >= len(trs):
            continue
        tr = trs[row_idx]
        tcs = tr.findall(f"{{{ns}}}tc")
        for ci, val in enumerate(cell_values):
            if ci >= len(tcs) or not val:
                continue
            tc = tcs[ci]
            t_elems = list(tc.iter(f"{{{ns}}}t"))
            for t in t_elems:
                t.text = ""
            if t_elems:
                t_elems[0].text = val


# ============================================================
# Auto-bullet deduplication
# ============================================================

AUTO_PREFIX_PATTERNS: dict[str, re.Pattern[str]] = {
    "L1": re.compile(r"^[□■]\s*"),
    "L2": re.compile(r"^[❍○]\s*"),
    "L3": re.compile(r"^[-\u2013\u2014]\s*"),
    "L4": re.compile(r"^[·•]\s*"),
    "note": re.compile(r"^\*\s*"),
}


def strip_auto_prefixes(paragraphs: list[dict]) -> list[dict]:
    """HWPX 스타일이 자동 삽입하는 불릿 심볼과 본문이 겹치지 않도록 선두를 제거."""
    result: list[dict] = []
    for para in paragraphs:
        text = para.get("text", "")
        pattern = AUTO_PREFIX_PATTERNS.get(para.get("type"))
        if pattern:
            text = pattern.sub("", text)
        result.append({**para, "text": text})
    return result


# ============================================================
# Numbering tracker — 상위 레벨 변경 시 하위 자동 리셋
# ============================================================

CIRCLED_NUMBERS: list[str] = [chr(0x2460 + i) for i in range(20)]


class NumberingTracker:
    NUMBERED_LEVELS = ["H3", "H4", "H5"]
    RESET_MAP = {
        "H1": ["H3", "H4", "H5"],
        "H2": ["H3", "H4", "H5"],
        "H3": ["H4", "H5"],
        "H4": ["H5"],
    }

    def __init__(self) -> None:
        self.counters = {lv: 0 for lv in self.NUMBERED_LEVELS}

    def on_level_enter(self, level: str) -> None:
        for child in self.RESET_MAP.get(level, []):
            self.counters[child] = 0

    def next_number(self, level: str) -> str:
        self.counters[level] += 1
        n = self.counters[level]
        if level == "H3":
            return f"{n})"
        if level == "H4":
            return f"({n})"
        if level == "H5":
            return CIRCLED_NUMBERS[n - 1] if n <= 20 else f"({n})"
        return str(n)


# ============================================================
# File version manager (v1 호환, CLI --output auto 용)
# ============================================================

DEFAULT_FILE_PREFIX = "3. 정성제안서_2026년 귀농귀촌 아카데미 운영 및 온라인콘텐츠 제작"


def get_max_version(search_dir: PathLike) -> int:
    search_dir = str(search_dir)
    max_v = 0
    dirs_to_scan = [search_dir]
    archive_dir = os.path.join(search_dir, "99_과거")
    if os.path.isdir(archive_dir):
        dirs_to_scan.append(archive_dir)
    for d in dirs_to_scan:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            m = re.search(r"_v(\d{3})", f)
            if m:
                v = int(m.group(1))
                if v > max_v:
                    max_v = v
    return max_v


def generate_versioned_path(output_dir: PathLike, prefix: str = DEFAULT_FILE_PREFIX) -> str:
    next_v = get_max_version(output_dir) + 1
    today = date.today().strftime("%y%m%d")
    filename = f"{prefix}_{today}_v{next_v:03d}.hwpx"
    return os.path.join(str(output_dir), filename)


def archive_old_versions(output_dir: PathLike, current_file: PathLike) -> None:
    output_dir = str(output_dir)
    archive_dir = os.path.join(output_dir, "99_과거")
    os.makedirs(archive_dir, exist_ok=True)
    current_name = os.path.basename(str(current_file))
    moved = 0
    for f in os.listdir(output_dir):
        if f == current_name or not f.endswith(".hwpx"):
            continue
        src = os.path.join(output_dir, f)
        if os.path.isfile(src):
            dst = os.path.join(archive_dir, f)
            if os.path.exists(dst):
                os.remove(dst)
            os.rename(src, dst)
            moved += 1
    if moved:
        print(f"  → Archived {moved} old file(s) to 99_과거/")


# ============================================================
# Markdown parser (임시, W2 regex_parser 도입 전까지 유지)
# ============================================================

def parse_markdown(md_content: str) -> list[dict]:
    """마크다운 문자열 → v1 paragraph dict 리스트.

    계층 매핑:
        #  → H1 (Ⅰ.)     Ctrl+1
        ## → H2 (1)      Ctrl+2
        ###→ H3 (1))     Ctrl+3  (자동 번호)
        ####→ H4 ((1))   Ctrl+4  (자동 번호)
        #####→ H5 (①)    Ctrl+5  (자동 번호)
        □  → L1          Ctrl+6
        ❍/○→ L2          Ctrl+7
        -  → L3          Ctrl+8
        ·  → L4          Ctrl+9
        */※→ note        Ctrl+0
    """
    paragraphs: list[dict] = []
    lines = md_content.split("\n")
    tracker = NumberingTracker()
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()

        if not stripped or stripped.startswith("---") or stripped.startswith("```"):
            i += 1
            continue
        if stripped.startswith("> "):
            i += 1
            continue

        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            text = stripped.lstrip("#").strip()
            if level == 1:
                if not re.match(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]", text):
                    i += 1
                    continue
                tracker.on_level_enter("H1")
                paragraphs.append({"type": "H1", "text": text})
            elif level == 2:
                tracker.on_level_enter("H2")
                text = re.sub(r"^\d+\.\s*", "", text)
                paragraphs.append({"type": "H2", "text": text})
            elif level == 3:
                tracker.on_level_enter("H3")
                text = re.sub(r"^\d+\)\s*", "", text)
                num = tracker.next_number("H3")
                paragraphs.append({"type": "H3", "text": f"{num} {text}"})
            elif level == 4:
                tracker.on_level_enter("H4")
                text = re.sub(r"^\(\d+\)\s*", "", text)
                num = tracker.next_number("H4")
                paragraphs.append({"type": "H4", "text": f"{num} {text}"})
            elif level == 5:
                tracker.on_level_enter("H5")
                text = re.sub(r"^[\u2460-\u2473]\s*", "", text)
                num = tracker.next_number("H5")
                paragraphs.append({"type": "H5", "text": f"{num} {text}"})
            i += 1
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            table_lines: list[str] = []
            while i < len(lines):
                s = lines[i].strip()
                if s.startswith("|") and s.endswith("|"):
                    table_lines.append(s)
                    i += 1
                else:
                    break
            if len(table_lines) >= 2:
                headers = [c.strip() for c in table_lines[0].split("|")[1:-1]]
                for tl in table_lines[1:]:
                    if re.match(r"^\|[\s\-|:]+\|$", tl):
                        continue
                    cells = [c.strip() for c in tl.split("|")[1:-1]]
                    first = cells[0] if cells else ""
                    details: list[str] = []
                    for ci in range(1, min(len(headers), len(cells))):
                        if cells[ci]:
                            details.append(f"{headers[ci]}: {cells[ci]}")
                    text = f"{first} — {', '.join(details)}" if details else first
                    paragraphs.append({"type": "L1", "text": text})
            continue

        text = re.sub(r"\*\*(.*?)\*\*", r"\1", stripped)

        h4_match = re.match(r"^\((\d+)\)\s+(.+)", text)
        if h4_match:
            tracker.on_level_enter("H4")
            paragraphs.append({"type": "H4", "text": text})
        elif re.match(r"^[\u2460-\u2473]\s", text) or re.match(r"^[\u2460-\u2473][^\s]", text):
            tracker.on_level_enter("H5")
            paragraphs.append({"type": "H5", "text": text})
        elif text.startswith("□"):
            paragraphs.append({"type": "L1", "text": text})
        elif text.startswith("○") or text.startswith("❍"):
            paragraphs.append({"type": "L2", "text": text})
        elif text.startswith("·"):
            paragraphs.append({"type": "L4", "text": text})
        elif text.startswith("※"):
            paragraphs.append({"type": "note", "text": text})
        elif stripped.startswith("- "):
            paragraphs.append({"type": "L3", "text": stripped[2:].strip()})
        elif stripped.startswith("* "):
            paragraphs.append({"type": "note", "text": stripped[2:].strip()})
        else:
            paragraphs.append({"type": "L1", "text": text})

        i += 1

    return paragraphs


# ============================================================
# Style remapper (reference document merge)
# ============================================================

def merge_reference_styles(tmpl_hdr_root, ref_hdr_root, ref_paras, para_range):
    """Reference HWPX 의 스타일/폰트를 template header 로 병합.

    v1 로직 그대로. 본 MVP 의 기본 경로에서는 쓰지 않고, reference 옵션 사용 시에만 호출.
    """
    HH_NS = "http://www.hancom.co.kr/hwpml/2011/head"  # noqa: F841 - 원본 주석 유지

    bf_needed, cp_needed, pp_needed = set(), set(), set()
    for i in range(min(para_range[1], len(ref_paras))):
        if i < para_range[0]:
            continue
        p = ref_paras[i]
        for elem in p.iter():
            ppr = elem.get("paraPrIDRef", "")
            if ppr:
                pp_needed.add(ppr)
            cpr = elem.get("charPrIDRef", "")
            if cpr:
                cp_needed.add(cpr)
            bfr = elem.get("borderFillIDRef", "")
            if bfr:
                bf_needed.add(bfr)

    tmpl_fonts_by_name: dict[str, str] = {}
    tmpl_max_font_id = 0
    tmpl_fontfaces_container = None
    for elem in tmpl_hdr_root.iter():
        t = etree.QName(elem.tag).localname if isinstance(elem.tag, str) else ""
        if t == "fontface":
            name = elem.get("name")
            fid = int(elem.get("id", "0"))
            if name:
                tmpl_fonts_by_name[name] = str(fid)
            if fid > tmpl_max_font_id:
                tmpl_max_font_id = fid
        elif t == "fontfaces":
            tmpl_fontfaces_container = elem

    ref_font_elems: dict[str, object] = {}
    for elem in ref_hdr_root.iter():
        t = etree.QName(elem.tag).localname if isinstance(elem.tag, str) else ""
        if t == "fontface":
            ref_font_elems[elem.get("id")] = elem

    def get_remapped_font_id(ref_font_id):
        nonlocal tmpl_max_font_id
        if not ref_font_id:
            return None
        ref_elem = ref_font_elems.get(ref_font_id)
        if ref_elem is None:
            return ref_font_id
        ref_name = ref_elem.get("name")
        if ref_name in tmpl_fonts_by_name:
            return tmpl_fonts_by_name[ref_name]
        tmpl_max_font_id += 1
        new_id_str = str(tmpl_max_font_id)
        tmpl_fonts_by_name[ref_name] = new_id_str
        if tmpl_fontfaces_container is not None:
            new_elem = deepcopy(ref_elem)
            new_elem.set("id", new_id_str)
            tmpl_fontfaces_container.append(new_elem)
        return new_id_str

    style_id_maps: dict[str, dict[str, str]] = {"borderFill": {}, "charPr": {}, "paraPr": {}}
    container_map = {
        "borderFill": "borderFills",
        "charPr": "charProperties",
        "paraPr": "paraProperties",
    }
    merge_count = 0

    for tag_name, needed_ids in [
        ("borderFill", bf_needed),
        ("charPr", cp_needed),
        ("paraPr", pp_needed),
    ]:
        if not needed_ids:
            continue
        max_id = 0
        container = None
        for elem in tmpl_hdr_root.iter():
            t = etree.QName(elem.tag).localname if isinstance(elem.tag, str) else ""
            if t == tag_name:
                idx = int(elem.get("id", "0"))
                if idx > max_id:
                    max_id = idx
            elif t == container_map[tag_name]:
                container = elem
        if container is None:
            continue

        ref_map = {}
        for elem in ref_hdr_root.iter():
            t = etree.QName(elem.tag).localname if isinstance(elem.tag, str) else ""
            if t == tag_name and elem.get("id", "") in needed_ids:
                ref_map[elem.get("id", "")] = elem

        added = 0
        for old_id in list(needed_ids):
            if not old_id:
                continue
            ref_elem = ref_map.get(old_id)
            if ref_elem is None:
                continue
            max_id += 1
            new_id = str(max_id)
            style_id_maps[tag_name][old_id] = new_id
            new_elem = deepcopy(ref_elem)
            new_elem.set("id", new_id)

            if tag_name == "charPr":
                for child in new_elem.iter():
                    ct = etree.QName(child.tag).localname if isinstance(child.tag, str) else ""
                    if ct == "fontRef":
                        for attr in ["hangul", "latin", "hanja", "japanese", "other", "symbol", "user"]:
                            if attr in child.attrib:
                                mapped = get_remapped_font_id(child.get(attr))
                                if mapped:
                                    child.set(attr, mapped)

            if tag_name == "paraPr":
                bf_ref = new_elem.get("borderFillIDRef")
                if bf_ref and bf_ref in style_id_maps["borderFill"]:
                    new_elem.set("borderFillIDRef", style_id_maps["borderFill"][bf_ref])

            container.append(new_elem)
            added += 1
            merge_count += 1

        if added > 0:
            cnt_attr = "itemCnt" if "itemCnt" in container.attrib else "count"
            if cnt_attr in container.attrib:
                container.set(cnt_attr, str(int(container.get(cnt_attr, "0")) + added))

    print(f"  -> Merged {merge_count} style definitions via Remapper")
    return style_id_maps


def rewrite_section_ids(section_xml_str: str, style_id_maps: dict) -> str:
    def remap_attr(match, map_dict):
        old_val = match.group(2)
        new_val = map_dict.get(old_val, old_val)
        return f"{match.group(1)}{new_val}{match.group(3)}"

    for attr_name, map_dict in [
        ("paraPrIDRef", style_id_maps["paraPr"]),
        ("charPrIDRef", style_id_maps["charPr"]),
        ("borderFillIDRef", style_id_maps["borderFill"]),
    ]:
        def replacer(match, md=map_dict):
            return remap_attr(match, md)

        section_xml_str = re.sub(
            f'({attr_name}=")([^"]+)(")', replacer, section_xml_str
        )
    return section_xml_str


# ============================================================
# Core conversion
# ============================================================

def _convert_paragraphs(
    paragraphs: list[dict],
    *,
    template: PathLike,
    output: PathLike,
    style_map: dict[str, dict[str, str]],
    reference: Optional[PathLike] = None,
    cover_range: Optional[str] = None,
    toc_range: Optional[str] = None,
    summary_range: Optional[str] = None,
    cover_keywords: Sequence[str] = (),
    proposal_title: Optional[str] = None,
    summary_mapping_path: Optional[PathLike] = None,
    run_fix_namespaces: bool = True,
) -> Path:
    """내부 공통 변환 루틴 (paragraphs: v1 dict 리스트)."""
    template = Path(template)
    output = Path(output)

    if not template.exists():
        raise FileNotFoundError(f"Template not found: {template}")

    if output.exists():
        raise FileExistsError(
            f"Output file already exists (overwrite forbidden): {output}"
        )

    print(f"📄 Template:  {template}")
    print(f"📦 Output:    {output}")

    print(f"\n[1/6] Parsed {len(paragraphs)} paragraphs")

    paragraphs = strip_auto_prefixes(paragraphs)
    print("[2/6] Auto-prefix deduplication applied")

    with zipfile.ZipFile(template, "r") as z:
        section_xml = z.read("Contents/section0.xml")
        header_xml = z.read("Contents/header.xml")
        all_files = {
            name: z.read(name)
            for name in z.namelist()
            if name not in ("Contents/section0.xml", "Contents/header.xml")
        }

    section_tree = etree.fromstring(section_xml)
    ns_map = section_tree.nsmap
    existing_paras = section_tree.findall(f"{{{NS_HP}}}p")

    h1_template = deepcopy(existing_paras[1]) if len(existing_paras) > 1 else None
    h2_template = deepcopy(existing_paras[2]) if len(existing_paras) > 2 else None

    front_paras: list = []
    merged_header = header_xml

    if reference and Path(reference).exists():
        print("[3/6] Merging reference front pages into template")
        reference = Path(reference)
        with zipfile.ZipFile(reference, "r") as rz:
            ref_sec_xml = rz.read("Contents/section0.xml")
            ref_header_xml = rz.read("Contents/header.xml")

        tmpl_hdr_root = etree.fromstring(header_xml)
        ref_hdr_root = etree.fromstring(ref_header_xml)
        ref_root = etree.fromstring(ref_sec_xml)
        ref_paras = ref_root.findall(f"{{{NS_HP}}}p")

        max_para = len(ref_paras)
        ranges: list[tuple[int, int, str]] = []
        for r_arg, label in [(cover_range, "cover"), (toc_range, "toc"), (summary_range, "summary")]:
            if r_arg:
                start, end = map(int, r_arg.split(":"))
                ranges.append((start, min(end, max_para), label))

        if ranges:
            ref_front_ids: dict[str, set[str]] = {"paraPr": set(), "charPr": set(), "borderFill": set()}
            for start, end, _ in ranges:
                for i in range(start, min(end, len(ref_paras))):
                    for elem in ref_paras[i].iter():
                        for attr, key in [
                            ("paraPrIDRef", "paraPr"),
                            ("charPrIDRef", "charPr"),
                            ("borderFillIDRef", "borderFill"),
                        ]:
                            val = elem.get(attr, "")
                            if val:
                                ref_front_ids[key].add(val)

            for elem in ref_hdr_root.iter():
                tag = etree.QName(elem.tag).localname if isinstance(elem.tag, str) else ""
                if tag == "paraPr" and elem.get("id", "") in ref_front_ids["paraPr"]:
                    for child in elem.iter():
                        ct = etree.QName(child.tag).localname if isinstance(child.tag, str) else ""
                        if ct == "border":
                            bf = child.get("borderFillIDRef", "")
                            if bf:
                                ref_front_ids["borderFill"].add(bf)

            print(
                f"  -> Collected front IDs: "
                f"pp={len(ref_front_ids['paraPr'])} cp={len(ref_front_ids['charPr'])} bf={len(ref_front_ids['borderFill'])}"
            )

            tmpl_fonts_by_name: dict[str, str] = {}
            tmpl_max_font_id = 0
            tmpl_fontfaces_container = None
            for elem in tmpl_hdr_root.iter():
                t = etree.QName(elem.tag).localname if isinstance(elem.tag, str) else ""
                if t == "fontface":
                    name = elem.get("name")
                    fid = int(elem.get("id", "0"))
                    if name:
                        tmpl_fonts_by_name[name] = str(fid)
                    tmpl_max_font_id = max(tmpl_max_font_id, fid)
                elif t == "fontfaces":
                    tmpl_fontfaces_container = elem

            ref_font_elems: dict[str, object] = {}
            for elem in ref_hdr_root.iter():
                t = etree.QName(elem.tag).localname if isinstance(elem.tag, str) else ""
                if t == "fontface":
                    ref_font_elems[elem.get("id")] = elem

            def remap_ref_font(ref_fid):
                nonlocal tmpl_max_font_id
                if not ref_fid:
                    return None
                fe = ref_font_elems.get(ref_fid)
                if fe is None:
                    return ref_fid
                fname = fe.get("name")
                if fname in tmpl_fonts_by_name:
                    return tmpl_fonts_by_name[fname]
                tmpl_max_font_id += 1
                new_id = str(tmpl_max_font_id)
                tmpl_fonts_by_name[fname] = new_id
                if tmpl_fontfaces_container is not None:
                    ne = deepcopy(fe)
                    ne.set("id", new_id)
                    tmpl_fontfaces_container.append(ne)
                return new_id

            front_remap: dict[str, dict[str, str]] = {"paraPr": {}, "charPr": {}, "borderFill": {}}
            container_map = {
                "borderFill": "borderFills",
                "charPr": "charProperties",
                "paraPr": "paraProperties",
            }
            merge_count = 0

            for tag_name, needed_ids in [
                ("borderFill", ref_front_ids["borderFill"]),
                ("charPr", ref_front_ids["charPr"]),
                ("paraPr", ref_front_ids["paraPr"]),
            ]:
                if not needed_ids:
                    continue

                max_id = 0
                container = None
                for elem in tmpl_hdr_root.iter():
                    t = etree.QName(elem.tag).localname if isinstance(elem.tag, str) else ""
                    if t == tag_name:
                        eid = elem.get("id", "")
                        max_id = max(max_id, int(eid) if eid.isdigit() else 0)
                    elif t == container_map[tag_name]:
                        container = elem
                if container is None:
                    continue

                ref_map: dict[str, object] = {}
                for elem in ref_hdr_root.iter():
                    t = etree.QName(elem.tag).localname if isinstance(elem.tag, str) else ""
                    if t == tag_name and elem.get("id", "") in needed_ids:
                        ref_map[elem.get("id")] = elem

                for old_id in sorted(needed_ids, key=lambda x: int(x) if x.isdigit() else 0):
                    if not old_id:
                        continue
                    ref_elem = ref_map.get(old_id)
                    if ref_elem is None:
                        continue

                    max_id += 1
                    new_id = str(max_id)
                    front_remap[tag_name][old_id] = new_id

                    new_elem = deepcopy(ref_elem)
                    new_elem.set("id", new_id)

                    if tag_name == "charPr":
                        for child in new_elem.iter():
                            ct = etree.QName(child.tag).localname if isinstance(child.tag, str) else ""
                            if ct == "fontRef":
                                for attr in ["hangul", "latin", "hanja", "japanese", "other", "symbol", "user"]:
                                    if attr in child.attrib:
                                        mapped = remap_ref_font(child.get(attr))
                                        if mapped:
                                            child.set(attr, mapped)

                    if tag_name == "paraPr":
                        for child in new_elem.iter():
                            ct = etree.QName(child.tag).localname if isinstance(child.tag, str) else ""
                            if ct == "border":
                                bf = child.get("borderFillIDRef", "")
                                if bf and bf in front_remap["borderFill"]:
                                    child.set("borderFillIDRef", front_remap["borderFill"][bf])

                    container.append(new_elem)
                    merge_count += 1

                cnt_attr = "itemCnt" if "itemCnt" in container.attrib else "count"
                if cnt_attr in container.attrib:
                    added = len([x for x in needed_ids if x in front_remap[tag_name]])
                    container.set(cnt_attr, str(int(container.get(cnt_attr, "0")) + added))

            print(f"  -> Merged {merge_count} reference styles into template header")

            for start, end, label in ranges:
                for i in range(start, min(end, len(ref_paras))):
                    p = deepcopy(ref_paras[i])
                    remove_lineseg(p)
                    for elem in p.iter():
                        for attr, rmap in [
                            ("paraPrIDRef", front_remap["paraPr"]),
                            ("charPrIDRef", front_remap["charPr"]),
                            ("borderFillIDRef", front_remap["borderFill"]),
                        ]:
                            val = elem.get(attr, "")
                            if val in rmap:
                                elem.set(attr, rmap[val])
                    front_paras.append(p)
                print(f"  -> {label}: {end - start} paragraphs")

        tmpl_secpr = (
            existing_paras[0].find(f".//{{{NS_HP}}}secPr") if existing_paras else None
        )
        if front_paras and tmpl_secpr is not None:
            ref_secpr = front_paras[0].find(f".//{{{NS_HP}}}secPr")
            if ref_secpr is not None:
                parent = ref_secpr.getparent()
                idx = list(parent).index(ref_secpr)
                parent.remove(ref_secpr)
                parent.insert(idx, deepcopy(tmpl_secpr))
                print("  -> secPr preserved from template")

        if front_paras:
            if proposal_title:
                try:
                    p3 = front_paras[3]
                    p4 = front_paras[4]
                    first_t3 = True
                    for t in list(p3.iter(f"{{{NS_HP}}}t")):
                        for c in list(t):
                            t.remove(c)
                        if first_t3:
                            t.text = proposal_title
                            first_t3 = False
                        else:
                            t.text = ""
                    for t in list(p4.iter(f"{{{NS_HP}}}t")):
                        for c in list(t):
                            t.remove(c)
                        t.text = ""
                except Exception:  # noqa: BLE001 - 원본 동일 (front 파라 index 부족한 경우 보호)
                    pass
                print(f"  -> Cover title updated: {proposal_title}")

            update_toc(front_paras, paragraphs, NS_HP)
            print("  -> TOC rebuilt from body headings")

            eval_mapping = None
            if summary_mapping_path and Path(summary_mapping_path).exists():
                with open(summary_mapping_path, "r", encoding="utf-8") as jf:
                    eval_mapping = json.load(jf)
                print(f"  -> Summary table updated from {summary_mapping_path}")
            update_summary_table(front_paras, paragraphs, eval_mapping, NS_HP)

        merged_header = etree.tostring(
            tmpl_hdr_root, xml_declaration=True, encoding="UTF-8", pretty_print=True
        )
    else:
        print("[3/6] No reference document (skipped)")

    print("[4/6] Building content paragraphs")

    cover_paras: list = []
    if cover_keywords and not front_paras:
        for p in existing_paras:
            texts: list[str] = []
            for t in p.iter(f"{{{NS_HP}}}t"):
                if t.text:
                    texts.append(t.text)
            full = "".join(texts).strip()
            if any(kw in full for kw in cover_keywords):
                cover_paras.append(deepcopy(p))

    for p in list(section_tree.iterchildren(f"{{{NS_HP}}}p")):
        section_tree.remove(p)

    if front_paras:
        for p in front_paras:
            section_tree.append(p)
    elif cover_paras:
        for p in cover_paras:
            section_tree.append(p)

    stats: dict[str, int] = {}
    h2_counter = 0

    for para in paragraphs:
        ptype = para["type"]
        text = para["text"]

        if ptype == "H1" and h1_template is not None:
            p_elem = deepcopy(h1_template)
            remove_lineseg(p_elem)
            for run in p_elem.iterchildren(f"{{{NS_HP}}}run"):
                for t_elem in run.iterchildren(f"{{{NS_HP}}}t"):
                    t_elem.text = text
                    break
                break
            p_elem.set("pageBreak", "1")
            h2_counter = 0
            section_tree.append(p_elem)

        elif ptype == "H2" and h2_template is not None:
            h2_counter += 1
            p_elem = deepcopy(h2_template)
            remove_lineseg(p_elem)
            if h2_counter >= 2:
                p_elem.set("pageBreak", "1")
            tbl = p_elem.find(f".//{{{NS_HP}}}tbl")
            if tbl is not None:
                cells = list(tbl.iter(f"{{{NS_HP}}}tc"))
                if len(cells) >= 3:
                    cell0_t = cells[0].find(f".//{{{NS_HP}}}t")
                    if cell0_t is not None:
                        cell0_t.text = str(h2_counter)
                    cell2_t = cells[2].find(f".//{{{NS_HP}}}t")
                    if cell2_t is not None:
                        clean_text = re.sub(r"^\d+\s+", "", text)
                        cell2_t.text = f" {clean_text}"
            else:
                for t_elem in p_elem.iter(f"{{{NS_HP}}}t"):
                    t_elem.text = text
                    break
            section_tree.append(p_elem)

        else:
            s = style_map.get(ptype, style_map.get("body", DEFAULT_STYLE_MAP["body"]))
            p_elem = create_paragraph(
                text, s["para"], s["char"], s.get("style", "0"), ns_map=ns_map
            )
            section_tree.append(p_elem)

        stats[ptype] = stats.get(ptype, 0) + 1

    print("[5/6] Serializing HWPX")

    para_count = len(section_tree.findall(f"{{{NS_HP}}}p"))
    new_xml = etree.tostring(
        section_tree, xml_declaration=True, encoding="UTF-8", pretty_print=True
    )

    output_dir = output.parent
    if str(output_dir) and not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)

    patched_header = disable_heading_auto_numbering(merged_header)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in all_files.items():
            zout.writestr(name, data)
        zout.writestr("Contents/header.xml", patched_header)
        zout.writestr("Contents/section0.xml", new_xml)

    print("\n[6/6] Summary")
    print(f"  Total paragraphs: {para_count}")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"    {k:18s}: {v:3d}")

    if run_fix_namespaces:
        from . import fix_namespaces as _fx

        result = _fx.fix_hwpx(output, fix_tables=True)
        print(
            f"\n🧼 Namespace post-process: {result['modified_files']} XML files modified"
            + (", ns prefixes removed" if result["ns_fixed"] else "")
            + (", table page-break fixed" if result["tables_fixed"] else "")
        )

    print(f"\n✅ Done: {output}")
    return output


# ---------------------------------------------------------------------------
# Public functional API
# ---------------------------------------------------------------------------

def convert_markdown(
    template: PathLike,
    md: PathLike | str,
    output: PathLike,
    *,
    style_map: Optional[dict[str, dict[str, str]]] = None,
    config: Optional[PathLike] = None,
    reference: Optional[PathLike] = None,
    cover_range: Optional[str] = None,
    toc_range: Optional[str] = None,
    summary_range: Optional[str] = None,
    cover_keywords: Sequence[str] = (),
    proposal_title: Optional[str] = None,
    summary_mapping_path: Optional[PathLike] = None,
    run_fix_namespaces: bool = True,
) -> Path:
    """마크다운을 바로 HWPX 로 변환.

    ``md`` 인자는 파일 경로 또는 마크다운 문자열 모두 허용한다.
    ``style_map`` 을 직접 넘기면 ``config`` 는 무시된다.
    """
    if style_map is None:
        style_map = load_config(config)

    md_text: str
    md_path_candidate = Path(md) if isinstance(md, (str, Path)) else None
    if md_path_candidate is not None and md_path_candidate.exists():
        md_text = md_path_candidate.read_text(encoding="utf-8")
    else:
        md_text = str(md)

    paragraphs = parse_markdown(md_text)
    return _convert_paragraphs(
        paragraphs,
        template=template,
        output=output,
        style_map=style_map,
        reference=reference,
        cover_range=cover_range,
        toc_range=toc_range,
        summary_range=summary_range,
        cover_keywords=cover_keywords,
        proposal_title=proposal_title,
        summary_mapping_path=summary_mapping_path,
        run_fix_namespaces=run_fix_namespaces,
    )


def convert(
    blocks,
    template: PathLike,
    output: PathLike,
    *,
    style_map: Optional[dict[str, dict[str, str]]] = None,
    reference: Optional[PathLike] = None,
    cover_range: Optional[str] = None,
    toc_range: Optional[str] = None,
    summary_range: Optional[str] = None,
    cover_keywords: Sequence[str] = (),
    proposal_title: Optional[str] = None,
    summary_mapping_path: Optional[PathLike] = None,
    run_fix_namespaces: bool = True,
    use_python_hwpx_writer: bool = False,
) -> Path:
    """IR :class:`~src.parser.ir_schema.Block` 리스트를 HWPX 로 변환.

    v0.15.0: ``use_python_hwpx_writer=True`` + 고급 기능 (reference / cover_range /
    toc_range / summary_range) 전부 None 이면 **python-hwpx 경로** 로 빠른 처리.
    실패 시 자동으로 legacy lxml 경로 폴백.
    """
    # Lazy import — 순환 의존 피하기
    from src.parser.ir_schema import Block, blocks_to_v1_paragraphs  # type: ignore

    if not blocks:
        raise ValueError("blocks 가 비어 있음 — 변환할 내용이 없습니다.")

    # dict 혹은 Block 혼용 허용 (관용적)
    normalized_paragraphs: list[dict] = []
    block_inputs: list[Block] = []
    for item in blocks:
        if isinstance(item, Block):
            block_inputs.append(item)
        elif isinstance(item, dict) and "type" in item and "text" in item:
            normalized_paragraphs.append(item)
        else:
            raise TypeError(
                f"blocks 의 각 항목은 Block 또는 v1 paragraph dict 이어야 합니다: {type(item)!r}"
            )

    if block_inputs:
        normalized_paragraphs = blocks_to_v1_paragraphs(block_inputs) + normalized_paragraphs

    if style_map is None:
        style_map = dict(DEFAULT_STYLE_MAP)

    # v0.15.0: python-hwpx 경로 — 단순 변환만 (고급 기능 None 일 때)
    advanced_used = any([
        reference is not None,
        cover_range is not None,
        toc_range is not None,
        summary_range is not None,
        proposal_title is not None,
        summary_mapping_path is not None,
        cover_keywords,
    ])
    if use_python_hwpx_writer and not advanced_used:
        try:
            from .hwpx_lib_adapter import is_available as _phwpx_ok
            if _phwpx_ok():
                from .hwpx_writer import write_ir_blocks
                # block_inputs 우선, 없으면 normalized_paragraphs (v1 dict) 전달
                src_blocks = block_inputs if block_inputs else normalized_paragraphs
                write_ir_blocks(
                    src_blocks,
                    template=template,
                    output=output,
                    style_map=style_map,
                )
                if run_fix_namespaces:
                    try:
                        from .fix_namespaces import fix_hwpx
                        fix_hwpx(str(output))
                    except Exception:  # noqa: BLE001 - 후처리 실패해도 HWPX 자체는 유효
                        pass
                return Path(output)
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger("hwpx.md_to_hwpx").info(
                "python-hwpx writer 경로 실패 (%s) → legacy 경로 폴백",
                type(exc).__name__,
            )
            # legacy 경로로 폴백 — output 이 부분 생성됐으면 지움
            try:
                Path(output).unlink(missing_ok=True)
            except OSError:
                pass

    return _convert_paragraphs(
        normalized_paragraphs,
        template=template,
        output=output,
        style_map=style_map,
        reference=reference,
        cover_range=cover_range,
        toc_range=toc_range,
        summary_range=summary_range,
        cover_keywords=cover_keywords,
        proposal_title=proposal_title,
        summary_mapping_path=summary_mapping_path,
        run_fix_namespaces=run_fix_namespaces,
    )


# ============================================================
# CLI (v1 호환)
# ============================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert structured Markdown to HWPX format",
        epilog="After conversion, fix_namespaces is auto-applied (disable with --no-fix-namespaces).",
    )
    parser.add_argument("--template", required=True, help="HWPX template file")
    parser.add_argument("--md", required=True, help="Markdown content file")
    parser.add_argument(
        "--output",
        required=True,
        help='Output HWPX file path, or "auto" for auto-versioned naming',
    )
    parser.add_argument("--config", default=None, help="YAML style configuration file")
    parser.add_argument("--reference", default=None, help="Reference HWPX for cover/TOC page import")
    parser.add_argument("--cover-range", default=None, help='e.g. "0:20"')
    parser.add_argument("--toc-range", default=None, help='e.g. "20:69"')
    parser.add_argument("--summary-range", default=None, help='e.g. "69:163"')
    parser.add_argument("--cover-keywords", nargs="*", default=[], help="Cover page keywords in template")
    parser.add_argument("--proposal-title", default=None, help="Title to replace on cover page")
    parser.add_argument("--summary-mapping", default=None, help="JSON file for summary table mapping")
    parser.add_argument("--output-dir", default=None, help="Output directory for auto mode")
    parser.add_argument(
        "--no-fix-namespaces",
        action="store_true",
        help="Skip the automatic fix_namespaces post-processing",
    )
    args = parser.parse_args(argv)

    if args.output.lower() == "auto":
        out_dir = args.output_dir or os.path.join(
            os.path.dirname(args.template) or ".", "..", "02_산출물"
        )
        out_dir = os.path.abspath(out_dir)
        os.makedirs(out_dir, exist_ok=True)
        target = generate_versioned_path(out_dir)
        archive_old_versions(out_dir, target)
        args.output = target
        print(f"📂 Auto-versioned output: {os.path.basename(target)}")

    convert_markdown(
        template=args.template,
        md=args.md,
        output=args.output,
        config=args.config,
        reference=args.reference,
        cover_range=args.cover_range,
        toc_range=args.toc_range,
        summary_range=args.summary_range,
        cover_keywords=args.cover_keywords,
        proposal_title=args.proposal_title,
        summary_mapping_path=args.summary_mapping,
        run_fix_namespaces=not args.no_fix_namespaces,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
