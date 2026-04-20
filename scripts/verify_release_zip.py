"""릴리즈 zip 의 "수신자 시뮬레이션".

zip → 임시 폴더에 압축 해제 → exe 기동 → 정상 종료 확인.
배포 전 최종 관문.

사용:
    python scripts/verify_release_zip.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path


# v0.10.1: Windows 기본 cp949 stdout 에서 이모지 출력 시 UnicodeEncodeError 회피.
# 이 스크립트는 UTF-8 출력을 가정 (✅/❌/📦 등 유니코드 이모지 사용).
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 재설정 실패 시 기본 인코딩 유지
            pass


ROOT = Path(__file__).resolve().parents[1]
RELEASE_DIR = ROOT / "release"


def find_latest_zip() -> Path | None:
    if not RELEASE_DIR.exists():
        return None
    zips = sorted(RELEASE_DIR.glob("HwpxAutomation-v*.zip"), key=lambda p: p.stat().st_mtime)
    return zips[-1] if zips else None


def main() -> int:
    z = find_latest_zip()
    if z is None:
        print(f"❌ {RELEASE_DIR} 에 zip 없음. 먼저 make_release.py 실행.")
        return 2

    print(f"📦 검증할 zip: {z.name} ({z.stat().st_size / (1024*1024):.1f} MB)")

    sandbox = Path(tempfile.mkdtemp(prefix="hwpx_zip_verify_"))
    print(f"📂 압축 해제 → {sandbox}")

    try:
        with zipfile.ZipFile(z) as zf:
            zf.extractall(sandbox)

        exe = sandbox / "HwpxAutomation" / "HwpxAutomation.exe"
        if not exe.exists():
            print(f"❌ 압축 해제 후 exe 없음: {exe}")
            return 1

        print(f"✅ 압축 해제 성공. exe 크기: {exe.stat().st_size / (1024*1024):.1f} MB")

        # 격리된 APPDATA 로 기동
        appdata = sandbox / "appdata"
        (appdata / "HwpxAutomation").mkdir(parents=True, exist_ok=True)
        (appdata / "HwpxAutomation" / "config.json").write_text(
            '{"version": 1, "first_run_completed": true, "use_gemini": false}',
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["APPDATA"] = str(appdata)

        print(f"▶ 기동 테스트 (8초)")
        proc = subprocess.Popen(
            [str(exe)], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            time.sleep(8)
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode("utf-8", errors="replace")[-500:]
                print(f"❌ 조기 종료: exit={proc.returncode}")
                print(f"   stderr:\n{stderr}")
                return 1
            print("✅ 8초 동안 정상 기동")
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        finally:
            if proc.poll() is None:
                proc.kill()

        # 번들 확인
        bundled = sandbox / "HwpxAutomation" / "_internal" / "templates" / "00_기본_10단계스타일.hwpx"
        if not bundled.exists():
            print(f"❌ 번들 템플릿 누락: {bundled}")
            return 1
        print(f"✅ 번들 템플릿: {bundled.stat().st_size:,} bytes")

        print("\n🎉 릴리즈 zip 검증 성공 — 배포 가능")
        return 0
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
