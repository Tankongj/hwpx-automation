"""필수 서류 목록 + 실제 폴더 → 체크리스트 결과 매칭.

파일명 기반 1차 매치 (결정론) + OCR fallback (선택, v0.6.0 본편에서). 이 foundation 은
결정론 경로만 동작.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Union

from .filename_matcher import extract_date_from_filename, match_keywords
from .models import (
    ChecklistItem,
    ChecklistResult,
    DocumentStatus,
    MatchedFile,
    RequiredDocument,
)


PathLike = Union[str, Path]


def _scan_folder(folder: Path, *, recursive: bool = False) -> list[Path]:
    """폴더 내 파일 목록. ``recursive=True`` 면 하위 폴더까지."""
    if not folder.exists():
        return []
    if recursive:
        return [p for p in folder.rglob("*") if p.is_file()]
    return [p for p in folder.iterdir() if p.is_file()]


def _build_matched_file(path: Path, *, use_pdf_fallback: bool = True) -> MatchedFile:
    """파일 한 개 → MatchedFile. 파일명 → (필요 시) PDF 내용 순으로 발행일 탐색."""
    issued = extract_date_from_filename(path.name)
    source = "filename" if issued else "unknown"

    # v0.8.0: 파일명에서 실패하고 PDF 면 텍스트/OCR fallback 시도
    if issued is None and use_pdf_fallback and path.suffix.lower() == ".pdf":
        try:
            from .pdf_date_extractor import extract_issued_date
            result = extract_issued_date(path)
            if result.issued_date:
                issued = result.issued_date
                source = result.source
        except Exception:  # noqa: BLE001 - 추출 실패가 매칭 전체를 깨면 안 됨
            pass

    return MatchedFile(
        path=path,
        size_bytes=path.stat().st_size if path.exists() else 0,
        issued_date=issued,
        issued_source=source,
    )


def build_checklist(
    required_docs: Iterable[RequiredDocument],
    folder: PathLike,
    *,
    today: date | None = None,
    recursive: bool = False,
) -> ChecklistResult:
    """필수 서류 목록 + 사용자 폴더 → :class:`ChecklistResult`.

    각 서류마다:
    1. 폴더에서 키워드 매칭 파일 찾음 (``recursive=True`` 면 하위 폴더까지)
    2. 찾았으면 파일명에서 발행일 추출
    3. ``max_age_days`` 조건 있으면 오늘 - 발행일 비교
    4. 상태: OK / WARNING / MISSING
    """
    folder_path = Path(folder)
    today = today or date.today()
    files = _scan_folder(folder_path, recursive=recursive)

    items: list[ChecklistItem] = []
    for doc in required_docs:
        matches = [
            _build_matched_file(f)
            for f in files
            if match_keywords(f.name, doc.filename_hints)
        ]
        item = ChecklistItem(doc=doc, matches=matches)

        if not matches:
            item.status = DocumentStatus.MISSING
        else:
            best = item.best_match
            status = DocumentStatus.OK
            warning = ""

            if doc.max_age_days is not None and best is not None:
                if best.issued_date is None:
                    # 발행일 모름 — OCR fallback 이 필요 (v0.6.0 본편)
                    status = DocumentStatus.WARNING
                    warning = f"파일명에서 발행일 추출 실패 — OCR 로 재확인 필요 ({doc.max_age_days}일 이내 요구됨)"
                else:
                    age = today - best.issued_date
                    if age > timedelta(days=doc.max_age_days):
                        status = DocumentStatus.WARNING
                        warning = (
                            f"발행일 {best.issued_date} ({age.days}일 전) — "
                            f"RFP 요구 {doc.max_age_days}일 초과"
                        )
            item.status = status
            item.warning_reason = warning

        items.append(item)

    return ChecklistResult(
        rfp_path="",
        folder_path=str(folder_path),
        items=items,
    )


__all__ = ["build_checklist"]
