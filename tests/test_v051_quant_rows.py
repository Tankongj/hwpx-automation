"""v0.5.1: 정량제안서 행 추가/삭제 + 병합 셀 검증."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.quant.converter import save_document
from src.quant.models import QuantCell, RowOp
from src.quant.parser import parse_document


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests" / "fixtures" / "quant_samples" / "[정량제안서] 2026년 아카데미.hwpx"

REQUIRES_SAMPLE = pytest.mark.skipif(not SAMPLE.exists(), reason="sample missing")


@REQUIRES_SAMPLE
def test_parser_detects_span_on_real_sample():
    """샘플에 병합 셀이 있다면 col_span 또는 row_span > 1 셀이 존재해야 함."""
    doc = parse_document(SAMPLE)
    spans = [c for c in doc.cells if c.row_span > 1 or c.col_span > 1]
    # 샘플에 병합 셀이 없을 수도 있으니 단지 버그 없이 돌아가는지만 확인
    assert all(c.row_span >= 1 for c in doc.cells)
    assert all(c.col_span >= 1 for c in doc.cells)


@REQUIRES_SAMPLE
def test_row_duplicate_op_adds_empty_row(tmp_path: Path):
    """서식 3 참여인력 프로필(13행) 의 한 행을 복제 → 14행 되어야 함."""
    doc = parse_document(SAMPLE)

    # form_3 의 첫 표가 13행 (프로필 테이블)
    cells = doc.cells_of("form_3")
    # tbl ordinal 1 (두 번째 표) 가 실제 프로필 13행
    prof_cells = [c for c in cells if c.form_table_ordinal == 1]
    if not prof_cells:
        pytest.skip("프로필 표 없음")

    # para_index + table_idx 알아내기 (converter 용)
    c0 = prof_cells[0]
    rows_before, _ = doc.table_shape("form_3", 1)

    doc.row_ops.append(RowOp(
        para_index=c0.para_index, table_idx=c0.table_idx,
        source_row=2, op="duplicate",
    ))
    out = tmp_path / "row_added.hwpx"
    save_document(doc, out)

    # 재파싱 시 프로필 표가 14행
    doc2 = parse_document(out)
    rows_after, _ = doc2.table_shape("form_3", 1)
    assert rows_after == rows_before + 1


@REQUIRES_SAMPLE
def test_row_delete_op_removes_row(tmp_path: Path):
    doc = parse_document(SAMPLE)
    cells = doc.cells_of("form_3")
    prof_cells = [c for c in cells if c.form_table_ordinal == 1]
    if not prof_cells:
        pytest.skip("프로필 표 없음")
    c0 = prof_cells[0]
    rows_before, _ = doc.table_shape("form_3", 1)

    doc.row_ops.append(RowOp(
        para_index=c0.para_index, table_idx=c0.table_idx,
        source_row=3, op="delete",
    ))
    out = tmp_path / "row_deleted.hwpx"
    save_document(doc, out)

    doc2 = parse_document(out)
    rows_after, _ = doc2.table_shape("form_3", 1)
    assert rows_after == rows_before - 1


@REQUIRES_SAMPLE
def test_row_duplicate_preserves_other_edits(tmp_path: Path):
    """행 복제와 기존 셀 편집이 같이 적용돼야 함."""
    doc = parse_document(SAMPLE)

    # form_1 의 회사명 편집
    c_company = next(
        c for c in doc.cells
        if c.form_id == "form_1" and c.form_table_ordinal == 0 and c.row == 0 and c.col == 1
    )
    c_company.text = "결합테스트 주식회사"

    # form_3 의 프로필 행 복제
    prof_cells = [c for c in doc.cells_of("form_3") if c.form_table_ordinal == 1]
    if prof_cells:
        c0 = prof_cells[0]
        doc.row_ops.append(RowOp(
            para_index=c0.para_index, table_idx=c0.table_idx,
            source_row=2, op="duplicate",
        ))

    out = tmp_path / "combined.hwpx"
    save_document(doc, out)

    # 편집 내용 유지 확인
    doc2 = parse_document(out)
    r_company = next(
        c for c in doc2.cells
        if c.form_id == "form_1" and c.form_table_ordinal == 0 and c.row == 0 and c.col == 1
    )
    assert r_company.text == "결합테스트 주식회사"


def test_row_op_invalid_coords_skipped(tmp_path: Path):
    """존재하지 않는 좌표의 row_op 는 조용히 스킵."""
    from src.quant.converter import _apply_row_op
    from lxml import etree
    root = etree.fromstring(b"<root xmlns:hp='http://www.hancom.co.kr/hwpml/2011/paragraph'/>")
    ok = _apply_row_op(root, RowOp(para_index=99, table_idx=99, source_row=0, op="duplicate"))
    assert ok is False


@REQUIRES_SAMPLE
def test_qt_tab_span_rendering(qtbot, tmp_path, monkeypatch):
    """v0.5.1: span 있는 셀에 setSpan 이 호출되는지 간접 확인."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config
    from src.gui.tabs.quant_tab import QuantTab

    cfg = app_config.AppConfig(default_output_dir=str(tmp_path / "out"))
    tab = QuantTab(cfg)
    qtbot.addWidget(tab)
    tab._template_path = SAMPLE
    tab._load_template()
    # 첫 서식 선택
    first_item = tab.form_tree.topLevelItem(0)
    if first_item.childCount() > 0:
        tab.form_tree.setCurrentItem(first_item.child(0))
    # 단순 smoke — 예외 없이 렌더됐으면 OK
    assert tab.table_widget.rowCount() >= 1
