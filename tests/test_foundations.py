"""v0.5.0/v0.6.0 foundation 스캐폴딩 sanity 테스트.

실제 본편 로직은 아직 TODO 지만, 데이터 모델과 간단한 헬퍼는 동작해야 한다.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.checklist import (
    ChecklistResult,
    DocumentStatus,
    RequiredDocument,
)
from src.checklist.filename_matcher import extract_date_from_filename, match_keywords
from src.checklist.matcher import build_checklist
from src.checklist.rfp_extractor import demo_required_documents, extract_from_rfp
from src.quant import QuantField, QuantForm, QuantProposal
from src.quant.converter import convert as quant_convert
from src.quant.parser import demo_proposal, parse_template


# ---------------------------------------------------------------------------
# Quant foundation
# ---------------------------------------------------------------------------

def test_quant_demo_proposal_structure():
    prop = demo_proposal()
    assert isinstance(prop, QuantProposal)
    # v0.5.0: demo_proposal 은 최소 1 폼 유지
    assert len(prop.forms) >= 1
    assert prop.forms[0].id == "form_1"
    assert any(f.id == "ceo_name" for f in prop.forms[0].fields)


def test_quant_proposal_set_get():
    prop = demo_proposal()
    prop.set("form_1", "ceo_name", "홍길동")
    assert prop.get("form_1", "ceo_name") == "홍길동"
    assert prop.get("form_1", "nonexistent") is None


def test_quant_missing_required_reports_holes():
    prop = demo_proposal()
    missing = prop.missing_required()
    # form_2 의 client 는 required=False 이므로 제외
    missing_fields = {fld for _, fld in missing}
    assert "ceo_name" in missing_fields
    assert "client" not in missing_fields


def test_quant_parse_template_stub_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_template(tmp_path / "nothing.hwpx")


def test_quant_converter_accepts_quant_document(tmp_path):
    """v0.5.0: converter 는 이제 QuantDocument 로 동작. 단, 빈 QuantProposal 은
    호환성 경로 없음 — 새 API 사용 필요 (별도 파일 test_v050_quant.py 에서 검증)."""
    # QuantDocument 경로는 별도 테스트에서 실제 샘플로 검증
    # 여기서는 import 가능성만 확인
    from src.quant.converter import save_document  # noqa: F401


# ---------------------------------------------------------------------------
# Checklist foundation
# ---------------------------------------------------------------------------

def test_extract_date_from_filename_yyyy_mm_dd():
    assert extract_date_from_filename("사업자등록증_2026-03-15.pdf") == date(2026, 3, 15)
    assert extract_date_from_filename("법인인감_2026.03.15.pdf") == date(2026, 3, 15)
    assert extract_date_from_filename("법인인감_2026_03_15.pdf") == date(2026, 3, 15)


def test_extract_date_from_filename_yyyymmdd():
    assert extract_date_from_filename("brn_20260315.pdf") == date(2026, 3, 15)


def test_extract_date_from_filename_yymmdd():
    assert extract_date_from_filename("260315_재무제표.xlsx") == date(2026, 3, 15)


def test_extract_date_from_filename_no_date():
    assert extract_date_from_filename("사업자등록증_사본.pdf") is None


def test_match_keywords_handles_whitespace_and_case():
    assert match_keywords("My_Business_Registration.pdf", ["business_registration"])
    assert match_keywords("사업자등록증 사본.pdf", ["사업자등록증"])
    assert match_keywords("법인 인감_증명서.pdf", ["법인인감"])
    assert not match_keywords("무관한파일.pdf", ["사업자등록증"])


def test_build_checklist_ok_missing_warning(tmp_path: Path):
    # 테스트 폴더에 파일들 배치
    (tmp_path / "사업자등록증_2026-03-15.pdf").write_bytes(b"dummy")
    (tmp_path / "법인인감_2025-10-01.pdf").write_bytes(b"dummy")   # 오래됨
    # 재무제표는 일부러 누락

    docs = [
        RequiredDocument(
            id="biz", name="사업자등록증", max_age_days=365,
            filename_hints=["사업자등록증"],
        ),
        RequiredDocument(
            id="seal", name="법인 인감증명서", max_age_days=90,
            filename_hints=["법인인감"],
        ),
        RequiredDocument(
            id="fs", name="재무제표", filename_hints=["재무제표"],
        ),
    ]

    result = build_checklist(docs, tmp_path, today=date(2026, 4, 19))
    statuses = {i.doc.id: i.status for i in result.items}
    assert statuses["biz"] == DocumentStatus.OK
    assert statuses["seal"] == DocumentStatus.WARNING    # 200일 전 발행 > 90일 제한
    assert statuses["fs"] == DocumentStatus.MISSING

    assert result.ok_count == 1
    assert result.warning_count == 1
    assert result.missing_count == 1
    assert result.is_submittable is False


def test_rfp_extractor_demo_list():
    docs = demo_required_documents()
    assert len(docs) >= 3
    assert all(isinstance(d, RequiredDocument) for d in docs)


def test_rfp_extractor_requires_key_or_network(tmp_path, monkeypatch):
    """v0.6.0: extract_from_rfp 는 이제 실제 구현. API Key 없으면 RuntimeError.

    구 foundation 의 NotImplementedError 는 이제 나지 않음."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import api_key_manager
    monkeypatch.setattr(api_key_manager, "get_key", lambda service=None: None)

    rfp = tmp_path / "rfp.pdf"
    rfp.write_bytes(b"dummy")
    with pytest.raises(RuntimeError, match="API Key"):
        extract_from_rfp(rfp)
