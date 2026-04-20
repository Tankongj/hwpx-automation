"""HWPX Automation MCP 서버 — v0.12.0.

Claude Code / Cursor / Windsurf 에서 우리 프로젝트의 기능을 **도구** 로 호출할 수 있게
한다. FastMCP 기반 stdio transport.

노출 도구:
- `extract_hwpx_text(path)` — HWPX 파일 → plain text
- `extract_hwp_text(path, prefer_full=False)` — HWP (구버전) 텍스트
- `verify_hwpx(path, doc_type)` — HWPX 구조 검증 (verify_hwpx)
- `parse_manuscript(txt_path)` — 원고 → IR block 배열
- `build_checklist(rfp_path, folder_path)` — RFP + 폴더 → 제출 체크리스트
- `analyze_template(hwpx_path)` — 템플릿 스타일 맵 추출
- `hwpx_lib_info()` — python-hwpx / olefile 등 라이브러리 가용성
- `sort_attachments(folder, docs, output)` — 첨부 정렬

사용:
    python -m src.mcp_server
    # 또는
    from src.mcp_server import run_server
    run_server()

Claude Desktop / Code 설정 (예시)::

    {
      "mcpServers": {
        "hwpx-automation": {
          "command": "python",
          "args": ["-m", "src.mcp_server"],
          "cwd": "D:/03_antigravity/25_hwpx automation v2"
        }
      }
    }
"""
from .server import run_server, create_mcp_app

__all__ = ["run_server", "create_mcp_app"]
