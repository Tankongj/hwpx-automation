"""v0.8.0: HWP 변환 + PDF 발행일 OCR + 정량 타입 힌트 검증."""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.checklist.hwp_converter import (
    ConverterInfo,
    convert_hwp_to_pdf,
    detect_libreoffice,
)
from src.checklist.pdf_date_extractor import (
    PdfDateResult,
    _find_date_near_keyword,
    _match_first_date,
    extract_issued_date,
    tesseract_available,
)
from src.quant.models import FieldType
from src.quant.type_hints import hint_for_label, summarize_hint


# ---------------------------------------------------------------------------
# Type hints
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,expected_type,expected_unit", [
    ("설립년도", FieldType.NUMBER, "년"),
    ("연도", FieldType.NUMBER, "년"),
    ("총 원", FieldType.NUMBER, "명"),
    ("사원 수", FieldType.NUMBER, "명"),
    ("발행일", FieldType.DATE, ""),
    ("일자", FieldType.DATE, ""),
    ("계약일", FieldType.DATE, ""),
    ("전화번호", FieldType.TEXT, ""),
    ("이메일", FieldType.TEXT, ""),
    ("회사 주소", FieldType.MULTILINE, ""),
    ("대표자", FieldType.TEXT, ""),
    ("금액", FieldType.NUMBER, "원"),
    ("비고", FieldType.MULTILINE, ""),
    ("그냥텍스트", FieldType.TEXT, ""),
])
def test_hint_for_label(label, expected_type, expected_unit):
    ftype, unit = hint_for_label(label)
    assert ftype == expected_type
    assert unit == expected_unit


def test_summarize_hint_with_unit():
    assert summarize_hint(FieldType.NUMBER, "명") == "NUMBER (명)"
    assert summarize_hint(FieldType.DATE, "") == "DATE"


# ---------------------------------------------------------------------------
# HWP converter
# ---------------------------------------------------------------------------

def test_detect_libreoffice_returns_summary_structure():
    info = detect_libreoffice()
    assert isinstance(info, ConverterInfo)
    # 결과는 ok / fail 둘 다 유효 (실제 환경 의존)
    assert info.summary()


def test_convert_hwp_rejects_non_hwp(tmp_path: Path):
    not_hwp = tmp_path / "file.pdf"
    not_hwp.write_bytes(b"x")
    with pytest.raises(ValueError, match=".hwp"):
        convert_hwp_to_pdf(not_hwp, libreoffice_path="/fake/soffice")


def test_convert_hwp_missing_file():
    with pytest.raises(FileNotFoundError):
        convert_hwp_to_pdf("/nonexistent.hwp", libreoffice_path="/fake/soffice")


def test_convert_hwp_runs_subprocess(tmp_path: Path, monkeypatch):
    """LibreOffice 실행을 모킹해서 cmd 인자와 output path 반환 동작 검증."""
    src = tmp_path / "rfp.hwp"
    src.write_bytes(b"dummy")

    # PDF 파일이 생성된 것처럼 흉내
    expected_pdf = tmp_path / "rfp.pdf"

    def fake_run(cmd, **kwargs):
        # --convert-to pdf 플래그 확인
        assert "--convert-to" in cmd and "pdf" in cmd
        assert str(src) in cmd
        expected_pdf.write_bytes(b"%PDF-fake%")
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    monkeypatch.setattr("src.checklist.hwp_converter.subprocess.run", fake_run)
    out = convert_hwp_to_pdf(src, libreoffice_path="/fake/soffice")
    assert out == expected_pdf
    assert out.exists()


def test_convert_hwp_handles_subprocess_failure(tmp_path: Path, monkeypatch):
    src = tmp_path / "rfp.hwp"
    src.write_bytes(b"dummy")

    result = MagicMock()
    result.returncode = 1
    result.stdout = ""
    result.stderr = "conversion error"
    monkeypatch.setattr(
        "src.checklist.hwp_converter.subprocess.run",
        lambda cmd, **kw: result,
    )
    with pytest.raises(RuntimeError, match="변환 실패"):
        convert_hwp_to_pdf(src, libreoffice_path="/fake/soffice")


# ---------------------------------------------------------------------------
# PDF date extractor — pattern helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("발행일: 2026년 3월 15일", date(2026, 3, 15)),
    ("2026-03-15 등록", date(2026, 3, 15)),
    ("발급일 2026. 3. 15.", date(2026, 3, 15)),
    ("20260315 서류 번호", date(2026, 3, 15)),
])
def test_match_first_date(text, expected):
    assert _match_first_date(text) == expected


def test_match_first_date_none():
    assert _match_first_date("no date here") is None


def test_find_date_near_keyword_prefers_keyword():
    """문서 앞쪽 날짜 vs '발행일' 근처 날짜 — 키워드 근처가 우선."""
    text = "임의 2024-01-01 그리고... 발행일: 2026년 3월 15일 끝"
    assert _find_date_near_keyword(text) == date(2026, 3, 15)


