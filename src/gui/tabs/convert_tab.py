"""변환 탭 — 원고 → HWPX 변환의 핵심 UI.

기획안 4.8 의 변환 탭 레이아웃::

    ┌─ 변환 ───────────────────────────────────┐
    │ 템플릿:  [기본 10단계 스타일       ▾]     │
    │ 원고:    [파일 선택]  원고.txt            │
    │ [ ] Gemini 해석 사용                      │
    │                                          │
    │ [ 변환 실행 ]                            │
    │                                          │
    │ ┌─ 진행 로그 ─────────────────────────┐ │
    │ │ ✓ 원고 분석 완료 (1,679줄)          │ │
    │ │ ✓ Gemini 호출 (비용 ₩9.3)           │ │
    │ │ ✓ HWPX 생성 완료                    │ │
    │ │ ✓ 검증 통과                         │ │
    │ └─────────────────────────────────────┘ │
    │                                          │
    │ [ 결과 저장... ]  [ 미리보기 탭으로 ]   │
    └──────────────────────────────────────────┘
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...settings import api_key_manager, app_config
from ...template.template_manager import TemplateManager
from ...utils.logger import get_logger
from ..workers.conversion_worker import (
    ConversionRequest,
    ConversionResult,
    ConversionWorker,
)


_log = get_logger("gui.convert_tab")


class ConvertTab(QWidget):
    """원고 .txt 를 HWPX 로 변환하는 탭."""

    # 외부(MainWindow) 에서 연결하는 시그널
    preview_requested = Signal(Path)          # 미리보기 탭으로 이동 요청
    status_message = Signal(str)              # 상태바 표시용
    conversion_finished = Signal(object)      # ConversionResult

    def __init__(
        self,
        template_manager: TemplateManager,
        config: app_config.AppConfig,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._template_manager = template_manager
        self._config = config
        self._txt_path: Optional[Path] = None
        self._last_output: Optional[Path] = None
        self._thread: Optional[QThread] = None
        self._worker: Optional[ConversionWorker] = None

        self._build_ui()
        self.refresh_templates()

    # ---- UI ----

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 템플릿 선택
        tpl_row = QHBoxLayout()
        tpl_row.addWidget(QLabel("템플릿:"))
        self.template_combo = QComboBox()
        self.template_combo.setMinimumWidth(260)
        tpl_row.addWidget(self.template_combo, stretch=1)
        layout.addLayout(tpl_row)

        # 원고 파일
        txt_row = QHBoxLayout()
        txt_row.addWidget(QLabel("원고:"))
        self.txt_btn = QPushButton("파일 선택...")
        self.txt_btn.clicked.connect(self._pick_txt)
        self.txt_label = QLabel("(선택 안 됨)")
        self.txt_label.setStyleSheet("color: #777;")
        txt_row.addWidget(self.txt_btn)
        txt_row.addWidget(self.txt_label, stretch=1)
        layout.addLayout(txt_row)

        # LLM 해석 옵션 (체크박스 라벨은 backend 에 따라 달라짐)
        self.gemini_check = QCheckBox()
        self.gemini_check.setChecked(self._config.use_gemini)
        layout.addWidget(self.gemini_check)
        self._refresh_gemini_checkbox()

        # 실행 버튼 + 진행바
        run_row = QHBoxLayout()
        self.run_btn = QPushButton("변환 실행")
        self.run_btn.setMinimumHeight(36)
        self.run_btn.setStyleSheet("QPushButton { font-weight: bold; }")
        self.run_btn.clicked.connect(self._start_conversion)
        run_row.addWidget(self.run_btn)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        run_row.addWidget(self.progress_bar, stretch=1)
        layout.addLayout(run_row)

        # 진행 로그
        layout.addWidget(QLabel("진행 로그:"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet(
            "QTextEdit { font-family: 'Consolas', 'D2Coding', monospace; "
            "background-color: #1e1e1e; color: #e0e0e0; }"
        )
        self.log.setMinimumHeight(220)
        layout.addWidget(self.log, stretch=1)

        # 저장/미리보기/로그 버튼
        btn_row = QHBoxLayout()
        self.save_as_btn = QPushButton("다른 이름으로 저장...")
        self.save_as_btn.setEnabled(False)
        self.save_as_btn.clicked.connect(self._save_as)
        self.preview_btn = QPushButton("미리보기 탭으로")
        self.preview_btn.setEnabled(False)
        self.preview_btn.clicked.connect(self._go_preview)
        self.copy_log_btn = QPushButton("로그 복사")
        self.copy_log_btn.setToolTip("진행 로그 전체를 클립보드에 복사")
        self.copy_log_btn.clicked.connect(self._copy_log_to_clipboard)
        self.save_log_btn = QPushButton("로그 저장...")
        self.save_log_btn.setToolTip("진행 로그를 .txt 파일로 저장")
        self.save_log_btn.clicked.connect(self._save_log_to_file)
        btn_row.addWidget(self.save_as_btn)
        btn_row.addWidget(self.preview_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.copy_log_btn)
        btn_row.addWidget(self.save_log_btn)
        layout.addLayout(btn_row)

    # ---- slots: input pickers ----

    def _pick_txt(self) -> None:
        start = str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "원고 파일 선택",
            start,
            "텍스트 파일 (*.txt);;모든 파일 (*.*)",
        )
        if not path:
            return
        self._txt_path = Path(path)
        self.txt_label.setText(self._txt_path.name)
        self.txt_label.setStyleSheet("color: #222;")
        self.txt_label.setToolTip(str(self._txt_path))

    # ---- slots: run ----

    def _start_conversion(self) -> None:
        # 중복 실행 차단 — 버튼 disable 과 이벤트 타이밍 사이 race 방지
        if self._thread is not None:
            return

        template_id = self.template_combo.currentData()
        if not template_id:
            self._warn("템플릿을 선택해 주세요.")
            return
        if self._txt_path is None or not self._txt_path.exists():
            self._warn("원고 파일을 선택해 주세요.")
            return
        try:
            txt_size = self._txt_path.stat().st_size
        except OSError as exc:
            self._warn(f"원고 파일을 읽을 수 없습니다: {exc}")
            return
        if txt_size == 0:
            self._warn("원고 파일이 비어 있습니다.")
            return

        try:
            template_path = self._template_manager.get_path(template_id)
        except KeyError:
            self._warn(f"템플릿을 찾을 수 없습니다: {template_id}")
            return
        if not template_path.exists():
            self._warn(f"템플릿 파일이 없습니다: {template_path}")
            return

        # 출력 경로 자동 생성 (기본 저장 경로 + 타임스탬프)
        output_dir = Path(self._config.default_output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"{self._txt_path.stem}_{ts}.hwpx"

        request = ConversionRequest(
            template_path=template_path,
            txt_path=self._txt_path,
            output_path=output_path,
            use_gemini=self.gemini_check.isChecked(),
            verify_after=True,
            ambiguous_long_threshold=self._config.ambiguous_long_threshold,
            resolver_backend=self._config.resolver_backend or "gemini",
        )

        self._clear_log()
        self._append_log(
            f"[{datetime.now().strftime('%H:%M:%S')}] 변환 시작", color="#4fc3f7"
        )
        self._append_log(f"  템플릿: {template_path.name}")
        self._append_log(f"  원고:   {self._txt_path.name}")
        self._append_log(f"  출력:   {output_path}")
        self._append_log("")

        self._set_running(True)
        self._spin_up_worker(request)

    def _spin_up_worker(self, request: ConversionRequest) -> None:
        self._thread = QThread(self)
        self._worker = ConversionWorker(request)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.signals.step.connect(self._on_step)
        self._worker.signals.progress.connect(self._on_progress)
        self._worker.signals.finished.connect(self._on_finished)
        self._worker.signals.failed.connect(self._on_failed)
        # v0.15.0: Self-MoA × Batch heartbeat
        self._worker.signals.batch_started.connect(self._on_batch_started)
        self._worker.signals.batch_finished.connect(self._on_batch_finished)

        # cleanup 연결
        self._worker.signals.finished.connect(self._thread.quit)
        self._worker.signals.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_worker)

        self._thread.start()

    # ---- worker signals ----

    def _on_step(self, current: int, total: int, text: str) -> None:
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self._append_log(f"[{current}/{total}] {text}", color="#81c784")
        self.status_message.emit(f"{current}/{total} {text}")

    def _on_progress(self, text: str) -> None:
        # _on_step 이 이미 상위 레벨 메시지를 찍었으므로 중복 방지
        if text.startswith("[") or not text:
            return
        self._append_log(text, color="#bdbdbd")

    # ---- v0.15.0: Self-MoA × Batch heartbeat ----

    def _on_batch_started(self, draws: int) -> None:
        """Self-MoA × Batch 시작 — heartbeat 타이머 가동."""
        from PySide6.QtCore import QTimer
        self._batch_start_ts = time.monotonic()
        self._batch_draws = int(draws)
        self._batch_timer = QTimer(self)
        self._batch_timer.setInterval(1000)  # 1초마다 갱신
        self._batch_timer.timeout.connect(self._on_batch_tick)
        self._batch_timer.start()
        self._append_log(
            f"⏳ Self-MoA × Batch 모드: draws={draws} — 배치 대기 시작 (Gemini 응답 대기)",
            color="#ffa726",
        )
        self.status_message.emit(f"Self-MoA × Batch 처리 중 (draws={draws})...")

    def _on_batch_tick(self) -> None:
        """heartbeat — 경과 시간 상태바에 표시."""
        elapsed = time.monotonic() - self._batch_start_ts
        mm = int(elapsed // 60)
        ss = int(elapsed % 60)
        self.status_message.emit(
            f"⏳ Self-MoA × Batch 처리 중 ({mm:02d}:{ss:02d} 경과, draws={self._batch_draws})",
        )

    def _on_batch_finished(self, ok: bool) -> None:
        """heartbeat 종료."""
        if hasattr(self, "_batch_timer") and self._batch_timer is not None:
            self._batch_timer.stop()
            self._batch_timer.deleteLater()
            self._batch_timer = None
        elapsed = time.monotonic() - getattr(self, "_batch_start_ts", time.monotonic())
        icon = "✅" if ok else "⚠️"
        self._append_log(
            f"{icon} Self-MoA × Batch 완료 — {elapsed:.1f}s",
            color="#4caf50" if ok else "#ffa726",
        )

    def _on_finished(self, result: ConversionResult) -> None:
        self._last_output = result.output_path
        self._append_log("")
        self._append_log("✅ 변환 완료", color="#4caf50")
        self._append_log(f"  파일: {result.output_path}")
        if result.gemini_report and result.gemini_report.call_count > 0:
            r = result.gemini_report
            self._append_log(
                f"  Gemini: 재분류 {r.changed} / 확인 {r.confirmed} "
                f"/ 응답누락 {r.no_decision} · ₩{r.cost.krw:.1f}"
            )
        if result.verify_report:
            v = result.verify_report
            icon = "✅" if v.ok else "⚠️"
            self._append_log(f"  검증: {icon} {v.passed}/{v.total} ({v.rate:.0f}%)")
        self._set_running(False)
        self.save_as_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)
        self.conversion_finished.emit(result)
        self.status_message.emit("변환 완료")

    def _on_failed(self, message: str) -> None:
        self._append_log("")
        self._append_log(f"❌ 변환 실패: {message}", color="#ef5350")
        self._set_running(False)
        self.status_message.emit("변환 실패")
        # 사용자에게 도움될 수 있도록 자세 다이얼로그 + 로그 저장 제안
        btn = QMessageBox.question(
            self,
            "변환 실패",
            f"변환이 실패했습니다.\n\n{message}\n\n진행 로그를 파일로 저장하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if btn == QMessageBox.StandardButton.Yes:
            self._save_log_to_file()

    def _cleanup_worker(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None

    # ---- slots: after finish ----

    def _save_as(self) -> None:
        if not self._last_output or not self._last_output.exists():
            return
        suggested = self._last_output.name
        path, _ = QFileDialog.getSaveFileName(
            self,
            "결과를 다른 이름으로 저장",
            suggested,
            "HWPX 파일 (*.hwpx);;모든 파일 (*.*)",
        )
        if not path:
            return
        target = Path(path)
        try:
            import shutil

            shutil.copy2(self._last_output, target)
            self._append_log(f"💾 사본 저장: {target}", color="#4fc3f7")
            self.status_message.emit(f"저장됨: {target.name}")
        except OSError as exc:
            self._warn(f"저장 실패: {exc}")

    def _go_preview(self) -> None:
        if self._last_output and self._last_output.exists():
            self.preview_requested.emit(self._last_output)

    def _copy_log_to_clipboard(self) -> None:
        from PySide6.QtWidgets import QApplication

        text = self.log.toPlainText()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self.status_message.emit("로그를 클립보드에 복사했습니다")

    def _save_log_to_file(self) -> None:
        text = self.log.toPlainText()
        if not text:
            QMessageBox.information(self, "저장", "저장할 로그가 없습니다.")
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_dir = Path(self._config.default_output_dir)
        default_dir.mkdir(parents=True, exist_ok=True)
        suggested = str(default_dir / f"convert_log_{ts}.txt")
        path, _ = QFileDialog.getSaveFileName(
            self, "진행 로그 저장", suggested, "텍스트 파일 (*.txt)"
        )
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            self._warn(f"저장 실패: {exc}")
            return
        self.status_message.emit(f"로그 저장됨: {Path(path).name}")

    # ---- public API ----

    def refresh_templates(self) -> None:
        """TemplateManager 변경 시 콤보박스 갱신."""
        self.template_combo.blockSignals(True)
        try:
            current_id = self.template_combo.currentData()
            self.template_combo.clear()

            entries = self._template_manager.list()
            default_id = None
            for e in entries:
                label = f"★ {e.name}" if e.is_default else e.name
                self.template_combo.addItem(label, e.id)
                if e.is_default:
                    default_id = e.id

            # 이전 선택 복원 or 기본 템플릿으로
            target_id = current_id or default_id
            if target_id:
                idx = self.template_combo.findData(target_id)
                if idx >= 0:
                    self.template_combo.setCurrentIndex(idx)
        finally:
            self.template_combo.blockSignals(False)

    def apply_config(self, config: app_config.AppConfig) -> None:
        """Settings 탭에서 config 변경 시 호출."""
        self._config = config
        self._refresh_gemini_checkbox()

    def _refresh_gemini_checkbox(self) -> None:
        """현재 백엔드에 맞춰 체크박스 라벨/활성화 상태 갱신."""
        backend = (self._config.resolver_backend or "gemini").lower()

        if backend == "none":
            self.gemini_check.setText("LLM 해석 사용 (설정 탭에서 백엔드 선택 필요)")
            self.gemini_check.setEnabled(False)
            self.gemini_check.setChecked(False)
            self.gemini_check.setToolTip("현재 백엔드: 사용 안 함")
            return

        if backend == "ollama":
            self.gemini_check.setText(
                "Ollama 로컬 해석 사용 (비용 0, 완전 오프라인)"
            )
            self.gemini_check.setEnabled(True)
            self.gemini_check.setChecked(self._config.use_gemini)
            self.gemini_check.setToolTip(
                f"모델: {self._config.ollama_model} @ {self._config.ollama_host}"
            )
            return

        # gemini (기본)
        self.gemini_check.setText("Gemini 해석 사용 (애매 블록만, 문서당 1회 호출)")
        if api_key_manager.has_key():
            self.gemini_check.setEnabled(True)
            self.gemini_check.setChecked(self._config.use_gemini)
            self.gemini_check.setToolTip(f"모델: {self._config.gemini_model}")
        else:
            self.gemini_check.setEnabled(False)
            self.gemini_check.setChecked(False)
            self.gemini_check.setToolTip(
                "API Key 가 등록되어 있지 않습니다 (설정 탭에서 등록)"
            )

    # ---- helpers ----

    def _set_running(self, running: bool) -> None:
        self.run_btn.setEnabled(not running)
        self.txt_btn.setEnabled(not running)
        self.template_combo.setEnabled(not running)
        self.gemini_check.setEnabled(not running and api_key_manager.has_key())
        self.progress_bar.setVisible(running)
        if not running:
            self.progress_bar.setValue(0)

    def _clear_log(self) -> None:
        self.log.clear()

    def _append_log(self, text: str, color: str = "#e0e0e0") -> None:
        cursor = self.log.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.insertText(text + "\n", fmt)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    def _warn(self, text: str) -> None:
        QMessageBox.warning(self, "알림", text)


__all__ = ["ConvertTab"]
