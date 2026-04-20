"""설정 탭 — API Key / 저장 경로 / Gemini 옵션 / 로그 레벨.

기획안 4.8 의 설정 탭. AppConfig 를 한눈에 보고 편집할 수 있고, 저장 시 즉시 디스크에
반영된다. 변경이 생기면 :attr:`config_changed` 를 emit 해서 다른 탭이 반응할 수 있게 한다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...settings import api_key_manager, app_config
from ...template.template_manager import default_template_dir
from ...utils import logger as logger_util
from ...utils.logger import get_logger
from ..widgets.api_key_dialog import ApiKeyDialog


_log = get_logger("gui.settings_tab")


class SettingsTab(QWidget):
    """AppConfig + API Key 편집 UI."""

    config_changed = Signal(object)   # AppConfig
    api_key_changed = Signal(bool)    # 키가 존재하는지
    status_message = Signal(str)

    def __init__(
        self,
        config: app_config.AppConfig,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._build_ui()
        self.reload_from_config()

    # ---- UI ----

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ─ API Key ─
        key_group = QGroupBox("Gemini API")
        key_layout = QFormLayout()
        self.key_status_label = QLabel()
        self.key_status_label.setWordWrap(True)
        self.change_key_btn = QPushButton("API Key 변경/등록...")
        self.change_key_btn.clicked.connect(self._change_key)
        self.test_key_btn = QPushButton("연결 테스트")
        self.test_key_btn.setToolTip("저장된 키로 Gemini 서버에 ping")
        self.test_key_btn.clicked.connect(self._test_key)
        self.delete_key_btn = QPushButton("API Key 삭제")
        self.delete_key_btn.clicked.connect(self._delete_key)

        key_btn_row = QHBoxLayout()
        key_btn_row.addWidget(self.change_key_btn)
        key_btn_row.addWidget(self.test_key_btn)
        key_btn_row.addWidget(self.delete_key_btn)
        key_btn_row.addStretch(1)

        key_wrap = QVBoxLayout()
        key_wrap.addWidget(self.key_status_label)
        key_wrap.addLayout(key_btn_row)

        key_layout.addRow("상태:", key_wrap)
        key_group.setLayout(key_layout)
        layout.addWidget(key_group)

        # ─ AI 백엔드 선택 ─
        backend_group = QGroupBox("AI 백엔드")
        backend_layout = QFormLayout()

        self.backend_combo = QComboBox()
        self.backend_combo.addItem("Gemini (클라우드, 기본)", "gemini")
        self.backend_combo.addItem("Ollama (로컬, 완전 오프라인)", "ollama")
        self.backend_combo.addItem("OpenAI (GPT)", "openai")
        self.backend_combo.addItem("Anthropic (Claude)", "anthropic")
        self.backend_combo.addItem("사용 안 함 (결정론 파서만)", "none")
        self.backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        backend_layout.addRow("백엔드:", self.backend_combo)

        self.backend_hint_label = QLabel()
        self.backend_hint_label.setWordWrap(True)
        self.backend_hint_label.setStyleSheet("color: #555; font-size: 10pt;")
        backend_layout.addRow("", self.backend_hint_label)

        backend_group.setLayout(backend_layout)
        layout.addWidget(backend_group)

        # ─ Gemini 옵션 ─
        self.gem_group = QGroupBox("Gemini 옵션")
        gem_layout = QFormLayout()
        self.use_gemini_check = QCheckBox("변환 시 Gemini 로 애매 블록 해석")
        self.use_gemini_check.toggled.connect(self._on_dirty)
        gem_layout.addRow("", self.use_gemini_check)

        self.model_edit = QLineEdit()
        self.model_edit.textChanged.connect(self._on_dirty)
        gem_layout.addRow("모델:", self.model_edit)

        self.daily_cap_spin = QSpinBox()
        self.daily_cap_spin.setRange(1, 100_000)
        self.daily_cap_spin.setSingleStep(100)
        self.daily_cap_spin.valueChanged.connect(self._on_dirty)
        gem_layout.addRow("일일 호출 한도:", self.daily_cap_spin)

        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(10, 500)
        self.threshold_spin.setSingleStep(10)
        self.threshold_spin.setSuffix(" 자")
        self.threshold_spin.setToolTip(
            "기호가 붙은 줄의 본문이 이 길이 이상이면 애매로 마킹 → LLM 해석.\n"
            "낮출수록 정확도 ↑ 비용 ↑ · 높일수록 비용 ↓ (기본 50)"
        )
        self.threshold_spin.valueChanged.connect(self._on_dirty)
        gem_layout.addRow("애매 기준 길이:", self.threshold_spin)

        self.gem_group.setLayout(gem_layout)
        layout.addWidget(self.gem_group)

        # ─ Ollama 옵션 ─
        self.ollama_group = QGroupBox("Ollama 옵션 (로컬)")
        ollama_layout = QFormLayout()

        self.ollama_host_edit = QLineEdit()
        self.ollama_host_edit.setPlaceholderText("http://localhost:11434")
        self.ollama_host_edit.textChanged.connect(self._on_dirty)
        ollama_layout.addRow("서버 URL:", self.ollama_host_edit)

        self.ollama_model_edit = QLineEdit()
        self.ollama_model_edit.setPlaceholderText("qwen2.5:7b")
        self.ollama_model_edit.textChanged.connect(self._on_dirty)
        ollama_layout.addRow("모델:", self.ollama_model_edit)

        ollama_btn_row = QHBoxLayout()
        self.ollama_probe_btn = QPushButton("서버 확인")
        self.ollama_probe_btn.clicked.connect(self._probe_ollama)
        self.ollama_help_btn = QPushButton("Ollama 설치 안내...")
        self.ollama_help_btn.clicked.connect(self._show_ollama_help)
        ollama_btn_row.addWidget(self.ollama_probe_btn)
        ollama_btn_row.addWidget(self.ollama_help_btn)
        ollama_btn_row.addStretch(1)
        ollama_layout.addRow("", ollama_btn_row)

        self.ollama_status_label = QLabel("")
        self.ollama_status_label.setWordWrap(True)
        ollama_layout.addRow("상태:", self.ollama_status_label)

        self.ollama_group.setLayout(ollama_layout)
        layout.addWidget(self.ollama_group)

        # ─ OpenAI 옵션 ─
        self.openai_group = QGroupBox("OpenAI 옵션")
        openai_layout = QFormLayout()

        self.openai_key_status_label = QLabel()
        openai_layout.addRow("API Key:", self.openai_key_status_label)

        openai_key_row = QHBoxLayout()
        self.openai_key_btn = QPushButton("OpenAI API Key 등록/변경...")
        self.openai_key_btn.clicked.connect(lambda: self._change_service_key("openai"))
        self.openai_key_del_btn = QPushButton("삭제")
        self.openai_key_del_btn.clicked.connect(lambda: self._delete_service_key("openai"))
        openai_key_row.addWidget(self.openai_key_btn)
        openai_key_row.addWidget(self.openai_key_del_btn)
        openai_key_row.addStretch(1)
        openai_layout.addRow("", openai_key_row)

        self.openai_model_edit = QLineEdit()
        self.openai_model_edit.setPlaceholderText("gpt-4o-mini")
        self.openai_model_edit.textChanged.connect(self._on_dirty)
        openai_layout.addRow("모델:", self.openai_model_edit)

        self.openai_group.setLayout(openai_layout)
        layout.addWidget(self.openai_group)

        # ─ Anthropic 옵션 ─
        self.anthropic_group = QGroupBox("Anthropic 옵션")
        anthropic_layout = QFormLayout()

        self.anthropic_key_status_label = QLabel()
        anthropic_layout.addRow("API Key:", self.anthropic_key_status_label)

        ant_key_row = QHBoxLayout()
        self.anthropic_key_btn = QPushButton("Anthropic API Key 등록/변경...")
        self.anthropic_key_btn.clicked.connect(lambda: self._change_service_key("anthropic"))
        self.anthropic_key_del_btn = QPushButton("삭제")
        self.anthropic_key_del_btn.clicked.connect(lambda: self._delete_service_key("anthropic"))
        ant_key_row.addWidget(self.anthropic_key_btn)
        ant_key_row.addWidget(self.anthropic_key_del_btn)
        ant_key_row.addStretch(1)
        anthropic_layout.addRow("", ant_key_row)

        self.anthropic_model_edit = QLineEdit()
        self.anthropic_model_edit.setPlaceholderText("claude-haiku-4-5-20251001")
        self.anthropic_model_edit.textChanged.connect(self._on_dirty)
        anthropic_layout.addRow("모델:", self.anthropic_model_edit)

        self.anthropic_group.setLayout(anthropic_layout)
        layout.addWidget(self.anthropic_group)

        # ─ Self-MoA (v0.4.0) ─
        moa_group = QGroupBox("Self-MoA (정확도 개선 옵션)")
        moa_group.setToolTip(
            "같은 모델을 N회 호출한 뒤 aggregator 로 합성. "
            "정확도 3~7% ↑, 비용 (N+1) 배"
        )
        moa_layout = QFormLayout()
        self.self_moa_check = QCheckBox("Self-MoA 사용 (비용 증가 주의)")
        self.self_moa_check.toggled.connect(self._on_dirty)
        moa_layout.addRow("", self.self_moa_check)

        self.self_moa_draws_spin = QSpinBox()
        self.self_moa_draws_spin.setRange(2, 10)
        self.self_moa_draws_spin.setSuffix(" 회")
        self.self_moa_draws_spin.setToolTip("권장 3회. 늘릴수록 정확도 ↑ 비용 ↑")
        self.self_moa_draws_spin.valueChanged.connect(self._on_dirty)
        moa_layout.addRow("독립 호출 수:", self.self_moa_draws_spin)

        moa_group.setLayout(moa_layout)
        layout.addWidget(moa_group)

        # ─ 저장 경로 ─
        path_group = QGroupBox("저장 경로")
        path_layout = QHBoxLayout()
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.textChanged.connect(self._on_dirty)
        self.browse_btn = QPushButton("찾아보기...")
        self.browse_btn.clicked.connect(self._pick_output_dir)
        path_layout.addWidget(self.output_dir_edit, stretch=1)
        path_layout.addWidget(self.browse_btn)
        path_group.setLayout(path_layout)
        layout.addWidget(path_group)

        # ─ 로그/기타 ─
        log_group = QGroupBox("로그 / 유틸")
        log_layout = QVBoxLayout()
        level_row = QHBoxLayout()
        level_row.addWidget(QLabel("로그 레벨:"))
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.log_level_combo.currentTextChanged.connect(self._on_dirty)
        level_row.addWidget(self.log_level_combo)
        level_row.addStretch(1)
        log_layout.addLayout(level_row)

        util_row = QHBoxLayout()
        self.open_log_btn = QPushButton("로그 폴더 열기")
        self.open_log_btn.clicked.connect(self._open_log_folder)
        self.open_templates_btn = QPushButton("템플릿 폴더 열기")
        self.open_templates_btn.clicked.connect(self._open_templates_folder)
        self.open_appdata_btn = QPushButton("앱 데이터 폴더 열기")
        self.open_appdata_btn.clicked.connect(self._open_appdata_folder)
        util_row.addWidget(self.open_log_btn)
        util_row.addWidget(self.open_templates_btn)
        util_row.addWidget(self.open_appdata_btn)
        util_row.addStretch(1)
        log_layout.addLayout(util_row)

        log_group.setLayout(log_layout)
        layout.addWidget(log_group)

        # ─ v0.7.0: 상업화 훅 ─
        commerce_group = QGroupBox("상업화 옵션 (v0.7.0, 기본 OFF)")
        commerce_layout = QFormLayout()
        self.require_login_check = QCheckBox("앱 시작 시 로그인 요구 (로컬 계정)")
        self.require_login_check.toggled.connect(self._on_dirty)
        commerce_layout.addRow("", self.require_login_check)

        self.ad_enabled_check = QCheckBox("광고 슬롯 표시")
        self.ad_enabled_check.toggled.connect(self._on_dirty)
        commerce_layout.addRow("", self.ad_enabled_check)

        self.telemetry_check = QCheckBox("사용량 텔레메트리 기록 (로컬 파일, 외부 전송 없음)")
        self.telemetry_check.toggled.connect(self._on_dirty)
        commerce_layout.addRow("", self.telemetry_check)

        self.auto_update_check_box = QCheckBox("앱 시작 시 새 버전 체크")
        self.auto_update_check_box.toggled.connect(self._on_dirty)
        commerce_layout.addRow("", self.auto_update_check_box)

        update_row = QHBoxLayout()
        self.check_update_btn = QPushButton("지금 업데이트 확인")
        self.check_update_btn.clicked.connect(self._check_update_now)
        update_row.addWidget(self.check_update_btn)
        update_row.addStretch(1)
        commerce_layout.addRow("", update_row)

        commerce_group.setLayout(commerce_layout)
        layout.addWidget(commerce_group)

        # ─ v0.12.0 쿠팡 파트너스 + v0.13.0 AdSense (매출 채널) ─
        rev_group = QGroupBox("💰 매출 채널 (광고 — 공란이면 비활성)")
        rev_layout = QFormLayout()

        # (QLineEdit/QSpinBox/QComboBox 는 모듈 상단에서 이미 import 됨)
        # Coupang
        self.coupang_id_spin = QSpinBox()
        self.coupang_id_spin.setRange(0, 99_999_999)
        self.coupang_id_spin.setToolTip("쿠팡 파트너스 계정 ID (숫자). 0 이면 비활성.")
        self.coupang_id_spin.valueChanged.connect(self._on_dirty)
        rev_layout.addRow("쿠팡 Partner ID", self.coupang_id_spin)

        self.coupang_track_edit = QLineEdit()
        self.coupang_track_edit.setPlaceholderText("AF...")
        self.coupang_track_edit.textChanged.connect(self._on_dirty)
        rev_layout.addRow("쿠팡 tracking code", self.coupang_track_edit)

        # AdSense
        self.adsense_pub_edit = QLineEdit()
        self.adsense_pub_edit.setPlaceholderText("ca-pub-XXXXXXXXXXXXXXXX")
        self.adsense_pub_edit.textChanged.connect(self._on_dirty)
        rev_layout.addRow("AdSense publisher_id", self.adsense_pub_edit)

        self.adsense_slot_edit = QLineEdit()
        self.adsense_slot_edit.setPlaceholderText("광고 단위 ID (숫자)")
        self.adsense_slot_edit.textChanged.connect(self._on_dirty)
        rev_layout.addRow("AdSense ad_slot", self.adsense_slot_edit)

        self.ad_priority_combo = QComboBox()
        self.ad_priority_combo.addItem("쿠팡 우선 → AdSense 폴백", "coupang_first")
        self.ad_priority_combo.addItem("AdSense 우선 → 쿠팡 폴백", "adsense_first")
        self.ad_priority_combo.addItem("쿠팡만", "coupang_only")
        self.ad_priority_combo.addItem("AdSense만", "adsense_only")
        self.ad_priority_combo.currentIndexChanged.connect(self._on_dirty)
        rev_layout.addRow("채널 우선순위", self.ad_priority_combo)

        # v0.15.0: 수익 대시보드 버튼
        dash_row = QHBoxLayout()
        self.dashboard_btn = QPushButton("📈 광고 수익 대시보드")
        self.dashboard_btn.setToolTip(
            "로컬 텔레메트리 기반 채널별 노출/클릭/추정수익 요약. "
            "텔레메트리 opt-in 필요."
        )
        self.dashboard_btn.clicked.connect(self._show_revenue_dashboard)
        dash_row.addWidget(self.dashboard_btn)
        dash_row.addStretch(1)
        rev_layout.addRow("", dash_row)

        rev_group.setLayout(rev_layout)
        layout.addWidget(rev_group)

        # ─ v0.11.0 원격 에러 트래킹 (Sentry opt-in) ─
        sentry_group = QGroupBox("🛰️ 원격 에러 리포팅 (Sentry opt-in)")
        sentry_layout = QFormLayout()

        self.err_reporting_check = QCheckBox("원격 에러 리포팅 활성")
        self.err_reporting_check.setToolTip(
            "개발자가 크래시 스택트레이스를 원격으로 받음. DSN 설정 필요. "
            "PIPA 준수 위해 PII 는 자동 스크러빙됨.",
        )
        self.err_reporting_check.toggled.connect(self._on_dirty)
        sentry_layout.addRow("", self.err_reporting_check)

        self.sentry_dsn_edit = QLineEdit()
        self.sentry_dsn_edit.setPlaceholderText("https://...@sentry.io/... (개발자용)")
        self.sentry_dsn_edit.textChanged.connect(self._on_dirty)
        sentry_layout.addRow("DSN", self.sentry_dsn_edit)

        sentry_group.setLayout(sentry_layout)
        layout.addWidget(sentry_group)

        # ─ v0.12.0 고급 AI 옵션 (instructor / batch) ─
        adv_group = QGroupBox("⚙️ 고급 AI 옵션 (실험적)")
        adv_layout = QFormLayout()

        self.use_instructor_check = QCheckBox("Instructor 통일 resolver (Pydantic 기반)")
        self.use_instructor_check.setToolTip(
            "4 백엔드 (Gemini/Ollama/OpenAI/Anthropic) 를 단일 API 로. "
            "자동 retry + validation feedback. 기본 OFF (기존 경로 유지).",
        )
        self.use_instructor_check.toggled.connect(self._on_dirty)
        adv_layout.addRow("", self.use_instructor_check)

        self.use_batch_check = QCheckBox("Gemini Batch API (50% 할인)")
        self.use_batch_check.setToolTip(
            "Self-MoA 같은 N회 호출을 배치로 묶어 50% 절감. "
            "대기 시간 길어짐 (수 분~수 시간).",
        )
        self.use_batch_check.toggled.connect(self._on_dirty)
        adv_layout.addRow("", self.use_batch_check)

        adv_group.setLayout(adv_layout)
        layout.addWidget(adv_group)

        # ─ 저장 버튼 ─
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_btn = QPushButton("설정 저장")
        self.save_btn.setMinimumWidth(120)
        self.save_btn.clicked.connect(self._save_config)
        self.save_btn.setEnabled(False)
        save_row.addWidget(self.save_btn)
        layout.addLayout(save_row)

        layout.addStretch(1)

    # ---- public ----

    def reload_from_config(self) -> None:
        """외부에서 config 가 바뀌었을 때 UI 갱신."""
        cfg = self._config
        # 백엔드 선택
        idx = self.backend_combo.findData(cfg.resolver_backend or "gemini")
        if idx >= 0:
            self.backend_combo.setCurrentIndex(idx)
        self._apply_backend_visibility(cfg.resolver_backend or "gemini")

        self.use_gemini_check.setChecked(cfg.use_gemini)
        self.model_edit.setText(cfg.gemini_model)
        self.daily_cap_spin.setValue(cfg.gemini_daily_cap)
        self.threshold_spin.setValue(cfg.ambiguous_long_threshold)

        self.ollama_host_edit.setText(cfg.ollama_host)
        self.ollama_model_edit.setText(cfg.ollama_model)
        self.ollama_status_label.setText("(아직 확인 전)")

        self.openai_model_edit.setText(cfg.openai_model)
        self.anthropic_model_edit.setText(cfg.anthropic_model)
        self._refresh_service_key_status("openai")
        self._refresh_service_key_status("anthropic")

        self.self_moa_check.setChecked(cfg.use_self_moa)
        self.self_moa_draws_spin.setValue(cfg.self_moa_draws)

        # v0.7.0 상업화 훅
        self.require_login_check.setChecked(cfg.require_login)
        self.ad_enabled_check.setChecked(cfg.ad_enabled)
        self.telemetry_check.setChecked(cfg.telemetry_optin)
        self.auto_update_check_box.setChecked(cfg.auto_update_check)

        # v0.12 / v0.13 광고 매출 채널
        self.coupang_id_spin.setValue(int(getattr(cfg, "coupang_partner_id", 0) or 0))
        self.coupang_track_edit.setText(getattr(cfg, "coupang_tracking_code", "") or "")
        self.adsense_pub_edit.setText(getattr(cfg, "adsense_publisher_id", "") or "")
        self.adsense_slot_edit.setText(getattr(cfg, "adsense_ad_slot", "") or "")
        priority_idx = self.ad_priority_combo.findData(
            getattr(cfg, "ad_channel_priority", "coupang_first"),
        )
        if priority_idx >= 0:
            self.ad_priority_combo.setCurrentIndex(priority_idx)

        # v0.11 Sentry
        self.err_reporting_check.setChecked(getattr(cfg, "error_reporting_optin", False))
        self.sentry_dsn_edit.setText(getattr(cfg, "sentry_dsn", "") or "")

        # v0.12 고급 옵션
        self.use_instructor_check.setChecked(getattr(cfg, "use_instructor_resolver", False))
        self.use_batch_check.setChecked(getattr(cfg, "use_gemini_batch", False))

        self.output_dir_edit.setText(cfg.default_output_dir)
        idx = self.log_level_combo.findText(cfg.log_level)
        if idx >= 0:
            self.log_level_combo.setCurrentIndex(idx)
        self._refresh_key_status()
        self.save_btn.setEnabled(False)

    # ---- slots ----

    def _refresh_key_status(self) -> None:
        has = api_key_manager.has_key()
        if has:
            self.key_status_label.setText("✅ API Key 등록됨 (keyring 에 안전하게 저장)")
            self.key_status_label.setStyleSheet("color: #2e7d32;")
            self.change_key_btn.setText("API Key 변경...")
        else:
            self.key_status_label.setText("⚠️ API Key 가 등록되지 않았습니다. Gemini 해석이 불가능합니다.")
            self.key_status_label.setStyleSheet("color: #c62828;")
            self.change_key_btn.setText("API Key 등록...")
        self.delete_key_btn.setEnabled(has)
        self.test_key_btn.setEnabled(has)

    def _change_key(self) -> None:
        dlg = ApiKeyDialog(self, first_run=False)
        dlg.exec()
        if dlg.api_key():
            self.status_message.emit("API Key 저장 완료")
        self._refresh_key_status()
        self.api_key_changed.emit(api_key_manager.has_key())

    def _delete_key(self) -> None:
        btn = QMessageBox.question(
            self,
            "API Key 삭제",
            "등록된 API Key 를 삭제하시겠습니까?\n삭제 후 Gemini 기능은 비활성화됩니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if btn != QMessageBox.StandardButton.Yes:
            return
        api_key_manager.delete_key()
        self.status_message.emit("API Key 삭제됨")
        self._refresh_key_status()
        self.api_key_changed.emit(False)

    def _pick_output_dir(self) -> None:
        start = self.output_dir_edit.text() or str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "기본 저장 경로 선택", start)
        if d:
            self.output_dir_edit.setText(d)

    def _on_backend_changed(self, _index: int) -> None:
        backend = self.backend_combo.currentData()
        self._apply_backend_visibility(backend)
        self._on_dirty()

    def _apply_backend_visibility(self, backend: str) -> None:
        hint = {
            "gemini": "Google Gemini 2.5 Flash — 클라우드 API. 문서당 ~₩10, 정확도 높음. 원고가 Google 서버로 전송됨.",
            "ollama": "Ollama — 로컬 LLM. 비용 0, 완전 오프라인. 원고가 외부로 전송되지 않음. 사전에 Ollama 설치 + `ollama pull qwen2.5:7b` 필요.",
            "openai": "OpenAI GPT — 클라우드 API. gpt-4o-mini 기준 문서당 ~₩15, 정확도 매우 높음. 원고가 OpenAI 서버로 전송됨.",
            "anthropic": "Anthropic Claude — 클라우드 API. Haiku 기준 문서당 ~₩30, 한국어 품질 좋음. 원고가 Anthropic 서버로 전송됨.",
            "none": "결정론 파서만 사용. 네트워크 전송 없음. 애매 계층은 원고에서 미리 기호를 명확히 찍어야 함.",
        }.get(backend, "")
        self.backend_hint_label.setText(hint)
        is_active = backend != "none"
        self.gem_group.setEnabled(is_active)
        self.ollama_group.setEnabled(is_active)
        self.openai_group.setEnabled(is_active)
        self.anthropic_group.setEnabled(is_active)
        # 선택된 백엔드만 bold
        selected_style = "QGroupBox { font-weight: bold; }"
        self.gem_group.setStyleSheet(selected_style if backend == "gemini" else "")
        self.ollama_group.setStyleSheet(selected_style if backend == "ollama" else "")
        self.openai_group.setStyleSheet(selected_style if backend == "openai" else "")
        self.anthropic_group.setStyleSheet(selected_style if backend == "anthropic" else "")

    def _probe_ollama(self) -> None:
        from ...parser.ollama_backend import probe_server

        host = self.ollama_host_edit.text().strip() or "http://localhost:11434"
        self.ollama_status_label.setText("확인 중…")
        QApplication.processEvents()   # "확인 중" 텍스트 즉시 반영
        result = probe_server(host, timeout=3.0)
        self.ollama_status_label.setText(result.summary())
        if result.ok and result.models:
            self.status_message.emit(f"Ollama 정상 ({len(result.models)} 모델)")
        elif not result.ok:
            self.status_message.emit("Ollama 연결 실패")

    def _refresh_service_key_status(self, service: str) -> None:
        label_map = {
            "openai": self.openai_key_status_label,
            "anthropic": self.anthropic_key_status_label,
        }
        del_btn_map = {
            "openai": self.openai_key_del_btn,
            "anthropic": self.anthropic_key_del_btn,
        }
        label = label_map.get(service)
        del_btn = del_btn_map.get(service)
        if label is None or del_btn is None:
            return
        has = api_key_manager.has_key(service=service)
        if has:
            label.setText("✅ 등록됨 (keyring)")
            label.setStyleSheet("color: #2e7d32;")
        else:
            label.setText("⚠️ 등록되지 않음")
            label.setStyleSheet("color: #c62828;")
        del_btn.setEnabled(has)

    def _change_service_key(self, service: str) -> None:
        from PySide6.QtWidgets import QInputDialog, QLineEdit

        display = {"openai": "OpenAI", "anthropic": "Anthropic"}.get(service, service)
        text, ok = QInputDialog.getText(
            self,
            f"{display} API Key",
            f"{display} API Key 를 입력하세요:\n(비밀번호 필드로 표시됩니다)",
            QLineEdit.EchoMode.Password,
        )
        if not ok or not text.strip():
            return
        try:
            storage = api_key_manager.set_key(text.strip(), service=service)
        except Exception as exc:  # noqa: BLE001
            _log.error("%s API Key 저장 실패: %s", service, type(exc).__name__)
            QMessageBox.critical(
                self, "저장 실패", f"{type(exc).__name__}: 자세한 내용은 로그 참고"
            )
            return
        self.status_message.emit(f"{display} API Key 저장됨 ({storage})")
        self._refresh_service_key_status(service)

    def _delete_service_key(self, service: str) -> None:
        display = {"openai": "OpenAI", "anthropic": "Anthropic"}.get(service, service)
        btn = QMessageBox.question(
            self,
            f"{display} API Key 삭제",
            f"{display} API Key 를 삭제하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if btn != QMessageBox.StandardButton.Yes:
            return
        api_key_manager.delete_key(service=service)
        self.status_message.emit(f"{display} API Key 삭제됨")
        self._refresh_service_key_status(service)

    def _show_ollama_help(self) -> None:
        QMessageBox.information(
            self,
            "Ollama 설치 안내",
            "Ollama 는 PC 에서 로컬 LLM 을 구동하는 무료 오픈소스 툴입니다.\n\n"
            "1. https://ollama.com/download 에서 Windows 설치 프로그램 다운로드\n"
            "2. 설치 완료 후 cmd/PowerShell 에서:\n"
            "     ollama pull qwen2.5:7b\n"
            "   (한국어 품질 좋고 8GB VRAM 이면 잘 돌아감)\n"
            "3. 이 설정 탭으로 돌아와 '서버 확인' 클릭\n\n"
            "대안 모델:\n"
            "  • llama3.1:8b  — 좀 더 가벼움 (4~8GB)\n"
            "  • qwen2.5:14b  — 정확도 더 높음 (16GB)\n"
            "  • qwen2.5:3b   — CPU 만 있어도 동작 (느림)",
        )

    def _test_key(self) -> None:
        """저장된 키로 Gemini ping. 결과를 메시지박스로 표시."""
        if not api_key_manager.has_key():
            QMessageBox.warning(self, "연결 테스트", "등록된 API Key 가 없습니다.")
            return
        try:
            from google import genai  # type: ignore

            client = genai.Client(api_key=api_key_manager.get_key())
            models = list(client.models.list())
        except ImportError:
            QMessageBox.critical(self, "연결 테스트", "google-genai 가 설치되어 있지 않습니다.")
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(
                self,
                "연결 테스트 실패",
                f"{type(exc).__name__}: {str(exc)[:200]}",
            )
            return
        QMessageBox.information(
            self,
            "연결 테스트 성공",
            f"API 연결 정상. 사용 가능 모델 {len(models)} 개.",
        )
        self.status_message.emit(f"연결 테스트: {len(models)} 모델 사용 가능")

    def _show_revenue_dashboard(self) -> None:
        """v0.15.0: 광고 수익 대시보드 모달."""
        from ...commerce.revenue_telemetry import compute_dashboard, format_dashboard
        from PySide6.QtWidgets import QDialog, QPlainTextEdit, QDialogButtonBox

        db = compute_dashboard(days=30)
        text = format_dashboard(db)

        dlg = QDialog(self)
        dlg.setWindowTitle("광고 수익 대시보드 (최근 30 일)")
        dlg.resize(620, 420)
        layout = QVBoxLayout(dlg)

        view = QPlainTextEdit()
        view.setPlainText(text)
        view.setReadOnly(True)
        view.setStyleSheet("QPlainTextEdit { font-family: 'Consolas', monospace; }")
        layout.addWidget(view)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(dlg.reject)
        box.accepted.connect(dlg.accept)
        layout.addWidget(box)

        dlg.exec()

    def _open_log_folder(self) -> None:
        log_dir = logger_util._default_log_dir()
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._open_folder(log_dir)

    def _open_templates_folder(self) -> None:
        self._open_folder(default_template_dir())

    def _open_appdata_folder(self) -> None:
        self._open_folder(logger_util._default_log_dir().parent)

    def _check_update_now(self) -> None:
        from .. import __version__ as _  # noqa: F401 - placeholder
        from ... import __version__ as current_version
        from ...commerce.updater import check_for_update

        self.status_message.emit("업데이트 확인 중…")
        QApplication.processEvents()
        info = check_for_update(current_version, repo=self._config.update_repo)
        if info.error:
            QMessageBox.warning(
                self, "업데이트 확인 실패", info.error,
            )
            return
        if info.available:
            btn = QMessageBox.question(
                self, "새 버전 발견",
                f"현재 v{info.current} → 최신 v{info.latest}\n\n"
                "릴리즈 페이지를 열까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if btn == QMessageBox.StandardButton.Yes and info.release_url:
                QDesktopServices.openUrl(QUrl(info.release_url))
        else:
            QMessageBox.information(
                self, "최신 버전",
                f"이미 최신 버전입니다 (v{info.current}).",
            )

    def _open_folder(self, path: Path) -> None:
        if not path.exists():
            QMessageBox.information(
                self, "폴더 열기", f"폴더가 아직 없습니다:\n{path}"
            )
            return
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        if not ok:
            QMessageBox.warning(
                self, "폴더 열기 실패", f"탐색기를 열 수 없습니다:\n{path}"
            )

    def _on_dirty(self, *_args) -> None:
        self.save_btn.setEnabled(True)

    def _save_config(self) -> None:
        """v0.13.0: dataclasses.replace 로 v0.8+ 필드 보존.

        기존 구현은 `AppConfig(...)` 를 처음부터 만들어서 `ad_urls`, `sentry_dsn`,
        `firebase_api_key`, `coupang_*`, `adsense_*`, `use_instructor_resolver` 등
        UI 에 노출 안 된 필드를 **전부 0/False/빈 값으로 덮어써서 날려먹던 버그**.
        """
        import dataclasses as _dc

        backend = self.backend_combo.currentData() or "gemini"
        new_cfg = _dc.replace(
            self._config,
            # 기본 탭
            use_gemini=self.use_gemini_check.isChecked(),
            gemini_daily_cap=self.daily_cap_spin.value(),
            gemini_model=self.model_edit.text().strip() or self._config.gemini_model,
            ambiguous_long_threshold=self.threshold_spin.value(),
            resolver_backend=backend,
            ollama_host=self.ollama_host_edit.text().strip() or self._config.ollama_host,
            ollama_model=self.ollama_model_edit.text().strip() or self._config.ollama_model,
            openai_model=self.openai_model_edit.text().strip() or self._config.openai_model,
            anthropic_model=self.anthropic_model_edit.text().strip() or self._config.anthropic_model,
            use_self_moa=self.self_moa_check.isChecked(),
            self_moa_draws=self.self_moa_draws_spin.value(),
            # v0.7.0
            require_login=self.require_login_check.isChecked(),
            ad_enabled=self.ad_enabled_check.isChecked(),
            telemetry_optin=self.telemetry_check.isChecked(),
            auto_update_check=self.auto_update_check_box.isChecked(),
            default_output_dir=self.output_dir_edit.text().strip() or self._config.default_output_dir,
            log_level=self.log_level_combo.currentText(),
            # v0.11.0 Sentry
            error_reporting_optin=self.err_reporting_check.isChecked(),
            sentry_dsn=self.sentry_dsn_edit.text().strip(),
            # v0.12.0 쿠팡 + AdSense + 우선순위
            coupang_partner_id=int(self.coupang_id_spin.value()),
            coupang_tracking_code=self.coupang_track_edit.text().strip(),
            adsense_publisher_id=self.adsense_pub_edit.text().strip(),
            adsense_ad_slot=self.adsense_slot_edit.text().strip(),
            ad_channel_priority=self.ad_priority_combo.currentData() or "coupang_first",
            # v0.12 고급 AI
            use_instructor_resolver=self.use_instructor_check.isChecked(),
            use_gemini_batch=self.use_batch_check.isChecked(),
        )
        try:
            app_config.save(new_cfg)
        except OSError as exc:
            QMessageBox.critical(self, "저장 실패", f"{exc}")
            return
        self._config = new_cfg
        self.save_btn.setEnabled(False)
        self.status_message.emit("설정 저장 완료")
        self.config_changed.emit(new_cfg)


__all__ = ["SettingsTab"]
