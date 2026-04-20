"""편집된 :class:`QuantDocument` → HWPX 쓰기 (v0.5.0 MVP).

cell-level 편집 결과를 원본 HWPX 에 in-place 반영해 새 파일로 저장.

알고리즘
--------
1. 원본 템플릿 ZIP 을 열어 모든 엔트리 읽음
2. ``Contents/section0.xml`` 파싱
3. 편집된 각 :class:`QuantCell` 에 대해:
   - ``para_index`` 로 대상 `<hp:p>` 찾음
   - 거기서 `<hp:tbl>` 순서로 ``table_idx`` 표 선택
   - `<hp:tr>` row 인덱스 → `<hp:tc>` col 인덱스 로 해당 셀 찾음
   - 셀 내 모든 `<hp:t>` 텍스트를 새 텍스트로 교체 (첫 `<hp:t>` 에 집어넣고 나머지는 빈 문자열)
4. 수정된 section0.xml 로 새 ZIP 작성
5. (선택) :mod:`src.hwpx.fix_namespaces` 후처리

제약
----
- 복잡한 셀(여러 `<hp:p>` 가 들어 있는 경우) 은 첫 `<hp:p>` 의 첫 `<hp:t>` 에만 새 텍스트를
  넣는다 — 나머지는 비워짐. 셀 안 여러 줄 입력이 필요하면 사용자가 "\n" 을 포함시키거나
  향후 버전에서 개선.
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable, Union

from lxml import etree

from copy import deepcopy

from ..hwpx import fix_namespaces as _fx
from ..utils.logger import get_logger
from .models import QuantCell, QuantDocument, RowOp


_log = get_logger("quant.converter")


PathLike = Union[str, Path]

NS_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


# ---------------------------------------------------------------------------
# Cell write helpers
# ---------------------------------------------------------------------------

def _set_cell_text(tc_elem, new_text: str) -> None:
    """``<hp:tc>`` 안의 텍스트를 ``new_text`` 로 교체."""
    # 해당 tc 안의 모든 <hp:t> 목록 (문서 순)
    t_elems = list(tc_elem.iter(f"{{{NS_HP}}}t"))
    if not t_elems:
        return  # 텍스트를 담을 곳이 없음 → 셀 스킵
    # 첫 t 에 전체 텍스트 넣고, 나머지는 비움
    t_elems[0].text = new_text or ""
    # 첫 t 의 자식(`<hp:tab>` 등) 은 유지. 나머지 t 는 text 만 비움
    for t in t_elems[1:]:
        t.text = ""


def _locate_table(root, para_index: int, table_idx: int):
    """section root → 대상 ``<hp:tbl>``. 없으면 ``None``."""
    paras = root.findall(f"{{{NS_HP}}}p")
    if para_index >= len(paras) or para_index < 0:
        return None
    tables = list(paras[para_index].iter(f"{{{NS_HP}}}tbl"))
    if table_idx >= len(tables) or table_idx < 0:
        return None
    return tables[table_idx]


def _apply_row_op(root, op: RowOp) -> bool:
    """``<hp:tr>`` 복제 또는 삭제. 성공 시 True."""
    tbl = _locate_table(root, op.para_index, op.table_idx)
    if tbl is None:
        return False
    trs = list(tbl.iterchildren(f"{{{NS_HP}}}tr"))
    if op.source_row < 0 or op.source_row >= len(trs):
        return False
    src = trs[op.source_row]

    if op.op == "delete":
        tbl.remove(src)
        _log.info("행 삭제: para=%d tbl=%d row=%d", op.para_index, op.table_idx, op.source_row)
        return True

    if op.op == "duplicate":
        new_tr = deepcopy(src)
        # 복제된 행의 셀 텍스트는 비움 (사용자가 새로 기입)
        for tc in new_tr.iter(f"{{{NS_HP}}}tc"):
            t_elems = list(tc.iter(f"{{{NS_HP}}}t"))
            for t in t_elems:
                t.text = ""
        # 원본 바로 다음에 삽입
        parent = src.getparent()
        idx = list(parent).index(src)
        parent.insert(idx + 1, new_tr)
        _log.info("행 복제: para=%d tbl=%d row=%d → +1", op.para_index, op.table_idx, op.source_row)
        return True

    _log.warning("알 수 없는 행 연산: %s", op.op)
    return False


def _locate_cell(
    root,
    para_index: int,
    table_idx: int,
    row: int,
    col: int,
):
    """section root + 좌표 → 대상 ``<hp:tc>`` 엘리먼트. 없으면 ``None``.

    v0.5.0 시맨틱: ``table_idx`` 는 **해당 paragraph 내** ``<hp:tbl>`` 순번 (0부터).
    따라서 para_index 의 paragraph 안에서 table_idx 번째 표만 찾으면 된다.
    """
    paras = root.findall(f"{{{NS_HP}}}p")
    if para_index >= len(paras) or para_index < 0:
        return None
    tables = list(paras[para_index].iter(f"{{{NS_HP}}}tbl"))
    if table_idx >= len(tables) or table_idx < 0:
        return None
    tbl = tables[table_idx]
    trs = list(tbl.iterchildren(f"{{{NS_HP}}}tr"))
    if row >= len(trs):
        return None
    tcs = list(trs[row].iterchildren(f"{{{NS_HP}}}tc"))
    if col >= len(tcs):
        return None
    return tcs[col]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_document(
    doc: QuantDocument,
    output_path: PathLike,
    *,
    run_fix_namespaces: bool = True,
) -> Path:
    """편집된 :class:`QuantDocument` 를 새 HWPX 파일로 저장.

    Parameters
    ----------
    doc : 편집된 문서. 원본 템플릿(``doc.template_path``) 기반으로 쓴다.
    output_path : 결과 파일 경로. 존재하면 :class:`FileExistsError`.
    run_fix_namespaces : 저장 후 네임스페이스 후처리. 기본 True.

    Returns
    -------
    Path : 실제로 쓰여진 경로.
    """
    src = Path(doc.template_path)
    if not src.exists():
        raise FileNotFoundError(f"템플릿이 없습니다: {src}")
    out = Path(output_path)
    if out.exists():
        raise FileExistsError(f"출력 파일이 이미 존재합니다: {out}")

    # 원본 ZIP 전체 내용 읽음
    with zipfile.ZipFile(src, "r") as zin:
        files = {name: zin.read(name) for name in zin.namelist()}

    if "Contents/section0.xml" not in files:
        raise ValueError("템플릿에 Contents/section0.xml 이 없습니다")

    section_bytes = files["Contents/section0.xml"]
    root = etree.fromstring(section_bytes)

    # v0.5.1: 행 조작 먼저 적용 (셀 write 이전에 표 구조 변경)
    row_ops_applied = 0
    for op in doc.row_ops:
        if _apply_row_op(root, op):
            row_ops_applied += 1
    if doc.row_ops:
        _log.info("quant.save_document: 행 연산 %d/%d 적용", row_ops_applied, len(doc.row_ops))

    applied = 0
    skipped = 0
    for cell in doc.cells:
        tc = _locate_cell(
            root, cell.para_index, cell.table_idx, cell.row, cell.col
        )
        if tc is None:
            skipped += 1
            continue
        _set_cell_text(tc, cell.text)
        applied += 1

    _log.info("quant.save_document: %d 셀 반영, %d 셀 스킵 (셀 찾지 못함)", applied, skipped)

    new_xml = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", pretty_print=True
    )
    files["Contents/section0.xml"] = new_xml

    # 디렉토리 준비
    out.parent.mkdir(parents=True, exist_ok=True)

    # 새 ZIP 작성 (원래 엔트리 순서 유지)
    with zipfile.ZipFile(src, "r") as zin_order:
        name_order = zin_order.namelist()

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in name_order:
            zout.writestr(name, files[name])

    if run_fix_namespaces:
        result = _fx.fix_hwpx(out, fix_tables=True)
        _log.info(
            "quant.save_document: fix_namespaces %s (ns=%s tbl=%s)",
            result.get("modified_files"),
            result.get("ns_fixed"),
            result.get("tables_fixed"),
        )

    return out


# Back-compat 이름
convert = save_document


__all__ = ["save_document", "convert"]
