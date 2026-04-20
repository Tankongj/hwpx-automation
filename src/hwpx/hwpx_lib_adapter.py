"""`python-hwpx` 라이브러리 어댑터 계층 — v0.12.0.

`airmang/python-hwpx` (PyPI 2.8+) 는 pure-Python HWPX 조작 라이브러리.
우리가 수동으로 작성한 lxml + ZIP 코드 (read 전용 ~80 LOC / write ~500 LOC) 를
단계적으로 대체.

**v0.12.0 도입 범위** (안전한 것부터):
- ✅ **읽기**: `extract_text(path)` — 기존 lxml 수동 파서의 drop-in 대체
- ✅ **검증**: `has_section(path)`, `count_paragraphs(path)` — 빠른 메타 조회
- 🔜 **쓰기**: `add_paragraph/add_table` — v0.13+ 에서 점진 전환
- 🔜 **템플릿 분석**: 스타일 맵핑 자동화 — v0.13+

**fallback 전략**: python-hwpx 가 설치돼 있지 않거나 실패하면 기존 lxml 경로 사용.
사용자 환경에서 라이브러리가 깨져도 앱은 계속 동작.

라이브러리 API 요약::

    from hwpx import HwpxDocument
    doc = HwpxDocument.open("in.hwpx")
    text = doc.export_text()
    md = doc.export_markdown()
    doc.add_paragraph("새 단락", para_pr_id_ref=3)
    doc.save_to_path("out.hwpx")
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Optional, Union

from ..utils.logger import get_logger


_log = get_logger("hwpx.lib_adapter")


PathLike = Union[str, Path]


# ---------------------------------------------------------------------------
# availability
# ---------------------------------------------------------------------------

_hwpx_available: Optional[bool] = None


def is_available() -> bool:
    """python-hwpx 라이브러리가 import 가능한지. 최초 호출 시 1 회 캐시."""
    global _hwpx_available
    if _hwpx_available is not None:
        return _hwpx_available
    try:
        import hwpx  # noqa: F401  (type: ignore)
        _hwpx_available = True
    except ImportError:
        _hwpx_available = False
    return _hwpx_available


def version() -> str:
    """python-hwpx 버전 문자열. 미설치 시 빈 문자열."""
    if not is_available():
        return ""
    try:
        import hwpx
        return getattr(hwpx, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# Text extraction — python-hwpx 경로 (빠르고 깨끗)
# ---------------------------------------------------------------------------


def extract_text(path: PathLike, *, max_len: int = 500_000) -> str:
    """HWPX 파일 전체 텍스트를 plain text 로 추출.

    python-hwpx 의 `export_text()` 를 우선 시도. 실패 / 미설치 시 `None` 반환하지 않고
    예외 (호출자가 fallback 구현).

    Parameters
    ----------
    path : HWPX 파일 경로
    max_len : 상한 (초과 시 `"... 이하 N 문자 생략"` 붙음)

    Returns
    -------
    str

    Raises
    ------
    FileNotFoundError
        파일 없음
    ImportError
        `python-hwpx` 미설치 (호출자는 이 때 lxml fallback 을 시도해야 함)
    RuntimeError
        라이브러리 내부 에러 (HWPX 구조 불량, 암호화 등)
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    if not is_available():
        raise ImportError("python-hwpx 가 설치돼 있지 않습니다.")

    try:
        # python-hwpx 는 manifest 일부 누락 시 경고 내지만 동작. 경고는 숨김.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from hwpx import HwpxDocument  # type: ignore
            doc = HwpxDocument.open(str(p))
            try:
                text = doc.export_text()
            finally:
                try:
                    doc.close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"python-hwpx 추출 실패: {type(exc).__name__}: {exc}") from exc

    if len(text) > max_len:
        text = text[:max_len] + f"\n\n[... 이하 {len(text) - max_len:,} 문자 생략]"
    return text


def extract_text_safe(path: PathLike, *, max_len: int = 500_000) -> Optional[str]:
    """python-hwpx 경로 시도 후 실패 시 None 반환 (non-raising)."""
    try:
        return extract_text(path, max_len=max_len)
    except Exception as exc:  # noqa: BLE001
        _log.debug("python-hwpx 경로 실패 → fallback 필요: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------


def count_paragraphs(path: PathLike) -> Optional[int]:
    """단락 수. 실패 시 None."""
    if not is_available():
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from hwpx import HwpxDocument  # type: ignore
            doc = HwpxDocument.open(str(path))
            try:
                n = len(doc.paragraphs)
            finally:
                try:
                    doc.close()
                except Exception:  # noqa: BLE001
                    pass
        return n
    except Exception as exc:  # noqa: BLE001
        _log.debug("count_paragraphs 실패: %s", exc)
        return None


def has_section(path: PathLike) -> Optional[bool]:
    """Section0 이상이 있는지. 파싱 실패 시 None."""
    if not is_available():
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from hwpx import HwpxDocument  # type: ignore
            doc = HwpxDocument.open(str(path))
            try:
                sections = doc.sections
            finally:
                try:
                    doc.close()
                except Exception:  # noqa: BLE001
                    pass
        return len(sections) > 0
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "is_available",
    "version",
    "extract_text",
    "extract_text_safe",
    "count_paragraphs",
    "has_section",
]
