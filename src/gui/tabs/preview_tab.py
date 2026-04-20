"""미리보기 탭 — 생성된 HWPX 를 HTML 로 렌더해서 보여준다.

QTextBrowser 기반 (QWebEngineView 대신). 완전 재현이 아니라 **계층/서식 감 잡기** 가
목적. 텍스트 복사도 가능 (QTextBrowser 는 selectable).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ...hwpx.visualize import render_hwpx_to_html
from ...utils.logger import get_logger


_log = get_logger("gui.preview_tab")


class PreviewTab(QWidget):
    """HWPX HTML 렌더링 탭."""

    status_message = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current: Optional[Path] = None
        self._build_ui()

    # ---- UI ----

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        self.open_btn = QPushButton("파일 열기...")
        self.open_btn.clicked.connect(self._pick_file)
        self.refresh_btn = QPushButton("새로고침")
        self.refresh_btn.clicked.connect(self._refresh)
        self.refresh_btn.setEnabled(False)
        self.open_ext_btn = QPushButton("한/글로 열기")
        self.open_ext_btn.clicked.connect(self._open_external)
        self.open_ext_btn.setEnabled(False)
        toolbar.addWidget(self.open_btn)
        toolbar.addWidget(self.refresh_btn)
        toolbar.addWidget(self.open_ext_btn)
        toolbar.addStretch(1)
        self.path_label = QLabel("(미리보기할 파일 없음)")
        self.path_label.setStyleSheet("color: #777;")
        toolbar.addWidget(self.path_label)
        layout.addLayout(toolbar)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setStyleSheet(
            "QTextBrowser { background-color: #fafafa; border: 1px solid #ccc; }"
        )
        layout.addWidget(self.browser, stretch=1)

        self._set_placeholder()

    # ---- public API ----

    def show_file(self, hwpx_path: Path) -> None:
        """외부에서 호출 — 특정 HWPX 를 미리보기로 로드."""
        path = Path(hwpx_path)
        if not path.exists():
            QMessageBox.warning(self, "미리보기 실패", f"파일이 없습니다: {path}")
            return
        try:
            html = render_hwpx_to_html(path)
        except Exception as exc:  # noqa: BLE001
            _log.exception("HWPX 렌더 실패")
            QMessageBox.critical(
                self, "미리보기 실패", f"{type(exc).__name__}: {exc}"
            )
            return

        self._current = path
        self.path_label.setText(path.name)
        self.path_label.setToolTip(str(path))
        self.path_label.setStyleSheet("color: #222;")
        self.browser.setHtml(html)
        self.refresh_btn.setEnabled(True)
        self.open_ext_btn.setEnabled(True)
        self.status_message.emit(f"미리보기: {path.name}")

    # ---- slots ----

    def _pick_file(self) -> None:
        start = str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "HWPX 파일 선택",
            start,
            "HWPX 파일 (*.hwpx);;모든 파일 (*.*)",
        )
        if path:
            self.show_file(Path(path))

    def _refresh(self) -> None:
        if self._current:
            self.show_file(self._current)

    def _open_external(self) -> None:
        if not self._current or not self._current.exists():
            return
        from PySide6.QtCore import QUrl

        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._current)))
        if not ok:
            QMessageBox.warning(
                self,
                "열기 실패",
                "한/글이 설치되어 있지 않거나 .hwpx 연결 프로그램이 없습니다.",
            )

    # ---- helpers ----

    def _set_placeholder(self) -> None:
        self.browser.setHtml(
            '<html><body style="color: #999; font-family: sans-serif; padding: 40px;">'
            '<h3>미리보기 없음</h3>'
            '<p>변환 탭에서 <b>변환 실행</b> 후 <b>미리보기 탭으로</b> 버튼을 누르거나,</p>'
            '<p>위쪽 <b>파일 열기...</b> 로 HWPX 파일을 직접 선택하세요.</p>'
            '</body></html>'
        )


__all__ = ["PreviewTab"]
