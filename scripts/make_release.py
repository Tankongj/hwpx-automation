"""릴리즈 zip 파일 생성.

빌드 후 ``dist/HwpxAutomation/`` 를 ``release/HwpxAutomation-v{version}.zip`` 으로 압축.
사용자에게 전달하기 쉬운 한 파일 형태.

사용:
    # 빌드와 동시에
    pyinstaller build.spec --clean --noconfirm
    python scripts/make_release.py

    # 또는 zip 만
    python scripts/make_release.py
"""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path


# v0.10.1: Windows 기본 cp949 stdout 에서 이모지 (📦 ✅ 🎉 등) 출력 시 UnicodeEncodeError 회피.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "dist" / "HwpxAutomation"
RELEASE_DIR = ROOT / "release"


def read_version() -> str:
    init_path = ROOT / "src" / "__init__.py"
    for line in init_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "unknown"


def make_zip() -> Path:
    if not DIST_DIR.exists():
        raise SystemExit(
            f"❌ {DIST_DIR} 가 없습니다. 먼저 pyinstaller build.spec 를 실행하세요."
        )

    version = read_version()
    RELEASE_DIR.mkdir(exist_ok=True)
    out_path = RELEASE_DIR / f"HwpxAutomation-v{version}.zip"
    if out_path.exists():
        out_path.unlink()

    print(f"📦 압축 중: {DIST_DIR} → {out_path}")

    files = sorted(DIST_DIR.rglob("*"))
    total = sum(1 for f in files if f.is_file())
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        written = 0
        for f in files:
            if not f.is_file():
                continue
            arcname = Path("HwpxAutomation") / f.relative_to(DIST_DIR)
            zf.write(f, arcname)
            written += 1
            if written % 100 == 0:
                print(f"  ... {written}/{total}")

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"\n✅ 완료: {out_path}")
    print(f"   크기: {size_mb:.1f} MB")
    print(f"   파일 수: {total}")
    return out_path


def print_release_notes(version: str) -> None:
    changelog = ROOT / "CHANGELOG.md"
    if not changelog.exists():
        return
    print("\n" + "=" * 60)
    print(" 릴리즈 노트")
    print("=" * 60)
    seen_version = False
    count = 0
    for line in changelog.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"## [{version}]"):
            seen_version = True
        elif line.startswith("## [") and seen_version:
            break
        if seen_version:
            print(line)
            count += 1
            if count > 40:
                print("  (… CHANGELOG.md 참고)")
                break


def main() -> int:
    version = read_version()
    print(f"HWPX Automation v{version} 릴리즈 준비")
    print("=" * 60)

    out = make_zip()
    print_release_notes(version)

    print(f"\n배포: {out} 파일 한 개만 전달하면 됩니다.")
    print("사용자는 압축 풀고 HwpxAutomation/HwpxAutomation.exe 실행.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
