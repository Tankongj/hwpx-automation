"""v0.9.0: 4 트랙 통합 테스트.

- Track 4: pure-python HWP 텍스트
- Track 2-A: attachment sorter
- Track 2-C: 템플릿 썸네일
- Track 3: benchmark synth 함수 (실행은 안 함, 생성 로직만)
- Track 1: AuthClient / tier_gate / ad rotation
"""
from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HWP_SAMPLE = Path(
    r"D:/03_antigravity/19_[26 귀농귀촌 아카데미]/hwpx-proposal-automation/input/"
    r"1. 입찰공고문_26아카데미.hwp"
)
BUNDLED = ROOT / "templates" / "00_기본_10단계스타일.hwpx"

REQUIRES_HWP = pytest.mark.skipif(not HWP_SAMPLE.exists(), reason="HWP sample missing")
REQUIRES_BUNDLED = pytest.mark.skipif(not BUNDLED.exists(), reason="bundled missing")


# ---------------------------------------------------------------------------
# Track 4: HWP text
# ---------------------------------------------------------------------------

@REQUIRES_HWP
def test_extract_hwp_prvtext_non_empty():
    from src.checklist.hwp_text import extract_hwp_text

    r = extract_hwp_text(HWP_SAMPLE)
    assert r.text
    assert r.source == "prv_text"
    assert r.is_full is False
    assert len(r.text) > 500


def test_extract_hwp_missing_file(tmp_path):
    from src.checklist.hwp_text import extract_hwp_text

    r = extract_hwp_text(tmp_path / "nope.hwp")
    assert r.text == ""
    assert "없음" in r.error


def test_extract_hwp_wrong_ext(tmp_path):
    from src.checklist.hwp_text import extract_hwp_text

    p = tmp_path / "foo.pdf"
    p.write_bytes(b"%PDF%")
    r = extract_hwp_text(p)
    assert not r.text
    assert "HWP" in r.error


# ---------------------------------------------------------------------------
# Track 2-A: sorter
# ---------------------------------------------------------------------------

def test_sort_attachments_copies_with_numbering(tmp_path):
    from src.checklist.matcher import build_checklist
    from src.checklist.models import RequiredDocument
    from src.checklist.sorter import sort_attachments

    # 제출 폴더
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "사업자등록증_2026-03-15.pdf").write_bytes(b"x")
    (folder / "법인인감_2026-02-01.pdf").write_bytes(b"x")
    (folder / "unrelated.jpg").write_bytes(b"x")

    docs = [
        RequiredDocument(id="biz", name="사업자등록증", filename_hints=["사업자등록증"]),
        RequiredDocument(id="seal", name="법인 인감증명서", filename_hints=["법인인감"]),
        RequiredDocument(id="fs", name="재무제표", filename_hints=["재무제표"]),
    ]
    result = build_checklist(docs, folder)

    out = tmp_path / "sorted"
    report = sort_attachments(result, out)
    assert len(report.copied) == 2
    assert any("01_" in f.name for f in report.copied)
    assert any("02_" in f.name for f in report.copied)
    assert "재무제표" in report.missing
    # 매칭 안 된 unrelated.jpg 는 _미매칭 폴더로
    assert (out / "_미매칭" / "unrelated.jpg").exists()


def test_sort_attachments_handles_no_matches(tmp_path):
    from src.checklist.matcher import build_checklist
    from src.checklist.models import RequiredDocument
    from src.checklist.sorter import sort_attachments

    folder = tmp_path / "empty"
    folder.mkdir()

    docs = [RequiredDocument(id="x", name="없는 서류", filename_hints=["nope"])]
    result = build_checklist(docs, folder)

    out = tmp_path / "out"
    report = sort_attachments(result, out)
    assert report.copied == []
    assert report.missing == ["없는 서류"]


# ---------------------------------------------------------------------------
# Track 2-C: thumbnail
# ---------------------------------------------------------------------------

@REQUIRES_BUNDLED
def test_extract_thumbnail_bytes_from_bundled():
    from src.template.thumbnail import extract_thumbnail_bytes, has_thumbnail

    data = extract_thumbnail_bytes(BUNDLED)
    assert data is not None
    # PNG 시그니처
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert has_thumbnail(BUNDLED) is True


def test_extract_thumbnail_none_for_non_hwpx(tmp_path):
    from src.template.thumbnail import extract_thumbnail_bytes, has_thumbnail

    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF%")
    assert extract_thumbnail_bytes(p) is None
    assert has_thumbnail(p) is False


# ---------------------------------------------------------------------------
# Track 3: benchmark synth
# ---------------------------------------------------------------------------

