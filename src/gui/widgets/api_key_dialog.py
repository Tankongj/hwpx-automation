"""Gemini API Key 입력 다이얼로그 — 첫 실행 온보딩 + 설정 탭 공용.

기획안 4.7 의 "처음 실행 시 GUI 입력 → AppData 에 암호화 저장" 플로우를 구현.
- Password 필드 + 간단한 포맷 검증
- "연결 테스트" 버튼 (옵션) — 실제 Gemini ping 으로 키 유효성 검증
- "저장 안 하고 건너뛰기" — Gemini 비활성 모드로 시작할 수 있게

보안
----
- QLineEdit.Password 모드 → 화면 표시 안 됨
- Logger 에 key 출력 금지
- 예외 메시지에 key 노출 금지 (try/except 에서 키는 로컬 변수로만 처리)
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ...settings import api_key_manager
from ...utils.logger import get_logger


_log = get_logger("gui.api_key_dialog")


class ApiKeyDialog(QDialog):
    """API Key 입력/저장 다이얼로그.

    사용 예::

        dlg = ApiKeyDialog(parent, first_run=True)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            key = dlg.api_key()   # 성공 시 키 반환 (저장도 완료된 상태)
    """

    def __init__(self, parent=None, *, first_run: bool = False) -> None:
        super().__init__(parent)
        self._first_run = first_run
        self._saved_key: Optional[str] = None
        self._skipped: bool = False

        self.setWindowTitle("Gemini API Key 설정")
        self.setModal(True)
        self.setMinimumWidth(520)

        self._build_ui()

    # ---- UI ----

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        if self._first_run:
            intro_text = (
                "HWPX Automation 은 원고의 애매한 계층을 해석하기 위해 Google Gemini API 를 사용합니다.\n"
                "문서당 호출은 1회이며 비용은 보통 원당 10원 내외입니다.\n\n"
                "API Key 는 OS 자격 증명 관리자(keyring)에 저장됩니다. 플레인 텍스트로 저장되지 않습니다."
            )
        else:
            intro_text = (
                "저장된 Gemini API Key 를 교체합니다. 비워 두고 저장하면 기존 키가 유지됩니다."
            )
        intro = QLabel(intro_text)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText("AIza... (Google AI Studio 에서 발급)")
        self.key_input.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.key_input)

        self.show_box = QCheckBox("키 표시")
        self.show_box.toggled.connect(
            lambda checked: self.key_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        layout.addWidget(self.show_box)

        self.test_btn = QPushButton("연결 테스트")
        self.test_btn.clicked.connect(self._on_test)
        self.test_btn.setEnabled(False)
        layout.addWidget(self.test_btn)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #555;")
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox()
        self.save_btn = buttons.addButton(
            "저장", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.save_btn.setEnabled(False)
        self.cancel_btn = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        if self._first_run:
            self.skip_btn = buttons.addButton(
                "건너뛰기 (Gemini 비활성)", QDialogButtonBox.ButtonRole.RejectRole
            )
            self.skip_btn.clicked.connect(self._on_skip)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ---- slots ----

    def _on_text_changed(self, txt: str) -> None:
        stripped = txt.strip()
        valid = len(stripped) >= 10  # 최소 길이 허들
        self.test_btn.setEnabled(valid)
        self.save_btn.setEnabled(valid)

    def _on_test(self) -> None:
        key = self.key_input.text().strip()
        if not key:
            return
        self.status_label.setText("테스트 중…")
        self.test_btn.setEnabled(False)
        try:
            ok, msg = self._ping_gemini(key)
        finally:
            self.test_btn.setEnabled(True)
        if ok:
            self.status_label.setText(f"연결 성공 ✅  {msg}")
        else:
            self.status_label.setText(f"연결 실패 ❌  {msg}")

    def _on_save(self) -> None:
        key = self.key_input.text().strip()
        if not key:
            QMessageBox.warning(self, "저장 실패", "API Key 가 비어 있습니다")
            return
        try:
            storage = api_key_manager.set_key(key)
        except Exception as exc:  # noqa: BLE001
            _log.error("API Key 저장 실패: %s", type(exc).__name__)
            QMessageBox.critical(self, "저장 실패", f"{type(exc).__name__}: 자세한 내용은 로그를 확인하세요.")
            return
        self._saved_key = key
        self.status_label.setText(f"저장 완료 ({storage})")
        self.accept()

    def _on_skip(self) -> None:
        self._skipped = True
        self.reject()

    # ---- external API ----

    def api_key(self) -> Optional[str]:
        """저장에 성공했을 때만 키를 돌려준다. 취소/건너뛰기 시 None."""
        return self._saved_key

    def was_skipped(self) -> bool:
        return self._skipped

    # ---- internals ----

    @staticmethod
    def _ping_gemini(api_key: str) -> tuple[bool, str]:
        """간단한 모델 조회로 키 유효성 확인."""
        try:
            from google import genai  # type: ignore

            client = genai.Client(api_key=api_key)
            # 가장 값싼 operation: list models (또는 get_model)
            models = list(client.models.list())
            return True, f"model count={len(models)}"
        except ImportError:
            return False, "google-genai 미설치"
        except Exception as exc:  # noqa: BLE001
            # key 노출 방지를 위해 메시지에 api_key 가 섞이지 않도록 타입/짧은 repr 만
            return False, f"{type(exc).__name__}"


__all__ = ["ApiKeyDialog"]
