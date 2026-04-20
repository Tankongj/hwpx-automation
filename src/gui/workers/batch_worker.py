"""Gemini Batch API 비동기 실행용 QThread 워커 — v0.13.0.

Batch API 는 수 분~수 시간 폴링이 필요하므로 UI thread 에서 돌리면 앱이 얼어붙는다.
이 워커는 :class:`~src.parser.gemini_batch.GeminiBatchClient.submit_and_wait` 호출을
별도 스레드에서 수행하고, 진행 상황을 Qt signal 로 통지.

사용 패턴::

    worker = GeminiBatchWorker(requests, api_key=..., model=...)
    worker.progress.connect(dialog.on_progress)
    worker.finished.connect(dialog.on_finished)
    worker.failed.connect(dialog.on_failed)
    worker.start()

GUI 쪽은 :class:`src.gui.widgets.batch_progress_dialog.BatchProgressDialog` 참고.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QThread, Signal

from ...parser.gemini_batch import (
    BatchRequest,
    BatchResult,
    GeminiBatchClient,
)
from ...utils.logger import get_logger


_log = get_logger("gui.batch_worker")


class GeminiBatchWorker(QThread):
    """Batch API 폴링을 별도 스레드에서. Signal 로 결과/오류 통지."""

    # 폴링 중 상태 — (state_name, elapsed_sec)
    progress = Signal(str, float)
    # 성공 시 BatchResult
    finished_ok = Signal(object)
    # 실패 시 (error_msg, BatchResult)
    failed = Signal(str, object)

    def __init__(
        self,
        requests: list[BatchRequest],
        *,
        api_key: str,
        model: str = "gemini-2.5-flash",
        poll_sec: int = 60,
        timeout_sec: int = 30 * 60,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._requests = list(requests)
        self._api_key = api_key
        self._model = model
        self._poll_sec = max(5, int(poll_sec))
        self._timeout_sec = max(60, int(timeout_sec))
        self._result: Optional[BatchResult] = None

    # ---- QThread entry ----

    def run(self) -> None:
        try:
            client = GeminiBatchClient(
                api_key=self._api_key,
                model=self._model,
                poll_sec=self._poll_sec,
            )
            result = client.submit_and_wait(
                self._requests,
                timeout_sec=self._timeout_sec,
                on_poll=self._on_poll,
            )
        except Exception as exc:  # noqa: BLE001
            _log.exception("Batch worker 예외")
            self._result = BatchResult(state="ERROR", error=f"{type(exc).__name__}: {exc}")
            self.failed.emit(self._result.error, self._result)
            return

        self._result = result
        if result.state == "SUCCEEDED":
            self.finished_ok.emit(result)
        else:
            self.failed.emit(result.error or f"상태 {result.state}", result)

    def _on_poll(self, state_name: str, elapsed: float) -> None:
        try:
            self.progress.emit(state_name, float(elapsed))
        except Exception:  # noqa: BLE001 - Qt 가 스레드 상태로 튕길 수 있음
            pass

    # ---- public ----

    @property
    def result(self) -> Optional[BatchResult]:
        return self._result


__all__ = ["GeminiBatchWorker"]
