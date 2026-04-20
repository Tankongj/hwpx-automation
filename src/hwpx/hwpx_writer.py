"""python-hwpx 기반 HWPX writer — v0.13.0 첫 조각.

**목표**: 기존 `hwpx.md_to_hwpx` (lxml 수동) 의 일부를 `python-hwpx` HwpxDocument API 로
대체. 전체 마이그레이션은 큰 작업이라 v0.13.0 은 **슬림한 "단락 추가" 경로** 만.

**범위 (v0.13.0)**:
- ✅ `write_paragraphs(template, blocks, output)` — 템플릿 HWPX 를 base 로 단락만 추가
- ✅ 계층별 paraPrIDRef 매핑 (style_map 이용)
- ✅ atomic save (tempfile → rename)
- 🔜 v0.14: 표/이미지/헤더·푸터, fix_namespaces 자동 적용
- 🔜 v0.15: 기존 md_to_hwpx 를 이 경로로 옮김 (flag switch)

**현재 용도**:
- RFP / checklist 결과 요약 보고서 HWPX 생성 (빠른 prototype)
- v1 엔진 경로에 비해 **의존성 줄고 실험 간편** — 기존 lxml 경로는 병렬 유지

**제약**:
- python-hwpx 가 없으면 :class:`ImportError`. 호출자가 lxml fallback 알아야
- 표/이미지/각주 등 복잡 객체는 아직 미지원 (v0.14+)
"""
from __future__ import annotations

import shutil
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

from ..utils.logger import get_logger


_log = get_logger("hwpx.writer")


PathLike = Union[str, Path]


@dataclass
class WriteBlock:
    """writer 가 이해하는 최소 블록 단위."""

    text: str
    level: int = 0              # -1(표지) / 0(본문) / 1~10(계층)
    style_id: Optional[str] = None   # 명시 style id — 없으면 level 매핑 사용


@dataclass
class WriteTable:
    """표 삽입 — v0.14.0.

    행×열 2D 리스트. 각 셀은 plain text (개행은 나중에 파싱).
    파이썬-hwpx 의 ``add_table(rows, cols)`` + 셀별 ``add_paragraph`` 로 구현.
    """

    rows: list[list[str]]        # [[row1_c1, row1_c2, ...], [row2_c1, ...], ...]
    header_row: bool = True      # True 면 첫 행을 헤더 스타일로
    width: Optional[int] = None  # HWPX 단위 (1 mm = 2834). None 이면 기본
    height: Optional[int] = None


@dataclass
class WriteReport:
    """write_paragraphs 결과 리포트."""

    output_path: Path
    paragraphs_added: int = 0
    tables_added: int = 0         # v0.14.0
    skipped: int = 0
    errors: list[str] = None   # type: ignore[assignment]

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


# ---------------------------------------------------------------------------
# level → paraPrIDRef 매핑
# ---------------------------------------------------------------------------
# style_map 은 {level: {"paraPrIDRef": "3", "charPrIDRef": "2", ...}} 형식.
# src.template.template_analyzer.StyleMap.to_engine_style_dict() 과 호환.


def _resolve_para_pr_id(level: int, style_map: Optional[dict]) -> Optional[str]:
    """level → paraPrIDRef 문자열. 못 찾으면 None (상속)."""
    if style_map is None:
        return None
    # StyleMap.to_engine_style_dict() 포맷
    entry = style_map.get(f"level_{level}") or style_map.get(str(level)) or style_map.get(level)
    if not isinstance(entry, dict):
        return None
    pid = entry.get("paraPrIDRef") or entry.get("para_pr_id_ref")
    return str(pid) if pid is not None else None