# ---------------------------------------------------------------------------
# extract_issued_date flow
# ---------------------------------------------------------------------------

def test_extract_issued_date_missing_file(tmp_path):
    r = extract_issued_date(tmp_path / "nope.pdf")
    assert r.issued_date is None
    assert "없음" in r.error


def test_extract_issued_date_non_pdf(tmp_path):
    p = tmp_path / "x.txt"
    p.write_bytes(b"x")
    r = extract_issued_date(p)
    assert r.issued_date is None
    assert "PDF" in r.error


def test_extract_issued_date_via_text(monkeypatch, tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(b"%PDF%")

    # pdfplumber 경로 mock → "발행일: 2026-03-15" 텍스트 반환
    monkeypatch.setattr(
        "src.checklist.pdf_date_extractor._extract_text_via_pdfplumber",
        lambda path, max_pages=3: "발행일 2026-03-15",
    )
    r = extract_issued_date(p, allow_ocr=False)
    assert r.issued_date == date(2026, 3, 15)
    assert r.source == "text"


def test_extract_issued_date_fallback_to_ocr(monkeypatch, tmp_path):
    p = tmp_path / "scanned.pdf"
    p.write_bytes(b"%PDF%")

    # text 경로 → 빈 문자열 (스캔 PDF)
    monkeypatch.setattr(
        "src.checklist.pdf_date_extractor._extract_text_via_pdfplumber",
        lambda path, max_pages=3: "",
    )
    # OCR 가능한 것으로 가장
    monkeypatch.setattr(
        "src.checklist.pdf_date_extractor.tesseract_available", lambda: True,
    )
    monkeypatch.setattr(
        "src.checklist.pdf_date_extractor._extract_text_via_tesseract",
        lambda path, max_pages=2: "2026년 4월 1일 발급",
    )
    r = extract_issued_date(p, allow_ocr=True)
    assert r.issued_date == date(2026, 4, 1)
    assert r.source == "ocr"


def test_extract_issued_date_no_match(monkeypatch, tmp_path):
    p = tmp_path / "blank.pdf"
    p.write_bytes(b"%PDF%")
    monkeypatch.setattr(
        "src.checklist.pdf_date_extractor._extract_text_via_pdfplumber",
        lambda path, max_pages=3: "이 문서에 날짜 없음",
    )
    monkeypatch.setattr(
        "src.checklist.pdf_date_extractor.tesseract_available", lambda: False,
    )
    r = extract_issued_date(p)
    assert r.issued_date is None
    assert r.source == "unknown"


# ---------------------------------------------------------------------------
# matcher integration — PDF date fallback
# ---------------------------------------------------------------------------

def test_matcher_uses_pdf_date_when_filename_lacks_date(tmp_path, monkeypatch):
    """파일명에 날짜 없지만 PDF 내용에 발행일 있으면 ✅ OK 상태."""
    # 파일명에는 날짜 없음
    target = tmp_path / "사업자등록증_사본.pdf"
    target.write_bytes(b"%PDF%")

    # extract_issued_date mock → 90일 이내 발행본
    monkeypatch.setattr(
        "src.checklist.pdf_date_extractor.extract_issued_date",
        lambda p, **kw: PdfDateResult(issued_date=date(2026, 4, 1), source="text"),
    )

    from src.checklist.matcher import build_checklist
    from src.checklist.models import DocumentStatus, RequiredDocument

    docs = [
        RequiredDocument(
            id="biz", name="사업자등록증", is_required=True,
            max_age_days=90,
            filename_hints=["사업자등록증"],
        )
    ]
    result = build_checklist(docs, tmp_path, today=date(2026, 4, 19))
    item = result.items[0]
    assert item.status == DocumentStatus.OK
    assert item.best_match.issued_source == "text"
    assert item.best_match.issued_date == date(2026, 4, 1)


def test_matcher_skips_pdf_fallback_for_non_pdf(tmp_path):
    target = tmp_path / "사업자등록증.jpg"
    target.write_bytes(b"JFIF")
    from src.checklist.matcher import build_checklist
    from src.checklist.models import DocumentStatus, RequiredDocument

    docs = [
        RequiredDocument(
            id="biz", name="사업자등록증", max_age_days=90,
            filename_hints=["사업자등록증"],
        )
    ]
    result = build_checklist(docs, tmp_path, today=date(2026, 4, 19))
    item = result.items[0]
    # PDF 가 아니므로 발행일 확인 불가 → WARNING (발행일 모름)
    assert item.status == DocumentStatus.WARNING


# ---------------------------------------------------------------------------
# tesseract_available smoke
# ---------------------------------------------------------------------------

def test_tesseract_available_returns_bool():
    assert isinstance(tesseract_available(), bool)
