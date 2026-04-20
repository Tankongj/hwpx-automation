"""제출서류 체크리스트 데이터 모델."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Optional


class DocumentStatus(Enum):
    OK = "ok"                  # 매칭 파일 발견 + 필수 조건 충족
    WARNING = "warning"        # 파일 발견했지만 일부 조건 미흡 (예: 발행일 오래됨)
    MISSING = "missing"        # 파일 없음
    UNKNOWN = "unknown"        # 확인 불가 (OCR 실패 등)


@dataclass
class RequiredDocument:
    """RFP 에서 요구하는 한 가지 제출서류."""

    id: str                               # "certificate_of_incorporation"
    name: str                             # "사업자등록증 사본"
    is_required: bool = True
    # 유효 조건 (v0.6.0 본편에서 확장)
    max_age_days: Optional[int] = None    # 예: 90 → 3개월 이내 발행본 필요
    filename_hints: list[str] = field(default_factory=list)
    # 예: ["사업자등록증", "business_registration"] — 파일명 매칭 패턴
    description: str = ""                 # RFP 원문에서 관련 문장 스니펫


@dataclass
class MatchedFile:
    """실제 폴더에서 찾은 파일 + 메타."""

    path: Path
    size_bytes: int = 0
    issued_date: Optional[date] = None    # 파일명 또는 OCR 로 추출한 발행일
    issued_source: str = ""               # "filename" / "ocr" / "unknown"


@dataclass
class ChecklistItem:
    """한 서류의 체크 상태."""

    doc: RequiredDocument
    matches: list[MatchedFile] = field(default_factory=list)
    status: DocumentStatus = DocumentStatus.MISSING
    warning_reason: str = ""

    @property
    def best_match(self) -> Optional[MatchedFile]:
        if not self.matches:
            return None
        # 가장 최신 발행일 우선
        dated = [m for m in self.matches if m.issued_date]
        if dated:
            return max(dated, key=lambda m: m.issued_date)  # type: ignore
        return self.matches[0]


@dataclass
class ChecklistResult:
    """전체 체크 결과. UI 표시용."""

    rfp_path: str
    folder_path: str
    items: list[ChecklistItem] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for i in self.items if i.status == DocumentStatus.OK)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.items if i.status == DocumentStatus.WARNING)

    @property
    def missing_count(self) -> int:
        return sum(1 for i in self.items if i.status == DocumentStatus.MISSING)

    @property
    def is_submittable(self) -> bool:
        """필수 서류 모두 OK 이면 제출 가능."""
        for item in self.items:
            if item.doc.is_required and item.status in (
                DocumentStatus.MISSING, DocumentStatus.UNKNOWN
            ):
                return False
        return True


__all__ = [
    "DocumentStatus",
    "RequiredDocument",
    "MatchedFile",
    "ChecklistItem",
    "ChecklistResult",
]