def _resolve_char_pr_id(level: int, style_map: Optional[dict]) -> Optional[str]:
    if style_map is None:
        return None
    entry = style_map.get(f"level_{level}") or style_map.get(str(level)) or style_map.get(level)
    if not isinstance(entry, dict):
        return None
    cid = entry.get("charPrIDRef") or entry.get("char_pr_id_ref")
    return str(cid) if cid is not None else None


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def write_paragraphs(
    template: PathLike,
    blocks: Sequence,  # WriteBlock | WriteTable
    output: PathLike,
    *,
    style_map: Optional[dict] = None,
    inherit_style: bool = True,
) -> WriteReport:
    """템플릿 HWPX 에 단락 배열을 추가하여 새 HWPX 로 저장.

    Parameters
    ----------
    template : 기반 HWPX (복사되어 modified → output 으로 저장)
    blocks : 추가할 단락들 (순서대로 append)
    output : 결과 HWPX 경로. 이미 있으면 ``FileExistsError``
    style_map : level → paraPrIDRef/charPrIDRef 매핑 (선택)
    inherit_style : True 면 template 의 현재 스타일을 상속. False 면 빈 단락 추가.

    Returns
    -------
    WriteReport
    """
    template = Path(template)
    output = Path(output)
    if not template.exists():
        raise FileNotFoundError(f"템플릿 없음: {template}")
    if output.exists():
        raise FileExistsError(f"출력 파일이 이미 존재: {output}")

    try:
        from hwpx import HwpxDocument  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "python-hwpx 가 설치돼 있지 않습니다. "
            "`pip install python-hwpx` 후 재시도하세요."
        ) from exc

    # atomic: tempfile 에 먼저 쓰고 rename
    tmp = output.with_suffix(output.suffix + ".tmp")
    report = WriteReport(output_path=output)

    # python-hwpx 의 manifest 경고 억제 (로그 소음 감소)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        doc = HwpxDocument.open(str(template))
        try:
            for block in blocks:
                if isinstance(block, WriteBlock):
                    try:
                        ppid = block.style_id or _resolve_para_pr_id(block.level, style_map)
                        cpid = _resolve_char_pr_id(block.level, style_map)
                        kwargs: dict = {}
                        if ppid is not None:
                            kwargs["para_pr_id_ref"] = ppid
                        if cpid is not None:
                            kwargs["char_pr_id_ref"] = cpid
                        kwargs["inherit_style"] = inherit_style
                        doc.add_paragraph(text=block.text or "", **kwargs)
                        report.paragraphs_added += 1
                    except Exception as exc:  # noqa: BLE001
                        report.errors.append(
                            f"L{block.level} '{block.text[:30]}...': {type(exc).__name__}: {exc}"
                        )
                        report.skipped += 1
                elif isinstance(block, WriteTable):
                    # v0.14.0: 표 삽입
                    try:
                        _insert_table(doc, block)
                        report.tables_added += 1
                    except Exception as exc:  # noqa: BLE001
                        report.errors.append(
                            f"Table {len(block.rows)}행: {type(exc).__name__}: {exc}"
                        )
                        report.skipped += 1
                else:
                    report.skipped += 1
                    continue

            # 원자적 저장
            doc.save_to_path(str(tmp))
        finally:
            try:
                doc.close()
            except Exception:  # noqa: BLE001
                pass

    # rename
    try:
        shutil.move(str(tmp), str(output))
    except OSError as exc:
        report.errors.append(f"rename 실패: {exc}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    _log.info(
        "write_paragraphs: %d 단락 추가 (skip=%d) → %s",
        report.paragraphs_added, report.skipped, output.name,
    )
    return report


# ---------------------------------------------------------------------------
# Convenience: 체크리스트 결과 → HWPX 보고서
# ---------------------------------------------------------------------------


def write_ir_blocks(
    blocks: Iterable,
    template: PathLike,
    output: PathLike,
    *,
    style_map: Optional[dict] = None,
    inherit_style: bool = True,
) -> WriteReport:
    """v0.15.0: IR :class:`~src.parser.ir_schema.Block` 리스트를 python-hwpx 로 HWPX 생성.

    기존 :func:`~src.hwpx.md_to_hwpx.convert` 의 **고급 기능 (reference 병합 / cover_range /
    toc_range / summary_range) 없이** 단순 변환 수행. 깔끔한 "원고 → HWPX" 에 최적.

    - Block.level 을 paraPrIDRef 로 매핑 (style_map 사용)
    - Block.text 는 그대로 삽입 (inherit_style=True 면 템플릿 현재 스타일 상속)
    - ambiguous / reason 은 저장 안 함 (순수 최종 결과물만)

    이 함수와 `md_to_hwpx.convert` 는 **plug-compatible** — style_map 형식 동일.

    Raises
    ------
    ImportError
        python-hwpx 미설치 시 (호출자가 legacy 경로 fallback)
    FileNotFoundError
        템플릿 없음
    FileExistsError
        출력 경로에 파일 이미 존재
    """
    # lazy — 순환 의존 방지
    from ..parser.ir_schema import Block  # type: ignore

    write_blocks: list[WriteBlock] = []
    for item in blocks:
        if isinstance(item, Block):
            write_blocks.append(WriteBlock(
                text=item.text or "",
                level=int(getattr(item, "level", 0) or 0),
            ))
        elif isinstance(item, WriteBlock):
            write_blocks.append(item)
        elif isinstance(item, dict):
            # v1 paragraph dict (type/text) 호환 — type 필드를 level 로 매핑
            text = str(item.get("text", ""))
            t = str(item.get("type", "body"))
            write_blocks.append(WriteBlock(
                text=text, level=_v1_type_to_level(t),
            ))
        # 그 외는 무시

    return write_paragraphs(
        template, write_blocks, output,
        style_map=style_map, inherit_style=inherit_style,
    )


