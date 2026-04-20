"""PDF 에서 발행일 추출 (v0.8.0).

2 단 전략:
1. **pdfplumber** 로 텍스트 추출 후 정규식 매칭 (디지털 PDF — 대부분)
2. **Tesseract OCR** 폴백 (스캔/이미지 PDF — pytesseract + tesseract 설치 필요)

Tesseract 는 **선택 의존성**. 없으면 2단계는 스킵하고 1단계만 시도. 디지털 PDF 는 어차피
Tesseract 없이도 동작.

발행일 패턴 (한국식 + ISO)::
    2026년 3월 15일 / 2026. 3. 15. / 2026-03-15 / 2026.03.15 / 20260315
    (처음 발견된 것 채택)
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional, Union

from ..utils.logger import get_logger


_log = get_logger("checklist.pdf_date")


PathLike = Union[str, Path]


# 발행일 정규식 패턴 — 문서 내 흔한 표기를 커버
_DATE_PATTERNS: list[re.Pattern[str]] = [
    # "2026년 3월 15일" / "2026 년 3 월 15 일"
    re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"),
    # "2026. 3. 15." / "2026.03.15"
    re.compile(r"(20\d{2})\s*[.\-]\s*(\d{1,2})\s*[.\-]\s*(\d{1,2})\.?"),
    # "2026-03-15"
    re.compile(r"(20\d{2})-(\d{2})-(\d{2})"),
    # "20260315"
    re.compile(r"(20\d{2})(\d{2})(\d{2})"),
]


# 발행일 추출 시 우선순위 키워드 (이 키워드 근처의 날짜가 진짜 발행일일 가능성 ↑)
_ISSUE_KEYWORDS = ("발행일", "발급일", "등록일", "작성일", "조회일")
_PROXIMITY_WINDOW = 80   # 키워드 뒤 80자 이내에서 찾기


@dataclass
class PdfDateResult:
    issued_date: Optional[date]
    source: str = "unknown"      # "text" / "ocr" / "filename" / "unknown"
    error: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _match_first_date(text: str) -> Optional[date]:
    """텍스트에서 첫 날짜 매치 반환."""
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        try:
            y, mo, d = map(int, m.groups())
            return date(y, mo, d)
        except (ValueError, TypeError):
            continue
    return None


def _find_date_near_keyword(text: str) -> Optional[date]:
    """``발행일`` 등 키워드 뒤 짧은 창 안에서 날짜 매치 우선."""
    low = text
    for kw in _ISSUE_KEYWORDS:
        idx = low.find(kw)
        if idx < 0:
            continue
        window = low[idx : idx + len(kw) + _PROXIMITY_WINDOW]
        d = _match_first_date(window)
        if d:
            return d
    return None


# ---------------------------------------------------------------------------
# pdfplumber text path
# ---------------------------------------------------------------------------

def _extract_text_via_pdfplumber(pdf_path: Path, max_pages: int = 3) -> str:
    """PDF 앞 max_pages 페이지에서 텍스트 추출. 실패하면 빈 문자열."""
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        _log.debug("pdfplumber 미설치")
        return ""

    text_parts: list[str] = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for i, page in enumerate(pdf.pages):
                if i >= max_pages:
                    break
                try:
                    t = page.extract_text() or ""
                except Exception:  # noqa: BLE001
                    t = ""
                if t:
                    text_parts.append(t)
    except Exception as exc:  # noqa: BLE001
        _log.debug("pdfplumber 오류: %s", exc)
        return ""
    return "\n".join(text_parts)


# ---------------------------------------------------------------------------
# Tesseract OCR fallback
# ---------------------------------------------------------------------------

def tesseract_available() -> bool:
    """pytesseract 가 import 되고 외부 tesseract 바이너리도 있는지."""
    try:
        import pytesseract  # type: ignore
    except ImportError:
        return False
    # 바이너리 탐지 (pytesseract 는 내부 tesseract_cmd 사용)
    exe = getattr(pytesseract.pytesseract, "tesseract_cmd", None)
    if exe and shutil.which(exe) is not None:
        return True
    return shutil.which("tesseract") is not None


def _extract_text_via_tesseract(pdf_path: Path, max_pages: int = 2) -> str:
    """스캔 PDF 이미지 → Tesseract 한국어 OCR. pdf2image 필요 (선택)."""
    try:
        import pytesseract  # type: ignore
        from pdf2image import convert_from_path  # type: ignore
    except ImportError:
        _log.debug("OCR 의존성 없음 (pytesseract / pdf2image)")
        return ""

    try:
        images = convert_from_path(str(pdf_path), dpi=200, first_page=1, last_page=max_pages)
    except Exception as exc:  # noqa: BLE001
        _log.debug("pdf2image 오류: %s (poppler 설치 필요할 수 있음)", exc)
        return ""

    parts: list[str] = []
    for img in images:
        try:
            t = pytesseract.image_to_string(img, lang="kor+eng")
        except Exception as exc:  # noqa: BLE001
            _log.debug("tesseract 오류: %s", exc)
            continue
        if t:
            parts.append(t)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_issued_date(
    pdf_path: PathLike,
    *,
    allow_ocr: bool = True,
) -> PdfDateResult:
    """PDF → 발행일 추출 결과.

    Parameters
    ----------
    allow_ocr : False 면 텍스트 경로만 시도 (Tesseract 스킵)

    Returns
    -------
    PdfDateResult : issued_date 가 None 이면 실패
    """
    path = Path(pdf_path)
    if not path.exists():
        return PdfDateResult(None, error=f"파일 없음: {path}")
    if path.suffix.lower() != ".pdf":
        return PdfDateResult(None, error=f"PDF 가 아닙니다: {path.suffix}")

    # 1차: 텍스트 추출
    text = _extract_text_via_pdfplumber(path)
    if text:
        d = _find_date_near_keyword(text) or _match_first_date(text)
        if d:
            return PdfDateResult(d, source="text")

    # 2차: OCR (옵션)
    if allow_ocr and tesseract_available():
        ocr_text = _extract_text_via_tesseract(path)
        if ocr_text:
            d = _find_date_near_keyword(ocr_text) or _match_first_date(ocr_text)
            if d:
                return PdfDateResult(d, source="ocr")

    return PdfDateResult(None, source="unknown", error="발행일 패턴 미발견")


__all__ = [
    "PdfDateResult",
    "extract_issued_date",
    "tesseract_available",
]
