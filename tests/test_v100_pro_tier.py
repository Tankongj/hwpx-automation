"""v0.10.0: pro-tier 실전 적용 / Firebase REST / HWP BodyText / sorter ZIP.

v0.9.0 의 스캐폴딩을 실제로 쓰도록 만든 것을 검증한다. 네트워크 호출 없음.
"""
from __future__ import annotations

import io
import json
import struct
import zipfile
import zlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Track 1: pro-tier enforcement on paid features
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_tier_session(tmp_path, monkeypatch):
    """세션을 None 으로 리셋하고 끝나면 복구."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.commerce import tier_gate

    original = tier_gate.current_session()
    tier_gate.set_current_session(None)
    try:
        yield
    finally:
        tier_gate.set_current_session(original)


def _as_pro():
    from src.commerce import tier_gate
    from src.commerce.auth_client import AuthSession
    from src.commerce.user_db import User

    user = User(username="pro_user", password_hash="x", salt="y", tier="pro")
    tier_gate.set_current_session(AuthSession(user=user, tier="pro"))


def test_self_moa_blocks_free_tier(fresh_tier_session):
    from src.commerce import tier_gate
    from src.parser.gemini_resolver import GenerateResult
    from src.parser.self_moa import SelfMoAClient

    base = MagicMock()
    base.generate = MagicMock(return_value=GenerateResult(text="[]"))
    base.model = "fake"

    with pytest.raises(tier_gate.TierDeniedError) as excinfo:
        SelfMoAClient(base, draws=3)
    assert "Self-MoA" in str(excinfo.value)
    assert "pro" in str(excinfo.value)


def test_self_moa_passes_for_pro_tier(fresh_tier_session):
    from src.parser.gemini_resolver import GenerateResult
    from src.parser.self_moa import SelfMoAClient

    _as_pro()
    base = MagicMock()
    base.generate = MagicMock(return_value=GenerateResult(text="[]"))
    base.model = "fake"
    client = SelfMoAClient(base, draws=3)
    assert client.draws == 3


def test_self_moa_skip_tier_check_for_testing(fresh_tier_session):
    """_skip_tier_check 로 단위 테스트에서 게이트 우회 가능."""
    from src.parser.gemini_resolver import GenerateResult
    from src.parser.self_moa import SelfMoAClient

    base = MagicMock()
    base.generate = MagicMock(return_value=GenerateResult(text="[]"))
    base.model = "fake"
    # free 티어지만 _skip_tier_check=True 로 생성 가능
    client = SelfMoAClient(base, draws=3, _skip_tier_check=True)
    assert client.draws == 3


def test_create_default_client_openai_blocked_for_free(fresh_tier_session, tmp_path):
    from src.commerce import tier_gate
    from src.parser import gemini_resolver
    from src.settings import app_config

    cfg = app_config.AppConfig(resolver_backend="openai")
    app_config.save(cfg)

    with pytest.raises(tier_gate.TierDeniedError):
        gemini_resolver.create_default_client()


def test_create_default_client_anthropic_blocked_for_free(fresh_tier_session, tmp_path):
    from src.commerce import tier_gate
    from src.parser import gemini_resolver
    from src.settings import app_config

    cfg = app_config.AppConfig(resolver_backend="anthropic")
    app_config.save(cfg)

    with pytest.raises(tier_gate.TierDeniedError):
        gemini_resolver.create_default_client()


def test_create_default_client_gemini_works_for_free(fresh_tier_session, tmp_path, monkeypatch):
    """Gemini 는 무료 티어에서도 동작 (API Key 있으면)."""
    from src.parser import gemini_resolver
    from src.settings import api_key_manager, app_config

    cfg = app_config.AppConfig(resolver_backend="gemini")
    app_config.save(cfg)
    monkeypatch.setattr(api_key_manager, "get_key", lambda service=None: "test-key")

    # google.genai 가 실제로는 없을 수 있으므로 모킹
    import sys
    from types import SimpleNamespace

    fake_client = SimpleNamespace(models=MagicMock())
    fake_genai = SimpleNamespace(
        Client=MagicMock(return_value=fake_client),
        types=SimpleNamespace(GenerateContentConfig=MagicMock(), ThinkingConfig=MagicMock()),
    )
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)

    client = gemini_resolver.create_default_client()
    # TierDeniedError 가 안 나면 성공
    assert client is not None


def test_batch_cli_free_processes_single_file(fresh_tier_session, tmp_path, monkeypatch, capsys):
    """무료 티어 CLI build-batch 는 여러 파일이 있어도 1 개만 처리."""
    from src.cli import _cmd_build_batch

    # 템플릿과 원고 3 개
    template = tmp_path / "tpl.hwpx"
    template.write_bytes(b"dummy")

    folder = tmp_path / "txts"
    folder.mkdir()
    for i in range(3):
        (folder / f"a{i}.txt").write_text("제안서", encoding="utf-8")

    outdir = tmp_path / "out"

    # parse / convert 를 가볍게 mock (파이프라인 자체는 테스트 대상 아님)
    from src import cli as cli_mod
    from src.parser import regex_parser
    from src.hwpx import md_to_hwpx

    monkeypatch.setattr(regex_parser, "parse_file", lambda p: [])
    # cli 에 이미 from-import 된 local 바인딩을 직접 교체
    monkeypatch.setattr(
        cli_mod, "analyze_template",
        lambda t: MagicMock(to_engine_style_dict=lambda: {}),
    )
    def _fake_convert(blocks, *, template, output, style_map, run_fix_namespaces):
        Path(output).write_bytes(b"fake hwpx")
    monkeypatch.setattr(md_to_hwpx, "convert", _fake_convert)

    args = MagicMock(
        template=str(template), folder=str(folder), output_dir=str(outdir),
        recursive=False, use_gemini=False, backend=None, pro_key=False,
    )
    rc = _cmd_build_batch(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert "pro 티어 전용" in captured.out
    # 1 개만 처리
    hwpxs = list(outdir.glob("*.hwpx"))
    assert len(hwpxs) == 1


def test_batch_cli_pro_processes_all_files(fresh_tier_session, tmp_path, monkeypatch):
    """pro 티어는 전 파일 처리."""
    _as_pro()
    from src.cli import _cmd_build_batch

    template = tmp_path / "tpl.hwpx"
    template.write_bytes(b"dummy")

    folder = tmp_path / "txts"
    folder.mkdir()
    for i in range(3):
        (folder / f"a{i}.txt").write_text("제안서", encoding="utf-8")

    outdir = tmp_path / "out"

    from src import cli as cli_mod
    from src.parser import regex_parser
    from src.hwpx import md_to_hwpx

    monkeypatch.setattr(regex_parser, "parse_file", lambda p: [])
    # cli 에 이미 from-import 된 local 바인딩을 직접 교체
    monkeypatch.setattr(
        cli_mod, "analyze_template",
        lambda t: MagicMock(to_engine_style_dict=lambda: {}),
    )
    def _fake_convert(blocks, *, template, output, style_map, run_fix_namespaces):
        Path(output).write_bytes(b"fake hwpx")
    monkeypatch.setattr(md_to_hwpx, "convert", _fake_convert)

    args = MagicMock(
        template=str(template), folder=str(folder), output_dir=str(outdir),
        recursive=False, use_gemini=False, backend=None, pro_key=False,
    )
    rc = _cmd_build_batch(args)
    assert rc == 0
    hwpxs = list(outdir.glob("*.hwpx"))
    assert len(hwpxs) == 3


def test_batch_cli_env_var_grants_pro(fresh_tier_session, tmp_path, monkeypatch):
    """HWPX_PRO_KEY=1 환경변수로 CLI 에서 pro 우회."""
    monkeypatch.setenv("HWPX_PRO_KEY", "1")
    from src.cli import _cmd_build_batch

    template = tmp_path / "tpl.hwpx"
    template.write_bytes(b"dummy")
    folder = tmp_path / "txts"
    folder.mkdir()
    for i in range(2):
        (folder / f"a{i}.txt").write_text("x", encoding="utf-8")
    outdir = tmp_path / "out"

    from src import cli as cli_mod
    from src.parser import regex_parser
    from src.hwpx import md_to_hwpx

    monkeypatch.setattr(regex_parser, "parse_file", lambda p: [])
    # cli 에 이미 from-import 된 local 바인딩을 직접 교체
    monkeypatch.setattr(
        cli_mod, "analyze_template",
        lambda t: MagicMock(to_engine_style_dict=lambda: {}),
    )
    def _fake_convert(blocks, *, template, output, style_map, run_fix_namespaces):
        Path(output).write_bytes(b"fake")
    monkeypatch.setattr(md_to_hwpx, "convert", _fake_convert)

    args = MagicMock(
        template=str(template), folder=str(folder), output_dir=str(outdir),
        recursive=False, use_gemini=False, backend=None, pro_key=False,
    )
    _cmd_build_batch(args)
    assert len(list(outdir.glob("*.hwpx"))) == 2


# ---------------------------------------------------------------------------
# Track 1 + GUI: ad placeholder auto-hides for pro
# ---------------------------------------------------------------------------


def test_ad_hidden_for_pro_user(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.commerce import tier_gate
    from src.commerce.auth_client import AuthSession
    from src.commerce.user_db import User
    from src.gui.widgets.ad_placeholder import AdPlaceholder

    # pro 세션
    user = User(username="p", password_hash="x", salt="y", tier="pro")
    tier_gate.set_current_session(AuthSession(user=user, tier="pro"))
    try:
        # _apply_ad_state 의 pro 체크 로직 직접 검증
        assert tier_gate.is_allowed("pro") is True

        ad = AdPlaceholder()
        qtbot.addWidget(ad)
        # 기본은 숨김
        assert ad.is_active is False
    finally:
        tier_gate.set_current_session(None)


# ---------------------------------------------------------------------------
# Track 2: Firebase REST (mocked urlopen)
# ---------------------------------------------------------------------------


def _fake_firebase_response(body: dict):
    """urllib.request.urlopen 이 반환할 response-like 객체."""
    class _Resp:
        def __init__(self, b):
            self._b = b.encode("utf-8") if isinstance(b, str) else b
        def read(self):
            return self._b
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    return _Resp(json.dumps(body))


def test_firebase_login_success_returns_session():
    from src.commerce.auth_client import FirebaseAuthClient

    def fake_opener(req, timeout):
        return _fake_firebase_response({
            "idToken": "fake.token.value",
            "localId": "user-id",
            "expiresIn": "3600",
        })

    client = FirebaseAuthClient(api_key="test-key", _opener=fake_opener)
    session = client.login("alice@example.com", "pw123")
    assert session is not None
    assert session.user.username == "alice@example.com"
    assert session.token == "fake.token.value"
    assert session.tier == "free"  # custom claim 없으므로


def test_firebase_login_with_pro_custom_claim():
    """JWT payload 에 tier='pro' 가 있으면 반영."""
    import base64

    claim_payload = base64.urlsafe_b64encode(
        json.dumps({"tier": "pro", "sub": "u"}).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    jwt = f"header.{claim_payload}.sig"

    from src.commerce.auth_client import FirebaseAuthClient

    def fake_opener(req, timeout):
        return _fake_firebase_response({
            "idToken": jwt, "localId": "u", "expiresIn": "3600",
        })

    client = FirebaseAuthClient(api_key="test-key", _opener=fake_opener)
    session = client.login("a@b.com", "pw")
    assert session is not None
    assert session.tier == "pro"


def test_firebase_login_http_error_returns_none():
    import urllib.error
    from src.commerce.auth_client import FirebaseAuthClient

    def fake_opener(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad", {}, io.BytesIO(
                json.dumps({"error": {"message": "INVALID_PASSWORD"}}).encode("utf-8")
            ),
        )

    client = FirebaseAuthClient(api_key="test-key", _opener=fake_opener)
    session = client.login("a@b.com", "wrong")
    assert session is None


def test_firebase_register_returns_session():
    from src.commerce.auth_client import FirebaseAuthClient

    def fake_opener(req, timeout):
        assert "signUp" in req.full_url
        return _fake_firebase_response({
            "idToken": "tok", "localId": "uid", "expiresIn": "3600",
        })

    client = FirebaseAuthClient(api_key="k", _opener=fake_opener)
    s = client.register("bob@test.com", "pw", email="bob@test.com")
    assert s.user.username == "bob@test.com"


def test_firebase_register_raises_on_http_error():
    import urllib.error
    from src.commerce.auth_client import FirebaseAuthClient

    def fake_opener(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad", {}, io.BytesIO(
                json.dumps({"error": {"message": "EMAIL_EXISTS"}}).encode("utf-8")
            ),
        )

    client = FirebaseAuthClient(api_key="k", _opener=fake_opener)
    # v0.10.1+: 한국어 번역된 메시지가 ValueError 에 실림
    with pytest.raises(ValueError, match="이미 가입된 이메일|EMAIL_EXISTS"):
        client.register("a@b.com", "pw")


def test_create_auth_client_firebase_uses_real_client_when_key_present(tmp_path, monkeypatch):
    """api_key 있으면 FirebaseAuthClient 실제 반환 (stub 아님)."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.commerce.auth_client import FirebaseAuthClient, create_auth_client
    from src.settings import app_config

    cfg = app_config.AppConfig(auth_backend="firebase", firebase_api_key="real-key")
    client = create_auth_client(cfg)
    assert isinstance(client, FirebaseAuthClient)
    assert client._use_stub is False


