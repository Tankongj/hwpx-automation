"""HWPX 템플릿 썸네일 추출 (v0.9.0 Track 2-C).

HWPX ZIP 에는 **``Preview/PrvImage.png``** 라는 썸네일 PNG 가 번들돼 있다 (한/글이 저장할 때
첫 페이지를 자동 렌더). 이걸 그대로 꺼내면 TemplateTab 상세 뷰에 썸네일 표시 가능.

PNG 가 없는 경우(오래된 HWPX) 는 ``None`` 반환 → 호출자가 플레이스홀더 표시.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Optional, Union


PathLike = Union[str, Path]
PREVIEW_ENTRY = "Preview/PrvImage.png"


def extract_thumbnail_bytes(hwpx_path: PathLike) -> Optional[bytes]:
    """HWPX 에서 ``Preview/PrvImage.png`` 원본 바이트 반환. 없으면 ``None``."""
    path = Path(hwpx_path)
    if not path.exists() or path.suffix.lower() != ".hwpx":
        return None
    try:
        with zipfile.ZipFile(path, "r") as z:
            if PREVIEW_ENTRY not in z.namelist():
                return None
            return z.read(PREVIEW_ENTRY)
    except (zipfile.BadZipFile, OSError):
        return None


def has_thumbnail(hwpx_path: PathLike) -> bool:
    return extract_thumbnail_bytes(hwpx_path) is not None


__all__ = ["extract_thumbnail_bytes", "has_thumbnail", "PREVIEW_ENTRY"]
