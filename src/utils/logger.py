"""간단한 로거.

콘솔 + (선택적으로) 파일 동시 출력. GUI 단계에서는 QTextEdit 핸들러로 확장 가능.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_DEFAULT_FMT = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def _default_log_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "HwpxAutomation" / "logs"
    return Path.home() / ".hwpx-automation" / "logs"


def _wrap_utf8(stream):
    """Windows 기본 cp949 스트림을 UTF-8 로 재포장. 실패 시 원본 그대로 반환."""
    try:
        enc = getattr(stream, "encoding", "") or ""
        if enc.lower() == "utf-8":
            return stream
        buf = getattr(stream, "buffer", None)
        if buf is None:
            return stream
        import io as _io

        return _io.TextIOWrapper(buf, encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:  # noqa: BLE001
        return stream


def configure(level: int = logging.INFO, log_file: Path | None = None) -> None:
    global _configured
    if _configured:
        return

    root = logging.getLogger("hwpx")
    root.setLevel(level)

    stream = logging.StreamHandler(_wrap_utf8(sys.stderr))
    stream.setFormatter(logging.Formatter(_DEFAULT_FMT, _DATE_FMT))
    root.addHandler(stream)

    if log_file is None:
        log_dir = _default_log_dir()
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "hwpx-automation.log"
        except OSError:
            log_file = None

    if log_file is not None:
        try:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(logging.Formatter(_DEFAULT_FMT, _DATE_FMT))
            root.addHandler(fh)
        except OSError:
            pass

    _configured = True


def get_logger(name: str = "hwpx") -> logging.Logger:
    if not _configured:
        configure()
    if name == "hwpx" or name.startswith("hwpx."):
        return logging.getLogger(name)
    return logging.getLogger(f"hwpx.{name}")
