"""W4: GUI 탭/워커 스모크 테스트.

실제 API 호출이나 한/글 실행은 없다 — Qt 위젯이 뜨고, 시그널이 흐르고, 워커가
파이프라인을 끝까지 실행하는지 확인.

pytest-qt 의 ``qtbot`` fixture 를 사용한다.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QTabWidget

from src.gui.main_window import MainWindow, TAB_PREVIEW
from src.gui.tabs.convert_tab import ConvertTab
from src.gui.tabs.preview_tab import PreviewTab
from src.gui.tabs.settings_tab import SettingsTab
from src.gui.tabs.template_tab import TemplateTab
from src.gui.workers.conversion_worker import (
    ConversionRequest,
    ConversionResult,
    ConversionWorker,
)
from src.hwpx.visualize import render_hwpx_to_html
from src.settings import api_key_manager, app_config
from src.template.template_manager import TemplateManager


ROOT = Path(__file__).resolve().parents[1]
BUNDLED_TEMPLATE = ROOT / "templates" / "00_기본_10단계스타일.hwpx"
FIXTURE = ROOT / "tests" / "fixtures" / "2026_귀농귀촌아카데미_원고.txt"

REQUIRES_TEMPLATE = pytest.mark.skipif(
    not BUNDLED_TEMPLATE.exists(), reason="bundled template missing"
)
REQUIRES_FIXTURE = pytest.mark.skipif(
    not (BUNDLED_TEMPLATE.exists() and FIXTURE.exists()),
    reason="template or fixture missing",
)


# ---------------------------------------------------------------------------
# Isolation fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_appdata(monkeypatch, tmp_path: Path):
    """APPDATA/HOME 을 tmp 로 격리 → 사용자 실제 keyring/config 건드리지 않음."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv(api_key_manager.ENV_OVERRIDE, raising=False)
    # 싱글턴 리셋
    api_key_manager.reset_singleton(None)
    yield tmp_path
    api_key_manager.reset_singleton(None)


@pytest.fixture
def template_manager(isolated_appdata: Path) -> TemplateManager:
    return TemplateManager(isolated_appdata / "templates")


@pytest.fixture
def default_config(isolated_appdata: Path) -> app_config.AppConfig:
    cfg = app_config.AppConfig(
        default_output_dir=str(isolated_appdata / "outputs"),
        first_run_completed=True,  # first-run 다이얼로그 자동 팝업 방지
    )
    app_config.save(cfg)
    return cfg


# ---------------------------------------------------------------------------
# Visualize (HWPX → HTML) — 로직 레이어 (GUI 없이도 테스트)
# ---------------------------------------------------------------------------

@REQUIRES_TEMPLATE
def test_visualize_renders_html():
    html = render_hwpx_to_html(BUNDLED_TEMPLATE)
    assert "<html" in html
    assert "<body" in html
    assert BUNDLED_TEMPLATE.name in html


# ---------------------------------------------------------------------------
# ConversionWorker — 실제 파이프라인 (Gemini 없이)
# ---------------------------------------------------------------------------

@REQUIRES_FIXTURE
def test_conversion_worker_end_to_end(qtbot, tmp_path: Path):
    out = tmp_path / "worker_out.hwpx"
    request = ConversionRequest(
        template_path=BUNDLED_TEMPLATE,
        txt_path=FIXTURE,
        output_path=out,
        use_gemini=False,
        verify_after=True,
    )
    worker = ConversionWorker(request)

    results: list[ConversionResult] = []
    failures: list[str] = []
    worker.signals.finished.connect(results.append)
    worker.signals.failed.connect(failures.append)

    # 직접 호출 (QThread 없이도 동작 — run() 은 동기)
    with qtbot.waitSignal(worker.signals.finished, timeout=30_000):
        worker.run()

    assert not failures
    assert len(results) == 1
    result = results[0]
    assert result.output_path == out
    assert out.exists()
    assert out.stat().st_size > 10_000
    assert result.verify_report is not None
    # 구조 검증은 모두 통과해야 한다
    struct = [c for c in result.verify_report.checks if c.category in ("common", "advanced")]
    assert all(c.passed for c in struct)


# ---------------------------------------------------------------------------
# Tab widget smoke tests
# ---------------------------------------------------------------------------

@REQUIRES_TEMPLATE
def test_convert_tab_loads_templates(qtbot, template_manager, default_config):
    tab = ConvertTab(template_manager, default_config)
    qtbot.addWidget(tab)
    # 기본 템플릿 하나는 반드시 있다
    assert tab.template_combo.count() >= 1
    # 첫 항목이 기본(★) 이거나 단일 항목이어야
    assert tab.template_combo.currentData()


