"""변환 파이프라인을 QThread 에서 실행하는 워커.

UI 스레드가 얼지 않도록 무거운 작업(regex_parser / Gemini API 호출 / HWPX 생성 /
verify) 을 이 워커에서 돈다. 진행 상황은 :class:`Signals` 로 emit.

사용 패턴 (ConvertTab 내부)::

    from PySide6.QtCore import QThread
    from .workers.conversion_worker import ConversionWorker, ConversionRequest

    self._thread = QThread(self)
    self._worker = ConversionWorker(request)
    self._worker.moveToThread(self._thread)
    self._thread.started.connect(self._worker.run)
    self._worker.signals.progress.connect(self._on_progress)
    self._worker.signals.finished.connect(self._on_finished)
    self._worker.signals.failed.connect(self._on_failed)
    self._worker.signals.finished.connect(self._thread.quit)
    self._thread.start()
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot

from ...hwpx import md_to_hwpx, verify_hwpx
from ...parser import gemini_resolver, regex_parser
from ...parser.ir_schema import Block
from ...template.template_analyzer import analyze as analyze_template
from ...utils.logger import get_logger


_log = get_logger("gui.worker.conversion")


@dataclass
class ConversionRequest:
    """워커에 던질 요청."""

    template_path: Path
    txt_path: Path
    output_path: Path
    use_gemini: bool = False          # (legacy 명명) — 실제로는 아래 backend 로 분기
    verify_after: bool = True
    doc_type: str = "qualitative"
    run_fix_namespaces: bool = True
    ambiguous_long_threshold: int = 50
    resolver_backend: str = "gemini"  # "gemini" / "ollama" / "none"


@dataclass
class ConversionResult:
    """워커가 돌려주는 결과 요약."""

    output_path: Path
    total_blocks: int = 0
    ambiguous_before: int = 0
    gemini_report: Optional[gemini_resolver.ResolveReport] = None
    verify_report: Optional[verify_hwpx.VerifyReport] = None


class _Signals(QObject):
    """워커가 emit 하는 시그널 집합."""

    progress = Signal(str)                 # 사용자 표시용 단계 메시지
    step = Signal(int, int, str)           # (현재 단계, 총 단계, 설명)
    finished = Signal(object)              # ConversionResult
    failed = Signal(str)                   # 사용자 표시용 에러 메시지
    # v0.15.0: Self-MoA × Batch 진행 표시 — GUI 에서 heartbeat 표시용
    batch_started = Signal(int)            # draws 개수
    batch_finished = Signal(bool)          # True=성공, False=실패/폴백


class ConversionWorker(QObject):
    """파이프라인을 순차 실행하는 워커.

    외부는 :attr:`signals` 에만 연결하면 된다. 취소 기능은 MVP 스코프 밖 — 긴 작업은
    원고 분석 < 1s, Gemini 호출 2~10s, 생성 1s 정도라 사용자 체감이 크지 않다.
    """

    def __init__(self, request: ConversionRequest, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.request = request
        self.signals = _Signals()

    # ---- API ----

    @Slot()
    def run(self) -> None:
        try:
            result = self._run_pipeline()
        except FileExistsError as exc:
            self.signals.failed.emit(str(exc))
        except FileNotFoundError as exc:
            self.signals.failed.emit(f"파일을 찾을 수 없습니다: {exc}")
        except PermissionError as exc:
            self.signals.failed.emit(
                f"쓰기 권한이 없습니다: {exc.filename or exc}\n"
                "설정 탭에서 다른 저장 경로를 지정하세요."
            )
        except ValueError as exc:
            # 빈 블록, 손상된 HWPX 등 의미 있는 검증 실패
            self.signals.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - 사용자에게 어떤 에러든 친절한 메시지로 전달
            _log.exception("변환 파이프라인 실패")
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.finished.emit(result)

    # ---- internals ----

    def _emit_step(self, step: int, total: int, text: str) -> None:
        self.signals.step.emit(step, total, text)
        self.signals.progress.emit(text)

    def _run_pipeline(self) -> ConversionResult:
        req = self.request
        total_steps = 5 if req.verify_after else 4

        # 1/N: 원고 파싱
        self._emit_step(1, total_steps, "원고 분석 중...")
        blocks = regex_parser.parse_file(
            req.txt_path,
            ambiguous_long_threshold=req.ambiguous_long_threshold,
        )
        if not blocks:
            raise ValueError(
                "원고에서 IR 블록을 하나도 얻지 못했습니다 — "
                "파일이 비어있거나 인식 가능한 기호가 없는지 확인하세요."
            )
        amb_before = len(regex_parser.ambiguous_blocks(blocks))
        self.signals.progress.emit(
            f"  → {len(blocks)} 블록, 애매 {amb_before}"
        )

        # 2/N: LLM 해석 (선택) — Gemini / Ollama / 비활성
        gemini_report: Optional[gemini_resolver.ResolveReport] = None
        backend_label = {"gemini": "Gemini", "ollama": "Ollama"}.get(
            req.resolver_backend, req.resolver_backend.title()
        )
        if req.use_gemini and req.resolver_backend != "none" and amb_before > 0:
            self._emit_step(
                2, total_steps, f"{backend_label} 해석 중 (애매 {amb_before}개)..."
            )
            try:
                client = gemini_resolver.create_default_client(
                    backend=req.resolver_backend
                )
                # v0.15.0: Self-MoA × Batch 경로 감지 → GUI 에 heartbeat signal
                is_batch_moa = bool(
                    getattr(client, "use_batch", False)
                    and getattr(client, "draws", 0) >= 2
                )
                if is_batch_moa:
                    self.signals.progress.emit(
                        f"  🔄 Self-MoA × Batch 모드 — N={client.draws} draws 배치 제출 (수 분~수 시간 소요)"
                    )
                    self.signals.batch_started.emit(int(client.draws))
                try:
                    gemini_report = gemini_resolver.resolve(blocks, client=client)
                finally:
                    if is_batch_moa:
                        self.signals.batch_finished.emit(True)
                self.signals.progress.emit(f"  → {gemini_report.human_summary()}")
            except Exception as exc:  # noqa: BLE001 - LLM 실패는 부드럽게 fallback
                self.signals.progress.emit(
                    f"  ⚠️ {backend_label} 해석 실패 ({type(exc).__name__}) — 결정론 결과로 진행"
                )
        elif req.use_gemini and amb_before == 0:
            self._emit_step(2, total_steps, "LLM 해석 건너뜀 (애매 블록 없음)")
        else:
            self._emit_step(2, total_steps, "LLM 해석 건너뜀 (비활성)")

        # 3/N: 템플릿 분석
        self._emit_step(3, total_steps, "템플릿 스타일 분석 중...")
        style_map_obj = analyze_template(req.template_path)
        style_map = style_map_obj.to_engine_style_dict()
        if style_map_obj.fallback_used_levels:
            self.signals.progress.emit(
                f"  ℹ️ 일부 레벨 fallback 사용: {sorted(style_map_obj.fallback_used_levels)}"
            )

        # 4/N: 변환
        self._emit_step(4, total_steps, "HWPX 생성 중...")
        md_to_hwpx.convert(
            blocks,
            template=req.template_path,
            output=req.output_path,
            style_map=style_map,
            run_fix_namespaces=req.run_fix_namespaces,
        )
        self.signals.progress.emit(f"  → {req.output_path.name} 생성 완료")

        # 5/N: 검증 (선택)
        verify_report: Optional[verify_hwpx.VerifyReport] = None
        if req.verify_after:
            self._emit_step(5, total_steps, "결과 HWPX 검증 중...")
            verify_report = verify_hwpx.verify(req.output_path, doc_type=req.doc_type)
            struct_passed = sum(
                1 for c in verify_report.checks
                if c.category in ("common", "advanced") and c.passed
            )
            struct_total = sum(
                1 for c in verify_report.checks if c.category in ("common", "advanced")
            )
            self.signals.progress.emit(
                f"  → 구조 검증 {struct_passed}/{struct_total} 통과 "
                f"(전체 {verify_report.passed}/{verify_report.total}, {verify_report.rate:.0f}%)"
            )

        return ConversionResult(
            output_path=req.output_path,
            total_blocks=len(blocks),
            ambiguous_before=amb_before,
            gemini_report=gemini_report,
            verify_report=verify_report,
        )


__all__ = ["ConversionRequest", "ConversionResult", "ConversionWorker"]
