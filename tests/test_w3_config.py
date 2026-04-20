"""W3: src.settings.app_config 로드/저장 확인."""
from __future__ import annotations

import json
from pathlib import Path

from src.settings import app_config


def test_load_returns_defaults_when_missing(tmp_path: Path):
    cfg = app_config.load(tmp_path / "nonexistent.json")
    assert isinstance(cfg, app_config.AppConfig)
    assert cfg.version == app_config.CONFIG_VERSION
    assert cfg.use_gemini is True
    assert cfg.first_run_completed is False


def test_save_then_load_round_trip(tmp_path: Path):
    target = tmp_path / "config.json"
    cfg = app_config.AppConfig(
        use_gemini=False,
        gemini_daily_cap=500,
        first_run_completed=True,
        log_level="DEBUG",
    )
    written = app_config.save(cfg, target)
    assert written == target
    loaded = app_config.load(target)
    assert loaded.use_gemini is False
    assert loaded.gemini_daily_cap == 500
    assert loaded.first_run_completed is True
    assert loaded.log_level == "DEBUG"


def test_unknown_fields_preserved_in_extras(tmp_path: Path):
    target = tmp_path / "config.json"
    # 알려지지 않은 필드가 있는 파일을 미리 기록
    target.write_text(
        json.dumps({
            "version": 1,
            "use_gemini": True,
            "future_feature": "ready",
        }),
        encoding="utf-8",
    )
    cfg = app_config.load(target)
    assert cfg.extras.get("future_feature") == "ready"


def test_load_corrupted_file_returns_defaults(tmp_path: Path):
    target = tmp_path / "bad.json"
    target.write_text("{invalid json}", encoding="utf-8")
    cfg = app_config.load(target)
    assert cfg.version == app_config.CONFIG_VERSION


def test_config_path_under_appdata_or_home(monkeypatch):
    monkeypatch.setenv("APPDATA", r"C:\Users\x\AppData\Roaming")
    p = app_config.config_path()
    assert str(p).endswith("HwpxAutomation\\config.json") or str(p).endswith(
        "HwpxAutomation/config.json"
    )
