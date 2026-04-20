"""AI 기본법 (인공지능 발전·신뢰 기반 조성 기본법) 2026-01-22 시행 대응.

**법적 의무** (제31조 / 제38조):
- 생성형 AI 로 만든 콘텐츠 (문서/이미지/음성) 는 "AI 가 생성했다는 사실" 을 표시해야 함
- 미준수 시 과태료 3,000만원 이하
- "고영향 AI" 가 아니므로 투명성 의무만 이행하면 OK (영향평가 불필요)

이 프로젝트 적용 방식:
1. **파일 메타데이터** — HWPX 의 `content.hpf` (OPF package metadata) 에 dc:description 으로 기록
2. **UI 표시** — 변환 탭에 "Gemini 사용 중" 뱃지, about 다이얼로그에 준수 안내
3. **보고서 푸터** — 체크리스트 보고서에 1줄 고지
4. **텔레메트리** — AI 호출 이벤트 기록 (사용자 opt-in 시)

공식 문서:
- 법 원문: https://www.law.go.kr/법령/인공지능발전과신뢰기반조성등에관한기본법
- 시행일: 2026-01-22 (2025-12-24 공포)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..utils.logger import get_logger


_log = get_logger("commerce.ai_disclosure")


# ---------------------------------------------------------------------------
# 표시 문구 (법에서 요구하는 "AI 가 만들었다는 사실" — 한국어 원문)
# ---------------------------------------------------------------------------

DISCLOSURE_UI_BADGE = "🤖 AI 지원"
DISCLOSURE_UI_TOOLTIP = (
    "이 문서는 Google Gemini / Anthropic Claude 등 생성형 AI 의 도움을 받아 만들어졌습니다.\n"
    "(AI 기본법 2026-01-22 시행 준수)"
)
DISCLOSURE_FILE_META = (
    "본 문서는 HWPX Automation v2 ({version}) 을 통해 "
    "생성형 AI ({backend}) 의 도움을 받아 작성되었습니다. "
    "생성 시각: {timestamp}. "
    "AI 기본법 (2026-01-22 시행) 준수 표시."
)
DISCLOSURE_REPORT_FOOTER = (
    "-" * 60 + "\n"
    "※ 본 보고서는 생성형 AI 의 도움을 받아 작성되었습니다. "
    "(AI 기본법 2026-01-22 시행)"
)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@dataclass
class DisclosureInfo:
    """문서에 삽입할 AI 생성 고지 정보."""

    version: str
    backend: str = "Gemini"      # "Gemini" / "Ollama" / "OpenAI" / "Claude" / "None"
    timestamp: str = ""
    enabled: bool = True          # False 면 no-op (AI 안 썼을 때)

    def format_file_meta(self) -> str:
        """HWPX 메타데이터 / 파일 속성용 문장."""
        if not self.enabled:
            return ""
        return DISCLOSURE_FILE_META.format(
            version=self.version,
            backend=self.backend,
            timestamp=self.timestamp or datetime.now().isoformat(timespec="seconds"),
        )

    def format_report_footer(self) -> str:
        """사람이 읽는 보고서 하단 고지."""
        if not self.enabled:
            return ""
        return DISCLOSURE_REPORT_FOOTER


def make_disclosure(
    *,
    backend: str = "Gemini",
    version: Optional[str] = None,
    ai_used: bool = True,
) -> DisclosureInfo:
    """현재 빌드 기준 DisclosureInfo 생성.

    Parameters
    ----------
    backend : 어떤 AI 를 썼는지. AI 안 썼으면 ``"None"`` 전달.
    version : 앱 버전. None 이면 src.__version__ 자동 조회.
    ai_used : False 면 ``enabled=False`` — 고지 불필요.
    """
    if version is None:
        try:
            from .. import __version__ as v
            version = v
        except ImportError:
            version = "unknown"

    return DisclosureInfo(
        version=version,
        backend=backend,
        timestamp=datetime.now().isoformat(timespec="seconds"),
        enabled=bool(ai_used and backend.lower() != "none"),
    )


def is_ai_backend(backend: str) -> bool:
    """백엔드 이름이 AI 계열인지 (로컬 Ollama 는 여전히 AI 로 간주)."""
    if not backend:
        return False
    return backend.lower() not in ("none", "off", "disabled", "")


__all__ = [
    "DisclosureInfo",
    "DISCLOSURE_UI_BADGE",
    "DISCLOSURE_UI_TOOLTIP",
    "DISCLOSURE_FILE_META",
    "DISCLOSURE_REPORT_FOOTER",
    "make_disclosure",
    "is_ai_backend",
]
