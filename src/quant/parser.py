"""정량제안서 HWPX → :class:`QuantDocument` 파서 (v0.5.0 MVP).

Cell-level 접근: 필드 타입 추론 없이 **모든 셀을 그대로 추출**한다. UI 는 표 구조를
그대로 보여주고 사용자가 셀 단위로 편집.

알고리즘
--------
1. ``Contents/section0.xml`` 을 lxml 로 파싱
2. 최상위 ``<hp:p>`` 를 순회하면서 선두 텍스트가 ``[서식 N]`` 패턴인 단락을 form 시작점으로 인식
3. 각 form 의 범위(다음 form 시작 또는 section 끝까지) 내 모든 ``<hp:tbl>`` 수집
4. 각 표의 각 ``<hp:tc>`` 를 (row, col) 인덱스와 함께 :class:`QuantCell` 로 수집

foundation 의 :func:`parse_template` / :func:`demo_proposal` 는 그대로 유지.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Iterable, Optional, Union

from lxml import etree

from .models import QuantCell, QuantDocument, QuantProposal


PathLike = Union[str, Path]

NS_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"

# 서식 헤더 패턴 — 예시:
#   "[서식 1] 일반현황 및 연혁"
#   "[ 서식 2 ] 조직도"
#   "[서식1]"
FORM_HEADER_RE = re.compile(r"^\s*\[\s*서식\s*(?P<n>\d+)\s*\]")


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _paragraph_text(p_elem) -> str:
    """``<hp:p>`` 의 모든 ``<hp:t>`` 텍스트를 이어붙인 값.

    주의: 중첩된 표 안의 텍스트도 포함됨. 서식 헤더 감지에는 충분히 유효 (헤더는 표 밖
    단독 단락으로 존재하므로).
    """
    parts: list[str] = []
    for t in p_elem.iter(f"{{{NS_HP}}}t"):
        if t.text:
            parts.append(t.text)
    return "".join(parts)


def _cell_text(tc_elem) -> str:
    """``<hp:tc>`` 안의 모든 ``<hp:t>`` 텍스트 결합."""
    return _paragraph_text(tc_elem)    # 동일한 iter 전략이 유효


def _paragraph_own_text(p_elem) -> str:
    """``<hp:p>`` 의 표 외부 텍스트만. 서식 헤더 판정용."""
    parts: list[str] = []
    # 직접 자식 run 의 t 만 (표 안 무시)
    for run in p_elem.iterchildren(f"{{{NS_HP}}}run"):
        for t in run.iter(f"{{{NS_HP}}}t"):
            # t 가 표 내부 자손이면 스킵
            parent = t.getparent()
            is_in_table = False
            while parent is not None and parent is not run:
                if parent.tag == f"{{{NS_HP}}}tbl":
                    is_in_table = True
                    break
                parent = parent.getparent()
            if not is_in_table and t.text:
                parts.append(t.text)
    return "".join(parts)


# ---------------------------------------------------------------------------
# Main parse (cell-level, v0.5.0 MVP)
# ---------------------------------------------------------------------------

def parse_document(hwpx_path: PathLike) -> QuantDocument:
    """HWPX 정량제안서 → :class:`QuantDocument`. 모든 form 의 모든 셀 추출."""
    path = Path(hwpx_path)
    if not path.exists():
        raise FileNotFoundError(str(path))

    with zipfile.ZipFile(path, "r") as z:
        if "Contents/section0.xml" not in z.namelist():
            raise ValueError(
                "HWPX 에 Contents/section0.xml 이 없습니다 — 손상된 파일일 수 있음"
            )
        section_bytes = z.read("Contents/section0.xml")

    root = etree.fromstring(section_bytes)
    paras = root.findall(f"{{{NS_HP}}}p")

    # 서식 헤더 위치 찾기
    form_boundaries: list[tuple[int, str, str]] = []   # (para_idx, form_id, label)
    for idx, p in enumerate(paras):
        own = _paragraph_own_text(p).strip()
        m = FORM_HEADER_RE.match(own)
        if m:
            n = m.group("n")
            form_id = f"form_{n}"
            form_boundaries.append((idx, form_id, own))

    doc = QuantDocument(template_path=str(path))

    if not form_boundaries:
        # [서식 N] 패턴이 없으면 전체를 form_0 으로 취급
        form_boundaries = [(0, "form_0", "(단일 문서)")]

    for i, (start_idx, form_id, label) in enumerate(form_boundaries):
        end_idx = (
            form_boundaries[i + 1][0]
            if i + 1 < len(form_boundaries)
            else len(paras)
        )
        doc.form_labels[form_id] = label

        # 이 form 범위 내의 모든 표를 순회.
        # - ``table_idx``: paragraph 내 <hp:tbl> 순번 (이 값으로 locate_cell 에서 찾는다)
        # - ``form_table_ordinal``: form 전체에서 몇 번째 표인지 (UI 표시 용)
        form_ordinal = 0
        for para_pos, p_elem in enumerate(paras[start_idx:end_idx], start=start_idx):
            # paragraph 내 최상위 직계 표들 (중첩 표 제외 — 최상위만 편집 대상으로)
            direct_tables = list(p_elem.iter(f"{{{NS_HP}}}tbl"))
            for intra_idx, tbl in enumerate(direct_tables):
                rows = list(tbl.iterchildren(f"{{{NS_HP}}}tr"))
                for r_idx, tr in enumerate(rows):
                    tcs = list(tr.iterchildren(f"{{{NS_HP}}}tc"))
                    for c_idx, tc in enumerate(tcs):
                        txt = _cell_text(tc)
                        # v0.5.1: colspan/rowspan — <hp:cellSpan colSpan="N" rowSpan="M"/>
                        row_span = 1
                        col_span = 1
                        cs = tc.find(f"{{{NS_HP}}}cellSpan")
                        if cs is not None:
                            try:
                                col_span = int(cs.get("colSpan", "1") or "1")
                                row_span = int(cs.get("rowSpan", "1") or "1")
                            except ValueError:
                                pass
                        doc.cells.append(
                            QuantCell(
                                form_id=form_id,
                                form_label=label,
                                para_index=para_pos,
                                table_idx=intra_idx,
                                row=r_idx,
                                col=c_idx,
                                text=txt,
                                form_table_ordinal=form_ordinal,
                                row_span=row_span,
                                col_span=col_span,
                                is_span_origin=True,
                            )
                        )
                form_ordinal += 1

    return doc


# ---------------------------------------------------------------------------
# Foundation stubs (v0.5.0 structured field mode — 사용자 샘플 더 확보 후 본격 구현)
# ---------------------------------------------------------------------------

def parse_template(template_path: PathLike) -> QuantProposal:
    """**Foundation stub** (구조 기반 필드 추출).

    v0.5.0 MVP 에서는 :func:`parse_document` (cell-level) 사용을 권장. 구조적 필드 추출은
    기관별 서식 변이가 커서 foundation 상태 유지. 본격 구현은 샘플 더 쌓인 뒤.
    """
    path = Path(template_path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    return QuantProposal(template_path=str(path), forms=[])


def demo_proposal() -> QuantProposal:
    """GUI 프로토타입 용 샘플 proposal (foundation 기반)."""
    from .models import FieldType, QuantField, QuantForm

    return QuantProposal(
        template_path="<demo>",
        forms=[
            QuantForm(
                id="form_1",
                label="[서식 1] 기관 일반현황",
                fields=[
                    QuantField(id="org_name", label="기관명", hint="(주)예시"),
                    QuantField(id="ceo_name", label="대표자명"),
                    QuantField(
                        id="found_year",
                        label="설립년도",
                        field_type=FieldType.NUMBER,
                        unit="년",
                    ),
                    QuantField(
                        id="employee_count",
                        label="총원",
                        field_type=FieldType.NUMBER,
                        unit="명",
                    ),
                ],
            ),
        ],
    )


__all__ = ["parse_document", "parse_template", "demo_proposal"]