# ---------------------------------------------------------------------------
# Track 3: HWP BodyText experimental parsing
# ---------------------------------------------------------------------------


HWP_SAMPLE = Path(
    r"D:/03_antigravity/19_[26 귀농귀촌 아카데미]/hwpx-proposal-automation/input/"
    r"1. 입찰공고문_26아카데미.hwp"
)
REQUIRES_HWP = pytest.mark.skipif(not HWP_SAMPLE.exists(), reason="HWP sample missing")


@REQUIRES_HWP
def test_extract_hwp_prefer_full_returns_body_or_falls_back():
    """실 샘플로 BodyText 시도. 성공하면 is_full=True, 실패해도 PrvText 폴백."""
    from src.checklist.hwp_text import extract_hwp_text

    r = extract_hwp_text(HWP_SAMPLE, prefer_full=True)
    assert r.text  # 어느 경로든 텍스트 반환
    assert r.source in ("body_text", "prv_text")
    # BodyText 성공 시 is_full, 폴백이면 False
    if r.source == "body_text":
        assert r.is_full is True
        # PrvText 보다는 길어야 의미 있음 (대략 2000 자 초과)
        assert len(r.text) > 2_000
    else:
        assert r.is_full is False


def test_extract_hwp_prefer_full_without_sample_falls_back(tmp_path):
    """샘플 없을 때 prefer_full=True 자체는 예외 없이 반환."""
    from src.checklist.hwp_text import extract_hwp_text

    r = extract_hwp_text(tmp_path / "missing.hwp", prefer_full=True)
    # 파일 없음 에러
    assert "없음" in r.error


