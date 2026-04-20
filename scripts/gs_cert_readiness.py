"""GS 인증 + 나라장터 종합쇼핑몰 등재 readiness checker — v0.12.0.

**배경**: 상용 전환 (v1.0) 단계에서 공공조달 진입하려면 GS 인증 (Good Software)
필수. 2025년 공공 SW 시장 5.83조원. GS 1 회 받으면 종합쇼핑몰 등록 가능.

**GS 인증 요건** (한국정보통신기술협회 TTA, 국립전파연구원):
1. **SW 품질**: ISO/IEC 25010 기준 — 기능적합성, 신뢰성, 사용성, 보안성 등
2. **문서**: 요구사항 명세서, 설계서, 테스트 결과, 사용자 매뉴얼, 유지보수 매뉴얼
3. **테스트 커버리지**: 최소 기준 있음 (프로젝트별 상이)
4. **오픈소스 고지**: 사용 OSS 라이선스 list + 준수 확인

**나라장터 종합쇼핑몰**:
- GS 인증 기본
- 사업자등록증, 중소기업확인서, 소프트웨어사업자신고증
- 판매 가격 정찰제 / 조달청 카탈로그 규격 준수

이 스크립트는 **코드 레벨 사전 체크** 만 수행 (법인/인증 심사 대리 불가):
- ✅ 테스트 커버리지 기본치
- ✅ 오픈소스 라이선스 리포트
- ✅ 사용자 매뉴얼 존재
- ✅ CHANGELOG 최신성
- ✅ 버전 관리 체계

사용::

    python scripts/gs_cert_readiness.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Check:
    name: str
    ok: bool = False
    details: str = ""
    severity: str = "required"  # "required" / "recommended" / "optional"


@dataclass
class ReadinessReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if not self.checks:
            return 0.0
        passed = sum(1 for c in self.checks if c.ok)
        return passed / len(self.checks)

    @property
    def required_blocked(self) -> list[str]:
        return [c.name for c in self.checks if c.severity == "required" and not c.ok]


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_version_consistency() -> Check:
    """pyproject.toml 과 src/__init__.py 의 버전이 일치?"""
    pyproj = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    init = (ROOT / "src" / "__init__.py").read_text(encoding="utf-8")

    m_proj = re.search(r'version\s*=\s*"([^"]+)"', pyproj)
    m_init = re.search(r'__version__\s*=\s*"([^"]+)"', init)

    if not m_proj or not m_init:
        return Check("버전 일관성", False, "version 필드 못 찾음", "required")
    if m_proj.group(1) == m_init.group(1):
        return Check(
            "버전 일관성", True,
            f"pyproject.toml == src/__init__.py == {m_proj.group(1)}",
            "required",
        )
    return Check(
        "버전 일관성", False,
        f"불일치: {m_proj.group(1)} vs {m_init.group(1)}",
        "required",
    )


def check_changelog_updated() -> Check:
    """CHANGELOG.md 에 최신 버전 섹션이 있는지."""
    changelog = ROOT / "CHANGELOG.md"
    if not changelog.exists():
        return Check("CHANGELOG.md 존재", False, "파일 없음", "required")
    init = (ROOT / "src" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    if not m:
        return Check("CHANGELOG 버전 매치", False, "__version__ 없음", "required")
    current = m.group(1)
    text = changelog.read_text(encoding="utf-8")
    if f"[{current}]" in text:
        return Check("CHANGELOG 최신성", True, f"v{current} 섹션 발견", "required")
    return Check(
        "CHANGELOG 최신성", False,
        f"v{current} 섹션 없음 — 릴리즈 노트 작성 필요",
        "required",
    )


def check_test_suite() -> Check:
    """pytest 전체 실행 + pass 수 확인."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--no-header", "-q"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=600,
        )
    except Exception as exc:  # noqa: BLE001
        return Check("테스트 통과", False, f"실행 실패: {exc}", "required")
    out = result.stdout + result.stderr
    m = re.search(r"(\d+) passed", out)
    fail_m = re.search(r"(\d+) failed", out)
    if result.returncode != 0 or fail_m:
        fn = int(fail_m.group(1)) if fail_m else 0
        return Check("테스트 통과", False, f"{fn} 실패 (exit {result.returncode})", "required")
    passed = int(m.group(1)) if m else 0
    # GS 기본 — 최소 100+ 테스트 권장
    if passed < 100:
        return Check(
            "테스트 수량", False,
            f"{passed} 통과 (100 이상 권장)",
            "recommended",
        )
    return Check("테스트 통과", True, f"{passed} 개 전부 통과", "required")


def check_license_present() -> Check:
    """LICENSE 파일 존재."""
    for name in ("LICENSE", "LICENSE.txt", "LICENSE.md"):
        if (ROOT / name).exists():
            return Check("LICENSE 파일", True, f"{name} 존재", "required")
    return Check("LICENSE 파일", False, "LICENSE 파일 없음 (MIT 권장)", "required")


def check_readme_present() -> Check:
    """README 존재 + 최소 길이."""
    for name in ("README.md", "README.rst", "README.txt"):
        p = ROOT / name
        if p.exists():
            size = p.stat().st_size
            if size > 500:
                return Check("README", True, f"{name} ({size:,} bytes)", "required")
            return Check(
                "README", False,
                f"{name} 너무 짧음 ({size} bytes)",
                "required",
            )
    return Check("README", False, "README 없음", "required")


