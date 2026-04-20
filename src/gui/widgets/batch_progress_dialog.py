"""Gemini Batch API 프로그레스 다이얼로그 — v0.13.0.

QProgressDialog 기반 — 상태 라벨 + 경과 시간 + 취소 버튼. Worker 가
``progress(state, elapsed)`` / ``finished_ok(result)`` / ``failed(msg, result)``
시그널을 보내면 갱신.

사용::

    from src.parser.gemini_batch import BatchRequest
    from src.gui.workers.batch_worker import GeminiBatchWorker
    from src.gui.widgets.batch_progress_dialog import BatchProgressDialog

    reqs = [BatchRequest(key=f"d{i}", prompt=pr) for i, pr in enumerate(prompts)]
    worker = GeminiBatchWorker(reqs, api_key=k, model=m)
    dialog = BatchProgressDialog(worker, parent=main_window)
    if dialog.exec() == dialog.Accepted:
        # 성공
        result = worker.result
    else:
        # 취소 또는 실패 — result.error 확인
        result = worker.result
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QProgressDialog, QWidget

from ..workers.batch_worker import GeminiBatchWorker
from ...utils.logger import get_logger


_log = get_logger("gui.batch_dialog")


class BatchProgressDialog(QProgressDialog):
    """Batch 작업 모달 다이얼로그. 취소 누르면 worker 는 background 에서 계속
    동작하지만 UI 는 닫힘. 취소 후 결과는 ``worker.result`` 로 확인.
    """

    def __init__(
        self,
        worker: GeminiBatchWorker,
        *,
        parent: Optional[QWidget] = None,
        title: str = "Gemini Batch 처리 중",
    ) -> None:
        super().__init__(parent)
        self._worker = worker
        self._elapsed = 0.0
        self._state_text = "대기 중"
        self._finished = False

        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumWidth(380)
        self.setAutoReset(False)
        self.setAutoClose(False)
        self.setRange(0, 0)  # indeterminate progress bar
        self.setLabelText(self._format_label())
        self.setCancelButtonText("백그라운드로 전환")

        # 주기적 UI 갱신 (elapsed 는 worker 에서 오지만 라벨은 이 타이머가 업데이트)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        worker.progress.connect(self._on_progress)
        worker.finished_ok.connect(self._on_ok)
        worker.failed.connect(self._on_failed)
        self.canceled.connect(self._on_cancel)

        # worker 시작 + 타이머 시작
        worker.start()
        self._timer.start()

    # ---- slots ----

    def _on_progress(self, state: str, elapsed: float) -> None:
        self._state_text = _humanize(state)
        self._elapsed = float(elapsed)
        self.setLabelText(self._format_label())

    def _on_ok(self, _result) -> None:
        self._finished = True
        self._timer.stop()
        self.setRange(0, 1)
        self.setValue(1)
        self.setLabelText("✅ 배치 처리 완료")
        self.accept()

    def _on_failed(self, msg: str, _result) -> None:
        self._finished = True
        self._timer.stop()
        self.setLabelText(f"❌ 실패: {msg[:120]}")
        self.reject()

    def _on_cancel(self) -> None:
        # QThread 는 강제 종료하지 않음 — 폴링은 백그라운드 계속
        _log.info("사용자가 취소 — worker 는 background 에서 계속 동작")

    def _tick(self) -> None:
        """1 초마다 라벨에 경과 시간 표시 (worker 의 progress 는 poll 단위라 드문드문)."""
        if not self._finished:
            self._elapsed += 1.0
            self.setLabelText(self._format_label())

    # ---- helpers ----

    def _format_label(self) -> str:
        mm = int(self._elapsed // 60)
        ss = int(self._elapsed % 60)
        return (
            f"상태: {self._state_text}\n"
            f"경과: {mm:02d}:{ss:02d}\n"
            f"(최대 30 분 — Gemini Batch 는 보통 수 분 내 완료)"
        )


def _humanize(state: str) -> str:
    """Google Batch state enum → 사람 읽기 쉬운 문구."""
    mapping = {
        "": "대기 중",
        "BATCH_STATE_PENDING": "제출 완료, 대기 중",
        "BATCH_STATE_RUNNING": "실행 중 (Gemini 가 배치 처리)",
        "BATCH_STATE_SUCCEEDED": "완료됨",
        "BATCH_STATE_FAILED": "실패",
        "BATCH_STATE_CANCELLED": "취소됨",
        "PENDING": "제출 완료, 대기 중",
        "RUNNING": "실행 중",
        "SUCCEEDED": "완료됨",
        "FAILED": "실패",
    }
    return mapping.get(state, state or "처리 중")


__all__ = ["BatchProgressDialog"]
