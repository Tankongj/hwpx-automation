"""v0.11.0: AI 기본법 / Gemini 3 / Sentry / G2B / Skills 검증.

외부 네트워크 없음 — 전부 mocked / local.
"""
from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest


ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# AI disclosure (AI 기본법 2026-01-22)
# ---------------------------------------------------------------------------


def test_disclosure_enabled_for_ai_backend():
    from src.commerce.ai_disclosure import make_disclosure

    d = make_disclosure(backend="Gemini", ai_used=True)
    assert d.enabled is True
    assert "Gemini" in d.format_file_meta()
    assert "AI 기본법" in d.format_report_footer()


def test_disclosure_disabled_when_no_ai():
    from src.commerce.ai_disclosure import make_disclosure

    d = make_disclosure(backend="None", ai_used=False)
    assert d.enabled is False
    assert d.format_file_meta() == ""
    assert d.format_report_footer() == ""


def test_is_ai_backend():
    from src.commerce.ai_disclosure import is_ai_backend

    assert is_ai_backend("Gemini") is True
    assert is_ai_backend("Claude") is True
    assert is_ai_backend("Ollama") is True  # 로컬도 AI
    assert is_ai_backend("None") is False
    assert is_ai_backend("off") is False
    assert is_ai_backend("") is False


def test_sorter_adds_ai_footer_when_backend_given(tmp_path):
    from src.checklist.matcher import build_checklist
    from src.checklist.models import RequiredDocument
    from src.checklist.sorter import sort_attachments

    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "사업자등록증.pdf").write_bytes(b"x")

    docs = [RequiredDocument(id="biz", name="사업자등록증", filename_hints=["사업자등록증"])]
    result = build_checklist(docs, folder)

    out = tmp_path / "sorted"
    report = sort_attachments(result, out, ai_backend="Gemini")
    text = report.report_path.read_text(encoding="utf-8")
    assert "AI 기본법" in text
    assert "생성형 AI" in text


def test_sorter_no_footer_when_backend_empty(tmp_path):
    from src.checklist.matcher import build_checklist
    from src.checklist.models import RequiredDocument
    from src.checklist.sorter import sort_attachments

    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "사업자등록증.pdf").write_bytes(b"x")

    docs = [RequiredDocument(id="biz", name="사업자등록증", filename_hints=["사업자등록증"])]
    result = build_checklist(docs, folder)

    out = tmp_path / "sorted"
    report = sort_attachments(result, out, ai_backend="")
    text = report.report_path.read_text(encoding="utf-8")
    assert "AI 기본법" not in text


# ---------------------------------------------------------------------------
# Gemini 3 Flash + 모델별 가격
# ---------------------------------------------------------------------------


def test_available_models_includes_gemini_3():
    from src.parser.gemini_resolver import AVAILABLE_MODELS

    assert "gemini-3-flash" in AVAILABLE_MODELS
    assert "gemini-3-flash-lite" in AVAILABLE_MODELS
    assert "gemini-2.5-flash" in AVAILABLE_MODELS  # back-compat


def test_price_for_model_gemini_2_5():
    from src.parser.gemini_resolver import price_for_model

    p_in, p_out = price_for_model("gemini-2.5-flash")
    assert p_in == 0.075
    assert p_out == 0.30


def test_price_for_model_gemini_3_flash_lite_is_cheapest():
    from src.parser.gemini_resolver import price_for_model

    lite = price_for_model("gemini-3-flash-lite")
    flash = price_for_model("gemini-3-flash")
    pro = price_for_model("gemini-3-pro")

    assert lite[0] < flash[0] < pro[0]  # 입력 가격 순서
    assert lite[1] < flash[1] < pro[1]  # 출력 가격 순서


def test_price_for_unknown_model_falls_back_to_default():
    from src.parser.gemini_resolver import price_for_model, PRICE_INPUT_USD_PER_M

    p_in, _ = price_for_model("gemini-unknown-foo")
    assert p_in == PRICE_INPUT_USD_PER_M


# ---------------------------------------------------------------------------
# Sentry opt-in scaffold
# ---------------------------------------------------------------------------


def test_sentry_init_no_dsn_returns_false():
    from src.utils import error_reporter

    assert error_reporter.init(dsn=None) is False
    assert error_reporter.is_initialized() is False


def test_sentry_init_empty_dsn_returns_false():
    from src.utils import error_reporter

    assert error_reporter.init(dsn="") is False


def test_capture_exception_silent_when_not_initialized():
    from src.utils import error_reporter

    # 초기화 안 한 상태 — 예외 없이 동작해야
    try:
        raise ValueError("test")
    except ValueError as exc:
        error_reporter.capture_exception(exc, source="test")
    # 도달하면 OK


def test_mask_email_hides_user():
    from src.utils.error_reporter import _mask_email

    result = _mask_email("error for alice@example.com")
    assert "alice" not in result
    assert "@example.com" in result
    assert "a***" in result


def test_scrub_pii_removes_sensitive_env():
    from src.utils.error_reporter import _scrub_pii

    event = {
        "request": {
            "env": {
                "API_KEY": "secret-123",
                "OPENAI_API_TOKEN": "sk-...",
                "NORMAL_VAR": "ok",
            }
        },
        "exception": {"values": []},
    }
    _scrub_pii(event, {})
    env = event["request"]["env"]
    assert env["API_KEY"] == "[Filtered]"
    assert env["OPENAI_API_TOKEN"] == "[Filtered]"
    assert env["NORMAL_VAR"] == "ok"


# ---------------------------------------------------------------------------
# G2B (나라장터) adapter scaffold
# ---------------------------------------------------------------------------


