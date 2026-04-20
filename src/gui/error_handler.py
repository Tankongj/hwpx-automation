"""전역 예외 핸들러.

:func:`sys.excepthook` 을 가로채 uncaught exception 이 앱을 조용히 죽이지 않도록.
대신 traceback 이 담긴 :class:`QMessageBox` 를 띄우고 클립보드 복사 버튼을 제공한다.

::

    from src.gui.error_handler import install_global_handler
    install_global_handler()

``QApplication`` 이 없는 컨텍스트(테스트/CLI)에서는 설치가 no-op 이다.
"""
from __future__ import annotations

import sys
import traceback
from types import TracebackType
from typing import Optional

from ..utils.logger import get_logger


_log = get_logger("gui.error_handler")

_previous_hook = None
_installed = False


def _format_exc(exc_type, exc_value, exc_tb) -> str:
    return "".join(traceback.format_exception(exc_type, exc_value, exc_tb)).strip()


def _handle(exc_type, exc_value, exc_tb) -> None:
    """uncaught exception 진입점."""
    # 이전 훅에도 전달 (로그/IDE 호환)
    if _previous_hook is not None:
        try:
            _previous_hook(exc_type, exc_value, exc_tb)
        except Exception:  # noqa: BLE001
            pass

    # KeyboardInterrupt 는 조용히
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    formatted = _format_exc(exc_type, exc_value, exc_tb)
    _log.error("uncaught exception:\n%s", formatted)

    # Qt 가 살아 있으면 다이얼로그 표시
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance()
        if app is None:
            return

        short = f"{exc_type.__name__}: {exc_value}"
        dlg = QMessageBox()
        dlg.setIcon(QMessageBox.Icon.Critical)
        dlg.setWindowTitle("예기치 못한 오류")
        dlg.setText("앱에서 처리되지 않은 오류가 발생했습니다.")
        dlg.setInformativeText(short)
        dlg.setDetailedText(formatted)
        dlg.setStandardButtons(
            QMessageBox.StandardButton.Ok
        )
        # 클립보드 복사 버튼 추가 (traceback 전체)
        copy_btn = dlg.addButton("상세 복사", QMessageBox.ButtonRole.ActionRole)
        dlg.exec()
        # ActionRole 버튼은 창을 닫지 않고 누르자마자 실행되므로 clicked 확인
        if dlg.clickedButton() is copy_btn:
            try:
                QApplication.clipboard().setText(formatted)
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        # 에러 핸들러가 에러를 내면 안 된다
        _log.error("error_handler 에서 2차 예외: %s", exc)


def install_global_handler() -> None:
    """앱 시작 시 한 번 호출. 중복 호출은 무시."""
    global _previous_hook, _installed
    if _installed:
        return
    _previous_hook = sys.excepthook
    sys.excepthook = _handle
    _installed = True
    _log.debug("글로벌 예외 핸들러 설치됨")


def uninstall() -> None:
    """테스트/정리용."""
    global _previous_hook, _installed
    if _installed and _previous_hook is not None:
        sys.excepthook = _previous_hook
        _previous_hook = None
        _installed = False


__all__ = ["install_global_handler", "uninstall"]