def test_records_to_text_parses_synthetic_record():
    """직접 구성한 HWP 레코드에서 UTF-16LE 텍스트 추출."""
    from src.checklist import hwp_text

    # HWPTAG_PARA_TEXT 레코드: 태그 0x43, size = len(utf16)
    text_u = "안녕하세요 world"
    payload = text_u.encode("utf-16-le")
    # 헤더 (size 4095 이하이므로 inline)
    # bits: tag_id (10) | level (10) | size (12)
    tag_id = 0x43
    level = 0
    size = len(payload)
    hdr = (size << 20) | (level << 10) | tag_id
    record_stream = struct.pack("<I", hdr) + payload

    result = hwp_text._records_to_text(record_stream)
    assert "안녕하세요" in result
    assert "world" in result


def test_records_to_text_handles_large_record_size():
    """size == 0xFFF 일 때 extended size (다음 4바이트) 사용."""
    from src.checklist import hwp_text

    text_u = "A" * 5000  # 10000 바이트 > 4095
    payload = text_u.encode("utf-16-le")
    tag_id = 0x43
    level = 0
    # inline size = 0xFFF 이면 extended 사용
    hdr = (0xFFF << 20) | (level << 10) | tag_id
    record_stream = (
        struct.pack("<I", hdr) + struct.pack("<I", len(payload)) + payload
    )
    result = hwp_text._records_to_text(record_stream)
    assert result.count("A") == 5000


