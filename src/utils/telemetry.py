"""사용량 텔레메트리 — v0.7.0 (로컬 기록 only, opt-in).

MVP 스테이지에선 **로컬 파일에만 기록** 하고 외부 전송은 하지 않는다. 추후 상업화 단계에서
서버로 보내는 uploader 를 추가할 수 있다.

파일: ``%APPDATA%\\HwpxAutomation\\telemetry.jsonl`` (append-only JSON Lines)

활성 조건: :attr:`src.settings.app_config.AppConfig.telemetry_optin` == True.
비활성일 때 ``record()`` 호출은 완전 무동작 — 아주 저렴함.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional


TELEMETRY_FILENAME = "telemetry.jsonl"


def _base_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "HwpxAutomation"
    return Path.home() / ".hwpx-automation"


def _telemetry_path() -> Path:
    return _base_dir() / TELEMETRY_FILENAME


_enabled: Optional[bool] = None


def configure(enabled: bool) -> None:
    """AppConfig 변경 시 전역 상태 갱신."""
    global _enabled
    _enabled = bool(enabled)


def is_enabled() -> bool:
    """Config 가 명시적으로 활성화했는지. 기본은 안전하게 비활성."""
    if _enabled is None:
        return False
    return _enabled


def record(event: str, **props: Any) -> None:
    """이벤트 기록. opt-in 안 했으면 no-op.

    Parameters
    ----------
    event : 이벤트 이름 (예: "conversion_started", "quant_saved")
    **props : 임의 속성 (JSON 직렬화 가능해야 함)

    성공/실패 모두 예외 던지지 않음 — 기록 실패가 앱 동작에 영향 주면 안 됨.
    """
    if not is_enabled():
        return
    try:
        path = _telemetry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.time(), "event": event, **props}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def clear() -> bool:
    """텔레메트리 파일 비우기. 사용자가 "기록 삭제" 누를 때."""
    path = _telemetry_path()
    if path.exists():
        try:
            path.unlink()
            return True
        except OSError:
            return False
    return False


def summary() -> dict[str, int]:
    """기록된 이벤트 종류별 카운트."""
    path = _telemetry_path()
    counts: dict[str, int] = {}
    if not path.exists():
        return counts
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            e = str(entry.get("event", "?"))
            counts[e] = counts.get(e, 0) + 1
    except OSError:
        pass
    return counts


__all__ = [
    "TELEMETRY_FILENAME",
    "configure",
    "is_enabled",
    "record",
    "clear",
    "summary",
]
