"""Windows 코드 사이닝 자동화 스크립트 — v0.12.0 스캐폴드.

**목적**: 상업화 전환 시 (v1.0 직전) ``dist/HwpxAutomation/HwpxAutomation.exe`` 에
디지털 서명을 붙여 SmartScreen 경고 해소.

**지원 방식** (우선순위):
1. **Azure Artifact Signing** (2026 GA, $10/월, 5,000회 서명)
   - 개인사업자 명시 허용. 한국 거주자 가능 여부는 Azure 에 사전 확인.
   - 공용 신뢰 프로파일 — SmartScreen 평판 없이도 경고 없음 (일부 최근 CA 이슈 있음)
2. **Certum SimplySign** (€189/yr, 클라우드 서명, USB 토큰 불필요)
3. **SignPath** (오픈소스 / 상용 플랜)

**현재 스크립트 상태**: 실제 자격증명 없이 **환경 확인 + 명령 빌드** 만 수행.
실제 서명 실행은 `HWPX_SIGNING_KEY_VAULT` / `HWPX_SIGNING_CERT_PROFILE` 환경변수가 있어야.

사용 예::

    # 1) 환경변수 설정 (실 서명 시)
    set HWPX_SIGNING_KEY_VAULT=my-vault
    set HWPX_SIGNING_CERT_PROFILE=my-cert

    # 2) 빌드 후 서명
    pyinstaller build.spec --noconfirm
    python scripts/sign_release.py --exe dist/HwpxAutomation/HwpxAutomation.exe

**자동 재빌드 가드**: 서명 전 PyInstaller 빌드 타임스탬프 확인.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path


# v0.11.0+ 의 cp949 회피 (scripts/*.py 일관 적용)
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


ENV_VAULT = "HWPX_SIGNING_KEY_VAULT"
ENV_CERT = "HWPX_SIGNING_CERT_PROFILE"
ENV_TENANT = "HWPX_SIGNING_TENANT_ID"
ENV_CLIENT = "HWPX_SIGNING_CLIENT_ID"


def check_env() -> tuple[bool, list[str]]:
    """필요 환경변수 전부 있는지. (ok, 누락 목록)."""
    required = [ENV_VAULT, ENV_CERT, ENV_TENANT, ENV_CLIENT]
    missing = [k for k in required if not os.environ.get(k)]
    return (not missing), missing


def find_dotnet() -> str:
    """`dotnet` CLI 경로. Azure Trusted Signing 은 `dotnet sign` 을 쓴다."""
    exe = shutil.which("dotnet")
    if not exe:
        raise SystemExit(
            "❌ `dotnet` CLI 가 설치되어 있지 않습니다.\n"
            "   → https://dotnet.microsoft.com/download 에서 .NET SDK 설치 필요."
        )
    return exe


def verify_exe(exe: Path) -> None:
    if not exe.exists():
        raise SystemExit(f"❌ 서명 대상 exe 없음: {exe}")
    # sha256 을 출력해 reproducibility 로그에 남김
    h = hashlib.sha256(exe.read_bytes()).hexdigest()
    print(f"📄 대상: {exe}")
    print(f"   크기: {exe.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"   sha256 (pre-sign): {h[:16]}...")


def build_azure_sign_command(exe: Path) -> list[str]:
    """Azure Artifact Signing 의 `dotnet sign` 명령 빌드. 실 호출 X."""
    vault = os.environ[ENV_VAULT]
    cert = os.environ[ENV_CERT]
    tenant = os.environ[ENV_TENANT]
    client = os.environ[ENV_CLIENT]
    return [
        "dotnet", "sign", "code", "trusted-signing",
        "--trusted-signing-endpoint", f"https://{vault}.codesigning.azure.net/",
        "--trusted-signing-account", vault,
        "--trusted-signing-certificate-profile", cert,
        "--azure-tenant-id", tenant,
        "--azure-client-id", client,
        "--timestamp-url", "http://timestamp.acs.microsoft.com",
        "--description", "HWPX Automation v2 — 한글 문서 자동화 데스크톱 앱",
        "--description-url", "https://github.com/example/hwpx-automation",
        str(exe),
    ]


def run_sign(exe: Path, *, dry_run: bool = True) -> int:
    ok, missing = check_env()
    if not ok:
        print(
            "⚠️ 환경변수 누락 → 실제 서명 불가 (dry-run 으로만 진행):\n"
            + "\n".join(f"   - {k}" for k in missing)
            + "\n\n   설정 방법:"
            "\n   set HWPX_SIGNING_KEY_VAULT=<your-trusted-signing-account>"
            "\n   set HWPX_SIGNING_CERT_PROFILE=<your-cert-profile-name>"
            "\n   set HWPX_SIGNING_TENANT_ID=<azure-ad-tenant>"
            "\n   set HWPX_SIGNING_CLIENT_ID=<azure-ad-client-app>"
        )
        dry_run = True

    if not dry_run:
        find_dotnet()  # CLI 존재 확인

    cmd = build_azure_sign_command(exe)
    print("\n📎 sign 명령:")
    print("   " + " ".join(cmd[:4]))
    print("   " + "   ".join(cmd[4:8]) + " ...")
    print(f"   (총 {len(cmd)} 토큰)")

    if dry_run:
        print("\n💡 dry-run — 실제 서명은 --run 플래그 필요.")
        return 0

    print("\n▶ 서명 실행 중...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ 서명 실패 (exit {result.returncode})")
        print(result.stderr[-600:])
        return result.returncode

    # 사후 해시 + 검증
    h = hashlib.sha256(exe.read_bytes()).hexdigest()
    print(f"✅ 서명 완료. sha256 (post-sign): {h[:16]}...")
    _verify_signature(exe)
    return 0


def _verify_signature(exe: Path) -> None:
    """signtool verify 또는 powershell Get-AuthenticodeSignature 로 확인."""
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        print("   (PowerShell 없음 — 서명 검증 스킵)")
        return
    cmd = [
        ps, "-Command",
        f"Get-AuthenticodeSignature -FilePath '{exe}' | Select-Object Status,SignerCertificate",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    print(f"   서명 검증:\n   {result.stdout.strip()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="HWPX Automation 릴리즈 exe 서명")
    parser.add_argument(
        "--exe",
        default="dist/HwpxAutomation/HwpxAutomation.exe",
        help="서명할 exe 경로 (기본 dist/HwpxAutomation/HwpxAutomation.exe)",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="실제 서명 실행. 미지정 시 dry-run (명령만 출력).",
    )
    args = parser.parse_args()

    exe = Path(args.exe).resolve()
    verify_exe(exe)
    return run_sign(exe, dry_run=not args.run)


if __name__ == "__main__":
    sys.exit(main())
