"""v0.13.0: AdSense + 설정 UI + Batch 프로그레스 + python-hwpx writer.

네트워크 호출 없음 (Qt 렌더링은 QWebEngineView 인스턴스화까지만).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUNDLED = ROOT / "templates" / "00_기본_10단계스타일.hwpx"
REQUIRES_BUNDLED = pytest.mark.skipif(not BUNDLED.exists(), reason="bundled template missing")


# ---------------------------------------------------------------------------
# Track 1: AdSense 위젯
# ---------------------------------------------------------------------------


def test_adsense_build_html_structure():
    from src.gui.widgets.adsense_ad import build_html

    html = build_html("ca-pub-1234567890123456", "1234567890")
    assert "ca-pub-1234567890123456" in html
    assert "1234567890" in html
    assert "pagead2.googlesyndication.com" in html
    assert "adsbygoogle" in html


def test_adsense_build_html_rejects_bad_publisher():
    from src.gui.widgets.adsense_ad import build_html

    # publisher_id 가 ca-pub- 로 시작 안 하면 placeholder HTML
    html = build_html("invalid-id", "1234")
    assert "광고 로드 불가" in html
    assert "adsbygoogle" not in html


def test_adsense_build_html_sanitizes_slot():
    """ad_slot 에 숫자 외 문자 들어가면 필터링."""
    from src.gui.widgets.adsense_ad import build_html

    html = build_html(
        "ca-pub-1234567890123456",
        "1234<script>bad()</script>",
    )
    assert "<script>bad" not in html
    assert "bad()" not in html
    # 숫자만 남아야 — 1234 는 유지
    assert '"1234"' in html or "1234" in html


def test_adsense_widget_construct(qtbot):
    from src.gui.widgets.adsense_ad import AdSenseWidget

    w = AdSenseWidget(
        publisher_id="ca-pub-1234567890123456",
        ad_slot="1234567890",
    )
    qtbot.addWidget(w)
    assert isinstance(w.is_rendered, bool)
    assert w.publisher_id == "ca-pub-1234567890123456"
    assert w.ad_slot == "1234567890"


def test_adsense_disclosure_label(qtbot):
    from src.gui.widgets.adsense_ad import AdSenseWidget, DISCLOSURE_TEXT

    w = AdSenseWidget(publisher_id="ca-pub-x", ad_slot="1")
    qtbot.addWidget(w)
    assert w._disc_label.text() == DISCLOSURE_TEXT
    assert "AdSense" in DISCLOSURE_TEXT


def test_ad_placeholder_activate_adsense(qtbot):
    from src.gui.widgets.ad_placeholder import AdPlaceholder

    ad = AdPlaceholder()
    qtbot.addWidget(ad)
    # 빈 인자 → 비활성
    assert ad.activate_adsense("", "") is False
    # 잘못된 형식 → 비활성
    assert ad.activate_adsense("not-ca-pub", "1234") is False
    # 정상 인자 → 활성
    ad.activate_adsense(
        publisher_id="ca-pub-1234567890123456",
        ad_slot="1234567890",
    )
    assert ad.is_active is True
    assert ad._adsense_widget is not None

    ad.deactivate()
    assert ad._adsense_widget is None


def test_app_config_adsense_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config

    cfg = app_config.AppConfig(
        adsense_publisher_id="ca-pub-1234567890123456",
        adsense_ad_slot="9876543210",
        adsense_format="rectangle",
        ad_channel_priority="adsense_first",
    )
    app_config.save(cfg)
    loaded = app_config.load()
    assert loaded.adsense_publisher_id == "ca-pub-1234567890123456"
    assert loaded.adsense_ad_slot == "9876543210"
    assert loaded.ad_channel_priority == "adsense_first"


# ---------------------------------------------------------------------------
# Track 2: 설정 탭 UI + save 버그 수정
# ---------------------------------------------------------------------------


def test_settings_save_preserves_v09_plus_fields(qtbot, tmp_path, monkeypatch):
    """v0.13.0 핵심 버그 수정: settings_tab 의 _save_config 가 v0.9+ 필드 날려먹던 문제."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config
    from src.gui.tabs.settings_tab import SettingsTab

    # v0.9~v0.12 모든 필드를 채운 config 저장
    cfg = app_config.AppConfig(
        firebase_api_key="FIREBASE-KEY",
        ad_urls=["https://a", "https://b"],
        ad_texts=["A", "B"],
        coupang_partner_id=982081,
        coupang_tracking_code="AF7480765",
        adsense_publisher_id="ca-pub-X",
        adsense_ad_slot="Y",
        sentry_dsn="https://abc@sentry.io/123",
        use_instructor_resolver=True,
        use_gemini_batch=True,
    )
    app_config.save(cfg)

    tab = SettingsTab(cfg)
    qtbot.addWidget(tab)

    # UI 변경 없이 저장 — dataclasses.replace 사용으로 필드 보존돼야
    tab._save_config()
    reloaded = app_config.load()

    assert reloaded.firebase_api_key == "FIREBASE-KEY"
    assert reloaded.ad_urls == ["https://a", "https://b"]
    assert reloaded.coupang_partner_id == 982081
    assert reloaded.adsense_publisher_id == "ca-pub-X"
    # Sentry/instructor/batch 는 UI 에 노출됐으니 그대로 유지
    assert reloaded.sentry_dsn == "https://abc@sentry.io/123"
    assert reloaded.use_instructor_resolver is True
    assert reloaded.use_gemini_batch is True


