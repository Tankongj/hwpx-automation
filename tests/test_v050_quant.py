"""v0.5.0: 정량제안서 파서/컨버터 실사용 검증.

- 실제 샘플 (`tests/fixtures/quant_samples/[정량제안서] 2026년 아카데미.hwpx`) 을 파싱
- 셀 편집 후 저장 → 재파싱 round-trip
- GUI 탭 smoke (pytest-qt)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.quant.converter import save_document
from src.quant.models import QuantCell, QuantDocument
from src.quant.parser import parse_document


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests" / "fixtures" / "quant_samples" / "[정량제안서] 2026년 아카데미.hwpx"

REQUIRES_SAMPLE = pytest.mark.skipif(
    not SAMPLE.exists(), reason="quant sample not present"
)


# ---------------------------------------------------------------------------
# Parser on real sample
# ---------------------------------------------------------------------------

@REQUIRES_SAMPLE
def test_parse_document_extracts_expected_forms():
    doc = parse_document(SAMPLE)
    assert isinstance(doc, QuantDocument)
    # 샘플에는 [서식 1] ~ [서식 4] 가 있다
    assert "form_1" in doc.form_labels
    assert "form_4" in doc.form_labels
    assert "서식 1" in doc.form_labels["form_1"] or "[서식 1]" in doc.form_labels["form_1"]


@REQUIRES_SAMPLE
def test_parse_document_yields_many_cells():
    doc = parse_document(SAMPLE)
    # 수백~수천 셀 나와야 정상 (1500 내외)
    assert len(doc.cells) > 500


@REQUIRES_SAMPLE
def test_parse_document_cells_have_valid_keys():
    """각 셀의 (para_index, table_idx, row, col) 는 0 이상이고 유일해야 한다."""
    doc = parse_document(SAMPLE)
    seen: set = set()
    for c in doc.cells:
        assert c.para_index >= 0
        assert c.table_idx >= 0
        assert c.row >= 0
        assert c.col >= 0
        assert c.key not in seen, f"중복 셀 키: {c.key}"
        seen.add(c.key)


@REQUIRES_SAMPLE
def test_parse_document_form_1_contains_company_name():
    """form_1 어딘가 '팜러닝' 문자열이 있어야 한다 (샘플 기준)."""
    doc = parse_document(SAMPLE)
    form_1_texts = [c.text for c in doc.cells_of("form_1")]
    assert any("팜러닝" in t for t in form_1_texts)


# ---------------------------------------------------------------------------
# Converter round-trip
# ---------------------------------------------------------------------------

@REQUIRES_SAMPLE
def test_converter_round_trip_preserves_edits(tmp_path: Path):
    doc = parse_document(SAMPLE)

    # form_1 의 (0,1) 과 (0,3) 편집
    t1 = next(
        c for c in doc.cells
        if c.form_id == "form_1" and c.form_table_ordinal == 0 and c.row == 0 and c.col == 1
    )
    t2 = next(
        c for c in doc.cells
        if c.form_id == "form_1" and c.form_table_ordinal == 0 and c.row == 0 and c.col == 3
    )
    t1.text = "라운드트립_회사명"
    t2.text = "라운드트립_대표자"

    out = tmp_path / "quant_rt.hwpx"
    save_document(doc, out)
    assert out.exists()

    # 재파싱
    doc2 = parse_document(out)
    r1 = next(
        c for c in doc2.cells
        if c.form_id == "form_1" and c.form_table_ordinal == 0 and c.row == 0 and c.col == 1
    )
    r2 = next(
        c for c in doc2.cells
        if c.form_id == "form_1" and c.form_table_ordinal == 0 and c.row == 0 and c.col == 3
    )
    assert r1.text == "라운드트립_회사명"
    assert r2.text == "라운드트립_대표자"


@REQUIRES_SAMPLE
def test_converter_writes_all_cells_without_skip(tmp_path: Path):
    """save_document 는 1497개 셀 전부 반영해야 한다 (skip=0)."""
    import logging

    doc = parse_document(SAMPLE)
    out = tmp_path / "all_cells.hwpx"

    # 로그 캡처
    with _capture_logs("hwpx.quant.converter") as records:
        save_document(doc, out)

    info_lines = " ".join(r.getMessage() for r in records if r.levelno == logging.INFO)
    # "N 셀 반영, M 셀 스킵" 패턴 — M=0 이어야 함
    assert "0 셀 스킵" in info_lines, f"스킵된 셀 발생: {info_lines}"


@REQUIRES_SAMPLE
def test_converter_refuses_to_overwrite_existing(tmp_path: Path):
    doc = parse_document(SAMPLE)
    out = tmp_path / "exists.hwpx"
    out.write_bytes(b"dummy")
    with pytest.raises(FileExistsError):
        save_document(doc, out)


@REQUIRES_SAMPLE
def test_parser_handles_document_without_form_markers(tmp_path: Path):
    """[서식 N] 패턴이 전혀 없어도 form_0 으로 표 하나는 추출해야 한다."""
    # 샘플을 단순 HWPX 로 가공하는 대신, 실제 번들 기본 템플릿을 사용
    bundled = ROOT / "templates" / "00_기본_10단계스타일.hwpx"
    if not bundled.exists():
        pytest.skip("bundled template missing")
    doc = parse_document(bundled)
    # 서식 헤더 없으므로 form_0 으로 fallback
    assert "form_0" in doc.form_labels or len(doc.form_labels) >= 1


# ---------------------------------------------------------------------------
# QuantDocument API
# ---------------------------------------------------------------------------

def test_quant_document_forms_helper():
    doc = QuantDocument(template_path="/tmp/x")
    doc.form_labels["form_1"] = "[서식 1] A"
    doc.form_labels["form_2"] = "[서식 2] B"
    assert doc.forms() == [("form_1", "[서식 1] A"), ("form_2", "[서식 2] B")]


def test_quant_cell_path_uses_ordinal():
    cell = QuantCell(
        form_id="form_1", form_label="x",
        para_index=42, table_idx=2, row=3, col=4,
        text="hi", form_table_ordinal=5,
    )
    assert cell.path == "form_1/tbl5/r3c4"
    assert cell.key == (42, 2, 3, 4)


# ---------------------------------------------------------------------------
# GUI smoke (pytest-qt)
# ---------------------------------------------------------------------------

@REQUIRES_SAMPLE
def test_quant_tab_loads_and_populates(qtbot, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config
    from src.gui.tabs.quant_tab import QuantTab

    cfg = app_config.AppConfig(default_output_dir=str(tmp_path / "out"))
    tab = QuantTab(cfg)
    qtbot.addWidget(tab)

    # 템플릿 주입 후 load
    tab._template_path = SAMPLE
    tab._load_template()

    # 트리에 form 4 개 이상 표시됨
    assert tab.form_tree.topLevelItemCount() >= 4
    # 저장 버튼 활성화
    assert tab.save_btn.isEnabled() is True


@REQUIRES_SAMPLE
def test_quant_tab_save_roundtrip(qtbot, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config
    from src.gui.tabs.quant_tab import QuantTab
    from PySide6.QtWidgets import QMessageBox

    # 모달 다이얼로그 자동 dismiss (테스트 hang 방지)
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **kw: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **kw: QMessageBox.StandardButton.Yes)

    out_dir = tmp_path / "out"
    cfg = app_config.AppConfig(default_output_dir=str(out_dir))
    tab = QuantTab(cfg)
    qtbot.addWidget(tab)

    tab._template_path = SAMPLE
    tab._load_template()

    # 한 셀 편집 — form_1 의 (0,1)
    t1 = next(
        c for c in tab._doc.cells
        if c.form_id == "form_1" and c.form_table_ordinal == 0 and c.row == 0 and c.col == 1
    )
    t1.text = "GUI 편집 테스트"

    tab._save()
    assert tab._last_output is not None
    assert tab._last_output.exists()

    # 재파싱 확인
    doc2 = parse_document(tab._last_output)
    r1 = next(
        c for c in doc2.cells
        if c.form_id == "form_1" and c.form_table_ordinal == 0 and c.row == 0 and c.col == 1
    )
    assert r1.text == "GUI 편집 테스트"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from contextlib import contextmanager
import logging


@contextmanager
def _capture_logs(logger_name: str):
    logger = logging.getLogger(logger_name)
    handler = _ListHandler()
    logger.addHandler(handler)
    level = logger.level
    logger.setLevel(logging.INFO)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)
