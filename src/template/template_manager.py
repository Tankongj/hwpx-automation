"""템플릿 라이브러리 관리.

기획안 4.3. 템플릿 파일과 메타데이터(``index.json``) 를 ``%APPDATA%\\HwpxAutomation\\templates\\``
아래에서 관리한다. 최초 실행 시에는 번들 기본 템플릿 (``templates/00_기본_10단계스타일.hwpx``)
을 이 위치로 복사해 초기화한다.

Public API
----------
- :class:`TemplateManager`        : CRUD 진입점
- :class:`TemplateEntry`          : 메타데이터 한 줄
- :func:`default_template_dir()`  : 기본 라이브러리 경로
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional, Union

from ..utils.logger import get_logger


PathLike = Union[str, Path]


_log = get_logger("template.manager")

DEFAULT_TEMPLATE_ID = "default_10"
INDEX_FILENAME = "index.json"
BUNDLED_TEMPLATE_RELPATH = Path("templates") / "00_기본_10단계스타일.hwpx"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TemplateEntry:
    """``index.json`` 한 항목."""

    id: str
    name: str
    file: str                       # 라이브러리 디렉토리 기준 파일명
    is_default: bool = False
    added_at: str = field(default_factory=lambda: date.today().isoformat())
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "TemplateEntry":
        return cls(
            id=data["id"],
            name=data["name"],
            file=data["file"],
            is_default=bool(data.get("is_default", False)),
            added_at=str(data.get("added_at", date.today().isoformat())),
            description=str(data.get("description", "")),
        )

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Library location helpers
# ---------------------------------------------------------------------------

def default_template_dir() -> Path:
    """OS 기본 앱 데이터 위치 (Windows: ``%APPDATA%\\HwpxAutomation\\templates``)."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "HwpxAutomation" / "templates"
    # POSIX/개발 환경 fallback
    return Path.home() / ".hwpx-automation" / "templates"


def _project_bundled_template() -> Optional[Path]:
    """개발 중 or PyInstaller 번들에서 기본 템플릿 파일 위치를 찾는다."""
    # PyInstaller one-file / one-dir 기준
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        cand = base / BUNDLED_TEMPLATE_RELPATH
        if cand.exists():
            return cand

    # 개발 모드: 프로젝트 루트에서 templates/
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        cand = parent / BUNDLED_TEMPLATE_RELPATH
        if cand.exists():
            return cand
    return None


# ---------------------------------------------------------------------------
# TemplateManager
# ---------------------------------------------------------------------------

class TemplateNotFoundError(KeyError):
    """지정한 ID 의 템플릿을 찾지 못함."""


