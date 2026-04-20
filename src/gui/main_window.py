"""메인 윈도우 — W4 에서 탭 실구현 연결.

기획안 4.8 의 4 탭(변환 / 템플릿관리 / 미리보기 / 설정) + 광고 placeholder + 메뉴/상태바.
각 탭은 자체 QWidget 이고, 메인 윈도우는 라우터 역할만 한다.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QMainWindow,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..settings import api_key_manager, app_config
from ..template.template_manager import TemplateManager
from ..utils import telemetry
from ..utils.logger import get_logger
from .tabs.checklist_tab import ChecklistTab
from .tabs.convert_tab import ConvertTab
from .tabs.preview_tab import PreviewTab
from .tabs.quant_tab import QuantTab
from .tabs.settings_tab import SettingsTab
from .tabs.template_tab import TemplateTab
from .widgets.ad_placeholder import AdPlaceholder
from .widgets.api_key_dialog import ApiKeyDialog


_log = get_logger("gui.main")


TAB_CONVERT = 0
TAB_TEMPLATE = 1
TAB_PREVIEW = 2
TAB_QUANT = 3
TAB_CHECKLIST = 4
TAB_SETTINGS = 5


class MainWindow(QMainWindow):
    """HWPX Automation v2 최상위 윈도우."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"HWPX Automation v{__version__}")
        self.resize(1100, 760)

        self._config = app_config.load()
        self._template_manager = TemplateManager()
        self._current_user = None   # v0.7.0: 로그인한 사용자 (None = 비로그인)

        # v0.7.0: 텔레메트리 설정 반영
        telemetry.configure(self._config.telemetry_optin)
        telemetry.record("app_start", version=__version__)

        # v0.11.0: 원격 에러 리포팅 (opt-in, DSN 설정된 경우만)
        if self._config.error_reporting_optin and self._config.sentry_dsn:
            from ..utils import error_reporter
            error_reporter.init(
                dsn=self._config.sentry_dsn,
                release=__version__,
                environment="production",
            )

        self._build_menu()
        self._build_central()
        self._build_statusbar()

        self._wire_signals()

        # v0.7.0: 광고 슬롯 상태 반영
        self._apply_ad_state()

    # ---- lifecycle ----

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        from PySide6.QtCore import QTimer

        if not self._config.first_run_completed:
            QTimer.singleShot(0, self._maybe_show_first_run_dialog)
        # v0.7.0: 필요하면 로그인 다이얼로그
        if self._config.require_login and self._current_user is None:
            QTimer.singleShot(50, self._require_login)
        # v0.7.0: 업데이트 체크 (백그라운드, non-blocking)
        if self._config.auto_update_check:
            QTimer.singleShot(2000, self._check_for_update_silent)

    # ---- first-run onboarding ----

    def _maybe_show_first_run_dialog(self) -> None:
        if api_key_manager.has_key():
            self._config.first_run_completed = True
            app_config.save(self._config)
            return

        dlg = ApiKeyDialog(self, first_run=True)
        dlg.exec()
        if dlg.api_key():
            _log.info("첫 실행 API Key 등록 완료")
            self._config.use_gemini = True
        elif dlg.was_skipped():
            _log.info("사용자가 API Key 등록을 건너뜀 — Gemini 비활성 모드")
            self._config.use_gemini = False
        self._config.first_run_completed = True
        app_config.save(self._config)
        # 탭 상태 재동기화
        self.convert_tab.apply_config(self._config)
        self.settings_tab.reload_from_config()

    # ---- build ----

    def _build_central(self) -> None:
        container = QWidget(self)
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.tabs = QTabWidget()

        self.convert_tab = ConvertTab(self._template_manager, self._config)
        self.template_tab = TemplateTab(self._template_manager)
        self.preview_tab = PreviewTab()
        self.quant_tab = QuantTab(self._config)
        self.checklist_tab = ChecklistTab(self._config)
        self.settings_tab = SettingsTab(self._config)

        self.tabs.addTab(self.convert_tab, "변환 (정성)")
        self.tabs.addTab(self.template_tab, "템플릿 관리")
        self.tabs.addTab(self.preview_tab, "미리보기")
        self.tabs.addTab(self.quant_tab, "정량")
        self.tabs.addTab(self.checklist_tab, "체크리스트")
        self.tabs.addTab(self.settings_tab, "설정")

        outer.addWidget(self.tabs)

        self.ad_slot = AdPlaceholder(self)
        outer.addWidget(self.ad_slot)

        self.setCentralWidget(container)

    def _build_menu(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("파일(&F)")
        exit_action = QAction("종료(&X)", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        tool_menu = menubar.addMenu("도구(&T)")
        act_convert = QAction("변환 탭", self)
        act_convert.setShortcut("Ctrl+1")
        act_convert.triggered.connect(lambda: self.tabs.setCurrentIndex(TAB_CONVERT))
        tool_menu.addAction(act_convert)

        act_template = QAction("템플릿 관리", self)
        act_template.setShortcut("Ctrl+2")
        act_template.triggered.connect(lambda: self.tabs.setCurrentIndex(TAB_TEMPLATE))
        tool_menu.addAction(act_template)

        act_preview = QAction("미리보기", self)
        act_preview.setShortcut("Ctrl+3")
        act_preview.triggered.connect(lambda: self.tabs.setCurrentIndex(TAB_PREVIEW))
        tool_menu.addAction(act_preview)

        act_quant = QAction("정량", self)
        act_quant.setShortcut("Ctrl+4")
        act_quant.triggered.connect(lambda: self.tabs.setCurrentIndex(TAB_QUANT))
        tool_menu.addAction(act_quant)

        act_checklist = QAction("체크리스트", self)
        act_checklist.setShortcut("Ctrl+5")
        act_checklist.triggered.connect(lambda: self.tabs.setCurrentIndex(TAB_CHECKLIST))
        tool_menu.addAction(act_checklist)

        act_settings = QAction("설정", self)
        act_settings.setShortcut("Ctrl+6")
        act_settings.triggered.connect(lambda: self.tabs.setCurrentIndex(TAB_SETTINGS))
        tool_menu.addAction(act_settings)

        help_menu = menubar.addMenu("도움말(&H)")
        about_action = QAction("정보(&A)", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _build_statusbar(self) -> None:
        sb = QStatusBar(self)
        sb.showMessage("준비됨")
        self.setStatusBar(sb)

    # ---- wiring ----

    def _wire_signals(self) -> None:
        # 변환 → 미리보기 이동 + 상태
        self.convert_tab.preview_requested.connect(self._handle_preview_requested)
        self.convert_tab.status_message.connect(self._show_status)
        self.convert_tab.conversion_finished.connect(
            lambda _r: None  # 필요 시 텔레메트리 훅
        )

        # 템플릿 변경 → 변환 탭 드롭다운 갱신
        self.template_tab.library_changed.connect(self.convert_tab.refresh_templates)
        self.template_tab.status_message.connect(self._show_status)

        # 미리보기
        self.preview_tab.status_message.connect(self._show_status)

        # 정량 탭 → 미리보기 이동 + 상태
        self.quant_tab.preview_requested.connect(self._handle_preview_requested)
        self.quant_tab.status_message.connect(self._show_status)

        # 체크리스트 탭 → 상태
        self.checklist_tab.status_message.connect(self._show_status)

        # 설정 → 변환 탭에 새 config 전파 + API Key 상태 변경
        self.settings_tab.config_changed.connect(self._on_config_changed)
        self.settings_tab.api_key_changed.connect(self._on_api_key_changed)
        self.settings_tab.status_message.connect(self._show_status)

    # ---- slots ----

    def _handle_preview_requested(self, path: Path) -> None:
        self.preview_tab.show_file(path)
        self.tabs.setCurrentIndex(TAB_PREVIEW)

    def _show_status(self, text: str) -> None:
        self.statusBar().showMessage(text, 5000)

    def _on_config_changed(self, cfg: app_config.AppConfig) -> None:
        self._config = cfg
        self.convert_tab.apply_config(cfg)
        self.quant_tab.apply_config(cfg)
        self.checklist_tab.apply_config(cfg)
        # v0.7.0: 상업화 훅 재반영
        telemetry.configure(cfg.telemetry_optin)
        self._apply_ad_state()
        self._show_status("설정이 저장됐습니다")

    def _on_api_key_changed(self, _has_key: bool) -> None:
        self.convert_tab.apply_config(self._config)

    def _show_about(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from ..commerce.ai_disclosure import DISCLOSURE_UI_TOOLTIP

        user_info = ""
        if self._current_user is not None:
            user_info = f"\n로그인: {self._current_user.username} ({self._current_user.tier})"
        QMessageBox.information(
            self,
            "정보",
            f"HWPX Automation v{__version__}\n\n"
            "한글(HWPX) 문서 자동 작성 데스크톱 앱.\n"
            f"정성/정량 변환 + 제출서류 체크리스트 + Ollama 로컬 + Self-MoA.{user_info}\n\n"
            f"📌 AI 고지 (AI 기본법 2026-01-22)\n{DISCLOSURE_UI_TOOLTIP}",
        )

    # ---- v0.7.0: commerce hooks ----

    def _apply_ad_state(self) -> None:
        """AppConfig.ad_enabled 를 AdPlaceholder 에 반영.

        v0.9.0: ``ad_urls`` + ``ad_texts`` 가 여러 개면 순환, 하나면 정적.
        ``ad_url`` (단일) 은 back-compat 폴백.
        v0.10.0: pro 티어 이상 사용자는 광고 자동 숨김.
        v0.12.0: 쿠팡 Partners 우선 (`coupang_partner_id` + `coupang_tracking_code` 설정됐으면).
        """
        from ..commerce import tier_gate  # lazy

        if not self._config.ad_enabled:
            self.ad_slot.deactivate()
            return

        # v0.10.0: 로그인한 pro 사용자는 광고 제거
        if tier_gate.is_allowed("pro"):
            self.ad_slot.deactivate()
            return

        # v0.12~v0.13: 실 매출 채널 우선순위 결정
        # ad_channel_priority: coupang_first / adsense_first / coupang_only / adsense_only
        priority = getattr(self._config, "ad_channel_priority", "coupang_first")
        coupang_ready = bool(
            self._config.coupang_partner_id and self._config.coupang_tracking_code
        )
        adsense_ready = bool(
            self._config.adsense_publisher_id and self._config.adsense_ad_slot
            and self._config.adsense_publisher_id.startswith("ca-pub-")
        )

        if priority in ("coupang_first", "coupang_only") and coupang_ready:
            ok = self.ad_slot.activate_coupang(
                partner_id=self._config.coupang_partner_id,
                tracking_code=self._config.coupang_tracking_code,
                template=self._config.coupang_template,
                width=self._config.coupang_width,
                height=self._config.coupang_height,
            )
            telemetry.record("ad_coupang_activated", success=bool(ok))
            return

        if priority in ("adsense_first", "adsense_only", "coupang_first") and adsense_ready:
            ok = self.ad_slot.activate_adsense(
                publisher_id=self._config.adsense_publisher_id,
                ad_slot=self._config.adsense_ad_slot,
                ad_format=self._config.adsense_format,
                width=self._config.adsense_width,
                height=self._config.adsense_height,
            )
            telemetry.record("ad_adsense_activated", success=bool(ok))
            return

        # adsense_first 이지만 AdSense 없고 쿠팡 있으면 쿠팡 폴백
        if priority == "adsense_first" and coupang_ready:
            ok = self.ad_slot.activate_coupang(
                partner_id=self._config.coupang_partner_id,
                tracking_code=self._config.coupang_tracking_code,
                template=self._config.coupang_template,
                width=self._config.coupang_width,
                height=self._config.coupang_height,
            )
            telemetry.record("ad_coupang_activated", success=bool(ok), fallback=True)
            return

        urls = list(self._config.ad_urls or [])
        texts = list(self._config.ad_texts or [])
        if not urls and self._config.ad_url:
            urls = [self._config.ad_url]
        if not texts:
            # 텍스트 미지정 → 기본 placeholder 텍스트 반복
            texts = ["광고 영역 — 설정 탭에서 비활성화 가능"] * max(len(urls), 1)

        # 길이 맞추기 (텍스트 기준)
        while len(texts) < len(urls):
            texts.append("광고")
        items = list(zip(texts, urls + [""] * (len(texts) - len(urls))))

        if len(items) > 1:
            self.ad_slot.activate_rotating(
                items, interval_sec=self._config.ad_rotation_sec, height=80,
            )
        elif items:
            self.ad_slot.activate(text=items[0][0], click_url=items[0][1])
        else:
            # 활성은 됐지만 URL 없음 → 정적 라벨만
            self.ad_slot.activate(text="광고 영역 (URL 미설정)", click_url="")

    def _require_login(self) -> None:
        from ..commerce import tier_gate
        from ..commerce.auth_client import AuthSession
        from .widgets.login_dialog import LoginDialog

        dlg = LoginDialog(self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            _log.info("로그인 실패/취소 — 앱 종료")
            self.close()
            return
        user = dlg.user()
        self._current_user = user
        if user:
            _log.info("로그인: %s (tier=%s)", user.username, user.tier)
            self._show_status(f"{user.username} 님 환영합니다 ({user.tier})")
            telemetry.record("login", tier=user.tier)
            # v0.9.0: tier_gate 에 세션 등록
            tier_gate.set_current_session(AuthSession(user=user, tier=user.tier))

    def _check_for_update_silent(self) -> None:
        """백그라운드 업데이트 체크. 새 버전 있으면 상태바 알림."""
        from ..commerce.updater import check_for_update

        try:
            info = check_for_update(
                __version__, repo=self._config.update_repo
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("업데이트 체크 실패: %s", exc)
            return
        if info.available:
            self._show_status(
                f"🔔 새 버전 사용 가능: v{info.latest} (현재 v{info.current})"
            )
            telemetry.record("update_available", latest=info.latest)


__all__ = ["MainWindow"]