def _v1_type_to_level(t: str) -> int:
    """v1 paragraph type 문자열 → IR level. 기본 0 (본문)."""
    mapping = {
        "title": -1, "cover": -1, "cover_title": -1,
        "heading1": 1, "heading": 1, "h1": 1,
        "heading2": 2, "h2": 2,
        "heading3": 3, "h3": 3,
        "heading4": 4, "h4": 4,
        "heading5": 5, "h5": 5,
        "heading6": 6, "h6": 6,
        "heading7": 7, "h7": 7,
        "heading8": 8, "h8": 8,
        "heading9": 9, "h9": 9,
        "heading10": 10, "h10": 10,
        "body": 0, "paragraph": 0, "p": 0,
    }
    return mapping.get(t.lower(), 0)


def write_checklist_report(
    template: PathLike,
    title: str,
    lines: Iterable[str],
    output: PathLike,
    *,
    style_map: Optional[dict] = None,
) -> WriteReport:
    """간단한 텍스트 보고서 → 템플릿 기반 HWPX.

    - title → level=1 (대제목)
    - 각 lines 줄 → level=0 (본문)

    예::

        write_checklist_report(
            template="templates/00_기본_10단계스타일.hwpx",
            title="2026 귀농귀촌 아카데미 — 제출 체크리스트",
            lines=[
                "사업자등록증: OK",
                "법인 인감증명서: 누락",
                "재무제표: 기간 초과",
            ],
            output="out/report.hwpx",
        )
    """
    blocks: list[WriteBlock] = [WriteBlock(text=title, level=1)]
    for line in lines:
        blocks.append(WriteBlock(text=str(line), level=0))
    return write_paragraphs(
        template, blocks, output, style_map=style_map,
    )


# ---------------------------------------------------------------------------
# Table insertion (v0.14.0)
# ---------------------------------------------------------------------------


def _insert_table(doc, block: "WriteTable") -> None:
    """python-hwpx HwpxDocument 에 표 삽입.

    ``add_table(rows, cols)`` 로 표 생성 후 각 셀에 add_paragraph 로 텍스트 채움.
    """
    if not block.rows or not block.rows[0]:
        raise ValueError("WriteTable 의 rows 가 비어 있음")

    n_rows = len(block.rows)
    n_cols = len(block.rows[0])

    kwargs: dict = {}
    if block.width is not None:
        kwargs["width"] = int(block.width)
    if block.height is not None:
        kwargs["height"] = int(block.height)

    table = doc.add_table(n_rows, n_cols, **kwargs)

    # python-hwpx 의 add_table 은 빈 셀을 가진 표를 돌려준다.
    # 셀 접근 API 는 버전별로 다르지만 공통으로 `cells` / `rows` / `iter_cells` 중 하나.
    cells = _iter_table_cells(table)
    flat = [cell for row in block.rows for cell in row]
    for cell_widget, text_value in zip(cells, flat):
        try:
            _set_cell_text(cell_widget, str(text_value or ""))
        except Exception:  # noqa: BLE001 - 개별 셀 실패는 무시 (표 자체는 유효)
            continue


def _iter_table_cells(table) -> list:
    """python-hwpx HwpxOxmlTable 에서 셀 순회. 버전별 API 차이 흡수."""
    # 우선 `.cells` 또는 `.iter_cells()` 시도
    if hasattr(table, "cells"):
        c = table.cells
        return list(c() if callable(c) else c)
    if hasattr(table, "iter_cells"):
        return list(table.iter_cells())
    # 폴백: row 순회 → 각 row 의 cells
    if hasattr(table, "rows"):
        out = []
        rows = table.rows
        rows = list(rows() if callable(rows) else rows)
        for r in rows:
            rc = getattr(r, "cells", None)
            if rc is not None:
                rc = list(rc() if callable(rc) else rc)
                out.extend(rc)
        return out
    return []


def _set_cell_text(cell, text: str) -> None:
    """셀에 텍스트 설정. python-hwpx 는 보통 add_paragraph 가 있음."""
    if hasattr(cell, "add_paragraph"):
        cell.add_paragraph(text=text, inherit_style=True)
    elif hasattr(cell, "text"):
        # property 일 수도, 메서드일 수도
        t = cell.text
        if callable(t):
            cell.text(text)
        else:
            cell.text = text
    elif hasattr(cell, "set_text"):
        cell.set_text(text)


__all__ = [
    "WriteBlock",
    "WriteTable",
    "WriteReport",
    "write_paragraphs",
    "write_ir_blocks",
    "write_checklist_report",
]
