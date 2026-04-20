"""v0.6.1: 체크리스트 재귀 폴더 스캔 + HWP 안내."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.checklist.matcher import build_checklist, _scan_folder
from src.checklist.models import DocumentStatus, RequiredDocument


def _make_doc(id_: str, name: str, hints: list[str]) -> RequiredDocument:
    return RequiredDocument(
        id=id_, name=name, is_required=True, filename_hints=hints,
    )


def test_scan_folder_non_recursive_by_default(tmp_path: Path):
    (tmp_path / "top.pdf").write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "inner.pdf").write_bytes(b"x")

    files = _scan_folder(tmp_path)
    names = [f.name for f in files]
    assert "top.pdf" in names
    assert "inner.pdf" not in names


def test_scan_folder_recursive(tmp_path: Path):
    (tmp_path / "top.pdf").write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "inner.pdf").write_bytes(b"x")

    files = _scan_folder(tmp_path, recursive=True)
    names = [f.name for f in files]
    assert "top.pdf" in names
    assert "inner.pdf" in names


def test_build_checklist_recursive_finds_nested_file(tmp_path: Path):
    """하위 폴더의 사업자등록증 파일을 재귀 모드에선 찾아야 한다."""
    sub = tmp_path / "등록서류"
    sub.mkdir()
    (sub / "사업자등록증_20260315.pdf").write_bytes(b"x")

    docs = [_make_doc("biz", "사업자등록증", ["사업자등록증"])]
    # non-recursive: MISSING
    r1 = build_checklist(docs, tmp_path, recursive=False)
    assert r1.items[0].status == DocumentStatus.MISSING
    # recursive: OK
    r2 = build_checklist(docs, tmp_path, recursive=True)
    assert r2.items[0].status == DocumentStatus.OK


def test_checklist_tab_has_recursive_checkbox(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config
    from src.gui.tabs.checklist_tab import ChecklistTab

    cfg = app_config.AppConfig(default_output_dir=str(tmp_path / "out"))
    tab = ChecklistTab(cfg)
    qtbot.addWidget(tab)
    assert hasattr(tab, "recursive_check")
    assert tab.recursive_check.isChecked() is False