@REQUIRES_TEMPLATE
def test_template_tab_add_remove_cycle(qtbot, template_manager, tmp_path: Path, monkeypatch):
    tab = TemplateTab(template_manager)
    qtbot.addWidget(tab)

    initial_count = tab.list_widget.count()
    assert initial_count >= 1

    # 실제 파일 업로드 시뮬레이션 — 번들 템플릿을 복사해서 "사용자 업로드" 케이스로
    fake_upload = tmp_path / "uploaded.hwpx"
    shutil.copy2(BUNDLED_TEMPLATE, fake_upload)

    entry = template_manager.add(fake_upload, name="테스트 업로드")
    tab.refresh()
    assert tab.list_widget.count() == initial_count + 1

    # 삭제
    template_manager.remove(entry.id)
    tab.refresh()
    assert tab.list_widget.count() == initial_count


def test_preview_tab_placeholder(qtbot):
    tab = PreviewTab()
    qtbot.addWidget(tab)
    # 초기엔 버튼 비활성
    assert tab.refresh_btn.isEnabled() is False
    assert tab.open_ext_btn.isEnabled() is False


@REQUIRES_TEMPLATE
def test_preview_tab_loads_file(qtbot):
    tab = PreviewTab()
    qtbot.addWidget(tab)
    tab.show_file(BUNDLED_TEMPLATE)
    assert tab.refresh_btn.isEnabled()
    assert tab.open_ext_btn.isEnabled()
    # QTextBrowser 에 내용이 들어갔다
    assert BUNDLED_TEMPLATE.name in tab.browser.toHtml()


def test_settings_tab_renders(qtbot, default_config):
    tab = SettingsTab(default_config)
    qtbot.addWidget(tab)
    # 저장 버튼은 초기 비활성
    assert tab.save_btn.isEnabled() is False
    # 값 하나 바꾸면 활성
    tab.use_gemini_check.setChecked(not default_config.use_gemini)
    assert tab.save_btn.isEnabled()


# ---------------------------------------------------------------------------
# MainWindow integration
# ---------------------------------------------------------------------------

@REQUIRES_TEMPLATE
def test_main_window_has_four_tabs(qtbot, isolated_appdata):
    """v0.5.0 에서 정량 탭 추가로 5개. 이름 테스트는 변화 허용."""
    win = MainWindow()
    qtbot.addWidget(win)
    assert win.tabs.count() == 6
    labels = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert labels[0].startswith("변환")
    assert labels[1].startswith("템플릿")
    assert labels[2].startswith("미리보기")
    assert "정량" in labels[3]
    assert "체크리스트" in labels[4]
    assert labels[5].startswith("설정")


@REQUIRES_TEMPLATE
def test_template_library_change_propagates_to_convert_tab(qtbot, isolated_appdata, tmp_path: Path):
    win = MainWindow()
    qtbot.addWidget(win)

    before = win.convert_tab.template_combo.count()

    # 템플릿 탭에서 직접 추가하는 로직 흐름 재현
    fake_upload = tmp_path / "propagate.hwpx"
    shutil.copy2(BUNDLED_TEMPLATE, fake_upload)
    win._template_manager.add(fake_upload, name="전파 테스트")

    # 템플릿 탭이 library_changed 를 emit 하면 ConvertTab.refresh_templates 가 돌아야 하는데,
    # 여기선 직접 refresh 호출 (매니저 경유 추가는 UI 이벤트 없이 된 상태이므로)
    win.template_tab.refresh()
    win.template_tab.library_changed.emit()

    after = win.convert_tab.template_combo.count()
    assert after == before + 1


@REQUIRES_FIXTURE
def test_convert_tab_preview_navigation(qtbot, isolated_appdata, tmp_path: Path):
    """변환 완료 → 미리보기 탭으로 자동 전환 시그널 체인."""
    win = MainWindow()
    qtbot.addWidget(win)

    dummy = tmp_path / "dummy.hwpx"
    shutil.copy2(BUNDLED_TEMPLATE, dummy)

    # convert_tab 의 preview_requested 직접 emit
    with qtbot.waitSignal(win.convert_tab.preview_requested, timeout=2000):
        win.convert_tab.preview_requested.emit(dummy)

    assert win.tabs.currentIndex() == TAB_PREVIEW
    assert dummy.name in win.preview_tab.browser.toHtml()
