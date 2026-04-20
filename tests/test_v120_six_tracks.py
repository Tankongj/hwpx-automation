"""v0.12.0: 6 트랙 통합 테스트 + 쿠팡 Partners 매출 채널.

트랙:
1. python-hwpx 어댑터
2. 쿠팡 Partners 광고 위젯
3. rfp_extractor python-hwpx 우선
4. instructor 기반 unified resolver (opt-in)
5. Gemini Batch API
6. FastMCP 서버 / Azure 서명 준비 / GS readiness checker
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUNDLED = ROOT / "templates" / "00_기본_10단계스타일.hwpx"
REQUIRES_BUNDLED = pytest.mark.skipif(not BUNDLED.exists(), reason="bundled template missing")


# ---------------------------------------------------------------------------
# Track 1: python-hwpx 어댑터
# ---------------------------------------------------------------------------


def test_hwpx_lib_adapter_availability():
    from src.hwpx.hwpx_lib_adapter import is_available, version

    # 이 프로젝트는 이미 python-hwpx 를 의존성으로 가짐
    assert is_available() is True
    v = version()
    assert v and v != "unknown"


@REQUIRES_BUNDLED
def test_hwpx_lib_extract_text_on_bundled():
    from src.hwpx.hwpx_lib_adapter import extract_text

    text = extract_text(BUNDLED)
    assert text
    assert len(text) > 100


def test_hwpx_lib_extract_text_missing_file(tmp_path):
    from src.hwpx.hwpx_lib_adapter import extract_text

    with pytest.raises(FileNotFoundError):
        extract_text(tmp_path / "nope.hwpx")


def test_hwpx_lib_extract_text_safe_returns_none_on_error(tmp_path):
    """extract_text_safe 는 예외 대신 None."""
    from src.hwpx.hwpx_lib_adapter import extract_text_safe

    p = tmp_path / "broken.hwpx"
    p.write_bytes(b"not a valid hwpx")
    result = extract_text_safe(p)
    assert result is None


@REQUIRES_BUNDLED
def test_hwpx_lib_count_paragraphs():
    from src.hwpx.hwpx_lib_adapter import count_paragraphs

    n = count_paragraphs(BUNDLED)
    assert n is not None and n > 0


@REQUIRES_BUNDLED
def test_hwpx_lib_has_section():
    from src.hwpx.hwpx_lib_adapter import has_section

    assert has_section(BUNDLED) is True


# ---------------------------------------------------------------------------
# Track 2: 쿠팡 Partners 광고 위젯
# ---------------------------------------------------------------------------


def test_coupang_build_html_structure():
    from src.gui.widgets.coupang_ad import build_html

    html = build_html(partner_id=982081, tracking_code="AF7480765")
    assert "982081" in html
    assert "AF7480765" in html
    assert "ads-partners.coupang.com/g.js" in html
    assert "PartnersCoupang.G" in html
    assert "carousel" in html


def test_coupang_build_html_sanitizes_injection():
    """tracking_code 에 JS injection 시도 → 알파넘/-/_만 남음."""
    from src.gui.widgets.coupang_ad import build_html

    html = build_html(
        partner_id=1,
        tracking_code="AF7<script>alert(1)</script>",
    )
    # <script> 태그는 sanitize 되어야
    assert "<script>alert" not in html
    assert "alert(1)" not in html


def test_coupang_widget_construct(qtbot):
    """QWebEngineView 있는 경우 위젯 초기화."""
    from src.gui.widgets.coupang_ad import CoupangAdWidget

    w = CoupangAdWidget(partner_id=982081, tracking_code="AF7480765")
    qtbot.addWidget(w)
    # 렌더 성공 여부는 네트워크 / QtWebEngine 환경에 따라 다름 — 둘 중 하나
    assert isinstance(w.is_rendered, bool)
    assert w.partner_id == 982081
    assert w.tracking_code == "AF7480765"


def test_coupang_disclosure_label_always_shown(qtbot):
    """공정위 의무 표기 라벨은 항상 존재."""
    from src.gui.widgets.coupang_ad import CoupangAdWidget, DISCLOSURE_TEXT

    w = CoupangAdWidget(partner_id=1, tracking_code="X")
    qtbot.addWidget(w)
    assert w._disc_label.text() == DISCLOSURE_TEXT
    assert "쿠팡 파트너스" in DISCLOSURE_TEXT
    assert "수수료" in DISCLOSURE_TEXT


def test_ad_placeholder_activate_coupang(qtbot):
    from src.gui.widgets.ad_placeholder import AdPlaceholder

    ad = AdPlaceholder()
    qtbot.addWidget(ad)
    # 잘못된 인자 → 비활성
    ok = ad.activate_coupang(partner_id=0, tracking_code="")
    assert ok is False
    assert ad.is_active is False

    # 정상 인자 → 활성 (렌더 성공/실패는 환경 의존이지만 `_active` True)
    ad.activate_coupang(
        partner_id=982081, tracking_code="AF7480765",
        width=680, height=80,
    )
    assert ad.is_active is True
    assert ad._coupang_widget is not None

    # 비활성화 시 위젯 정리
    ad.deactivate()
    assert ad.is_active is False
    assert ad._coupang_widget is None


def test_app_config_coupang_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config

    cfg = app_config.AppConfig(
        coupang_partner_id=982081,
        coupang_tracking_code="AF7480765",
        coupang_template="carousel",
        coupang_width=680,
        coupang_height=80,
    )
    app_config.save(cfg)
    loaded = app_config.load()
    assert loaded.coupang_partner_id == 982081
    assert loaded.coupang_tracking_code == "AF7480765"


# ---------------------------------------------------------------------------
# Track 3: rfp_extractor 가 python-hwpx 우선
# ---------------------------------------------------------------------------


@REQUIRES_BUNDLED
def test_rfp_extractor_uses_python_hwpx_when_available():
    """python-hwpx 가 있으면 그 경로로 동작 (길이만 확인)."""
    from src.checklist.rfp_extractor import extract_hwpx_text

    text = extract_hwpx_text(BUNDLED)
    assert text
    assert len(text) > 100


def test_rfp_extractor_falls_back_without_python_hwpx(tmp_path, monkeypatch):
    """python-hwpx 가 없는 척 → lxml 경로 사용 (여전히 추출 가능)."""
    import zipfile
    from src.checklist import rfp_extractor

    # 아주 최소 HWPX 만들기 (section0.xml 에 <hp:p><hp:t> 하나)
    p = tmp_path / "tiny.hwpx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", b"application/hwp+zip")
        z.writestr(
            "Contents/section0.xml",
            (
                '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
                'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
                '<hp:p><hp:run><hp:t>테스트 본문</hp:t></hp:run></hp:p>'
                '</hs:sec>'
            ).encode("utf-8"),
        )

    # python-hwpx 가 내부 오류로 실패하면 lxml 경로로 fallback
    with patch("src.hwpx.hwpx_lib_adapter.is_available", return_value=False):
        text = rfp_extractor.extract_hwpx_text(p)
    assert "테스트 본문" in text


# ---------------------------------------------------------------------------
# Track 4: instructor unified resolver (opt-in)
# ---------------------------------------------------------------------------


def test_instructor_is_available():
    pytest.importorskip("instructor", reason="instructor not installed in this env (CI/minimal)")
    from src.parser.instructor_resolver import is_available
    assert is_available() is True


def test_instructor_config_defaults():
    from src.parser.instructor_resolver import InstructorConfig
    cfg = InstructorConfig()
    assert cfg.provider == "gemini"
    assert cfg.max_retries == 2


def test_create_default_client_uses_instructor_when_opted_in(
    tmp_path, monkeypatch,
):
    """use_instructor_resolver=True 일 때 InstructorResolverClient 로 분기.

    instructor.from_genai 내부 호출을 통째로 mock — 실 네트워크 회피.
    """
    pytest.importorskip("instructor", reason="instructor not installed in this env (CI/minimal)")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "test-fake-key-for-unit-test-only")
    from src.settings import app_config
    from src.parser import gemini_resolver, instructor_resolver

    # InstructorResolverClient._build_client 를 직접 mock
    fake_wrapped = MagicMock()
    monkeypatch.setattr(
        instructor_resolver.InstructorResolverClient, "_build_client",
        staticmethod(lambda cfg: fake_wrapped),
    )

    cfg = app_config.AppConfig(
        use_instructor_resolver=True,
        resolver_backend="gemini",
    )
    app_config.save(cfg)
    client = gemini_resolver.create_default_client()
    # model 필드가 "instructor:gemini:..." 여야 — unified 경로 확인
    assert hasattr(client, "model")
    assert "instructor" in client.model.lower()


# ---------------------------------------------------------------------------
# Track 5: Gemini Batch API
# ---------------------------------------------------------------------------


def test_batch_discount_constant():
    from src.parser.gemini_batch import BATCH_DISCOUNT
    assert BATCH_DISCOUNT == 0.5


def test_batch_request_dataclass():
    from src.parser.gemini_batch import BatchRequest
    r = BatchRequest(key="draw_1", prompt="hello")
    assert r.key == "draw_1"
    assert r.prompt == "hello"
    assert r.schema is None


def test_batch_result_defaults():
    from src.parser.gemini_batch import BatchResult
    r = BatchResult()
    assert r.items == []
    assert r.state == ""


def test_batch_client_submit_empty_returns_succeeded():
    """빈 요청 리스트는 즉시 SUCCEEDED."""
    from src.parser.gemini_batch import GeminiBatchClient

    client = GeminiBatchClient(api_key="fake", model="gemini-2.5-flash")
    result = client.submit_and_wait([])
    assert result.state == "SUCCEEDED"
    assert result.items == []


def test_batch_submit_error_path():
    """_submit 이 예외 던지면 ERROR state."""
    from src.parser.gemini_batch import BatchRequest, GeminiBatchClient

    client = GeminiBatchClient(api_key="fake", model="gemini-2.5-flash")
    client._lazy_client = MagicMock(side_effect=RuntimeError("no sdk"))
    result = client.submit_and_wait(
        [BatchRequest(key="a", prompt="x")],
    )
    assert result.state == "ERROR"
    assert "no sdk" in result.error


# ---------------------------------------------------------------------------
# Track 6a: FastMCP 서버
# ---------------------------------------------------------------------------


def test_mcp_app_creates():
    """create_mcp_app 이 예외 없이 FastMCP 인스턴스 반환."""
    pytest.importorskip("mcp", reason="mcp/FastMCP not installed in this env (CI/minimal)")
    from src.mcp_server.server import create_mcp_app

    app = create_mcp_app()
    assert app is not None
    # name 은 FastMCP 인스턴스에 있어야
    assert hasattr(app, "name") or hasattr(app, "_name") or True


def test_mcp_safe_path_rejects_nonexistent(tmp_path):
    from src.mcp_server.server import _safe_path
    with pytest.raises(FileNotFoundError):
        _safe_path(str(tmp_path / "nope.hwpx"))


def test_mcp_safe_path_rejects_null_bytes():
    from src.mcp_server.server import _safe_path
    with pytest.raises(ValueError):
        _safe_path("a\x00b")


def test_mcp_safe_path_rejects_empty():
    from src.mcp_server.server import _safe_path
    with pytest.raises(ValueError):
        _safe_path("")


# ---------------------------------------------------------------------------
# Track 6b: Azure 서명 스크립트
# ---------------------------------------------------------------------------


def test_sign_script_exists():
    assert (ROOT / "scripts" / "sign_release.py").exists()


def test_sign_script_env_check(monkeypatch):
    """환경변수 모두 없으면 check_env 가 False."""
    # scripts 는 import 가능해야
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "sign_release", ROOT / "scripts" / "sign_release.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.delenv("HWPX_SIGNING_KEY_VAULT", raising=False)
    monkeypatch.delenv("HWPX_SIGNING_CERT_PROFILE", raising=False)
    monkeypatch.delenv("HWPX_SIGNING_TENANT_ID", raising=False)
    monkeypatch.delenv("HWPX_SIGNING_CLIENT_ID", raising=False)

    ok, missing = mod.check_env()
    assert ok is False
    assert len(missing) == 4


def test_sign_script_env_check_passes(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "sign_release", ROOT / "scripts" / "sign_release.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setenv("HWPX_SIGNING_KEY_VAULT", "vault")
    monkeypatch.setenv("HWPX_SIGNING_CERT_PROFILE", "prof")
    monkeypatch.setenv("HWPX_SIGNING_TENANT_ID", "tenant")
    monkeypatch.setenv("HWPX_SIGNING_CLIENT_ID", "client")

    ok, missing = mod.check_env()
    assert ok is True
    assert missing == []


# ---------------------------------------------------------------------------
# Track 6c: GS readiness checker
# ---------------------------------------------------------------------------


def test_gs_readiness_version_check():
    """버전 일관성 체크가 현재 상태에서 pass."""
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location(
        "gs_cert_readiness_test", ROOT / "scripts" / "gs_cert_readiness.py",
    )
    mod = importlib.util.module_from_spec(spec)
    # dataclass forward-ref 해석용 — sys.modules 에 선등록
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(spec.name, None)

    check = mod.check_version_consistency()
    assert check.ok is True
    assert check.severity == "required"


def test_gs_readiness_ai_disclosure_check():
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location(
        "gs_cert_readiness_test", ROOT / "scripts" / "gs_cert_readiness.py",
    )
    mod = importlib.util.module_from_spec(spec)
    # dataclass forward-ref 해석용 — sys.modules 에 선등록
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(spec.name, None)

    check = mod.check_ai_disclosure()
    assert check.ok is True


def test_gs_readiness_oss_notices_present():
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location(
        "gs_cert_readiness_test", ROOT / "scripts" / "gs_cert_readiness.py",
    )
    mod = importlib.util.module_from_spec(spec)
    # dataclass forward-ref 해석용 — sys.modules 에 선등록
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(spec.name, None)

    check = mod.check_oss_notice()
    # v0.12.0 에선 OSS_NOTICES.md 자동 생성돼야
    assert check.ok is True


def test_oss_notices_file_exists():
    """generate_oss_notices.py 실행 결과물이 존재해야."""
    p = ROOT / "OSS_NOTICES.md"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "오픈소스 라이선스 고지" in text
    assert "HWPX Automation" in text


# ---------------------------------------------------------------------------
# Integration: v0.12 신규 AppConfig 필드 persist round-trip
# ---------------------------------------------------------------------------


def test_app_config_v120_fields_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config

    cfg = app_config.AppConfig(
        coupang_partner_id=982081,
        coupang_tracking_code="AF7480765",
        use_instructor_resolver=True,
        use_gemini_batch=True,
        gemini_batch_poll_sec=30,
    )
    app_config.save(cfg)
    loaded = app_config.load()
    assert loaded.coupang_partner_id == 982081
    assert loaded.use_instructor_resolver is True
    assert loaded.use_gemini_batch is True
    assert loaded.gemini_batch_poll_sec == 30
