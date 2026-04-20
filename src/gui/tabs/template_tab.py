"""템플릿 관리 탭 — 라이브러리 CRUD GUI.

기획안 4.8::

    ┌─ 템플릿 관리 ─────────────────────────────┐
    │ ★ 기본 10단계 스타일                      │
    │   농정원 2026 공고양식                     │
    │   청양군 기본양식                          │
    │                                          │
    │ [+ 추가] [- 삭제] [★ 기본으로]            │
    └──────────────────────────────────────────┘
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...template.template_manager import (
    DEFAULT_TEMPLATE_ID,
    TemplateManager,
    TemplateNotFoundError,
)
from ...template.template_analyzer import analyze as analyze_template
from ...template.thumbnail import extract_thumbnail_bytes
from ...utils.logger import get_logger


_log = get_logger("gui.template_tab")


class TemplateTab(QWidget):
    """템플릿 라이브러리 CRUD."""

    # 라이브러리 변경 시 emit — ConvertTab 드롭다운 갱신용
    library_changed = Signal()
    status_message = Signal(str)

    def __init__(
        self,
        template_manager: TemplateManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._template_manager = template_manager
        self._build_ui()
        self.refresh()

    # ---- UI ----

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # 좌측: 목록 + 버튼
        left = QVBoxLayout()
        left.addWidget(QLabel("템플릿 라이브러리:"))

        self.list_widget = QListWidget()
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        left.addWidget(self.list_widget, stretch=1)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("+ 추가")
        self.add_btn.clicked.connect(self._add_template)
        self.del_btn = QPushButton("- 삭제")
        self.del_btn.clicked.connect(self._delete_template)
        self.default_btn = QPushButton("★ 기본으로")
        self.default_btn.clicked.connect(self._set_default)
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.del_btn)
        btn_row.addWidget(self.default_btn)
        left.addLayout(btn_row)

        # 우측: 썸네일 + 상세 정보
        right = QVBoxLayout()
        right.addWidget(QLabel("썸네일:"))
        self.thumbnail_label = QLabel("(썸네일 없음)")
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setMinimumHeight(200)
        self.thumbnail_label.setMaximumHeight(260)
        self.thumbnail_label.setStyleSheet(
            "QLabel { border: 1px solid #ccc; background-color: #fafafa; color: #999; }"
        )
        right.addWidget(self.thumbnail_label)

        right.addWidget(QLabel("상세 정보:"))
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumWidth(320)
        self.detail.setStyleSheet(
            "QTextEdit { background-color: #f5f5f5; border: 1px solid #ccc; "
            "font-family: 'Consolas', 'D2Coding', monospace; font-size: 11pt; }"
        )
        right.addWidget(self.detail, stretch=1)

        layout.addLayout(left, stretch=1)
        layout.addLayout(right, stretch=1)

    # ---- data ----

    def refresh(self) -> None:
        """라이브러리에서 다시 불러와 목록 재구성."""
        self.list_widget.clear()
        entries = self._template_manager.list()
        for e in entries:
            prefix = "★ " if e.is_default else "  "
            label = f"{prefix}{e.name}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, e.id)
            item.setToolTip(e.description or f"ID: {e.id}")
            self.list_widget.addItem(item)
        self._update_buttons_state()
        if entries:
            self.list_widget.setCurrentRow(0)
        else:
            self.detail.clear()

    def _current_id(self) -> Optional[str]:
        item = self.list_widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _update_buttons_state(self) -> None:
        current_id = self._current_id()
        is_default_entry = current_id == DEFAULT_TEMPLATE_ID
        self.del_btn.setEnabled(current_id is not None and not is_default_entry)
        self.default_btn.setEnabled(current_id is not None and not is_default_entry)

    # ---- slots ----

    def _on_selection_changed(self, current, _previous) -> None:
        self._update_buttons_state()
        cid = current.data(Qt.ItemDataRole.UserRole) if current else None
        if cid is None:
            self.detail.clear()
            self._clear_thumbnail()
            return
        try:
            entry = self._template_manager.get(cid)
            path = self._template_manager.get_path(cid)
        except TemplateNotFoundError:
            self.detail.setPlainText("(찾을 수 없음)")
            self._clear_thumbnail()
            return
        self._load_thumbnail(path)

        lines = [
            f"이름:       {entry.name}",
            f"ID:         {entry.id}",
            f"파일:       {entry.file}",
            f"기본:       {'예' if entry.is_default else '아니오'}",
            f"등록일:     {entry.added_at}",
        ]
        if entry.description:
            lines.append(f"설명:       {entry.description}")
        lines.append(f"경로:       {path}")

        # 템플릿 분석 결과 요약 (있으면)
        if path.exists():
            try:
                sm = analyze_template(path)
                lines.append("")
                lines.append("─ 스타일 매핑 (analyzer 결과) ─")
                lines.append(
                    f"페이지: {sm.page_setup.paper} "
                    f"여백 {sm.page_setup.margin_top_mm:.0f}/{sm.page_setup.margin_bottom_mm:.0f}/"
                    f"{sm.page_setup.margin_left_mm:.0f}/{sm.page_setup.margin_right_mm:.0f}mm"
                )
                for lv in sorted(sm.level_to_ids):
                    if lv <= 0:
                        continue
                    name = sm.level_to_name.get(lv, "(fallback)")
                    size = sm.level_to_font_size_pt.get(lv, 0)
                    lines.append(f"  레벨 {lv:2d}: {name:16s} {size:4.1f}pt")
                if sm.fallback_used_levels:
                    lines.append(
                        f"  ⚠️ fallback: {sorted(sm.fallback_used_levels)}"
                    )
            except Exception as exc:  # noqa: BLE001
                lines.append("")
                lines.append(f"⚠️ 분석 실패: {type(exc).__name__}: {exc}")

        self.detail.setPlainText("\n".join(lines))

    def _clear_thumbnail(self) -> None:
        self.thumbnail_label.setText("(썸네일 없음)")
        self.thumbnail_label.setPixmap(QPixmap())

    def _load_thumbnail(self, path) -> None:
        data = extract_thumbnail_bytes(path)
        if not data:
            self.thumbnail_label.setText("(썸네일 없음)")
            self.thumbnail_label.setPixmap(QPixmap())
            return
        pix = QPixmap()
        if pix.loadFromData(data):
            # 가로 최대 280px 로 scale, aspect 유지
            scaled = pix.scaledToWidth(
                280, Qt.TransformationMode.SmoothTransformation
            )
            self.thumbnail_label.setPixmap(scaled)
            self.thumbnail_label.setText("")
        else:
            self.thumbnail_label.setText("(썸네일 로드 실패)")

    def _add_template(self) -> None:
        start = str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "업로드할 HWPX 템플릿 선택",
            start,
            "HWPX 파일 (*.hwpx)",
        )
        if not path:
            return

        src = Path(path)
        default_name = src.stem
        name, ok = QInputDialog.getText(
            self,
            "템플릿 이름",
            "라이브러리에 표시될 이름:",
            text=default_name,
        )
        if not ok or not name.strip():
            return

        try:
            entry = self._template_manager.add(src, name=name.strip())
        except (FileNotFoundError, ValueError) as exc:
            QMessageBox.warning(self, "등록 실패", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            _log.exception("템플릿 등록 실패")
            QMessageBox.critical(self, "등록 실패", f"{type(exc).__name__}: {exc}")
            return

        self.status_message.emit(f"템플릿 등록: {entry.name}")
        self.refresh()
        self.library_changed.emit()
        # 방금 추가한 항목으로 선택 이동
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == entry.id:
                self.list_widget.setCurrentRow(i)
                break

    def _delete_template(self) -> None:
        cid = self._current_id()
        if not cid or cid == DEFAULT_TEMPLATE_ID:
            return
        entry = self._template_manager.get(cid)
        btn = QMessageBox.question(
            self,
            "템플릿 삭제",
            f"'{entry.name}' 을 삭제하시겠습니까?\n(파일도 함께 제거됩니다)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if btn != QMessageBox.StandardButton.Yes:
            return
        try:
            self._template_manager.remove(cid)
        except (ValueError, TemplateNotFoundError) as exc:
            QMessageBox.warning(self, "삭제 실패", str(exc))
            return
        self.status_message.emit(f"템플릿 삭제: {entry.name}")
        self.refresh()
        self.library_changed.emit()

    def _set_default(self) -> None:
        cid = self._current_id()
        if not cid:
            return
        try:
            entry = self._template_manager.set_default(cid)
        except TemplateNotFoundError as exc:
            QMessageBox.warning(self, "설정 실패", str(exc))
            return
        self.status_message.emit(f"기본 템플릿: {entry.name}")
        self.refresh()
        self.library_changed.emit()


__all__ = ["TemplateTab"]