def test_sanitize_hwp_control_preserves_newlines_strips_markers():
    from src.checklist.hwp_text import _sanitize_hwp_control

    raw = "안녕\x00\x01\x02하세요\n두번째줄\x03끝"
    cleaned = _sanitize_hwp_control(raw)
    assert "안녕" in cleaned
    assert "하세요" in cleaned
    assert "두번째줄" in cleaned
    assert "\n" in cleaned
    assert "\x00" not in cleaned
    assert "\x01" not in cleaned


# ---------------------------------------------------------------------------
# Track 4: Sorter ZIP + report
# ---------------------------------------------------------------------------


def _build_minimal_result(tmp_path: Path):
    """최소한의 ChecklistResult 구성 — 사업자등록증 1 매칭, 법인인감 1 누락."""
    from src.checklist.matcher import build_checklist
    from src.checklist.models import RequiredDocument

    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "사업자등록증_2026-03-15.pdf").write_bytes(b"x")
    (folder / "일부_기타문서.pdf").write_bytes(b"y")  # 매칭 안 됨

    docs = [
        RequiredDocument(id="biz", name="사업자등록증", filename_hints=["사업자등록증"]),
        RequiredDocument(id="seal", name="법인 인감증명서", filename_hints=["법인인감"]),
    ]
    return build_checklist(docs, folder)


