"""HWP → PDF 변환 (v0.8.0).

사용자 PC 에 설치된 **LibreOffice** (soffice.exe) 를 headless 모드로 호출해 HWP 를 PDF 로
변환한다. LibreOffice 6+ 는 한컴 HWP 필터 기본 내장.

왜 HWP → PDF 인가 (HWP → HWPX 가 아님):
- 우리 RFP 추출기가 이미 PDF / HWPX 를 지원. PDF 경로는 Gemini document-processing 으로
  깔끔하게 처리됨.
- LibreOffice 의 HWP → HWPX 변환은 버전에 따라 품질 편차가 큼. PDF 는 거의 항상 잘 됨.
- 따라서 **HWP 발견 시 자동으로 PDF 로 변환** 후 기존 PDF 경로 재사용.

사용자 요구사항:
- LibreOffice 설치 필요 (https://www.libreoffice.org/download/). 없으면 안내 메시지.
- 한/글이 설치되어 있지 않아도 동작 (이게 핵심).

대안 (옵션, 향후):
- 한컴 Automation (상업 라이선스 필요) — 스킵
- kordoc / rhwp (Python) — HWP5 파싱은 되지만 HWPX 로 역직렬화 도구가 성숙하지 않음
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from ..utils.logger import get_logger


_log = get_logger("checklist.hwp_converter")


PathLike = Union[str, Path]


# LibreOffice 를 찾을 때 시도하는 경로들 (Windows 기본 설치)
_WINDOWS_SOFFICE_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    # Portable / 사용자 설치
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\LibreOffice\program\soffice.exe"),
]


@dataclass
class ConverterInfo:
    """LibreOffice 탐지 결과."""

    available: bool
    path: Optional[str] = None
    version: str = ""
    error: str = ""

    def summary(self) -> str:
        if self.available:
            return f"✅ LibreOffice 사용 가능 ({self.path}) {self.version}"
        return f"❌ LibreOffice 를 찾을 수 없음 — {self.error or '설치되어 있지 않음'}"


def detect_libreoffice() -> ConverterInfo:
    """LibreOffice soffice.exe 를 탐지.

    1) PATH 에서 soffice
    2) Windows 기본 설치 경로들
    """
    path = shutil.which("soffice") or shutil.which("soffice.exe")
    if path is None:
        for cand in _WINDOWS_SOFFICE_CANDIDATES:
            if cand and Path(cand).exists():
                path = cand
                break
    if path is None:
        return ConverterInfo(
            available=False,
            error="soffice 를 PATH/기본 경로에서 찾지 못했습니다. "
            "https://www.libreoffice.org/download/ 에서 설치해 주세요.",
        )

    try:
        proc = subprocess.run(
            [path, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        version = (proc.stdout or "").strip()
    except Exception as exc:  # noqa: BLE001
        return ConverterInfo(
            available=False, path=path, error=f"버전 조회 실패: {exc}"
        )

    return ConverterInfo(available=True, path=path, version=version)


def convert_hwp_to_pdf(
    hwp_path: PathLike,
    out_dir: Optional[PathLike] = None,
    *,
    timeout: int = 120,
    libreoffice_path: Optional[str] = None,
) -> Path:
    """HWP 파일 → PDF. 성공 시 생성된 PDF 경로 반환.

    Parameters
    ----------
    hwp_path : 변환할 ``.hwp`` 파일
    out_dir : 결과 PDF 출력 폴더. None 이면 hwp_path 와 같은 폴더.
    libreoffice_path : soffice 경로 override (주로 테스트)

    Raises
    ------
    FileNotFoundError : HWP 파일 없음
    ValueError : 확장자가 .hwp 가 아님
    RuntimeError : LibreOffice 실행 실패 / 변환 결과물 없음
    """
    src = Path(hwp_path)
    if not src.exists():
        raise FileNotFoundError(str(src))
    if src.suffix.lower() != ".hwp":
        raise ValueError(f".hwp 파일만 변환 가능합니다: {src.suffix}")

    soffice = libreoffice_path
    if soffice is None:
        info = detect_libreoffice()
        if not info.available:
            raise RuntimeError(info.error)
        soffice = info.path

    target_dir = Path(out_dir) if out_dir else src.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    # LibreOffice 는 --outdir 파라미터로만 지정 가능 (파일명은 src.stem + .pdf)
    # 임시 userProfile 을 만들어 사용자 기본 설정 간섭 방지
    with tempfile.TemporaryDirectory(prefix="hwpx_lo_") as profile_dir:
        cmd = [
            soffice,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            f"-env:UserInstallation=file:///{profile_dir.replace(os.sep, '/')}",
            "--convert-to", "pdf",
            "--outdir", str(target_dir),
            str(src),
        ]
        _log.info("HWP → PDF 변환: %s", src.name)
        try:
            proc = subprocess.run(
                cmd, check=False, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"LibreOffice 변환 시간 초과: {exc}") from exc
        except OSError as exc:
            raise RuntimeError(f"LibreOffice 실행 실패: {exc}") from exc

        if proc.returncode != 0:
            tail = (proc.stderr or "")[-300:]
            raise RuntimeError(
                f"LibreOffice 변환 실패 (exit {proc.returncode}): {tail}"
            )

    result = target_dir / f"{src.stem}.pdf"
    if not result.exists():
        raise RuntimeError(
            f"변환 후 PDF 가 생성되지 않음: {result}\n"
            "LibreOffice 로그를 확인하거나 다른 이름으로 저장해 보세요."
        )
    _log.info("HWP → PDF 완료: %s → %s", src.name, result.name)
    return result


__all__ = ["ConverterInfo", "detect_libreoffice", "convert_hwp_to_pdf"]
