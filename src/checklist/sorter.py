"""체크리스트 결과 → 첨부 서류 자동 정렬기 (v0.9.0 Track 2).

체크리스트에서 매칭된 파일들을 **원본 폴더 그대로 두고** 새 폴더에 번호매겨 복사.

예::

    입력 폴더:
        사업자등록증_2026-03-15.pdf
        my_법인인감_2025-12-01.pdf
        scan1.pdf     (매칭 안 됨)

    체크리스트 순서: [사업자등록증, 법인인감, 재무제표]

    출력 폴더 (정렬됨):
        01_사업자등록증_2026-03-15.pdf   ← RFP 순서 1
        02_법인인감_2025-12-01.pdf      ← RFP 순서 2
        _미매칭/scan1.pdf                ← 매칭 안 된 파일 보존

원본은 건드리지 않음 (copy only).
"""
from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Union

from ..utils.logger import get_logger
from .models import ChecklistResult, DocumentStatus


_log = get_logger("checklist.sorter")


PathLike = Union[str, Path]

REPORT_FILENAME = "_제출서류_보고서.txt"


@dataclass
class SortReport:
    """정렬 결과 요약."""

    output_dir: Path
    copied: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)       # 매칭 없는 서류 이름
    unmatched_files: list[Path] = field(default_factory=list)  # 폴더엔 있는데 안 매칭된 파일들
    zip_path: Optional[Path] = None                        # v0.10.0: ZIP 으로 묶은 결과
    report_path: Optional[Path] = None                     # v0.10.0: 보고서 파일 경로

    def summary(self) -> str:
        zip_info = f" / ZIP 생성" if self.zip_path else ""
        return (
            f"복사 {len(self.copied)} / 누락 서류 {len(self.missing)} / "
            f"미매칭 파일 {len(self.unmatched_files)}{zip_info}"
        )


# 파일명 안전화 — Windows 금지 문자 제거
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_filename(name: str) -> str:
    return _INVALID_CHARS.sub("_", name).strip()


