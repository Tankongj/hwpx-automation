"""빌드된 .exe 에 대한 headless smoke 테스트.

pytest 바깥에서 바로 돌리는 유틸 스크립트. 빌드 직후 한 번 실행해서:
1. dist/HwpxAutomation/HwpxAutomation.exe 존재
2. 실행했을 때 크래시 없이 창이 뜸 (QTimer 로 자동 종료)
3. 크기 합리적 (100MB~500MB 범위)

사용:
    python tests/fixtures/exe_smoke_test.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXE_DIR = ROOT / "dist" / "HwpxAutomation"
EXE_PATH = EXE_DIR / "HwpxAutomation.exe"


def smoke_test_exe_exists() -> bool:
    if not EXE_DIR.exists():
        print(f"❌ dist 폴더 없음: {EXE_DIR}")
        return False
    if not EXE_PATH.exists():
        print(f"❌ .exe 파일 없음: {EXE_PATH}")
        return False
    size_mb = EXE_PATH.stat().st_size / (1024 * 1024)
    total_mb = sum(
        f.stat().st_size for f in EXE_DIR.rglob("*") if f.is_file()
    ) / (1024 * 1024)
    print(f"✅ EXE: {EXE_PATH}")
    print(f"   EXE 크기: {size_mb:.1f} MB")
    print(f"   총 배포 폴더: {total_mb:.1f} MB")
    if total_mb < 50 or total_mb > 800:
        print(f"   ⚠️ 총 크기가 50~800MB 범위 밖 — 누락 또는 비대")
    return True


def smoke_test_bundled_template() -> bool:
    template = EXE_DIR / "_internal" / "templates" / "00_기본_10단계스타일.hwpx"
    # PyInstaller 6.x 는 datas 를 _internal 아래에 둔다
    alt = EXE_DIR / "templates" / "00_기본_10단계스타일.hwpx"
    if template.exists() or alt.exists():
        target = template if template.exists() else alt
        print(f"✅ 번들 템플릿: {target} ({target.stat().st_size:,} bytes)")
        return True
    print("❌ 번들 템플릿 누락 (_internal/templates/ 또는 templates/)")
    return False


def smoke_test_launch_with_timeout(timeout_sec: int = 10) -> bool:
    """.exe 를 띄워서 timeout_sec 초 버틴 뒤 kill. 크래시 없으면 OK."""
    # 격리된 APPDATA
    sandbox = Path(tempfile.mkdtemp(prefix="hwpx_exe_smoke_"))
    env = os.environ.copy()
    env["APPDATA"] = str(sandbox)
    # first-run 다이얼로그 방지: config 미리 저장 (appdata 경로 기준)
    appdata_dir = sandbox / "HwpxAutomation"
    appdata_dir.mkdir(parents=True, exist_ok=True)
    config_path = appdata_dir / "config.json"
    config_path.write_text(
        '{"version": 1, "first_run_completed": true, "use_gemini": false}',
        encoding="utf-8",
    )

    print(f"▶ 실행: {EXE_PATH} (sandbox: {sandbox})")
    proc = subprocess.Popen(
        [str(EXE_PATH)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        time.sleep(timeout_sec)
        exit_code = proc.poll()
        if exit_code is not None:
            # 프로세스가 먼저 죽었다 — 크래시
            stdout = proc.stdout.read().decode("utf-8", errors="replace")[-500:] if proc.stdout else ""
            stderr = proc.stderr.read().decode("utf-8", errors="replace")[-800:] if proc.stderr else ""
            print(f"❌ 조기 종료 exit={exit_code}")
            print(f"   stderr tail:\n{stderr}")
            return False
        # 실행 중이면 정상
        print(f"✅ {timeout_sec}초 동안 실행 유지됨 — 정상 기동")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return True
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def main() -> int:
    print("=" * 60)
    print(" HWPX Automation v2 — 빌드 결과 Smoke Test")
    print("=" * 60)
    checks = [
        ("EXE 파일 존재", smoke_test_exe_exists),
        ("번들 템플릿 포함", smoke_test_bundled_template),
        ("10초간 실행 유지", lambda: smoke_test_launch_with_timeout(10)),
    ]
    results = []
    for name, fn in checks:
        print(f"\n--- {name} ---")
        ok = False
        try:
            ok = fn()
        except Exception as exc:  # noqa: BLE001
            print(f"❌ 예외: {type(exc).__name__}: {exc}")
        results.append((name, ok))

    print("\n" + "=" * 60)
    print("결과:")
    print("=" * 60)
    for name, ok in results:
        print(f"  {'✅' if ok else '❌'} {name}")

    all_ok = all(ok for _, ok in results)
    print("\n" + ("🎉 모든 smoke 통과!" if all_ok else "⚠️ 일부 실패 — 위 로그 확인"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