def test_settings_save_updates_coupang_from_ui(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config
    from src.gui.tabs.settings_tab import SettingsTab

    cfg = app_config.AppConfig()  # 모든 필드 기본값
    app_config.save(cfg)

    tab = SettingsTab(cfg)
    qtbot.addWidget(tab)

    # UI 에서 쿠팡 정보 입력
    tab.coupang_id_spin.setValue(123456)
    tab.coupang_track_edit.setText("AFTESTCODE")
    tab.adsense_pub_edit.setText("ca-pub-999")
    tab.adsense_slot_edit.setText("42")
    tab.use_instructor_check.setChecked(True)
    tab.use_batch_check.setChecked(True)
    tab.err_reporting_check.setChecked(True)
    tab.sentry_dsn_edit.setText("https://x@sentry.io/1")

    tab._save_config()
    reloaded = app_config.load()
    assert reloaded.coupang_partner_id == 123456
    assert reloaded.coupang_tracking_code == "AFTESTCODE"
    assert reloaded.adsense_publisher_id == "ca-pub-999"
    assert reloaded.adsense_ad_slot == "42"
    assert reloaded.use_instructor_resolver is True
    assert reloaded.use_gemini_batch is True
    assert reloaded.error_reporting_optin is True
    assert reloaded.sentry_dsn == "https://x@sentry.io/1"


# ---------------------------------------------------------------------------
# Track 3: Batch progress dialog + worker
# ---------------------------------------------------------------------------


def test_batch_worker_empty_requests_succeeds(qtbot):
    from src.gui.workers.batch_worker import GeminiBatchWorker

    worker = GeminiBatchWorker(
        requests=[],
        api_key="fake",
        model="gemini-2.5-flash",
    )

    finished_spy = []
    worker.finished_ok.connect(lambda r: finished_spy.append(r))

    worker.start()
    worker.wait(3000)  # 3s
    assert worker.result is not None
    assert worker.result.state == "SUCCEEDED"
    qtbot.waitUntil(lambda: len(finished_spy) == 1, timeout=2000)


def test_batch_dialog_humanize():
    from src.gui.widgets.batch_progress_dialog import _humanize

    assert "제출" in _humanize("BATCH_STATE_PENDING")
    assert "실행" in _humanize("BATCH_STATE_RUNNING")
    assert "완료" in _humanize("BATCH_STATE_SUCCEEDED")
    # 알 수 없는 state → 문자열 그대로
    assert _humanize("UNKNOWN_FOO") == "UNKNOWN_FOO"


def test_batch_dialog_handles_immediate_success(qtbot):
    """빈 요청 → worker 즉시 SUCCEEDED → dialog accept()."""
    from src.gui.workers.batch_worker import GeminiBatchWorker
    from src.gui.widgets.batch_progress_dialog import BatchProgressDialog

    worker = GeminiBatchWorker(
        requests=[], api_key="fake", model="gemini-2.5-flash",
    )
    dialog = BatchProgressDialog(worker)
    qtbot.addWidget(dialog)

    # worker.start() 는 dialog 생성자에서 호출됨
    # 결과 나올 때까지 대기
    worker.wait(3000)
    qtbot.wait(300)  # signal delivery
    assert worker.result is not None
    assert worker.result.state == "SUCCEEDED"


# ---------------------------------------------------------------------------
# Track 4: python-hwpx writer
# ---------------------------------------------------------------------------


@REQUIRES_BUNDLED
def test_write_paragraphs_basic(tmp_path):
    from src.hwpx.hwpx_writer import WriteBlock, write_paragraphs

    out = tmp_path / "out.hwpx"
    blocks = [
        WriteBlock(text="제목 A", level=1),
        WriteBlock(text="본문 1", level=0),
        WriteBlock(text="본문 2", level=0),
    ]
    report = write_paragraphs(BUNDLED, blocks, out)
    assert out.exists()
    assert report.paragraphs_added == 3
    assert report.skipped == 0
    assert not report.errors


@REQUIRES_BUNDLED
def test_write_paragraphs_verifies_by_extract(tmp_path):
    """저장한 HWPX 를 다시 열어 우리가 넣은 텍스트가 나오는지."""
    from src.hwpx.hwpx_writer import WriteBlock, write_paragraphs
    from src.checklist.rfp_extractor import extract_hwpx_text

    out = tmp_path / "roundtrip.hwpx"
    write_paragraphs(
        BUNDLED,
        [WriteBlock(text="WRITER-TEST-토큰", level=0)],
        out,
    )
    text = extract_hwpx_text(out)
    assert "WRITER-TEST-토큰" in text


@REQUIRES_BUNDLED
def test_write_paragraphs_refuses_overwrite(tmp_path):
    from src.hwpx.hwpx_writer import WriteBlock, write_paragraphs

    out = tmp_path / "existing.hwpx"
    out.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        write_paragraphs(BUNDLED, [WriteBlock(text="x")], out)


@REQUIRES_BUNDLED
def test_write_checklist_report(tmp_path):
    from src.hwpx.hwpx_writer import write_checklist_report

    out = tmp_path / "report.hwpx"
    report = write_checklist_report(
        template=BUNDLED,
        title="2026 귀농귀촌 체크",
        lines=["사업자등록증: OK", "법인 인감증명서: 누락"],
        output=out,
    )
    assert out.exists()
    assert report.paragraphs_added == 3  # title + 2 lines


def test_write_paragraphs_missing_template(tmp_path):
    from src.hwpx.hwpx_writer import WriteBlock, write_paragraphs

    with pytest.raises(FileNotFoundError):
        write_paragraphs(
            tmp_path / "nope.hwpx",
            [WriteBlock(text="x")],
            tmp_path / "out.hwpx",
        )


def test_resolve_para_pr_id_from_style_map():
    from src.hwpx.hwpx_writer import _resolve_para_pr_id

    style_map = {
        "level_1": {"paraPrIDRef": "3", "charPrIDRef": "2"},
        "level_0": {"paraPrIDRef": "0"},
    }
    assert _resolve_para_pr_id(1, style_map) == "3"
    assert _resolve_para_pr_id(0, style_map) == "0"
    # 누락된 레벨 → None (상속)
    assert _resolve_para_pr_id(5, style_map) is None
    # style_map None → None
    assert _resolve_para_pr_id(1, None) is None