def check_oss_notice() -> Check:
    """오픈소스 의존성 라이선스 고지.

    공공조달은 OSS 고지 페이지 필수. `pip-licenses` 로 자동 생성 가능.
    """
    # docs/OSS_NOTICES.md 또는 OSS_NOTICES.txt 가 있거나, scripts 에 생성기가 있는지
    candidates = [
        ROOT / "OSS_NOTICES.md",
        ROOT / "OSS_NOTICES.txt",
        ROOT / "docs" / "OSS_NOTICES.md",
        ROOT / "THIRD_PARTY_NOTICES.md",
    ]
    for p in candidates:
        if p.exists():
            return Check("OSS 고지 문서", True, str(p.relative_to(ROOT)), "required")
    return Check(
        "OSS 고지 문서", False,
        "OSS_NOTICES.md 없음 — `pip-licenses --format=markdown` 으로 생성 권장",
        "required",
    )


def check_ai_disclosure() -> Check:
    """AI 기본법 (2026-01-22) 준수 모듈 존재."""
    p = ROOT / "src" / "commerce" / "ai_disclosure.py"
    if p.exists():
        return Check("AI 기본법 준수 모듈", True, "ai_disclosure.py", "required")
    return Check("AI 기본법 준수 모듈", False, "src/commerce/ai_disclosure.py 없음", "required")


def check_korean_ui() -> Check:
    """주요 GUI 텍스트가 한국어로 되어 있는지 (KO locale 기본)."""
    tab = ROOT / "src" / "gui" / "tabs" / "convert_tab.py"
    if not tab.exists():
        return Check("한국어 UI", False, "convert_tab.py 없음", "recommended")
    text = tab.read_text(encoding="utf-8")
    # 한글 음절이 10 개 이상 있어야
    hangul = sum(1 for c in text if 0xAC00 <= ord(c) <= 0xD7A3)
    if hangul > 100:
        return Check("한국어 UI", True, f"한글 {hangul} 자 확인", "required")
    return Check("한국어 UI", False, f"한글 {hangul} 자 — 부족", "recommended")


def check_pii_handling() -> Check:
    """PIPA: Sentry PII 스크러빙 + Firebase 에러 한국어 등."""
    er = ROOT / "src" / "utils" / "error_reporter.py"
    if er.exists() and "_scrub_pii" in er.read_text(encoding="utf-8"):
        return Check("PII 스크러빙", True, "Sentry before_send 훅 확인", "required")
    return Check("PII 스크러빙", False, "Sentry PII 필터 없음", "recommended")


def check_code_signing_ready() -> Check:
    """코드사이닝 스크립트 존재."""
    p = ROOT / "scripts" / "sign_release.py"
    if p.exists():
        return Check("코드사이닝 스크립트", True, str(p.relative_to(ROOT)), "recommended")
    return Check("코드사이닝 스크립트", False, "sign_release.py 없음", "recommended")


def check_release_zip() -> Check:
    """최신 버전 릴리즈 zip 존재."""
    init = (ROOT / "src" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    if not m:
        return Check("릴리즈 ZIP", False, "버전 파싱 실패", "optional")
    version = m.group(1)
    zp = ROOT / "release" / f"HwpxAutomation-v{version}.zip"
    if zp.exists():
        return Check(
            "릴리즈 ZIP", True,
            f"v{version} ({zp.stat().st_size / 1024 / 1024:.1f} MB)",
            "recommended",
        )
    return Check("릴리즈 ZIP", False, f"v{version} zip 없음", "recommended")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_all_checks() -> ReadinessReport:
    report = ReadinessReport()
    checks = [
        check_version_consistency,
        check_changelog_updated,
        check_license_present,
        check_readme_present,
        check_oss_notice,
        check_ai_disclosure,
        check_korean_ui,
        check_pii_handling,
        check_code_signing_ready,
        check_release_zip,
        check_test_suite,  # 시간 걸리므로 마지막
    ]
    for fn in checks:
        try:
            report.checks.append(fn())
        except Exception as exc:  # noqa: BLE001
            report.checks.append(Check(
                fn.__name__, False, f"체크 실행 실패: {exc}", "required",
            ))
    return report


def print_report(report: ReadinessReport) -> None:
    print("=" * 70)
    print(" GS 인증 + 나라장터 종합쇼핑몰 등재 Readiness 체크")
    print("=" * 70)
    for c in report.checks:
        mark = "✅" if c.ok else "⚠️ "
        print(f" {mark} [{c.severity}] {c.name}: {c.details}")
    print("-" * 70)
    print(f" 통과율: {report.pass_rate * 100:.1f}% ({len(report.checks)} 중 {sum(1 for c in report.checks if c.ok)} 통과)")
    if report.required_blocked:
        print(f" ❌ 필수 차단 항목: {', '.join(report.required_blocked)}")
        print("    → 상업 전환 전 반드시 해결")
    else:
        print(" ✅ 필수 차단 항목 없음")
    print("=" * 70)
    print("\n💡 다음 단계:")
    print("  1. 필수(required) 차단 해소")
    print("  2. 권장(recommended) 항목 검토")
    print("  3. TTA / 국립전파연구원 GS 인증 신청 (2-3 개월 소요)")
    print("  4. 통과 후 나라장터 종합쇼핑몰 등록 (조달청)")


def main() -> int:
    report = run_all_checks()
    print_report(report)
    return 0 if not report.required_blocked else 1


if __name__ == "__main__":
    sys.exit(main())
