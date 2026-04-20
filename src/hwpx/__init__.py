"""HWPX 엔진: v1(Tankongj/hwpx-proposal-automation) 유래 XML 조작 로직.

공개 API (함수형):

- :func:`fix_namespaces.fix_hwpx` — lxml ns0:/ns1: prefix 제거 등 후처리
- :func:`md_to_hwpx.convert_markdown` — 마크다운(또는 추후 IR)을 HWPX로 변환
- :func:`verify_hwpx.verify` — 구조/스타일/계층 검증

기존 CLI 진입점(`python -m src.hwpx.md_to_hwpx ...`)도 유지됩니다.
"""

from . import fix_namespaces, md_to_hwpx, verify_hwpx, visualize

__all__ = ["fix_namespaces", "md_to_hwpx", "verify_hwpx", "visualize"]