def _fake_g2b_response(body: dict):
    class _Resp:
        def __init__(self, data):
            self._raw = json.dumps(data).encode("utf-8")
        def read(self):
            return self._raw
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    return _Resp(body)


def test_g2b_requires_service_key():
    from src.checklist.g2b_adapter import G2BClient

    with pytest.raises(ValueError, match="ServiceKey"):
        G2BClient(service_key="", _skip_tier_check=True)


def test_g2b_blocks_free_tier(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.commerce import tier_gate
    from src.checklist.g2b_adapter import G2BClient

    tier_gate.set_current_session(None)  # 무료
    with pytest.raises(tier_gate.TierDeniedError):
        G2BClient(service_key="TESTKEY")


def test_g2b_search_normal_path(tmp_path, monkeypatch):
    """mocked 응답으로 search_bids 전체 경로 검증."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.checklist.g2b_adapter import G2BClient

    fake_body = {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
            "body": {
                "totalCount": 1,
                "numOfRows": 10,
                "pageNo": 1,
                "items": [
                    {
                        "bidNtceNo": "20260419001-00",
                        "bidNtceNm": "2026 귀농귀촌 아카데미 운영",
                        "ntceInsttNm": "농림수산식품교육문화정보원",
                        "asignBdgtAmt": "150000000",
                        "bidNtceDt": "202604190900",
                        "bidClseDt": "202604301800",
                    },
                ],
            },
        },
    }

    def fake_opener(url, timeout):
        return _fake_g2b_response(fake_body)

    client = G2BClient(
        service_key="X", _skip_tier_check=True, _opener=fake_opener,
    )
    r = client.search_bids(keyword="귀농귀촌", days=7)
    assert not r.error
    assert r.total_count == 1
    assert len(r.items) == 1
    bid = r.items[0]
    assert bid.bid_no == "20260419001-00"
    assert "귀농귀촌" in bid.title
    assert bid.amount_krw == 150_000_000


def test_g2b_handles_api_error(monkeypatch):
    from src.checklist.g2b_adapter import G2BClient

    fake_body = {"response": {"header": {"resultCode": "30", "resultMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"}}}

    def fake_opener(url, timeout):
        return _fake_g2b_response(fake_body)

    client = G2BClient("X", _skip_tier_check=True, _opener=fake_opener)
    r = client.search_bids(keyword="X")
    assert r.error
    assert "SERVICE_KEY" in r.error


def test_g2b_handles_http_error(monkeypatch):
    from src.checklist.g2b_adapter import G2BClient

    def fake_opener(url, timeout):
        raise urllib.error.HTTPError(url, 500, "Server Error", {}, io.BytesIO(b""))

    client = G2BClient("X", _skip_tier_check=True, _opener=fake_opener)
    r = client.search_bids(keyword="X")
    assert "HTTP 500" in r.error
    assert r.items == []


# ---------------------------------------------------------------------------
# Firebase error 한국어 치환
# ---------------------------------------------------------------------------


def test_firebase_error_kr_for_known_codes():
    from src.commerce.auth_client import firebase_error_to_korean

    assert "비밀번호" in firebase_error_to_korean("INVALID_PASSWORD")
    assert "이미 가입" in firebase_error_to_korean("EMAIL_EXISTS")
    assert "이메일" in firebase_error_to_korean("EMAIL_NOT_FOUND")
    assert "너무 많이" in firebase_error_to_korean("TOO_MANY_ATTEMPTS_TRY_LATER")


def test_firebase_error_kr_strips_detail_message():
    """'CODE : extra info' 형태도 코드 매칭."""
    from src.commerce.auth_client import firebase_error_to_korean

    assert "6자 이상" in firebase_error_to_korean(
        "WEAK_PASSWORD : Password should be at least 6 characters"
    )


def test_firebase_error_kr_falls_back_to_original():
    from src.commerce.auth_client import firebase_error_to_korean

    # 모르는 코드는 원문 유지
    result = firebase_error_to_korean("UNKNOWN_FIREBASE_CODE")
    assert "UNKNOWN_FIREBASE_CODE" in result


# ---------------------------------------------------------------------------
# Claude Code Skills 파일 존재 확인
# ---------------------------------------------------------------------------


def test_claude_skills_directory_exists():
    skills_dir = ROOT / ".claude" / "skills"
    assert skills_dir.exists()
    skill_files = list(skills_dir.glob("*.md"))
    assert len(skill_files) >= 4, f"Claude skills 4개 이상 기대, 실제 {len(skill_files)}"


def test_claude_skills_have_frontmatter():
    """각 skill 파일이 YAML frontmatter (name + description) 를 가져야."""
    skills_dir = ROOT / ".claude" / "skills"
    for md in skills_dir.glob("*.md"):
        text = md.read_text(encoding="utf-8")
        assert text.startswith("---"), f"{md.name}: frontmatter 시작 안 함"
        # closing ---
        head = text.split("---", 2)
        assert len(head) >= 3, f"{md.name}: frontmatter 닫힘 부족"
        front = head[1]
        assert "name:" in front
        assert "description:" in front


# ---------------------------------------------------------------------------
# AppConfig v0.11.0 필드
# ---------------------------------------------------------------------------


def test_app_config_v110_fields_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config

    cfg = app_config.AppConfig(
        error_reporting_optin=True,
        sentry_dsn="https://key@sentry.io/1234",
    )
    app_config.save(cfg)
    loaded = app_config.load()
    assert loaded.error_reporting_optin is True
    assert loaded.sentry_dsn == "https://key@sentry.io/1234"
