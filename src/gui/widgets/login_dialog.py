"""로그인 / 회원가입 다이얼로그 — v0.7.0 상업화 훅.

MVP 단계에서는 로컬 사용자 DB 만 사용 (:mod:`src.commerce.user_db`). 향후 서버 백엔드
붙이면 이 다이얼로그 뒤만 교체하면 됨.

회원제는 **기본 OFF** — 사용자가 설정 탭에서 켜야 앱 시작 시 로그인 요구.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ...commerce.user_db import User, UserStore
from ...utils.logger import get_logger


_log = get_logger("gui.login_dialog")


class LoginDialog(QDialog):
    """로컬 로그인 / 회원가입.

    ``Accepted`` 로 종료 시 :meth:`user` 에 로그인된 사용자가 들어 있다.
    """

    def __init__(self, parent=None, *, store: Optional[UserStore] = None) -> None:
        super().__init__(parent)
        self._store = store or UserStore()
        self._user: Optional[User] = None
        self.setWindowTitle("로그인")
        self.setModal(True)
        self.setMinimumWidth(380)

        self._mode = "login"   # "login" or "register"
        self._build_ui()
        self._render_for_mode()

    # ---- UI ----

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self.intro_label = QLabel()
        self.intro_label.setWordWrap(True)
        layout.addWidget(self.intro_label)

        self.form = QFormLayout()
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("영문/한글 3~30자")
        self.form.addRow("사용자명:", self.username_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("6자 이상")
        self.form.addRow("비밀번호:", self.password_edit)

        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("(선택) 복구용")
        self.form.addRow("이메일:", self.email_edit)

        layout.addLayout(self.form)

        self.toggle_btn = QPushButton("회원가입으로 전환")
        self.toggle_btn.clicked.connect(self._toggle_mode)
        layout.addWidget(self.toggle_btn)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #c62828;")
        layout.addWidget(self.status_label)

    # ---- state ----

    def _render_for_mode(self) -> None:
        if self._mode == "login":
            self.setWindowTitle("로그인")
            self.intro_label.setText(
                "등록된 사용자로 로그인하세요. (로컬 전용 — 인터넷 전송 없음)"
            )
            self.toggle_btn.setText("회원가입으로 전환 →")
            self.email_edit.setVisible(False)
            label_widget = self.form.labelForField(self.email_edit)
            if label_widget is not None:
                label_widget.setVisible(False)
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("로그인")
        else:
            self.setWindowTitle("회원가입")
            self.intro_label.setText(
                "새 로컬 계정을 만듭니다. 비밀번호는 PBKDF2 해시로 저장되며 평문 저장되지 않습니다."
            )
            self.toggle_btn.setText("← 로그인으로 전환")
            self.email_edit.setVisible(True)
            label_widget = self.form.labelForField(self.email_edit)
            if label_widget is not None:
                label_widget.setVisible(True)
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("회원가입")

    def _toggle_mode(self) -> None:
        self._mode = "register" if self._mode == "login" else "login"
        self._render_for_mode()
        self.status_label.setText("")

    # ---- slots ----

    def _on_accept(self) -> None:
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        email = self.email_edit.text().strip()
        if not username or not password:
            self.status_label.setText("사용자명과 비밀번호를 입력하세요.")
            return

        if self._mode == "register":
            try:
                user = self._store.register(username, password, email)
            except ValueError as exc:
                self.status_label.setText(str(exc))
                return
            self._user = user
            QMessageBox.information(
                self, "가입 완료", f"{user.username} 님 가입 완료! 자동 로그인됩니다."
            )
            self.accept()
            return

        # login
        user = self._store.verify(username, password)
        if user is None:
            self.status_label.setText("사용자명 또는 비밀번호가 일치하지 않습니다.")
            return
        self._user = user
        self.accept()

    # ---- API ----

    def user(self) -> Optional[User]:
        return self._user


__all__ = ["LoginDialog"]
