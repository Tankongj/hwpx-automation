"""HWPX 자가 검증 시스템.

v1(Tankongj/hwpx-proposal-automation) 의 `scripts/verify_hwpx.py` 를 v2 로 포팅.
문자열 print 중심이던 원본을 함수형 API(:func:`verify`) + dataclass 보고서로 재구성했다.
CLI 동작(`python -m src.hwpx.verify_hwpx foo.hwpx`) 은 기존과 동일하게 유지.

Public API
----------
- :func:`verify(hwpx_path, doc_type='auto', company_keywords=None) -> VerifyReport`
- :class:`VerifyReport` — ``passed / total / rate / checks`` 구조

Check 목록
----------
- common : 파일 구조, content.hpf 일관성, 날짜 placeholder
- advanced: namespace pollution, 표 treatAsChar, linesegarray 이상, bullet 중복
- qualitative : Ⅰ~Ⅳ 장 구조, 심볼 계층, 문체, 장 수 추정
- quantitative: 표 채움 비율, resume section, 회사 키워드
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Union

from lxml import etree


NS = "http://www.hancom.co.kr/hwpml/2011/paragraph"

PathLike = Union[str, Path]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    """개별 체크 결과."""

    name: str
    category: str
    passed: bool
    detail: str

    @property
    def icon(self) -> str:
        return "✅" if self.passed else "❌"


@dataclass
class VerifyReport:
    """검증 보고서."""

    hwpx_path: str
    doc_type: str
    passed: int
    total: int
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return (self.passed / self.total * 100.0) if self.total else 0.0

    @property
    def ok(self) -> bool:
        """MVP DoD 기준: 70% 이상 통과면 pass."""
        return self.rate >= 70.0

    @property
    def status(self) -> str:
        r = self.rate
        if r >= 90:
            return "Excellent"
        if r >= 70:
            return "Good"
        if r >= 50:
            return "Fair"
        return "Poor"

    @property
    def failed(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


# ---------------------------------------------------------------------------
# HWPX reading
# ---------------------------------------------------------------------------

def _get_all_text(element) -> str:
    texts: list[str] = []
    for t in element.iter("{%s}t" % NS):
        if t.text:
            texts.append(t.text)
    return "".join(texts).strip()


def _read_hwpx(path: PathLike) -> dict:
    """HWPX zip → sections(parsed trees) + raw files(dict)."""
    result: dict[str, Any] = {"sections": {}, "files": {}, "filelist": []}
    with zipfile.ZipFile(path, "r") as z:
        result["filelist"] = z.namelist()
        for name in z.namelist():
            result["files"][name] = z.read(name)
            if name.startswith("Contents/section") and name.endswith(".xml"):
                result["sections"][name] = etree.fromstring(z.read(name))
    return result


# ---------------------------------------------------------------------------
# Common checks
# ---------------------------------------------------------------------------

def _check_file_opens(data, **_) -> tuple[bool, str]:
    has_section = any("section" in f for f in data["filelist"])
    has_header = "Contents/header.xml" in data["filelist"]
    has_hpf = "Contents/content.hpf" in data["filelist"]
    ok = has_section and has_header and has_hpf
    return ok, f"section={has_section}, header={has_header}, hpf={has_hpf}"


def _check_hpf_integrity(data, **_) -> tuple[bool, str]:
    hpf_text = data["files"].get("Contents/content.hpf", b"").decode("utf-8", errors="replace")
    sections = [
        f for f in data["filelist"]
        if f.startswith("Contents/section") and f.endswith(".xml")
    ]
    missing: list[str] = []
    for s in sections:
        sec_id = s.replace("Contents/", "").replace(".xml", "")
        if sec_id not in hpf_text:
            missing.append(sec_id)
    ok = len(missing) == 0
    detail = (
        f"sections={len(sections)}, missing={missing}"
        if missing
        else f"sections={len(sections)}, all referenced"
    )
    return ok, detail


def _check_date_filled(data, **_) -> tuple[bool, str]:
    section0 = data["sections"].get("Contents/section0.xml")
    if section0 is None:
        return False, "section0 not found"
    full_text = _get_all_text(section0)
    has_placeholder = "0.  0." in full_text or "월    일" in full_text
    return (not has_placeholder), f"Date placeholder: {'found ❌' if has_placeholder else 'none ✅'}"


# ---------------------------------------------------------------------------
# Advanced structural checks
# ---------------------------------------------------------------------------

def _check_treat_as_char(data, **_) -> tuple[bool, str]:
    section0 = data["sections"].get("Contents/section0.xml")
    if section0 is None:
        return True, "section0 not found — skipped"

    xml_bytes = data["files"].get("Contents/section0.xml", b"")
    xml_str = xml_bytes.decode("utf-8", errors="replace") if isinstance(xml_bytes, bytes) else xml_bytes
    count_1 = len(re.findall(r'treatAsChar="1"', xml_str))
    count_0 = len(re.findall(r'treatAsChar="0"', xml_str))
    if count_1 == 0:
        return True, f"treatAsChar=0: {count_0}, treatAsChar=1: 0 ✅"
    return (
        False,
        f"treatAsChar=0: {count_0}, treatAsChar=1: {count_1} ⚠️ (run fix_namespaces --fix-tables)",
    )


def _check_namespace_pollution(data, **_) -> tuple[bool, str]:
    for name in data["filelist"]:
        if not name.endswith(".xml"):
            continue
        xml_bytes = data["files"].get(name, b"")
        xml_str = xml_bytes.decode("utf-8", errors="replace") if isinstance(xml_bytes, bytes) else xml_bytes
        ns_matches = re.findall(r"</?ns\d+:", xml_str)
        if ns_matches:
            return False, f"{name}: {len(ns_matches)} ns prefix ⚠️ (run fix_namespaces)"
    return True, "No namespace pollution found ✅"


def _check_lineseg_anomaly(data, **_) -> tuple[bool, str]:
    xml_bytes = data["files"].get("Contents/section0.xml", b"")
    xml_str = xml_bytes.decode("utf-8", errors="replace") if isinstance(xml_bytes, bytes) else xml_bytes
    large = re.findall(r'vertsize="(\d{5,})"', xml_str)
    if large:
        return False, f"Found {len(large)} large vertsize values (≥10000) — may cause layout issues"
    return True, "No linesegarray anomalies found ✅"


def _check_bullet_duplication(data, **_) -> tuple[bool, str]:
    section0 = data["sections"].get("Contents/section0.xml")
    if section0 is None:
        return True, "section0 not found — skipped"
    full_text = _get_all_text(section0)
    dups = 0
    for pattern in ["□ □", "○ ○", "❍ ❍", "― ―", "- - "]:
        dups += full_text.count(pattern)
    if dups:
        return False, f"Found {dups} probable double-bullet occurrences ⚠️"
    return True, "No bullet duplication found ✅"


# ---------------------------------------------------------------------------
# Qualitative proposal checks
# ---------------------------------------------------------------------------

def _check_qualitative_structure(data, **_) -> tuple[bool, str]:
    section0 = data["sections"].get("Contents/section0.xml")
    if section0 is None:
        return False, "section0 not found"
    full_text = _get_all_text(section0)
    chapters = ["Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ"]
    found = [ch for ch in chapters if ch in full_text]
    return (len(found) >= 3), f"Chapters found: {found}"


def _check_symbol_system(data, **_) -> tuple[bool, str]:
    section0 = data["sections"].get("Contents/section0.xml")
    if section0 is None:
        return False, "section0 not found"
    full_text = _get_all_text(section0)
    symbols = {
        "□": full_text.count("□"),
        "○": full_text.count("○"),
        "―": full_text.count("―"),
        "※": full_text.count("※"),
    }
    ok = all(v > 0 for v in symbols.values())
    return ok, f"□={symbols['□']}, ○={symbols['○']}, ―={symbols['―']}, ※={symbols['※']}"


def _check_writing_style(data, **_) -> tuple[bool, str]:
    section0 = data["sections"].get("Contents/section0.xml")
    if section0 is None:
        return False, "section0 not found"
    full_text = _get_all_text(section0)
    endings = ["임", "음", "함", "됨"]
    total = sum(full_text.count(e) for e in endings)
    bad_total = sum(full_text.count(b) for b in ["할 수 있", "가능하다", "것이다"])
    ok = total > 20 and bad_total < 5
    return ok, f"Formal endings: {total}, vague expressions: {bad_total}"


def _check_paragraph_count(data, **_) -> tuple[bool, str]:
    section0 = data["sections"].get("Contents/section0.xml")
    if section0 is None:
        return False, "section0 not found"
    paras = section0.findall(".//{%s}p" % NS)
    tables = section0.findall(".//{%s}tbl" % NS)
    est_pages = len(paras) / 35
    ok = 10 <= est_pages <= 55
    return ok, f"Paragraphs: {len(paras)}, Tables: {len(tables)}, Est. pages: {est_pages:.0f}"


# ---------------------------------------------------------------------------
# Quantitative proposal checks
# ---------------------------------------------------------------------------

def _check_table_fill_rate(data, **_) -> tuple[bool, str]:
    section0 = data["sections"].get("Contents/section0.xml")
    if section0 is None:
        return False, "section0 not found"

    total_cells = 0
    filled_cells = 0
    table_stats: list[str] = []

    tables = section0.findall(".//{%s}tbl" % NS)
    for ti, tbl in enumerate(tables):
        t_total = 0
        t_filled = 0
        for row in tbl.findall("{%s}tr" % NS):
            for cell in row.findall("{%s}tc" % NS):
                t_total += 1
                text = _get_all_text(cell)
                if text and text not in ("(빈칸)", "-", ""):
                    t_filled += 1
        total_cells += t_total
        filled_cells += t_filled
        rate = (t_filled / t_total * 100) if t_total else 0
        table_stats.append(f"Table{ti + 1}: {t_filled}/{t_total} ({rate:.0f}%)")

    overall = (filled_cells / total_cells * 100) if total_cells else 0
    ok = overall >= 50
    detail = (
        f"Total: {filled_cells}/{total_cells} ({overall:.0f}%) | "
        + ", ".join(table_stats[:5])
    )
    return ok, detail


def _check_resume_section(data, **_) -> tuple[bool, str]:
    if "Contents/section1.xml" in data["filelist"]:
        size = len(data["files"]["Contents/section1.xml"])
        return True, f"section1: {size:,} bytes"
    return False, "section1 not found — resume not merged"


def _check_company_data(data, *, company_keywords: Iterable[str] | None = None, **_) -> tuple[bool, str]:
    keywords = list(company_keywords or [])
    if not keywords:
        return True, "No company keywords configured — skipped"
    section0 = data["sections"].get("Contents/section0.xml")
    if section0 is None:
        return False, "section0 not found"
    full_text = _get_all_text(section0)
    found = [kw for kw in keywords if kw in full_text]
    missing = [kw for kw in keywords if kw not in full_text]
    ok = len(missing) == 0
    return ok, f"Found: {found}, Missing: {missing}"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

_CheckFn = Callable[..., tuple[bool, str]]

_COMMON_CHECKS: list[tuple[str, str, _CheckFn]] = [
    ("File Structure",      "common",   _check_file_opens),
    ("content.hpf Integrity","common",  _check_hpf_integrity),
    ("Date Fields",         "common",   _check_date_filled),
    ("Namespace Pollution", "advanced", _check_namespace_pollution),
    ("Table treatAsChar",   "advanced", _check_treat_as_char),
    ("Lineseg Anomaly",     "advanced", _check_lineseg_anomaly),
    ("Bullet Duplication",  "advanced", _check_bullet_duplication),
]

_QUALITATIVE_CHECKS: list[tuple[str, str, _CheckFn]] = [
    ("Chapter Structure (Ⅰ~Ⅳ)", "qualitative", _check_qualitative_structure),
    ("Symbol System (□○―※)",     "qualitative", _check_symbol_system),
    ("Writing Style",             "qualitative", _check_writing_style),
    ("Page Count",                "qualitative", _check_paragraph_count),
]

_QUANTITATIVE_CHECKS: list[tuple[str, str, _CheckFn]] = [
    ("Table Fill Rate", "quantitative", _check_table_fill_rate),
    ("Resume Section",  "quantitative", _check_resume_section),
    ("Company Data",    "quantitative", _check_company_data),
]


def _autodetect_type(path: PathLike) -> str:
    fname = os.path.basename(str(path)).lower()
    if "정량" in fname or "quantitative" in fname:
        return "quantitative"
    return "qualitative"


def verify(
    hwpx_path: PathLike,
    doc_type: str = "auto",
    company_keywords: Iterable[str] | None = None,
) -> VerifyReport:
    """HWPX 파일을 검증하고 :class:`VerifyReport` 를 돌려준다.

    Parameters
    ----------
    hwpx_path : str or Path
    doc_type : {"auto", "qualitative", "quantitative"}
    company_keywords : 정량 모드에서 본문에 등장해야 할 회사 키워드 (선택)
    """
    path = Path(hwpx_path)
    if not path.exists():
        raise FileNotFoundError(str(path))

    if doc_type == "auto":
        doc_type = _autodetect_type(path)

    data = _read_hwpx(path)

    spec = list(_COMMON_CHECKS)
    if doc_type == "qualitative":
        spec += _QUALITATIVE_CHECKS
    elif doc_type == "quantitative":
        spec += _QUANTITATIVE_CHECKS

    extra = {"company_keywords": list(company_keywords or [])}
    checks: list[CheckResult] = []
    for name, category, fn in spec:
        try:
            ok, detail = fn(data, **extra)
        except Exception as exc:  # noqa: BLE001 - intentional catch-all per check
            ok, detail = False, f"Error: {exc}"
        checks.append(CheckResult(name=name, category=category, passed=ok, detail=detail))

    passed = sum(1 for c in checks if c.passed)
    return VerifyReport(
        hwpx_path=str(path),
        doc_type=doc_type,
        passed=passed,
        total=len(checks),
        checks=checks,
    )


def print_report(report: VerifyReport) -> None:
    """CLI/로그 용 상세 출력."""
    print(f"\n{'=' * 60}")
    print("📋 HWPX Self-Verification System")
    print(f"   File: {os.path.basename(report.hwpx_path)}")
    try:
        print(f"   Size: {os.path.getsize(report.hwpx_path):,} bytes")
    except OSError:
        pass
    print(f"   Type: {report.doc_type}")
    print("=" * 60)
    print(f"{'Item':<30} {'Result':^8} {'Detail'}")
    print("─" * 60)
    for c in report.checks:
        print(f"{c.name:<30} {c.icon:^8} {c.detail}")

    print(f"\n{'=' * 60}")
    print(f"📊 Summary: {report.passed}/{report.total} passed ({report.rate:.0f}%)")
    status = report.status
    if status == "Excellent":
        print("🏆 Status: Excellent — Ready to submit")
    elif status == "Good":
        print("⚠️ Status: Good — Minor fixes needed")
    elif status == "Fair":
        print("🔧 Status: Fair — Significant fixes needed")
    else:
        print("❌ Status: Poor — Major rework needed")
    print("=" * 60)

    failed = report.failed
    if failed:
        print("\n🔴 Failed items:")
        for i, c in enumerate(failed, 1):
            print(f"  {i}. [{c.category}] {c.name}: {c.detail}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HWPX Self-Verification System")
    parser.add_argument("hwpx_path", help="HWPX file to verify")
    parser.add_argument(
        "--type",
        choices=["qualitative", "quantitative", "auto"],
        default="auto",
        help="Document type (default: auto-detect)",
    )
    parser.add_argument(
        "--company-keywords",
        nargs="*",
        default=[],
        help="Company-specific keywords to check for (quantitative only)",
    )
    args = parser.parse_args(argv)

    if not os.path.exists(args.hwpx_path):
        print(f"❌ File not found: {args.hwpx_path}", file=sys.stderr)
        return 1

    report = verify(args.hwpx_path, args.type, args.company_keywords)
    print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
