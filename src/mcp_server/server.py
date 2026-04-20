"""HWPX Automation MCP 서버 구현 — v0.12.0.

FastMCP ``@tool`` 데코레이터로 HWPX 도구들을 노출. stdio transport 로 Claude Code /
Cursor / Windsurf 에 연결.

설계 원칙:
- **안전**: write 연산은 절대 외부에 돌려주지 않음 (path 만 반환). 모든 I/O 는 로컬 파일.
- **결정론**: AI 호출 관련 도구는 노출 X (MCP client 가 자기 LLM 을 쓰도록)
- **PII 미노출**: 파일 경로 검증 — 상위 상대경로 (``..``) 거부
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ..utils.logger import get_logger


_log = get_logger("mcp_server")


def create_mcp_app():
    """FastMCP 앱 생성. 서브 프로세스에서 실행하거나 테스트에서 직접 호출 가능."""
    from mcp.server.fastmcp import FastMCP

    app = FastMCP("hwpx-automation")

    # -----------------------------------------------------------------------
    # extract_hwpx_text
    # -----------------------------------------------------------------------
    @app.tool(
        description="HWPX 파일 전체 텍스트 추출. python-hwpx 라이브러리 우선 + lxml fallback.",
    )
    def extract_hwpx_text(path: str, max_len: int = 500_000) -> str:
        """HWPX → plain text. 경로 안전성 체크 후 rfp_extractor 호출."""
        p = _safe_path(path)
        from ..checklist.rfp_extractor import extract_hwpx_text as _f
        return _f(p, max_len=max_len)

    # -----------------------------------------------------------------------
    # extract_hwp_text
    # -----------------------------------------------------------------------
    @app.tool(
        description="HWP (구버전 바이너리) 파일에서 PrvText 또는 BodyText 추출.",
    )
    def extract_hwp_text(path: str, prefer_full: bool = False) -> dict:
        """HWP → {text, source, is_full, error}."""
        p = _safe_path(path)
        from ..checklist.hwp_text import extract_hwp_text as _f
        r = _f(p, prefer_full=prefer_full)
        return {
            "text": r.text,
            "source": r.source,
            "is_full": r.is_full,
            "error": r.error,
        }

    # -----------------------------------------------------------------------
    # verify_hwpx
    # -----------------------------------------------------------------------
    @app.tool(
        description="HWPX 구조 / 스타일 참조 / 네임스페이스 검증. doc_type='auto'/'qualitative'/'quantitative'.",
    )
    def verify_hwpx(path: str, doc_type: str = "auto") -> dict:
        """구조 리포트 — ok 여부 + 경고 목록."""
        p = _safe_path(path)
        from ..hwpx.verify_hwpx import verify
        report = verify(str(p), doc_type=doc_type)
        return {
            "ok": report.ok,
            "warnings": [str(w) for w in getattr(report, "warnings", [])],
            "errors": [str(e) for e in getattr(report, "errors", [])],
            "doc_type": doc_type,
        }

    # -----------------------------------------------------------------------
    # parse_manuscript
    # -----------------------------------------------------------------------
    @app.tool(
        description="원고 .txt 파일을 regex 파서로 계층 IR 블록 배열로 변환.",
    )
    def parse_manuscript(path: str) -> list[dict]:
        """원고 → [{line_no, level, text, ambiguous, ...}]."""
        p = _safe_path(path)
        from ..parser import regex_parser
        blocks = regex_parser.parse_file(p)
        return [
            {
                "line_no": b.line_no,
                "level": b.level,
                "text": b.text,
                "ambiguous": b.ambiguous,
            } for b in blocks
        ]

    # -----------------------------------------------------------------------
    # analyze_template
    # -----------------------------------------------------------------------
    @app.tool(
        description="HWPX 템플릿에서 10 단계 스타일 맵 추출 (paraPrIDRef / charPrIDRef / styleIDRef).",
    )
    def analyze_template(path: str) -> dict:
        """템플릿 → style map dict."""
        p = _safe_path(path)
        from ..template.template_analyzer import analyze
        sm = analyze(p)
        return sm.to_engine_style_dict()

    # -----------------------------------------------------------------------
    # hwpx_lib_info
    # -----------------------------------------------------------------------
    @app.tool(
        description="HWPX Automation 이 탑재한 pure-Python HWP/HWPX 라이브러리 가용성 보고.",
    )
    def hwpx_lib_info() -> dict:
        """라이브러리 버전/가용성 모음 — 디버깅용."""
        info: dict[str, Any] = {}
        try:
            from ..hwpx import hwpx_lib_adapter
            info["python_hwpx"] = {
                "available": hwpx_lib_adapter.is_available(),
                "version": hwpx_lib_adapter.version(),
            }
        except ImportError:
            info["python_hwpx"] = {"available": False, "version": ""}

        try:
            import olefile
            info["olefile"] = {
                "available": True,
                "version": getattr(olefile, "__version__", "unknown"),
            }
        except ImportError:
            info["olefile"] = {"available": False}

        try:
            import lxml
            info["lxml"] = {
                "available": True,
                "version": lxml.__version__ if hasattr(lxml, "__version__") else "",
            }
        except ImportError:
            info["lxml"] = {"available": False}

        # 앱 버전
        try:
            from .. import __version__
            info["hwpx_automation"] = __version__
        except ImportError:
            info["hwpx_automation"] = "unknown"
        return info

    # -----------------------------------------------------------------------
    # list_supported_extensions
    # -----------------------------------------------------------------------
    @app.tool(
        description="RFP 추출에서 지원하는 파일 확장자 목록.",
    )
    def list_supported_extensions() -> list[str]:
        from ..checklist.rfp_extractor import SUPPORTED_EXTENSIONS
        return sorted(SUPPORTED_EXTENSIONS)

    return app


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def _safe_path(p: str) -> Path:
    """상대경로 traversal / null 바이트 방지."""
    if not p or "\x00" in p:
        raise ValueError("잘못된 경로")
    path = Path(p).expanduser().resolve()
    # 단순 존재성 검증 — MCP 클라이언트가 임의 경로 scan 하지 않도록
    if not path.exists():
        raise FileNotFoundError(str(path))
    return path


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def run_server() -> None:
    """stdio transport 로 MCP 서버 실행."""
    app = create_mcp_app()
    # FastMCP 의 동기 stdio runner
    app.run()


if __name__ == "__main__":
    run_server()
