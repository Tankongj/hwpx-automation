"""파일명 기반 날짜/키워드 매처 — 결정론 경로.

RFP 가 요구하는 서류와 사용자 폴더의 파일들을 매칭. OCR 이 필요 없는 1차 매치용.

기획안 8.2 의 패턴 테이블을 그대로 구현.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Iterable, Optional


# 파일명에서 발행일 추출 — 자주 쓰이는 한국식 네이밍 패턴
DATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?P<y>\d{4})[-_.](?P<m>\d{2})[-_.](?P<d>\d{2})"),     # 2026-03-15 / 2026.03.15 / 2026_03_15
    re.compile(r"_(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})(?=[._-]|$)"),   # _20260315
    re.compile(r"(?P<y2>\d{2})(?P<m>\d{2})(?P<d>\d{2})_"),             # 260315_
]


def extract_date_from_filename(filename: str) -> Optional[date]:
    """파일명에서 발행일을 추론. 없으면 ``None``."""
    stem = Path(filename).stem
    for pat in DATE_PATTERNS:
        m = pat.search(stem)
        if not m:
            continue
        try:
            if "y2" in m.groupdict() and m.group("y2"):
                yy = int(m.group("y2"))
                year = 2000 + yy if yy < 50 else 1900 + yy
            else:
                year = int(m.group("y"))
            month = int(m.group("m"))
            day = int(m.group("d"))
            return date(year, month, day)
        except (ValueError, TypeError):
            continue
    return None


def match_keywords(filename: str, keywords: Iterable[str]) -> bool:
    """파일명(확장자 제외) 이 키워드 중 하나라도 포함하는지. 대소문자 무시 + 공백/_ 무시."""
    stem = Path(filename).stem
    norm = stem.replace("_", "").replace(" ", "").replace("-", "").lower()
    for kw in keywords:
        kwn = kw.replace("_", "").replace(" ", "").replace("-", "").lower()
        if kwn and kwn in norm:
            return True
    return False


__all__ = ["DATE_PATTERNS", "extract_date_from_filename", "match_keywords"]
