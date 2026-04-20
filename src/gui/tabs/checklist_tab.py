r"""제출서류 체크리스트 탭 (v0.6.0).

UI 레이아웃::

    ┌─ 체크리스트 ────────────────────────────────────┐
    │ RFP:    [파일 선택...] 26_입찰공고문.hwpx         │
    │ 폴더:   [폴더 선택...] D:\제출서류\               │
    │ [ RFP 분석 ] [ 데모 서류로 대조 ]                 │
    │                                                  │
    │ ─ 진행 로그 ─                                    │
    │                                                  │
    │ ─ 결과 ─                                         │
    │ ✅/⚠️/❌  서류명   매칭파일    발행일    사유       │
    │  ...                                             │
    │                                                  │
    │ [ 보고서 저장... ]  요약: N OK / N WARN / N MISS │
    └──────────────────────────────────────────────────┘
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...checklist.matcher import build_checklist
from ...checklist.models import (
    ChecklistResult,
    DocumentStatus,
    RequiredDocument,
)
from ...checklist.rfp_extractor import demo_required_documents
from ...settings import app_config, api_key_manager
from ...utils.logger import get_logger
from ..workers.rfp_worker import (
    RfpExtractRequest,
    RfpExtractResult,
    RfpExtractWorker,
)


_log = get_logger("gui.checklist_tab")


_STATUS_STYLE = {
    DocumentStatus.OK: ("✅", QColor("#e8f5e9")),
    DocumentStatus.WARNING: ("⚠️", QColor("#fff3e0")),
    DocumentStatus.MISSING: ("❌", QColor("#ffebee")),
    DocumentStatus.UNKNOWN: ("❓", QColor("#f5f5f5")),
}


class ChecklistTab(QWidget):
    """RFP 분석 + 제출서류 체크리스트."""

    status_message = Signal(str)

    def __init__(self, config: app_config.AppConfig, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config = config
        self._rfp_path: Optional[Path] = None
        self._folder_path: Optional[Path] = None
        self._required_docs: list[RequiredDocument] = []
        self._result: Optional[ChecklistResult] = None
        self._thread: Optional[QThread] = None
        self._worker: Optional[RfpExtractWorker] = None

        self._build_ui()

    # ---- UI ----

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        # RFP 파일 선택
        rfp_row = QHBoxLayout()
        rfp_row.addWidget(QLabel("RFP:"))
        self.rfp_btn = QPushButton("파일 선택...")
        self.rfp_btn.clicked.connect(self._pick_rfp)
        self.rfp_label = QLabel("(선택 안 됨)")
        self.rfp_label.setStyleSheet("color: #777;")
        rfp_row.addWidget(self.rfp_btn)
        rfp_row.addWidget(self.rfp_label, stretch=1)
        layout.addLayout(rfp_row)

        # 폴더 선택
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("제출서류 폴더:"))
        self.folder_btn = QPushButton("폴더 선택...")
        self.folder_btn.clicked.connect(self._pick_folder)
        self.folder_label = QLabel("(선택 안 됨)")
        self.folder_label.setStyleSheet("color: #777;")
        folder_row.addWidget(self.folder_btn)
        folder_row.addWidget(self.folder_label, stretch=1)
        # v0.6.1: 재귀 폴더 스캔 체크박스
        self.recursive_check = QCheckBox("하위 폴더 포함")
        self.recursive_check.setToolTip("체크 시 선택한 폴더의 모든 하위 폴더까지 스캔")
        folder_row.addWidget(self.recursive_check)
        layout.addLayout(folder_row)

        # 실행 버튼들
        run_row = QHBoxLayout()
        self.analyze_btn = QPushButton("RFP 분석 + 체크")
        self.analyze_btn.setMinimumHeight(32)
        self.analyze_btn.setStyleSheet("QPushButton { font-weight: bold; }")
        self.analyze_btn.clicked.connect(self._start_analysis)
        self.demo_btn = QPushButton("데모 서류로 대조 (API Key 없어도 OK)")
        self.demo_btn.clicked.connect(self._run_with_demo)
        run_row.addWidget(self.analyze_btn)
        run_row.addWidget(self.demo_btn)
        run_row.addStretch(1)
        layout.addLayout(run_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 0)  # indeterminate
        layout.addWidget(self.progress_bar)

        # 진행 로그
        layout.addWidget(QLabel("진행 로그:"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(100)
        self.log.setStyleSheet(
            "QTextEdit { font-family: 'Consolas','D2Coding',monospace; "
            "background-color: #1e1e1e; color: #e0e0e0; }"
        )
        layout.addWidget(self.log)

        # 결과 테이블
        layout.addWidget(QLabel("결과:"))
        self.result_table = QTableWidget(0, 5)
        self.result_table.setHorizontalHeaderLabels(
            ["상태", "서류명", "매칭 파일", "발행일", "사유"]
        )
        hh = self.result_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.result_table.verticalHeader().setDefaultSectionSize(28)
        layout.addWidget(self.result_table, stretch=1)

        # 요약 + 정렬 + 저장
        summary_row = QHBoxLayout()
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("font-weight: bold;")
        self.sort_btn = QPushButton("파일 자동 정렬...")
        self.sort_btn.setToolTip("매칭된 파일들을 번호매겨 새 폴더에 복사 (원본 보존)")
        self.sort_btn.setEnabled(False)
        self.sort_btn.clicked.connect(self._sort_attachments)
        self.save_report_btn = QPushButton("보고서 저장...")
        self.save_report_btn.setEnabled(False)
        self.save_report_btn.clicked.connect(self._save_report)
        summary_row.addWidget(self.summary_label, stretch=1)
        summary_row.addWidget(self.sort_btn)
        summary_row.addWidget(self.save_report_btn)
        layout.addLayout(summary_row)

    # ---- pickers ----

    def _pick_rfp(self) -> None:
        start = str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "RFP 파일 선택", start,
            "RFP (*.pdf *.hwpx *.hwp);;PDF (*.pdf);;HWPX (*.hwpx);;HWP (*.hwp);;모든 파일 (*.*)",
        )
        if not path:
            return
        chosen = Path(path)
        # v0.8.0: HWP 는 LibreOffice 가 있으면 자동 PDF 변환 제안
        if chosen.suffix.lower() == ".hwp":
            chosen = self._handle_hwp_selection(chosen) or chosen
            if chosen.suffix.lower() == ".hwp":
                return    # 변환 안 됨 / 사용자 취소 → 선택 취소
        self._rfp_path = chosen
        self.rfp_label.setText(self._rfp_path.name)
        self.rfp_label.setStyleSheet("color: #222;")
        self.rfp_label.setToolTip(str(self._rfp_path))

    def _handle_hwp_selection(self, hwp_path: Path) -> Optional[Path]:
        """HWP 파일 선택 시 옵션 제안:
        1) LibreOffice 있으면 PDF 변환 (전체 본문)
        2) 없으면 PrvText 기반 미리보기 분석 (제한적)
        3) 사용자가 HWPX 로 수동 변환
        """
        from ...checklist.hwp_converter import convert_hwp_to_pdf, detect_libreoffice

        info = detect_libreoffice()

        if info.available:
            # LibreOffice 있으면 PDF 변환 (전체 본문)
            btn = QMessageBox.question(
                self, "HWP → PDF 변환",
                f"선택한 파일은 HWP 입니다. LibreOffice 로 PDF 변환 후 진행할까요?\n\n"
                f"파일: {hwp_path.name}\n"
                f"LibreOffice: {info.version or info.path}\n"
                f"변환에 10~30초 걸릴 수 있음 (전체 본문 분석 가능).\n\n"
                "No 를 선택하면 HWP 내부 미리보기(앞 2,000자)만 분석 — 짧은 공고문은 OK.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if btn == QMessageBox.StandardButton.Cancel:
                return None
            if btn == QMessageBox.StandardButton.Yes:
                self._append_log(f"HWP → PDF 변환 시작: {hwp_path.name}")
                self._set_running(True)
                QApplication.processEvents()
                try:
                    pdf_path = convert_hwp_to_pdf(hwp_path)
                except Exception as exc:  # noqa: BLE001
                    self._append_log(f"❌ 변환 실패: {type(exc).__name__}: {exc}")
                    QMessageBox.critical(
                        self, "변환 실패",
                        f"{type(exc).__name__}: {str(exc)[:300]}",
                    )
                    return None
                finally:
                    self._set_running(False)
                self._append_log(f"✅ PDF 생성: {pdf_path.name}")
                return pdf_path
            # No → HWP 그대로 (PrvText 경로)
            self._append_log("HWP 미리보기 모드로 진행 (전체 본문 아님)")
            return hwp_path

        # LibreOffice 없음 — HWP 를 PrvText 로 바로 분석 제안
        btn = QMessageBox.question(
            self, "HWP 파일 처리",
            "LibreOffice 가 설치돼 있지 않아 전체 본문 변환이 불가능합니다.\n\n"
            "HWP 내부에 저장된 **미리보기 텍스트(앞 2,000 자)** 로 분석할까요?\n"
            "- 짧은 공고문/입찰공고 → 충분\n"
            "- 긴 제안요청서(규격서) → 앞부분만 보이므로 일부 서류 누락 가능\n\n"
            "전체 본문이 필요하면 LibreOffice 설치 (https://www.libreoffice.org/download/) "
            "또는 한/글에서 HWPX/PDF 로 저장 후 그 파일 선택.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if btn != QMessageBox.StandardButton.Yes:
            return None
        self._append_log(f"HWP 미리보기 모드: {hwp_path.name}")
        return hwp_path

    def _pick_folder(self) -> None:
        start = str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "제출서류 폴더 선택", start)
        if not d:
            return
        self._folder_path = Path(d)
        self.folder_label.setText(self._folder_path.name)
        self.folder_label.setStyleSheet("color: #222;")
        self.folder_label.setToolTip(str(self._folder_path))

    # ---- run: real analysis ----

    def _start_analysis(self) -> None:
        if self._thread is not None:
            return
        if self._rfp_path is None or not self._rfp_path.exists():
            QMessageBox.warning(self, "알림", "RFP 파일을 먼저 선택하세요.")
            return
        if self._folder_path is None or not self._folder_path.exists():
            QMessageBox.warning(self, "알림", "제출서류 폴더를 먼저 선택하세요.")
            return
        if not api_key_manager.has_key(service="gemini"):
            QMessageBox.warning(
                self,
                "Gemini API Key 없음",
                "RFP 분석에는 Gemini API Key 가 필요합니다.\n"
                "설정 탭에서 등록하거나, '데모 서류로 대조' 버튼을 사용하세요.",
            )
            return

        self._clear_log()
        self._append_log(f"[{datetime.now().strftime('%H:%M:%S')}] RFP 분석 시작")
        self._append_log(f"  RFP:    {self._rfp_path}")
        self._append_log(f"  폴더:   {self._folder_path}")
        self._set_running(True)

        req = RfpExtractRequest(
            rfp_path=self._rfp_path, model=self._config.gemini_model
        )
        self._thread = QThread(self)
        self._worker = RfpExtractWorker(req)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.signals.progress.connect(self._append_log)
        self._worker.signals.finished.connect(self._on_rfp_finished)
        self._worker.signals.failed.connect(self._on_rfp_failed)
        self._worker.signals.finished.connect(self._thread.quit)
        self._worker.signals.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_worker)
        self._thread.start()

    def _on_rfp_finished(self, result: RfpExtractResult) -> None:
        self._required_docs = result.documents
        self._append_log(f"✅ RFP 에서 {len(result.documents)} 개 서류 추출")
        for d in result.documents:
            badge = "필수" if d.is_required else "선택"
            age = f" ({d.max_age_days}일)" if d.max_age_days else ""
            self._append_log(f"  • [{badge}{age}] {d.name}")
        # 이제 폴더와 매치
        self._run_matcher()

    def _on_rfp_failed(self, message: str) -> None:
        self._append_log(f"❌ RFP 분석 실패: {message}")
        self._set_running(False)
        self.status_message.emit("RFP 분석 실패")

    def _cleanup_worker(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None
        self._set_running(False)

    # ---- run: demo mode ----

    def _run_with_demo(self) -> None:
        if self._folder_path is None or not self._folder_path.exists():
            QMessageBox.warning(self, "알림", "제출서류 폴더를 먼저 선택하세요.")
            return
        self._clear_log()
        self._append_log("데모 필수서류 5종으로 체크 (Gemini 호출 없음)")
        self._required_docs = demo_required_documents()
        self._run_matcher()

    # ---- matcher ----

    def _run_matcher(self) -> None:
        if self._folder_path is None:
            return
        recursive = self.recursive_check.isChecked()
        self._append_log(
            f"폴더 스캔: {self._folder_path}" + (" (재귀)" if recursive else " (바로 아래만)")
        )
        # v0.6.1: HWP 파일 안내
        hwp_files = list(self._folder_path.glob("*.hwp"))
        if hwp_files:
            self._append_log(
                f"  ℹ️ HWP 파일 {len(hwp_files)}개 발견 — 파일명 매칭은 되지만 "
                "내용 기반 확인(OCR 등) 은 HWPX 로 변환 후 가능"
            )
        result = build_checklist(
            self._required_docs, self._folder_path, recursive=recursive
        )
        self._result = result
        self._populate_result_table(result)
        self._append_log(
            f"완료: OK {result.ok_count} / WARN {result.warning_count} / MISS {result.missing_count}"
        )
        self.save_report_btn.setEnabled(True)
        self.sort_btn.setEnabled(True)
        self._set_running(False)
        self.status_message.emit(
            f"체크 완료 — 제출가능: {'예' if result.is_submittable else '아니오'}"
        )

    def _populate_result_table(self, result: ChecklistResult) -> None:
        self.result_table.setRowCount(len(result.items))
        for row, item in enumerate(result.items):
            icon, bg = _STATUS_STYLE.get(item.status, ("?", QColor("#ffffff")))

            status_item = QTableWidgetItem(icon)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setBackground(QBrush(bg))

            name_item = QTableWidgetItem(item.doc.name)
            name_item.setToolTip(item.doc.description)

            match_text = ""
            date_text = ""
            if item.best_match:
                match_text = item.best_match.path.name
                if item.best_match.issued_date:
                    # v0.8.0: 발행일 출처도 같이 표시
                    src = item.best_match.issued_source
                    src_badge = {
                        "filename": "📄",
                        "text": "📃",
                        "ocr": "🔍",
                    }.get(src, "")
                    date_text = f"{item.best_match.issued_date} {src_badge}"

            match_item = QTableWidgetItem(match_text)
            date_item = QTableWidgetItem(date_text)

            reason_text = item.warning_reason
            if not reason_text and item.status == DocumentStatus.MISSING:
                hints = ", ".join(item.doc.filename_hints[:3])
                reason_text = f"키워드 미매치: {hints}"
            reason_item = QTableWidgetItem(reason_text)

            for col, cell in enumerate([status_item, name_item, match_item, date_item, reason_item]):
                if col != 0:
                    cell.setBackground(QBrush(bg))
                self.result_table.setItem(row, col, cell)

        submittable = "✅ 제출 가능" if result.is_submittable else "⚠️ 필수 서류 부족 — 제출 불가"
        self.summary_label.setText(
            f"OK {result.ok_count}  /  WARNING {result.warning_count}  /  MISSING {result.missing_count}    ·    {submittable}"
        )

    # ---- report ----

    def _sort_attachments(self) -> None:
        """매칭된 파일들을 번호매겨 새 폴더로 복사 (+ v0.10.0 ZIP 옵션)."""
        if self._result is None:
            return
        from ...checklist.sorter import sort_attachments

        suggested = Path(self._config.default_output_dir) / (
            f"첨부정렬_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        d = QFileDialog.getExistingDirectory(
            self, "정렬 결과를 저장할 폴더 (새로 만들거나 기존 선택)",
            str(suggested.parent),
        )
        if not d:
            return
        target = Path(d) / suggested.name

        # v0.10.0: ZIP 묶음 여부 확인
        reply = QMessageBox.question(
            self,
            "ZIP 으로 묶기?",
            "정렬된 폴더를 ZIP 파일로도 묶으시겠습니까?\n"
            "(제출용 첨부파일로 바로 업로드하기 편합니다)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        make_zip = reply == QMessageBox.StandardButton.Yes

        try:
            report = sort_attachments(
                self._result, target, write_report=True, make_zip=make_zip,
            )
        except Exception as exc:  # noqa: BLE001
            _log.exception("정렬 실패")
            QMessageBox.critical(self, "정렬 실패", f"{type(exc).__name__}: {exc}")
            return
        zip_line = f"\nZIP 파일:        {report.zip_path}" if report.zip_path else ""
        report_line = (
            f"\n보고서:          {report.report_path.name}" if report.report_path else ""
        )
        msg = (
            f"📁 {target}\n\n"
            f"복사된 파일:     {len(report.copied)}\n"
            f"누락 서류:       {len(report.missing)}\n"
            f"매칭 없는 파일:  {len(report.unmatched_files)} (→ _미매칭/ 로 복사)"
            f"{report_line}{zip_line}\n"
        )
        self._append_log(f"✅ 파일 정렬 완료 → {target.name}")
        self.status_message.emit(f"정렬: {report.summary()}")
        QMessageBox.information(self, "정렬 완료", msg)

    def _save_report(self) -> None:
        if self._result is None:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_dir = Path(self._config.default_output_dir)
        default_dir.mkdir(parents=True, exist_ok=True)
        suggested = default_dir / f"checklist_report_{ts}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "체크리스트 보고서 저장", str(suggested),
            "텍스트 파일 (*.txt);;Markdown (*.md)"
        )
        if not path:
            return
        text = self._format_report(self._result)
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "저장 실패", f"{exc}")
            return
        self.status_message.emit(f"보고서 저장됨: {Path(path).name}")

    def _format_report(self, result: ChecklistResult) -> str:
        lines = [
            "# 제출서류 체크리스트 보고서",
            "",
            f"- 생성: {datetime.now().isoformat(timespec='seconds')}",
            f"- RFP: {result.rfp_path or '(데모 모드)'}",
            f"- 폴더: {result.folder_path}",
            f"- 요약: OK {result.ok_count} / WARN {result.warning_count} / MISS {result.missing_count}",
            f"- 제출 가능: {'예' if result.is_submittable else '아니오'}",
            "",
            "## 상세",
            "",
        ]
        for item in result.items:
            icon = {
                DocumentStatus.OK: "✅",
                DocumentStatus.WARNING: "⚠️",
                DocumentStatus.MISSING: "❌",
                DocumentStatus.UNKNOWN: "❓",
            }.get(item.status, "?")
            lines.append(f"### {icon} {item.doc.name}")
            if item.doc.description:
                lines.append(f"- 설명: {item.doc.description}")
            if item.doc.max_age_days:
                lines.append(f"- 발급일 제한: {item.doc.max_age_days}일 이내")
            if item.best_match:
                lines.append(f"- 매칭 파일: `{item.best_match.path.name}`")
                if item.best_match.issued_date:
                    lines.append(f"- 발행일: {item.best_match.issued_date}")
            else:
                hints = ", ".join(item.doc.filename_hints)
                lines.append(f"- 검색 키워드: {hints}")
            if item.warning_reason:
                lines.append(f"- ⚠️ {item.warning_reason}")
            lines.append("")
        return "\n".join(lines)

    # ---- helpers ----

    def _set_running(self, running: bool) -> None:
        self.analyze_btn.setEnabled(not running)
        self.demo_btn.setEnabled(not running)
        self.rfp_btn.setEnabled(not running)
        self.folder_btn.setEnabled(not running)
        self.progress_bar.setVisible(running)

    def _clear_log(self) -> None:
        self.log.clear()

    def _append_log(self, text: str) -> None:
        self.log.append(text)

    def apply_config(self, config: app_config.AppConfig) -> None:
        self._config = config


__all__ = ["ChecklistTab"]
