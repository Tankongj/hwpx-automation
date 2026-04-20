"""정량제안서 탭 — HWPX 템플릿 셀 단위 편집 (v0.5.0 MVP).

UI 레이아웃::

    ┌─ 정량 ────────────────────────────────────────┐
    │ 템플릿: [파일 선택...] [정량제안서] 2026년 ...  │
    │ [ 로드 ]                                       │
    │                                                │
    │ ┌─ 서식 목록 ──┐  ┌─ 표 편집 ──────────────┐  │
    │ │ □ [서식 1]    │  │ r0 [회사명] [주식회사...]│  │
    │ │ □ [서식 2]    │  │ r0 [대표자] [최필승]    │  │
    │ │ □ [서식 3]    │  │ ...                    │  │
    │ └───────────────┘  └────────────────────────┘  │
    │                                                │
    │ [ 저장 ]  [ 미리보기 탭으로 ]                  │
    └────────────────────────────────────────────────┘

사용 플로우:
1. 템플릿 HWPX 선택 → 로드
2. 좌측 트리에서 서식/표 선택 → 우측에 셀 격자
3. 셀 클릭해서 편집 (QTableWidget inline edit)
4. 저장 → 새 HWPX 파일로 출력
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...quant.converter import save_document
from ...quant.models import QuantCell, QuantDocument, RowOp
from ...quant.parser import parse_document
from ...quant.type_hints import hint_for_label, summarize_hint
from ...settings import app_config
from ...utils.logger import get_logger


_log = get_logger("gui.quant_tab")


class QuantTab(QWidget):
    """정량제안서 편집 탭."""

    preview_requested = Signal(Path)
    status_message = Signal(str)

    def __init__(self, config: app_config.AppConfig, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config = config
        self._doc: Optional[QuantDocument] = None
        self._last_output: Optional[Path] = None
        self._current_form_id: Optional[str] = None
        self._current_table_ord: Optional[int] = None
        # QTableWidget 의 cellChanged 콜백이 초기 로드 때 연속 발사되는 걸 막는 플래그
        self._suppress_signals = False

        self._build_ui()

    # ---- UI ----

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        # 상단: 템플릿 선택
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("템플릿:"))
        self.template_btn = QPushButton("파일 선택...")
        self.template_btn.clicked.connect(self._pick_template)
        self.template_label = QLabel("(선택 안 됨)")
        self.template_label.setStyleSheet("color: #777;")
        top_row.addWidget(self.template_btn)
        top_row.addWidget(self.template_label, stretch=1)
        self.load_btn = QPushButton("로드")
        self.load_btn.setEnabled(False)
        self.load_btn.clicked.connect(self._load_template)
        top_row.addWidget(self.load_btn)
        layout.addLayout(top_row)

        # 중앙: 서식 트리 + 표 에디터 (splitter)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.form_tree = QTreeWidget()
        self.form_tree.setHeaderLabels(["서식 / 표"])
        self.form_tree.setMinimumWidth(240)
        self.form_tree.currentItemChanged.connect(self._on_tree_selection)
        splitter.addWidget(self.form_tree)

        editor_panel = QWidget()
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        self.table_info_label = QLabel("(서식/표를 선택하세요)")
        self.table_info_label.setStyleSheet("color: #555; font-weight: bold;")
        editor_layout.addWidget(self.table_info_label)

        # v0.5.1: 행 조작 툴바
        row_ops_bar = QHBoxLayout()
        self.add_row_btn = QPushButton("+ 선택 행 아래에 추가")
        self.add_row_btn.setToolTip("선택한 행을 복제해서 바로 아래에 빈 칸으로 삽입")
        self.add_row_btn.setEnabled(False)
        self.add_row_btn.clicked.connect(self._add_row)
        self.del_row_btn = QPushButton("- 선택 행 삭제")
        self.del_row_btn.setEnabled(False)
        self.del_row_btn.clicked.connect(self._delete_row)
        row_ops_bar.addWidget(self.add_row_btn)
        row_ops_bar.addWidget(self.del_row_btn)
        row_ops_bar.addStretch(1)
        self.pending_ops_label = QLabel("")
        self.pending_ops_label.setStyleSheet("color: #d84315;")
        row_ops_bar.addWidget(self.pending_ops_label)
        editor_layout.addLayout(row_ops_bar)

        self.table_widget = QTableWidget(0, 0)
        self.table_widget.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.table_widget.verticalHeader().setDefaultSectionSize(28)
        self.table_widget.cellChanged.connect(self._on_cell_changed)
        self.table_widget.itemSelectionChanged.connect(self._on_row_selection_changed)
        editor_layout.addWidget(self.table_widget, stretch=1)
        splitter.addWidget(editor_panel)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, stretch=1)

        # 하단: 저장 / 미리보기 버튼
        bottom_row = QHBoxLayout()
        self.save_btn = QPushButton("저장")
        self.save_btn.setEnabled(False)
        self.save_btn.setStyleSheet("QPushButton { font-weight: bold; }")
        self.save_btn.clicked.connect(self._save)
        self.save_as_btn = QPushButton("다른 이름으로 저장...")
        self.save_as_btn.setEnabled(False)
        self.save_as_btn.clicked.connect(self._save_as)
        self.preview_btn = QPushButton("미리보기 탭으로")
        self.preview_btn.setEnabled(False)
        self.preview_btn.clicked.connect(self._go_preview)
        bottom_row.addWidget(self.save_btn)
        bottom_row.addWidget(self.save_as_btn)
        bottom_row.addStretch(1)
        bottom_row.addWidget(self.preview_btn)
        layout.addLayout(bottom_row)

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #666;")
        layout.addWidget(self.stats_label)

    # ---- slots: input ----

    def _pick_template(self) -> None:
        start = str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "정량제안서 HWPX 템플릿 선택", start,
            "HWPX 파일 (*.hwpx);;모든 파일 (*.*)",
        )
        if not path:
            return
        self._template_path = Path(path)
        self.template_label.setText(self._template_path.name)
        self.template_label.setStyleSheet("color: #222;")
        self.template_label.setToolTip(str(self._template_path))
        self.load_btn.setEnabled(True)

    def _load_template(self) -> None:
        path = getattr(self, "_template_path", None)
        if not path:
            return
        try:
            doc = parse_document(path)
        except Exception as exc:  # noqa: BLE001
            _log.exception("quant parse 실패")
            QMessageBox.critical(
                self, "로드 실패", f"{type(exc).__name__}: {exc}"
            )
            return
        self._doc = doc
        self._populate_tree()
        n_forms = len(doc.form_labels)
        n_cells = len(doc.cells)
        self.stats_label.setText(f"로드됨 — 서식 {n_forms}개, 셀 {n_cells}개")
        self.save_btn.setEnabled(True)
        self.save_as_btn.setEnabled(True)
        self.status_message.emit(f"정량제안서 로드: {n_forms}개 서식 / {n_cells}개 셀")

    # ---- tree ----

    def _populate_tree(self) -> None:
        self.form_tree.clear()
        if self._doc is None:
            return
        for form_id, label in self._doc.forms():
            form_item = QTreeWidgetItem([label])
            form_item.setData(0, Qt.ItemDataRole.UserRole, ("form", form_id, None))
            tables = self._doc.tables_of(form_id)
            for ord_idx in tables:
                rows, cols = self._doc.table_shape(form_id, ord_idx)
                table_item = QTreeWidgetItem([f"  표 #{ord_idx + 1} ({rows}×{cols})"])
                table_item.setData(
                    0, Qt.ItemDataRole.UserRole, ("table", form_id, ord_idx)
                )
                form_item.addChild(table_item)
            self.form_tree.addTopLevelItem(form_item)
            form_item.setExpanded(True)

    def _on_tree_selection(self, current, _previous) -> None:
        if current is None or self._doc is None:
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind, form_id, ord_idx = data
        if kind == "form":
            # 해당 form 의 첫 표를 선택
            tables = self._doc.tables_of(form_id)
            if tables:
                ord_idx = tables[0]
                # 자식으로 selection 이동
                if current.childCount() > 0:
                    child = current.child(0)
                    self.form_tree.blockSignals(True)
                    self.form_tree.setCurrentItem(child)
                    self.form_tree.blockSignals(False)
            else:
                self.table_widget.setRowCount(0)
                self.table_widget.setColumnCount(0)
                self.table_info_label.setText(f"{form_id}: 표 없음")
                return
        self._load_table_into_widget(form_id, ord_idx)

    # ---- editor grid ----

    def _load_table_into_widget(self, form_id: str, ord_idx: int) -> None:
        if self._doc is None:
            return
        cells = self._doc.cells_of_table(form_id, ord_idx)
        if not cells:
            return
        rows, cols = self._doc.table_shape(form_id, ord_idx)

        self._suppress_signals = True
        try:
            self.table_widget.clear()
            self.table_widget.setRowCount(rows)
            self.table_widget.setColumnCount(cols)
            self.table_widget.setHorizontalHeaderLabels(
                [f"C{c}" for c in range(cols)]
            )
            self.table_widget.setVerticalHeaderLabels(
                [f"R{r}" for r in range(rows)]
            )

            # 셀 채우기 (v0.5.1: 병합 셀 setSpan, v0.8.0: 타입 힌트 tooltip)
            # 먼저 레이블-후보 셀을 한 바퀴 돌면서 좌표→힌트 매핑 구성.
            # "레이블이 col=N 에 있으면, 같은 행의 col=N+1 셀이 값" 이라는 가정.
            hints: dict[tuple[int, int], str] = {}
            label_cells = [c for c in cells if c.col == 0 or (
                c.col % 2 == 0 and 1 <= len(c.text) <= 20
            )]
            for lc in label_cells:
                ftype, unit = hint_for_label(lc.text)
                if ftype.name == "TEXT" and not unit:
                    continue   # 기본값인 TEXT 는 tooltip 생략
                hint_str = summarize_hint(ftype, unit)
                # 오른쪽 셀에 힌트 할당
                hints[(lc.row, lc.col + 1)] = hint_str

            for cell in cells:
                item = QTableWidgetItem(cell.text)
                item.setData(Qt.ItemDataRole.UserRole, cell.key)
                # 레이블로 추정되는 셀(첫 열이거나 홀수열인데 짧은 텍스트) 은 옅은 색 배경
                if cell.col == 0 or (cell.col % 2 == 0 and len(cell.text) < 20):
                    item.setBackground(QBrush(QColor("#f0f4ff")))
                # v0.8.0: 타입 힌트 tooltip
                hint = hints.get((cell.row, cell.col))
                if hint:
                    item.setToolTip(f"힌트: {hint}")
                self.table_widget.setItem(cell.row, cell.col, item)
                # 병합 셀이면 span 적용
                if cell.row_span > 1 or cell.col_span > 1:
                    self.table_widget.setSpan(
                        cell.row, cell.col, cell.row_span, cell.col_span
                    )

            # 자동 열 너비
            self.table_widget.resizeColumnsToContents()
        finally:
            self._suppress_signals = False

        self._current_form_id = form_id
        self._current_table_ord = ord_idx
        form_label = self._doc.form_labels.get(form_id, form_id)
        self.table_info_label.setText(
            f"{form_label} — 표 #{ord_idx + 1}  ({rows}행 × {cols}열)"
        )

    # ---- v0.5.1: row operations ----

    def _on_row_selection_changed(self) -> None:
        has_sel = bool(self.table_widget.selectedItems())
        self.add_row_btn.setEnabled(has_sel and self._doc is not None)
        self.del_row_btn.setEnabled(has_sel and self._doc is not None)

    def _selected_row(self) -> Optional[int]:
        items = self.table_widget.selectedItems()
        if not items:
            return None
        return items[0].row()

    def _current_table_coords(self) -> Optional[tuple[int, int]]:
        """현재 편집 중인 표의 (para_index, table_idx)."""
        if self._doc is None or self._current_form_id is None or self._current_table_ord is None:
            return None
        cells = self._doc.cells_of_table(self._current_form_id, self._current_table_ord)
        if not cells:
            return None
        c = cells[0]
        return (c.para_index, c.table_idx)

    def _add_row(self) -> None:
        if self._doc is None:
            return
        row = self._selected_row()
        coords = self._current_table_coords()
        if row is None or coords is None:
            return
        para_index, table_idx = coords
        op = RowOp(
            para_index=para_index, table_idx=table_idx,
            source_row=row, op="duplicate",
        )
        self._doc.row_ops.append(op)
        self._update_pending_ops_label()
        QMessageBox.information(
            self, "행 추가 예약",
            f"행 {row + 1} 을 복제해 바로 아래에 추가하도록 예약됐습니다.\n"
            "저장 시 실제 HWPX 에 반영됩니다.",
        )

    def _delete_row(self) -> None:
        if self._doc is None:
            return
        row = self._selected_row()
        coords = self._current_table_coords()
        if row is None or coords is None:
            return
        btn = QMessageBox.question(
            self, "행 삭제 예약",
            f"행 {row + 1} 을 삭제하도록 예약할까요?\n저장 시 실제 HWPX 에 반영됩니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if btn != QMessageBox.StandardButton.Yes:
            return
        para_index, table_idx = coords
        op = RowOp(
            para_index=para_index, table_idx=table_idx,
            source_row=row, op="delete",
        )
        self._doc.row_ops.append(op)
        self._update_pending_ops_label()

    def _update_pending_ops_label(self) -> None:
        if self._doc is None:
            self.pending_ops_label.setText("")
            return
        n = len(self._doc.row_ops)
        if n:
            dup = sum(1 for o in self._doc.row_ops if o.op == "duplicate")
            dele = sum(1 for o in self._doc.row_ops if o.op == "delete")
            self.pending_ops_label.setText(
                f"⚠️ 대기 중 행 연산: 복제 {dup} / 삭제 {dele} (저장 시 반영)"
            )
        else:
            self.pending_ops_label.setText("")

    def _on_cell_changed(self, row: int, col: int) -> None:
        if self._suppress_signals or self._doc is None:
            return
        item = self.table_widget.item(row, col)
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if key is None:
            return
        # QuantCell.key == (para_index, table_idx, row, col)
        # 도큐먼트의 해당 셀 찾아서 text 업데이트
        for c in self._doc.cells:
            if c.key == tuple(key):
                c.text = item.text()
                break

    # ---- save ----

    def _build_output_path(self) -> Path:
        out_dir = Path(self._config.default_output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        src_name = Path(self._template_path).stem
        return out_dir / f"{src_name}_edited_{ts}.hwpx"

    def _save(self) -> None:
        if self._doc is None:
            return
        out = self._build_output_path()
        try:
            save_document(self._doc, out)
        except FileExistsError as exc:
            QMessageBox.warning(self, "저장 실패", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            _log.exception("quant save 실패")
            QMessageBox.critical(
                self, "저장 실패", f"{type(exc).__name__}: {exc}"
            )
            return
        self._last_output = out
        self.preview_btn.setEnabled(True)
        self.status_message.emit(f"저장 완료: {out.name}")
        # v0.5.1: row_ops 반영된 상태로 doc 재로드 → UI 갱신
        had_ops = bool(self._doc.row_ops) if self._doc else False
        if had_ops:
            try:
                self._doc = parse_document(out)
                self._populate_tree()
                self._update_pending_ops_label()
                self.status_message.emit("저장 완료 — 행 조작 반영, 에디터 재로드")
            except Exception as exc:  # noqa: BLE001
                _log.warning("저장 후 재로드 실패: %s", exc)
        QMessageBox.information(
            self, "저장 완료", f"결과 파일:\n{out}"
        )

    def _save_as(self) -> None:
        if self._doc is None:
            return
        suggested = self._build_output_path()
        path, _ = QFileDialog.getSaveFileName(
            self, "다른 이름으로 저장", str(suggested),
            "HWPX 파일 (*.hwpx)"
        )
        if not path:
            return
        target = Path(path)
        if target.exists():
            btn = QMessageBox.question(
                self, "덮어쓰기", f"{target.name} 이미 존재합니다. 덮어쓸까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if btn != QMessageBox.StandardButton.Yes:
                return
            target.unlink()
        try:
            save_document(self._doc, target)
        except Exception as exc:  # noqa: BLE001
            _log.exception("quant save-as 실패")
            QMessageBox.critical(self, "저장 실패", f"{type(exc).__name__}: {exc}")
            return
        self._last_output = target
        self.preview_btn.setEnabled(True)
        self.status_message.emit(f"저장 완료: {target.name}")

    def _go_preview(self) -> None:
        if self._last_output and self._last_output.exists():
            self.preview_requested.emit(self._last_output)

    # ---- public ----

    def apply_config(self, config: app_config.AppConfig) -> None:
        self._config = config


__all__ = ["QuantTab"]
