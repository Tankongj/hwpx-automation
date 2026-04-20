"""Update installer — v0.16.0.

메인 앱이 호출하는 업데이트 오케스트레이션. 실 파일 교체는
:mod:`src.commerce.update_helper` (별도 프로세스) 에게 위임한다 —
Windows 에서 실행 중인 exe 를 자기 자신이 덮어쓸 수 없기 때문.

**흐름**::

    main app                                        helper process
    --------                                        --------------
    1. check_for_update()
    2. download zip → %TEMP%
    3. SHA-256 검증
    4. (Azure signed 면) 서명 검증  ← v0.16 은 항상 skip
    5. extract → staging dir
    6. spawn helper with args
    7. (main exits)   --------▶ 7'. wait for main PID to die
                                 8'. backup app_dir → app_dir.bak
                                 9'. copy staging/* → app_dir (except config.json / user_db)
                                10'. verify version
                                11'. relaunch main app
                                12'. cleanup staging + backup

**실패 모드 & 롤백**:
- 다운로드 실패 → 메인 앱 계속 실행 (no-op)
- SHA 불일치 → 다운로드 파일 삭제 후 UI 에 에러 반환
- 서명 실패 (미래) → 동일
- helper 파일 복사 중 실패 → helper 가 backup 복원 후 main 재시작
- helper 자체 크래시 → 사용자가 수동으로 ``.bak`` 복원 가능하도록 경로 로깅
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx

from ..utils.logger import get_logger
from .update_manifest import UpdateAsset, UpdateManifest


_log = get_logger("commerce.update_installer")

# 메인 앱의 --update-helper 플래그로 helper 실행을 재사용 (PyInstaller 친화)
HELPER_FLAG = "--update-helper"

# 업데이트 중에도 반드시 보존해야 할 경로 (상대). helper 가 건너뜀.
#
# ``config.json`` 은 ``%APPDATA%\HwpxAutomation\`` 에 있어서 app_dir 과 분리돼
# 있는 게 정상이지만, 방어적으로 나열.
PRESERVED_PATHS: tuple[str, ...] = (
    "config.json",
    "user_db.sqlite",
    "user_db.sqlite-journal",
    "user_db.sqlite-wal",
    "logs",
)


@dataclass
class InstallResult:
    ok: bool
    message: str = ""
    staging_dir: Optional[Path] = None
    helper_pid: Optional[int] = None


def _sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def verify_signature(zip_path: Path, manifest: UpdateManifest) -> tuple[bool, str]:
    """서명 검증. v0.16.0 은 항상 통과 (signature=None 허용).

    Azure Trusted Signing 도입 (v0.17+) 후:
      - manifest.signature != None 이면 검증 강제
      - 검증 실패 → False, reason
    """
    if manifest.signature is None:
        # v0.16 과도기 — SHA-256 으로만 보호, 서명 미검증 허용
        return True, "signature absent (v0.16 transitional)"
    # TODO(v0.17): Azure Trusted Signing 검증 활성화
    # from .update_signature import verify_azure_signature
    # return verify_azure_signature(zip_path, manifest.signature)
    return True, "signature verification stub (pending Azure integration)"


def download_asset(
    asset: UpdateAsset,
    dest_dir: Path,
    *,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    timeout: float = 300.0,
) -> Path:
    """Asset zip 을 dest_dir 에 다운로드. 실패하면 예외."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    fn = dest_dir / f"update_{hashlib.sha1(asset.url.encode()).hexdigest()[:12]}.zip"
    downloaded = 0
    with httpx.stream("GET", asset.url, timeout=timeout, follow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", "0") or 0)
        with fn.open("wb") as f:
            for chunk in r.iter_bytes(chunk_size=1 << 20):
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(downloaded, total or asset.size_bytes)
    return fn


def verify_download(zip_path: Path, asset: UpdateAsset) -> tuple[bool, str]:
    """SHA-256 & 크기 검증."""
    if not zip_path.exists():
        return False, "다운로드 파일 없음"
    actual = _sha256_of(zip_path)
    if actual != asset.sha256.lower():
        return False, f"SHA-256 불일치: expected {asset.sha256[:16]}..., got {actual[:16]}..."
    if asset.size_bytes > 0 and zip_path.stat().st_size != asset.size_bytes:
        return False, f"크기 불일치: expected {asset.size_bytes}, got {zip_path.stat().st_size}"
    return True, "OK"


def extract_to_staging(zip_path: Path, staging_dir: Path) -> Path:
    """zip 을 staging_dir 에 풀어 놓음. 디렉토리가 있으면 비움."""
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
    staging_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Zip Slip 방어: 각 엔트리 경로가 staging_dir 내부인지 확인
        for info in zf.infolist():
            target = (staging_dir / info.filename).resolve()
            if staging_dir.resolve() not in target.parents and target != staging_dir.resolve():
                raise RuntimeError(f"zip 내 부정 경로 탐지: {info.filename}")
        zf.extractall(staging_dir)
    return staging_dir


def _find_main_executable() -> Path:
    """현재 실행 중인 메인 exe (PyInstaller 환경) 또는 python 인터프리터 경로."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    # dev / test 환경 — 인터프리터
    return Path(sys.executable)


def spawn_helper(
    staging_dir: Path,
    app_dir: Path,
    *,
    new_version: str,
    relaunch: bool = True,
) -> int:
    """helper 프로세스 spawn 후 PID 반환. 메인 앱은 그 직후 종료해야 함."""
    exe = _find_main_executable()
    # PyInstaller 로 빌드된 메인 exe 는 --update-helper 를 인자로 받으면
    # helper 경로로 분기한다 (main.py 참조). dev 환경에선 python 으로 스크립트 실행.
    current_pid = os.getpid()
    args: list[str] = [str(exe)]
    if not getattr(sys, "frozen", False):
        # dev: helper 스크립트 경로 추가
        helper_py = Path(__file__).parent / "update_helper.py"
        args.append(str(helper_py))

    args.extend([
        HELPER_FLAG,
        "--staging", str(staging_dir),
        "--target", str(app_dir),
        "--wait-pid", str(current_pid),
        "--new-version", new_version,
    ])
    if relaunch:
        args.append("--relaunch")

    # Windows: DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP 로 부모와 분리
    creationflags = 0
    if sys.platform == "win32":
        creationflags = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP

    proc = subprocess.Popen(
        args,
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    _log.info("helper spawned: pid=%s args=%s", proc.pid, args[:4])
    return proc.pid


def install_update(
    info_asset: UpdateAsset,
    manifest: UpdateManifest,
    *,
    app_dir: Path,
    new_version: str,
    temp_root: Optional[Path] = None,
    relaunch: bool = True,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> InstallResult:
    """풀 파이프라인: 다운로드 → 검증 → 추출 → helper 스폰.

    성공 시 ``InstallResult.helper_pid`` 가 채워진다. 메인 앱은 이 값을 확인 후
    **즉시 종료** 해야 helper 가 파일 교체를 시작할 수 있다.
    """
    temp_root = temp_root or Path(tempfile.gettempdir()) / "HwpxAutomation_update"
    temp_root.mkdir(parents=True, exist_ok=True)

    # 1. 다운로드
    try:
        zip_path = download_asset(info_asset, temp_root, progress_cb=progress_cb)
    except Exception as exc:  # noqa: BLE001
        return InstallResult(ok=False, message=f"다운로드 실패: {exc}")

    # 2. SHA 검증
    ok, msg = verify_download(zip_path, info_asset)
    if not ok:
        zip_path.unlink(missing_ok=True)
        return InstallResult(ok=False, message=msg)

    # 3. 서명 검증 (v0.16 = skip)
    ok, msg = verify_signature(zip_path, manifest)
    if not ok:
        zip_path.unlink(missing_ok=True)
        return InstallResult(ok=False, message=f"서명 검증 실패: {msg}")

    # 4. staging 추출
    staging = temp_root / f"staging_v{new_version}"
    try:
        extract_to_staging(zip_path, staging)
    except Exception as exc:  # noqa: BLE001
        return InstallResult(ok=False, message=f"압축 해제 실패: {exc}")

    # zip 은 유지할 필요 없음 (실패 시 재다운로드 빠름)
    zip_path.unlink(missing_ok=True)

    # 5. helper spawn
    try:
        pid = spawn_helper(staging, app_dir, new_version=new_version, relaunch=relaunch)
    except Exception as exc:  # noqa: BLE001
        return InstallResult(
            ok=False,
            message=f"helper 실행 실패: {exc}",
            staging_dir=staging,
        )

    return InstallResult(ok=True, message="업데이트 준비 완료, 앱을 종료합니다", staging_dir=staging, helper_pid=pid)


__all__ = [
    "HELPER_FLAG",
    "PRESERVED_PATHS",
    "InstallResult",
    "download_asset",
    "extract_to_staging",
    "install_update",
    "spawn_helper",
    "verify_download",
    "verify_signature",
]