class TemplateManager:
    """템플릿 라이브러리 CRUD.

    상태는 모두 디스크(``index.json`` + 파일) 에 있고, 인스턴스는 가볍다.
    GUI, CLI 어디서든 ``TemplateManager()`` 로 바로 써도 된다.
    """

    def __init__(self, library_dir: Optional[PathLike] = None, *, auto_init: bool = True) -> None:
        self.library_dir = Path(library_dir) if library_dir else default_template_dir()
        if auto_init:
            self.ensure_initialized()

    # ---- init ----

    @property
    def index_path(self) -> Path:
        return self.library_dir / INDEX_FILENAME

    def ensure_initialized(self) -> None:
        """라이브러리 디렉토리와 인덱스가 없으면 생성하고, 번들 기본 템플릿을 복사한다."""
        self.library_dir.mkdir(parents=True, exist_ok=True)

        entries = self._load_index_quiet()

        if not any(e.id == DEFAULT_TEMPLATE_ID for e in entries):
            bundled = _project_bundled_template()
            if bundled is None:
                _log.warning(
                    "번들 기본 템플릿을 찾지 못했습니다 (%s). "
                    "template_manager.add() 로 수동 등록하거나 번들 파일을 확인하세요.",
                    BUNDLED_TEMPLATE_RELPATH,
                )
            else:
                target = self.library_dir / bundled.name
                if not target.exists():
                    shutil.copy2(bundled, target)
                    _log.info("기본 템플릿 복사됨: %s → %s", bundled, target)
                entries.append(
                    TemplateEntry(
                        id=DEFAULT_TEMPLATE_ID,
                        name="기본 10단계 스타일",
                        file=bundled.name,
                        is_default=True,
                        description="휴먼명조/HY견고딕, Ctrl+1~0 10단계, A4 여백",
                    )
                )
                self._save_index(entries)

        elif not self.index_path.exists():
            self._save_index(entries)

    # ---- Load / save ----

    def _load_index_quiet(self) -> list[TemplateEntry]:
        if not self.index_path.exists():
            return []
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            _log.error("index.json 로드 실패 (%s). 새로 생성합니다.", exc)
            return []
        if not isinstance(raw, list):
            _log.error("index.json 형식 오류: 리스트가 아님. 새로 생성합니다.")
            return []
        entries: list[TemplateEntry] = []
        for item in raw:
            try:
                entries.append(TemplateEntry.from_dict(item))
            except KeyError as exc:
                _log.warning("index.json 항목 무시(필드 누락 %s): %r", exc, item)
        return entries

    def _save_index(self, entries: Iterable[TemplateEntry]) -> None:
        data = [e.to_dict() for e in entries]
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(self.index_path)

    # ---- Public API ----

    def list(self) -> list[TemplateEntry]:
        """라이브러리의 모든 템플릿 항목."""
        return self._load_index_quiet()

    def get(self, template_id: str) -> TemplateEntry:
        """ID → :class:`TemplateEntry`. 없으면 :class:`TemplateNotFoundError`."""
        for e in self._load_index_quiet():
            if e.id == template_id:
                return e
        raise TemplateNotFoundError(template_id)

    def get_path(self, template_id: str) -> Path:
        """ID → 실제 HWPX 파일 경로."""
        entry = self.get(template_id)
        return self.library_dir / entry.file

    def get_default(self) -> TemplateEntry:
        """현재 기본 템플릿 항목. 없으면 :class:`TemplateNotFoundError`."""
        for e in self._load_index_quiet():
            if e.is_default:
                return e
        raise TemplateNotFoundError("no default template registered")

    def add(
        self,
        hwpx_path: PathLike,
        name: str,
        *,
        template_id: Optional[str] = None,
        description: str = "",
    ) -> TemplateEntry:
        """사용자가 업로드한 HWPX 를 라이브러리에 등록.

        원본 파일을 라이브러리 디렉토리로 **복사** 하고 인덱스에 추가한다. ID 가 주어지지
        않으면 ``user_<uuid8>`` 로 자동 생성.
        """
        src = Path(hwpx_path)
        if not src.exists():
            raise FileNotFoundError(f"HWPX 파일이 없습니다: {src}")
        if src.suffix.lower() != ".hwpx":
            raise ValueError(f"HWPX 파일만 등록 가능합니다: {src}")

        if template_id is None:
            template_id = f"user_{uuid.uuid4().hex[:8]}"

        entries = self._load_index_quiet()
        if any(e.id == template_id for e in entries):
            raise ValueError(f"이미 존재하는 ID 입니다: {template_id}")
        if any(e.name == name for e in entries):
            raise ValueError(f"이미 같은 이름의 템플릿이 있습니다: {name}")

        dest_name = self._unique_filename(src.name, existing=entries)
        dest = self.library_dir / dest_name
        shutil.copy2(src, dest)

        entry = TemplateEntry(
            id=template_id,
            name=name,
            file=dest_name,
            is_default=False,
            added_at=date.today().isoformat(),
            description=description,
        )
        entries.append(entry)
        self._save_index(entries)
        _log.info("템플릿 등록: %s (%s)", name, template_id)
        return entry

    def remove(self, template_id: str) -> None:
        """사용자 템플릿 삭제. 기본 템플릿(``default_10``) 은 삭제 불가."""
        if template_id == DEFAULT_TEMPLATE_ID:
            raise ValueError("기본 템플릿은 삭제할 수 없습니다")
        entries = self._load_index_quiet()
        match = next((e for e in entries if e.id == template_id), None)
        if match is None:
            raise TemplateNotFoundError(template_id)
        entries = [e for e in entries if e.id != template_id]
        # 파일 제거 (실패는 무시 but 경고)
        target = self.library_dir / match.file
        try:
            if target.exists():
                target.unlink()
        except OSError as exc:
            _log.warning("템플릿 파일 삭제 실패 %s: %s", target, exc)
        self._save_index(entries)
        _log.info("템플릿 제거: %s", template_id)

    def set_default(self, template_id: str) -> TemplateEntry:
        """기본 템플릿 지정 (이전 기본은 자동 해제)."""
        entries = self._load_index_quiet()
        if not any(e.id == template_id for e in entries):
            raise TemplateNotFoundError(template_id)
        for e in entries:
            e.is_default = e.id == template_id
        self._save_index(entries)
        return self.get(template_id)

    # ---- helpers ----

    def _unique_filename(self, base_name: str, existing: Iterable[TemplateEntry]) -> str:
        used = {e.file for e in existing}
        if base_name not in used:
            return base_name
        stem = Path(base_name).stem
        suffix = Path(base_name).suffix or ".hwpx"
        i = 1
        while True:
            candidate = f"{stem}_{i}{suffix}"
            if candidate not in used:
                return candidate
            i += 1


__all__ = [
    "DEFAULT_TEMPLATE_ID",
    "INDEX_FILENAME",
    "TemplateEntry",
    "TemplateManager",
    "TemplateNotFoundError",
    "default_template_dir",
]
