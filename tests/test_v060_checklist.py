"""v0.6.0: 제출서류 체크리스트 본편 검증.

실제 Gemini 호출 없이 mock 으로 테스트. HWPX 텍스트 추출은 실샘플로 검증.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.checklist.models import DocumentStatus, RequiredDocument
from src.checklist.rfp_extractor import (
    SUPPORTED_EXTENSIONS,
    _parse_response_to_docs,
    demo_required_documents,
    extract_from_rfp,
    extract_hwpx_text,
)


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "tests" / "fixtures" / "rfp_samples"
SAMPLE_HWPX = SAMPLE_DIR / "1. 입찰공고문_26아카데미.hwpx"
SAMPLE_PDF = SAMPLE_DIR / "2026 입찰공고문.pdf"

REQUIRES_HWPX = pytest.mark.skipif(not SAMPLE_HWPX.exists(), reason="HWPX sample missing")


# ---------------------------------------------------------------------------
# HWPX text extraction (no Gemini)
# ---------------------------------------------------------------------------

@REQUIRES_HWPX
def test_extract_hwpx_text_returns_non_empty():
    text = extract_hwpx_text(SAMPLE_HWPX)
    assert len(text) > 1000
    # 입찰공고문 핵심 키워드
    assert "입찰" in text or "공고" in text


@REQUIRES_HWPX
def test_extract_hwpx_text_respects_max_len():
    text = extract_hwpx_text(SAMPLE_HWPX, max_len=500)
    # 상한 + 알림 메시지까지 대략 550~650자 이내
    assert len(text) <= 700
    assert "이하" in text


def test_extract_hwpx_text_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_hwpx_text(tmp_path / "nope.hwpx")


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def test_parse_response_valid_json():
    text = json.dumps({
        "documents": [
            {
                "id": "biz_reg",
                "name": "사업자등록증",
                "is_required": True,
                "max_age_days": 365,
                "filename_hints": ["사업자등록증", "brn"],
                "description": "RFP 3.1",
            },
            {
                "id": "fs",
                "name": "재무제표",
                "is_required": True,
                "max_age_days": 0,   # 제한 없음
                "filename_hints": ["재무제표"],
                "description": "",
            },
        ]
    })
    docs = _parse_response_to_docs(text)
    assert len(docs) == 2
    assert docs[0].id == "biz_reg"
    assert docs[0].max_age_days == 365
    # max_age_days=0 은 None 으로 정규화
    assert docs[1].max_age_days is None


def test_parse_response_skips_malformed_items():
    text = json.dumps({
        "documents": [
            {"id": "ok", "name": "정상", "is_required": True, "filename_hints": ["x"]},
            {"id": "", "name": "빈 id", "filename_hints": []},       # skip
            "string instead of object",                                # skip
            {"name": "id 없음", "filename_hints": []},                # skip (KeyError)
        ]
    })
    docs = _parse_response_to_docs(text)
    assert len(docs) == 1
    assert docs[0].id == "ok"


def test_parse_response_empty_or_bad_json():
    assert _parse_response_to_docs("") == []
    assert _parse_response_to_docs("not json") == []
    assert _parse_response_to_docs('{"wrong_shape": true}') == []
    assert _parse_response_to_docs("[1, 2, 3]") == []


# ---------------------------------------------------------------------------
# extract_from_rfp — 포맷 검증 + API Key 없음 에러
# ---------------------------------------------------------------------------

def test_extract_from_rfp_rejects_unsupported_extension(tmp_path):
    """v0.9.0 부터 .hwp 도 지원됨 — 여기선 진짜 미지원 포맷으로 체크."""
    f = tmp_path / "rfp.docx"
    f.write_bytes(b"dummy")
    with pytest.raises(ValueError, match="지원하지 않는"):
        extract_from_rfp(f, api_key="fake")


def test_extract_from_rfp_requires_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import api_key_manager

    # keyring / fernet 전부 None
    monkeypatch.setattr(api_key_manager, "get_key", lambda service=None: None)

    f = tmp_path / "rfp.pdf"
    f.write_bytes(b"dummy")
    with pytest.raises(RuntimeError, match="API Key"):
        extract_from_rfp(f)


def test_extract_from_rfp_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_from_rfp(tmp_path / "nope.pdf", api_key="fake")


def test_supported_extensions():
    assert ".pdf" in SUPPORTED_EXTENSIONS
    assert ".hwpx" in SUPPORTED_EXTENSIONS
    # v0.9.0: HWP 도 지원 목록에 추가됨 (PrvText fallback)
    assert ".hwp" in SUPPORTED_EXTENSIONS
    # 여전히 docx / txt 등은 미지원
    assert ".docx" not in SUPPORTED_EXTENSIONS


# ---------------------------------------------------------------------------
# extract_from_rfp HWPX path — Gemini mocked
# ---------------------------------------------------------------------------

@REQUIRES_HWPX
def test_extract_from_rfp_hwpx_calls_gemini_with_text(monkeypatch):
    """HWPX → 텍스트 추출 → Gemini 호출 경로. Gemini 응답을 mock."""
    fake_resp = SimpleNamespace(
        text=json.dumps({
            "documents": [
                {
                    "id": "biz_reg",
                    "name": "사업자등록증",
                    "is_required": True,
                    "max_age_days": 365,
                    "filename_hints": ["사업자등록증"],
                    "description": "",
                }
            ]
        }),
        usage_metadata=SimpleNamespace(prompt_token_count=1000, candidates_token_count=50),
    )
    mock_models = MagicMock()
    mock_models.generate_content.return_value = fake_resp
    fake_genai_client = SimpleNamespace(models=mock_models, files=MagicMock())
    fake_Client = MagicMock(return_value=fake_genai_client)

    # google.genai 모듈 patch
    fake_types_module = SimpleNamespace(
        GenerateContentConfig=MagicMock(),
        ThinkingConfig=MagicMock(),
    )
    fake_genai_module = SimpleNamespace(Client=fake_Client, types=fake_types_module)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types_module)
    import google as google_pkg
    monkeypatch.setattr(google_pkg, "genai", fake_genai_module, raising=False)

    docs = extract_from_rfp(SAMPLE_HWPX, api_key="fake-key")
    assert len(docs) == 1
    assert docs[0].id == "biz_reg"
    # generate_content 호출됨 (HWPX 는 files.upload 안 씀)
    assert mock_models.generate_content.called
    # contents 인자에 RFP 본문이 들어갔는지
    args = mock_models.generate_content.call_args
    contents = args.kwargs.get("contents") or (args.args[1] if len(args.args) > 1 else "")
    assert "입찰" in str(contents) or "공고" in str(contents)


# ---------------------------------------------------------------------------
# Demo list
# ---------------------------------------------------------------------------

def test_demo_required_documents():
    docs = demo_required_documents()
    assert len(docs) >= 3
    assert all(isinstance(d, RequiredDocument) for d in docs)
    assert any("사업자등록증" in d.name for d in docs)


# ---------------------------------------------------------------------------
# GUI smoke
# ---------------------------------------------------------------------------

def test_checklist_tab_smoke(qtbot, tmp_path, monkeypatch):
    """ChecklistTab 이 뜨고 초기 상태가 올바름."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config
    from src.gui.tabs.checklist_tab import ChecklistTab

    cfg = app_config.AppConfig(default_output_dir=str(tmp_path / "out"))
    tab = ChecklistTab(cfg)
    qtbot.addWidget(tab)

    # 초기엔 결과 테이블 비어있음, 저장 버튼 비활성
    assert tab.result_table.rowCount() == 0
    assert tab.save_report_btn.isEnabled() is False