def sort_attachments(
    result: ChecklistResult,
    output_dir: PathLike,
    *,
    include_unmatched: bool = True,
    filename_template: str = "{i:02d}_{doc_name}{ext}",
    write_report: bool = True,
    make_zip: bool = False,
    zip_name: Optional[str] = None,
    ai_backend: str = "",
) -> SortReport:
    """체크리스트 결과를 기반으로 파일들을 새 폴더에 번호매겨 복사.

    Parameters
    ----------
    result : :class:`ChecklistResult` — 이미 매칭 끝난 상태
    output_dir : 결과 저장할 폴더. 없으면 생성.
    include_unmatched : True 면 매칭 안 된 원 폴더 파일을 ``_미매칭/`` 하위로 복사.
    filename_template : 출력 파일명 템플릿. ``{i}`` (1부터), ``{doc_id}``, ``{doc_name}``,
        ``{ext}``, ``{orig_stem}`` 치환 가능.
    write_report : **v0.10.0**. True 면 ``_제출서류_보고서.txt`` 파일을 출력 폴더에 기록.
    make_zip : **v0.10.0**. True 면 복사가 끝난 뒤 출력 폴더 내용을 ZIP 으로 묶음.
        ZIP 은 `output_dir` **밖**에 생성됨 (재귀 압축 회피).
    zip_name : ZIP 파일명. None 이면 ``{출력폴더명}.zip``.

    Returns
    -------
    SortReport
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    report = SortReport(output_dir=out)
    copied_source_paths: set[Path] = set()

    for i, item in enumerate(result.items, start=1):
        if item.status == DocumentStatus.MISSING:
            report.missing.append(item.doc.name)
            continue
        best = item.best_match
        if best is None:
            report.missing.append(item.doc.name)
            continue

        src = best.path
        ext = src.suffix
        filename = filename_template.format(
            i=i,
            doc_id=item.doc.id,
            doc_name=_safe_filename(item.doc.name),
            ext=ext,
            orig_stem=_safe_filename(src.stem),
        )
        dest = out / _safe_filename(filename)

        # 같은 이름 충돌 회피
        counter = 1
        base_dest = dest
        while dest.exists():
            dest = base_dest.with_stem(f"{base_dest.stem}_{counter}")
            counter += 1

        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            _log.warning("복사 실패 %s → %s: %s", src, dest, exc)
            continue

        report.copied.append(dest)
        copied_source_paths.add(src.resolve())

    if include_unmatched and result.folder_path:
        source_folder = Path(result.folder_path)
        unmatched_dir = out / "_미매칭"
        for f in source_folder.iterdir():
            if not f.is_file():
                continue
            if f.resolve() in copied_source_paths:
                continue
            # 매칭 안 됨 → _미매칭/ 로
            unmatched_dir.mkdir(exist_ok=True)
            dest = unmatched_dir / f.name
            if dest.exists():
                continue
            try:
                shutil.copy2(f, dest)
            except OSError:
                continue
            report.unmatched_files.append(dest)

    # v0.10.0: 보고서 기록 (v0.11.0: AI 고지 footer 지원)
    if write_report:
        report.report_path = _write_report_file(out, result, report, ai_backend=ai_backend)

    # v0.10.0: ZIP 묶기
    if make_zip:
        report.zip_path = _make_zip(out, zip_name=zip_name)

    _log.info("정렬 완료: %s", report.summary())
    return report


def _write_report_file(
    out: Path,
    result: ChecklistResult,
    report: SortReport,
    *,
    ai_backend: str = "",
) -> Path:
    """사람이 읽기 쉬운 보고서 텍스트 파일을 출력 폴더에 기록.

    v0.11.0: ``ai_backend`` 가 AI 계열이면 **AI 기본법 (2026-01-22)** 준수 footer 자동 추가.
    """
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("제출서류 정렬 보고서")
    lines.append(f"생성: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"원본 폴더: {result.folder_path or '(미지정)'}")
    lines.append(f"출력 폴더: {out}")
    lines.append("=" * 60)
    lines.append("")

    lines.append(f"[요약] {report.summary()}")
    lines.append(f"  - 제출 가능 여부: {'✅ 가능' if result.is_submittable else '❌ 불가'}")
    lines.append(f"  - OK {result.ok_count} / 경고 {result.warning_count} / 누락 {result.missing_count}")
    lines.append("")

    if report.copied:
        lines.append("[복사 완료]")
        for p in report.copied:
            lines.append(f"  ✔ {p.name}")
        lines.append("")

    if report.missing:
        lines.append("[누락된 서류] — 반드시 추가 제출 필요")
        for name in report.missing:
            lines.append(f"  ✘ {name}")
        lines.append("")

    if report.unmatched_files:
        lines.append("[매칭되지 않은 원본 파일] — _미매칭/ 으로 복사됨")
        for p in report.unmatched_files:
            lines.append(f"  · {p.name}")
        lines.append("")

    # v0.11.0: AI 기본법 고지 footer — ai_backend 가 AI 였으면 자동 추가
    if ai_backend:
        from ..commerce.ai_disclosure import is_ai_backend, make_disclosure

        if is_ai_backend(ai_backend):
            disc = make_disclosure(backend=ai_backend, ai_used=True)
            lines.append("")
            lines.append(disc.format_report_footer())

    report_path = out / REPORT_FILENAME
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def _make_zip(out: Path, *, zip_name: Optional[str] = None) -> Path:
    """``out`` 폴더 내용을 ZIP 으로 묶는다. ZIP 파일은 ``out`` **바깥** 에 생성."""
    name = zip_name or f"{out.name}.zip"
    zip_path = out.parent / name
    # 재귀 압축 방지 — out 바깥에 생성하므로 OK
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in out.rglob("*"):
            if item.is_file():
                arcname = item.relative_to(out)
                zf.write(item, arcname=str(arcname))
    return zip_path


__all__ = ["SortReport", "sort_attachments", "REPORT_FILENAME"]