def test_sort_writes_report_by_default(tmp_path):
    from src.checklist.sorter import REPORT_FILENAME, sort_attachments

    result = _build_minimal_result(tmp_path)
    out = tmp_path / "sorted"
    report = sort_attachments(result, out)

    assert report.report_path is not None
    assert report.report_path.name == REPORT_FILENAME
    assert report.report_path.exists()
    content = report.report_path.read_text(encoding="utf-8")
    assert "제출서류 정렬 보고서" in content
    assert "사업자등록증" in content
    assert "법인 인감증명서" in content


def test_sort_skip_report(tmp_path):
    from src.checklist.sorter import sort_attachments

    result = _build_minimal_result(tmp_path)
    out = tmp_path / "sorted"
    report = sort_attachments(result, out, write_report=False)
    assert report.report_path is None
    assert not (out / "_제출서류_보고서.txt").exists()


def test_sort_make_zip(tmp_path):
    from src.checklist.sorter import sort_attachments

    result = _build_minimal_result(tmp_path)
    out = tmp_path / "sorted"
    report = sort_attachments(result, out, make_zip=True)

    assert report.zip_path is not None
    assert report.zip_path.exists()
    assert report.zip_path.suffix == ".zip"
    # ZIP 은 out 바깥 (재귀 압축 방지)
    assert report.zip_path.parent == out.parent

    # ZIP 안에 복사본 + 보고서 들어 있어야
    with zipfile.ZipFile(report.zip_path) as zf:
        names = zf.namelist()
        assert any("사업자등록증" in n for n in names)
        assert any("_제출서류_보고서" in n for n in names)


def test_sort_custom_zip_name(tmp_path):
    from src.checklist.sorter import sort_attachments

    result = _build_minimal_result(tmp_path)
    out = tmp_path / "sorted"
    report = sort_attachments(
        result, out, make_zip=True, zip_name="제출자료_최종.zip",
    )
    assert report.zip_path.name == "제출자료_최종.zip"
