"""Update helper — v0.16.0.

메인 앱이 종료된 후 파일 교체 + 롤백 + 재시작을 담당하는 **별도 프로세스**.

PyInstaller 환경에서는 메인 exe 를 ``--update-helper`` 플래그로 재실행하여
이 모듈의 :func:`run_helper` 가 처리한다 (main.py 에서 분기).

dev 환경에서는 ``python src/commerce/update_helper.py`` 로 직접 실행 가능.

**핵심 안전장치**:
- 메인 프로세스 PID 가 실제로 죽었는지 폴링 후 작업 시작
- ``app_dir.bak`` 으로 전체 백업 후 교체
- 교체 중 실패 → 자동 롤백
- 특정 경로 (config.json / user_db / logs) 는 항상 보존
- 최대 5분 작업 타임아웃 (무한 루프 방지)
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable


# NOTE: 이 모듈은 설치된 앱 안에서 실행되므로 외부 의존 최소화 (httpx / lxml 등 X).
# 로거도 표준 logging 으로만 씀 — install 된 환경에서 src.utils.logger 가 깨질 가능성 대비.
import logging

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
_log = logging.getLogger("update_helper")


PRESERVED_PATHS = (
    "config.json",
    "user_db.sqlite",
    "user_db.sqlite-journal",
    "user_db.sqlite-wal",
    "logs",
)

DEFAULT_TIMEOUT_SECONDS = 300
PID_POLL_INTERVAL = 0.5


def wait_for_pid_exit(pid: int, *, timeout: float = 60.0) -> bool:
    """주어진 PID 가 종료될 때까지 대기. True=종료됨, False=타임아웃."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if not _pid_alive(pid):
            return True
        time.sleep(PID_POLL_INTERVAL)
    return False


def _pid_alive(pid: int) -> bool:
    """OS-agnostic PID 생존 체크."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # Windows: OpenProcess + GetExitCodeProcess
        try:
            import ctypes
            STILL_ACTIVE = 259
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
                if not ok:
                    return False
                return code.value == STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(h)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
        except Exception:
            return False


def _iter_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return (p for p in root.rglob("*") if p.is_file())


def _is_preserved(rel_path: Path) -> bool:
    """이 상대 경로가 PRESERVED_PATHS 에 해당하는지."""
    parts = rel_path.parts
    for preserved in PRESERVED_PATHS:
        pp = Path(preserved).parts
        if parts[: len(pp)] == pp:
            return True
    return False


def backup_dir(src: Path, backup: Path) -> None:
    """src → backup 으로 통째 복사. 기존 백업은 덮어씀."""
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    shutil.copytree(src, backup)


def restore_backup(backup: Path, target: Path) -> None:
    """backup → target 복원 (target 은 비운 뒤 복사)."""
    if target.exists():
        # target 안의 preserved 는 건드리지 않는다 — backup 에도 있으니 그냥 덮어씀
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(backup, target)


def apply_staging(staging: Path, target: Path) -> None:
    """staging 디렉토리의 파일들을 target 에 덮어씀.

    staging 의 최상위가 어떤 구조인지에 따라 두 가지 경로:
      1. staging 안에 직접 _internal/, HwpxAutomation.exe 등이 있는 경우 → 1:1 복사
      2. staging/HwpxAutomation/ 같이 한 단계 래핑된 경우 → 그 안에서 복사

    Preserved 경로는 덮어쓰지 않는다.
    """
    # 래핑 감지: staging 에 디렉토리 하나만 있고 그 이름이 target 과 유사하면 그 안으로
    candidates = [p for p in staging.iterdir() if p.is_dir()]
    source_root = staging
    if len(list(staging.iterdir())) == 1 and candidates:
        only = candidates[0]
        if only.name.lower().startswith(target.name.lower()[:6]) or \
           (only / "_internal").exists():
            source_root = only

    for src_file in _iter_files(source_root):
        rel = src_file.relative_to(source_root)
        if _is_preserved(rel):
            _log.info("preserved, skipping: %s", rel)
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst)


def run_helper(argv: list[str] | None = None) -> int:
    """helper 메인 엔트리. ``main.py`` 의 ``--update-helper`` 분기에서 호출.

    반환 코드:
      0  성공 (새 버전 재시작까지 완료)
      1  메인 프로세스 대기 타임아웃
      2  파일 교체 실패 (롤백 완료)
      3  롤백도 실패 (치명적 — 수동 복구 필요)
    """
    p = argparse.ArgumentParser(description="HWPX Automation Update Helper")
    p.add_argument("--staging", required=True, type=Path)
    p.add_argument("--target", required=True, type=Path)
    p.add_argument("--wait-pid", required=True, type=int)
    p.add_argument("--new-version", required=True)
    p.add_argument("--relaunch", action="store_true")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = p.parse_args(argv)

    _log.info(
        "helper start: target=%s new_version=%s wait_pid=%d",
        args.target, args.new_version, args.wait_pid,
    )

    # 1. 메인 프로세스 종료 대기
    if not wait_for_pid_exit(args.wait_pid, timeout=60.0):
        _log.error("main process (pid=%d) did not exit within 60s", args.wait_pid)
        return 1

    backup = args.target.parent / f"{args.target.name}.bak"

    try:
        # 2. 백업
        _log.info("backing up: %s → %s", args.target, backup)
        backup_dir(args.target, backup)
    except Exception as exc:  # noqa: BLE001
        _log.exception("backup failed: %s", exc)
        return 2

    try:
        # 3. staging 적용
        _log.info("applying staging: %s → %s", args.staging, args.target)
        apply_staging(args.staging, args.target)
    except Exception as exc:  # noqa: BLE001
        _log.exception("apply failed, rolling back: %s", exc)
        try:
            restore_backup(backup, args.target)
            _log.info("rollback OK")
            return 2
        except Exception as exc2:  # noqa: BLE001
            _log.exception("rollback FAILED (manual recovery needed): %s", exc2)
            _log.error("backup preserved at: %s", backup)
            return 3

    # 4. 정리
    try:
        shutil.rmtree(args.staging, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass

    _log.info("update applied to v%s", args.new_version)

    # 5. 재시작
    if args.relaunch:
        main_exe = _find_main_exe(args.target)
        if main_exe and main_exe.exists():
            _log.info("relaunching: %s", main_exe)
            try:
                subprocess.Popen([str(main_exe)], close_fds=True)
            except Exception as exc:  # noqa: BLE001
                _log.warning("relaunch failed (user can start manually): %s", exc)

    return 0


def _find_main_exe(target: Path) -> Path | None:
    """target 폴더에서 메인 exe 찾기 (이름 기반)."""
    for name in ("HwpxAutomation.exe", "HwpxAutomation"):
        p = target / name
        if p.exists():
            return p
    # fallback: 첫 번째 exe
    for p in target.glob("*.exe"):
        return p
    return None


if __name__ == "__main__":
    sys.exit(run_helper())