def test_checklist_tab_demo_mode_e2e(qtbot, tmp_path, monkeypatch):
    """데모 버튼으로 체크리스트 전체 플로우 — Gemini 호출 없이."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config
    from src.gui.tabs.checklist_tab import ChecklistTab

    # 샘플 폴더 준비 (사업자등록증만 있음)
    folder = tmp_path / "my_docs"
    folder.mkdir()
    (folder / "사업자등록증_2026-03-15.pdf").write_bytes(b"dummy")

    cfg = app_config.AppConfig(default_output_dir=str(tmp_path / "out"))
    tab = ChecklistTab(cfg)
    qtbot.addWidget(tab)

    tab._folder_path = folder
    tab._run_with_demo()

    # 5 서류 표시됨 (demo_required_documents 가 5개)
    assert tab.result_table.rowCount() == 5
    # 첫 행이 OK 상태 (사업자등록증 매칭)
    assert tab.save_report_btn.isEnabled() is True
    # 결과는 "제출 불가" (4종 MISSING)
    assert tab._result is not None
    assert tab._result.is_submittable is False
    assert tab._result.ok_count == 1
    assert tab._result.missing_count >= 3


def test_checklist_save_report(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config
    from src.gui.tabs.checklist_tab import ChecklistTab

    folder = tmp_path / "docs"
    folder.mkdir()

    cfg = app_config.AppConfig(default_output_dir=str(tmp_path / "out"))
    tab = ChecklistTab(cfg)
    qtbot.addWidget(tab)
    tab._folder_path = folder
    tab._run_with_demo()

    # _format_report 직접 호출
    report_text = tab._format_report(tab._result)
    assert "체크리스트 보고서" in report_text
    assert "사업자등록증" in report_text
    assert "OK" in report_text or "MISS" in report_text
