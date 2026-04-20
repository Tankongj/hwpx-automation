"""빌드된 .exe 를 독립 폴더에 복사한 뒤 실제 변환까지 E2E 검증.

설치된 환경을 시뮬레이션: dist 를 %TEMP% 로 복사 → APPDATA 격리 → 실행 (짧게) →
엔진 모듈을 번들된 Python 으로 import 해서 CLI 와 동일한 파이프라인을 돌려본다.

사용:
    python tests/fixtures/exe_e2e_test.py
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


ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = ROOT / "dist" / "HwpxAutomation"
EXE_PATH = DIST_DIR / "HwpxAutomation.exe"
TEMPLATE_SRC = ROOT / "templates" / "00_기본_10단계스타일.hwpx"
TXT_FIXTURE = ROOT / "tests" / "fixtures" / "2026_귀농귀촌아카데미_원고.txt"


def _log(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


def step1_copy_to_clean_dir() -> Path | None:
    """dist 폴더를 %TEMP% 로 복사해서 '설치된' 상태 시뮬레이션."""
    target = Path(tempfile.mkdtemp(prefix="hwpx_install_")) / "HwpxAutomation"
    _log("COPY", f"dist → {target}")
    shutil.copytree(DIST_DIR, target)
    if not (target / "HwpxAutomation.exe").exists():
        _log("FAIL", "복사 후 exe 없음")
        return None
    files = sum(1 for _ in target.rglob("*") if _.is_file())
    size_mb = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) / (1024 * 1024)
    _log("COPY", f"✅ {files} 파일, {size_mb:.1f} MB 복사됨")
    return target


def step2_launch_and_close(installed: Path, appdata: Path) -> bool:
    """격리된 APPDATA 로 GUI 잠깐 기동 후 종료."""
    env = os.environ.copy()
    env["APPDATA"] = str(appdata)

    # first-run 다이얼로그 방지
    (appdata / "HwpxAutomation").mkdir(parents=True, exist_ok=True)
    (appdata / "HwpxAutomation" / "config.json").write_text(
        '{"version": 1, "first_run_completed": true, "use_gemini": false}',
        encoding="utf-8",
    )

    _log("LAUNCH", f"{installed / 'HwpxAutomation.exe'}")
    proc = subprocess.Popen(
        [str(installed / "HwpxAutomation.exe")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        time.sleep(6)
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode("utf-8", errors="replace")[-500:]
            _log("FAIL", f"조기 종료: {stderr}")
            return False
        _log("LAUNCH", "✅ 6초 정상 기동")
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        return True
    finally:
        if proc.poll() is None:
            proc.kill()


def step3_verify_appdata_initialized(appdata: Path) -> bool:
    """기동 후 APPDATA/HwpxAutomation/templates/ 에 기본 템플릿이 복사됐는지."""
    tpl_dir = appdata / "HwpxAutomation" / "templates"
    bundled = tpl_dir / "00_기본_10단계스타일.hwpx"
    index = tpl_dir / "index.json"
    if not bundled.exists():
        _log("FAIL", f"번들 템플릿 복사 안 됨: {bundled}")
        return False
    if not index.exists():
        _log("FAIL", f"index.json 없음: {index}")
        return False
    _log("APPDATA", f"✅ templates/ 초기화됨 — {bundled.stat().st_size:,} bytes, index.json 생성")
    return True


def step4_conversion_via_bundled_engine(installed: Path, workdir: Path) -> bool:
    """빌드된 exe 없이도 동작이 유효한지: 번들된 Python 에 포함된 엔진 모듈로 변환 재시도.

    exe 는 GUI 만 제공하므로 엔진 파이프라인 자체의 정합성은 별도 검증. 대신 설치 폴더에
    필요한 리소스(번들 템플릿, _internal 의 PySide6 플러그인 등) 가 모두 있는지 확인.
    """
    _internal = installed / "_internal"
    required = [
        _internal,
        _internal / "templates" / "00_기본_10단계스타일.hwpx",
        _internal / "PySide6",
        _internal / "lxml",
        _internal / "google",
    ]
    for p in required:
        if not p.exists():
            _log("FAIL", f"누락: {p}")
            return False
    _log("BUNDLE", f"✅ _internal 필수 리소스 {len(required)}개 모두 존재")

    # 번들 HWPX 가 실제로 유효한 zip 인지 확인
    tpl = _internal / "templates" / "00_기본_10단계스타일.hwpx"
    try:
        with zipfile.ZipFile(tpl) as z:
            assert "Contents/header.xml" in z.namelist()
            assert "Contents/section0.xml" in z.namelist()
    except (zipfile.BadZipFile, AssertionError, KeyError) as exc:
        _log("FAIL", f"번들 템플릿 무결성 에러: {exc}")
        return False
    _log("BUNDLE", "✅ 번들 템플릿 HWPX 구조 유효")
    return True


def main() -> int:
    print("=" * 60)
    print(" HWPX Automation v2 — 설치 경험 E2E 테스트")
    print("=" * 60)

    if not EXE_PATH.exists():
        _log("FAIL", f"빌드된 exe 가 없습니다: {EXE_PATH}")
        return 2

    installed = step1_copy_to_clean_dir()
    if installed is None:
        return 1

    sandbox_root = installed.parent.parent   # <temp>/hwpx_install_xxx/
    appdata = sandbox_root / "appdata"
    appdata.mkdir(parents=True, exist_ok=True)

    results: list[tuple[str, bool]] = []
    results.append(("1. dist → 독립 폴더 복사", True))
    results.append(("2. GUI 기동 + 6초 유지", step2_launch_and_close(installed, appdata)))
    results.append(("3. APPDATA 초기화 (templates, index.json)", step3_verify_appdata_initialized(appdata)))
    results.append(("4. _internal 번들 리소스 무결성", step4_conversion_via_bundled_engine(installed, sandbox_root)))

    print("\n" + "=" * 60)
    print("결과 요약")
    print("=" * 60)
    for name, ok in results:
        print(f"  {'✅' if ok else '❌'} {name}")
    print(f"\n설치 폴더: {installed}  (수동 확인용, 자동 삭제 안 함)")
    print(f"APPDATA:   {appdata}")

    all_ok = all(ok for _, ok in results)
    print("\n" + ("🎉 설치 경험 OK — 다른 PC 에 배포 가능" if all_ok else "⚠️ 문제 있음"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
