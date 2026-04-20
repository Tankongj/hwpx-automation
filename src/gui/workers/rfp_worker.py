"""RFP 추출 워커 — Gemini 네트워크 호출이라 UI 스레드에서 분리."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot

from ...checklist.models import RequiredDocument
from ...checklist.rfp_extractor import extract_from_rfp
from ...utils.logger import get_logger


_log = get_logger("gui.worker.rfp")


@dataclass
class RfpExtractRequest:
    rfp_path: Path
    model: str = "gemini-2.5-flash"


@dataclass
class RfpExtractResult:
    documents: list[RequiredDocument] = field(default_factory=list)


class _Signals(QObject):
    progress = Signal(str)
    finished = Signal(object)     # RfpExtractResult
    failed = Signal(str)


class RfpExtractWorker(QObject):
    """Gemini 로 RFP → 필수 서류 목록 추출."""

    def __init__(self, request: RfpExtractRequest, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.request = request
        self.signals = _Signals()

    @Slot()
    def run(self) -> None:
        req = self.request
        self.signals.progress.emit(f"{req.rfp_path.name} 분석 중...")
        try:
            docs = extract_from_rfp(req.rfp_path, model=req.model)
        except FileNotFoundError as exc:
            self.signals.failed.emit(f"파일을 찾을 수 없습니다: {exc}")
        except ValueError as exc:
            self.signals.failed.emit(str(exc))
        except RuntimeError as exc:
            self.signals.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            _log.exception("RFP 추출 파이프라인 실패")
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.progress.emit(f"총 {len(docs)}개 서류 추출됨")
            self.signals.finished.emit(RfpExtractResult(documents=docs))


__all__ = ["RfpExtractRequest", "RfpExtractResult", "RfpExtractWorker"]