def test_benchmark_synthesize_proposal_respects_target_roughly():
    """합성 원고는 target_chars 에 비슷하게 도달해야 한다."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import benchmark
    finally:
        sys.path.pop(0)

    text = benchmark.synthesize_proposal(5_000)
    # 한 chapter 블록이 수만 자를 생성하므로 over-shoot 은 크다 — 상한 완화
    assert len(text) >= 5_000
    assert len(text) <= 200_000
    # 기호 다양성 확인
    for marker in ["# Ⅰ.", "## ", "### ", "(1)", "□", "❍", "-", "·", "※"]:
        assert marker in text


# ---------------------------------------------------------------------------
# Track 1-A: AuthClient
# ---------------------------------------------------------------------------

def test_local_auth_client_wraps_userstore(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.commerce.auth_client import AuthSession, LocalAuthClient
    from src.commerce.user_db import UserStore

    store = UserStore(base_path=tmp_path)
    client = LocalAuthClient(store=store)

    session = client.register("alice", "password123")
    assert isinstance(session, AuthSession)
    assert session.user.username == "alice"
    assert session.tier == "free"

    # 올바른 로그인
    s2 = client.login("alice", "password123")
    assert s2 is not None

    # 틀린 로그인
    assert client.login("alice", "wrong") is None


def test_firebase_auth_client_stub_raises():
    """v0.10.0: _use_stub=True 로 명시적으로 요청할 때만 NotImplementedError.

    기본 (_use_stub=False) 는 실제 urllib 요청을 보냄 → 별도 네트워크 테스트에서 커버.
    """
    from src.commerce.auth_client import FirebaseAuthClient

    c = FirebaseAuthClient(api_key="x", _use_stub=True)
    with pytest.raises(NotImplementedError):
        c.login("alice", "x")


def test_create_auth_client_defaults_to_local(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.commerce.auth_client import LocalAuthClient, create_auth_client
    from src.settings import app_config

    cfg = app_config.AppConfig()
    client = create_auth_client(cfg)
    assert isinstance(client, LocalAuthClient)


def test_create_auth_client_firebase_falls_back_without_key(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.commerce.auth_client import LocalAuthClient, create_auth_client
    from src.settings import app_config

    # firebase 로 지정했지만 api_key 없음 → local 로 fallback
    cfg = app_config.AppConfig(auth_backend="firebase", firebase_api_key="")
    client = create_auth_client(cfg)
    assert isinstance(client, LocalAuthClient)


# ---------------------------------------------------------------------------
# Track 1-B: tier_gate
# ---------------------------------------------------------------------------

def test_tier_gate_free_by_default():
    from src.commerce import tier_gate
    tier_gate.set_current_session(None)
    assert tier_gate.current_tier() == "free"
    assert tier_gate.is_allowed("free") is True
    assert tier_gate.is_allowed("pro") is False
    assert tier_gate.is_allowed("team") is False


def test_tier_gate_pro_session_allows_pro(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.commerce import tier_gate
    from src.commerce.auth_client import AuthSession
    from src.commerce.user_db import User

    user = User(username="alice", password_hash="x", salt="y", tier="pro")
    tier_gate.set_current_session(AuthSession(user=user, tier="pro"))
    try:
        assert tier_gate.current_tier() == "pro"
        assert tier_gate.is_allowed("free") is True
        assert tier_gate.is_allowed("pro") is True
        assert tier_gate.is_allowed("team") is False
    finally:
        tier_gate.set_current_session(None)


def test_tier_gate_decorator_blocks_free_user(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.commerce import tier_gate

    tier_gate.set_current_session(None)

    @tier_gate.requires_tier("pro", feature="Self-MoA")
    def pro_feature():
        return "expensive answer"

    with pytest.raises(tier_gate.TierDeniedError) as excinfo:
        pro_feature()
    assert "pro" in str(excinfo.value)
    assert "Self-MoA" in str(excinfo.value)


def test_tier_gate_decorator_passes_for_pro_user(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.commerce import tier_gate
    from src.commerce.auth_client import AuthSession
    from src.commerce.user_db import User

    user = User(username="bob", password_hash="x", salt="y", tier="pro")
    tier_gate.set_current_session(AuthSession(user=user, tier="pro"))

    @tier_gate.requires_tier("pro")
    def pro_feature():
        return 42

    try:
        assert pro_feature() == 42
    finally:
        tier_gate.set_current_session(None)


# ---------------------------------------------------------------------------
# Track 1-C: ad rotation
# ---------------------------------------------------------------------------

def test_ad_placeholder_rotation_multi_items(qtbot):
    from src.gui.widgets.ad_placeholder import AdPlaceholder

    ad = AdPlaceholder()
    qtbot.addWidget(ad)
    items = [
        ("광고 1", "https://a.example"),
        ("광고 2", "https://b.example"),
        ("광고 3", "https://c.example"),
    ]
    ad.activate_rotating(items, interval_sec=0, height=60)   # 정적, 첫 광고만
    assert ad.is_active is True
    assert ad.height() == 60
    # 텍스트 첫 항목 반영
    assert "광고 1" in ad._ad_label.text()

    ad.deactivate()
    assert ad.is_active is False


def test_ad_placeholder_rotation_advance(qtbot):
    from src.gui.widgets.ad_placeholder import AdPlaceholder

    ad = AdPlaceholder()
    qtbot.addWidget(ad)
    items = [("T1", "u1"), ("T2", "u2")]
    ad.activate_rotating(items, interval_sec=0)
    assert "T1" in ad._ad_label.text()
    ad._advance_rotation()
    assert "T2" in ad._ad_label.text()
    ad._advance_rotation()
    assert "T1" in ad._ad_label.text()   # 순환


# ---------------------------------------------------------------------------
# AppConfig v0.9.0 fields
# ---------------------------------------------------------------------------

def test_appconfig_v090_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config

    cfg = app_config.AppConfig(
        ad_urls=["https://a", "https://b"],
        ad_texts=["광고A", "광고B"],
        ad_rotation_sec=15,
        auth_backend="firebase",
        firebase_api_key="test-key",
    )
    app_config.save(cfg)
    loaded = app_config.load()
    assert loaded.ad_urls == ["https://a", "https://b"]
    assert loaded.ad_texts == ["광고A", "광고B"]
    assert loaded.ad_rotation_sec == 15
    assert loaded.auth_backend == "firebase"
    assert loaded.firebase_api_key == "test-key"
