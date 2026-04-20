"""QApplication 부팅 헬퍼.

:mod:`src.main` 에서 호출되며, 테스트 시 ``create_app()`` 만 따로 써서
:class:`~src.gui.main_window.MainWindow` 를 띄울 수도 있다.
"""
from __future__ import annotations

import sys
from typing import Optional


def create_app(argv: Optional[list[str]] = None):
    """QApplication 싱글턴을 만들어 반환."""
    from PySide6.QtWidgets import QApplication  # deferred import — no Qt, still CLI-safe

    app = QApplication.instance()
    if app is None:
        app = QApplication(argv if argv is not None else sys.argv)
    # 한글 UI 기본 설정
    app.setApplicationName("HWPX Automation")
    app.setOrganizationName("HwpxAutomation")
    app.setApplicationDisplayName("HWPX Automation v2")
    return app


def run(argv: Optional[list[str]] = None) -> int:
    """진짜 실행(이벤트 루프 진입). :func:`src.main.main` 에서 부른다."""
    from .gui.error_handler import install_global_handler
    from .gui.main_window import MainWindow

    app = create_app(argv)
    install_global_handler()
    window = MainWindow()
    window.show()
    return app.exec()
