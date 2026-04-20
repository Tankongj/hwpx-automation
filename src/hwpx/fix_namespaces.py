"""HWPX namespace post-processor.

v1(Tankongj/hwpx-proposal-automation) 의 `scripts/fix_namespaces.py` 를 v2 로
포팅한 것. lxml 직렬화가 삽입하는 ``ns0:`` / ``ns1:`` prefix 와 이중 escape 된
XML entity 를 제거하고 XML 선언을 복원한다. 이 후처리를 거치지 않으면 한/글에서
빈 페이지로 표시될 수 있다.

Public API
----------
- :func:`fix_hwpx(hwpx_path, fix_tables=False)` — 파일을 *in-place* 로 수정
- :func:`fix_xml_declaration` / :func:`fix_namespace_prefixes` / :func:`fix_entity_corruption` /
  :func:`fix_table_pagebreak` — 문자열 단위 transform

CLI 사용(그대로 유지):
    python -m src.hwpx.fix_namespaces output.hwpx
    python -m src.hwpx.fix_namespaces output.hwpx --fix-tables
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Union


PathLike = Union[str, Path]


# ---------------------------------------------------------------------------
# String-level transforms (pure)
# ---------------------------------------------------------------------------

def fix_xml_declaration(content: str) -> str:
    """XML 선언이 누락되었으면 UTF-8 선언을 맨 앞에 붙인다."""
    if not content.strip().startswith("<?xml"):
        content = '<?xml version="1.0" encoding="UTF-8"?>\n' + content
    return content


def fix_namespace_prefixes(content: str) -> str:
    """lxml 이 삽입한 ``ns0:`` / ``ns1:`` prefix 를 제거한다.

    한/글은 prefix 가 붙어 있으면 파싱 실패 → 빈 페이지로 렌더링된다.
    """
    # <ns0:p ...> → <p ...>
    content = re.sub(r"<ns\d+:", "<", content)
    # </ns0:p> → </p>
    content = re.sub(r"</ns\d+:", "</", content)
    # ns0:paraPrIDRef → paraPrIDRef
    content = re.sub(r"\bns\d+:", "", content)
    # xmlns:ns0="..." 선언 제거
    content = re.sub(r'\s+xmlns:ns\d+="[^"]*"', "", content)
    return content


def fix_entity_corruption(content: str) -> str:
    """이중 escape 된 XML entity 를 원상복구."""
    content = content.replace("&amp;amp;", "&amp;")
    content = content.replace("&amp;lt;", "&lt;")
    content = content.replace("&amp;gt;", "&gt;")
    content = content.replace("&amp;quot;", "&quot;")
    content = content.replace("&amp;apos;", "&apos;")
    return content


def fix_table_pagebreak(content: str) -> str:
    """표 페이지 넘김 조건을 맞추도록 속성 조정.

    한/글에서 표가 페이지 넘김되려면 세 조건이 동시에 만족돼야 한다:
        1. ``tbl.textWrap="SQUARE"``
        2. ``tbl.pageBreak="TABLE"``
        3. ``pos.treatAsChar="0"``  ← 가장 자주 빠지는 조건
    """
    content = re.sub(r'treatAsChar="1"', 'treatAsChar="0"', content)
    content = re.sub(r'horzRelTo="COLUMN"', 'horzRelTo="PARA"', content)
    # linesegarray 의 비정상적으로 큰 vertsize → 1200 으로 리셋 (한/글 재계산 유도)
    content = re.sub(r'vertsize="(\d{5,})"', 'vertsize="1200"', content)
    return content


# ---------------------------------------------------------------------------
# File-level entry
# ---------------------------------------------------------------------------

def fix_hwpx(hwpx_path: PathLike, fix_tables: bool = False) -> dict:
    """HWPX 파일을 *in-place* 로 후처리.

    Returns
    -------
    dict
        ``{"modified_files": int, "ns_fixed": bool, "tables_fixed": bool}``
    """
    src = Path(hwpx_path)
    if not src.exists():
        raise FileNotFoundError(str(src))

    tmp = Path(tempfile.mktemp(suffix=".hwpx"))
    fixed_count = 0
    ns_fixed = False
    tables_fixed = False

    with zipfile.ZipFile(src, "r") as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)

            if item.filename.endswith(".xml"):
                text = data.decode("utf-8")
                original = text

                text = fix_xml_declaration(text)
                text = fix_namespace_prefixes(text)
                text = fix_entity_corruption(text)

                if text != original:
                    ns_fixed = True

                # section XML 에만 표 페이지 넘김 보정을 적용
                if fix_tables and item.filename.startswith("Contents/section"):
                    before = text
                    text = fix_table_pagebreak(text)
                    if text != before:
                        tables_fixed = True

                if text != original:
                    fixed_count += 1

                data = text.encode("utf-8")

            zout.writestr(item, data)

    shutil.move(str(tmp), str(src))

    return {
        "modified_files": fixed_count,
        "ns_fixed": ns_fixed,
        "tables_fixed": tables_fixed,
    }


# Back-compat alias — v1 호환
fix_hwpx_namespaces = fix_hwpx


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fix HWPX XML namespaces after text replacement or lxml serialization",
    )
    parser.add_argument("hwpx_file", help="HWPX file to fix (modified in-place)")
    parser.add_argument(
        "--fix-tables",
        action="store_true",
        help="Also fix table page-breaking (treatAsChar=0, vertsize reset)",
    )
    args = parser.parse_args(argv)

    try:
        result = fix_hwpx(args.hwpx_file, fix_tables=args.fix_tables)
    except FileNotFoundError as exc:
        print(f"Error: File not found: {exc}", file=sys.stderr)
        return 1

    print(
        f"[OK] Namespace fix complete: {args.hwpx_file} "
        f"({result['modified_files']} XML files modified)"
    )
    if result["ns_fixed"]:
        print("[OK] Namespace prefixes (ns0:/ns1:) removed.")
    if args.fix_tables and result["tables_fixed"]:
        print("[OK] Table page-break fix applied (treatAsChar=0, vertsize reset).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
