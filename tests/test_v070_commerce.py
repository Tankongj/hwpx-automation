"""v0.7.0: 상업화 훅 검증 — user_db + updater + telemetry + login dialog."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.commerce.user_db import User, UserStore
from src.commerce.updater import UpdateInfo, _parse_semver, check_for_update
from src.utils import telemetry


# ---------------------------------------------------------------------------
# UserStore
# ---------------------------------------------------------------------------

def test_user_store_register_and_verify(tmp_path: Path):
    store = UserStore(base_path=tmp_path)
    store.register("alice", "password123", email="alice@example.com")

    # 성공
    u = store.verify("alice", "password123")
    assert u is not None
    assert u.username == "alice"

    # 비밀번호 틀림
    assert store.verify("alice", "wrong") is None
    # 없는 사용자
    assert store.verify("bob", "xx") is None


def test_user_store_rejects_short_password(tmp_path: Path):
    store = UserStore(base_path=tmp_path)
    with pytest.raises(ValueError, match="6 자"):
        store.register("alice", "short")


def test_user_store_rejects_duplicate(tmp_path: Path):
    store = UserStore(base_path=tmp_path)
    store.register("alice", "password123")
    with pytest.raises(ValueError, match="이미 존재"):
        store.register("alice", "different")


def test_user_store_password_is_hashed(tmp_path: Path):
    store = UserStore(base_path=tmp_path)
    store.register("alice", "mysecretpw123")
    # 저장된 JSON 에 평문 비밀번호가 없어야 함
    saved = (tmp_path / "users.json").read_text(encoding="utf-8")
    assert "mysecretpw123" not in saved
    assert "password_hash" in saved
    assert "salt" in saved


def test_user_store_delete(tmp_path: Path):
    store = UserStore(base_path=tmp_path)
    store.register("alice", "password123")
    store.register("bob", "password456")
    assert store.delete("alice") is True
    assert store.get("alice") is None
    assert store.get("bob") is not None
    # 없는 사용자 삭제는 False
    assert store.delete("charlie") is False


# ---------------------------------------------------------------------------
# Updater
# ---------------------------------------------------------------------------

def test_parse_semver_basic():
    assert _parse_semver("0.7.0") == (0, 7, 0)
    assert _parse_semver("v1.2.3") == (1, 2, 3)
    assert _parse_semver("0.1.0-dev") == (0, 1, 0)
    assert _parse_semver("1.2") == (1, 2, 0)


def test_check_for_update_newer_available(monkeypatch):
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "tag_name": "v0.8.0",
        "html_url": "https://github.com/x/y/releases/tag/v0.8.0",
        "assets": [
            {"name": "HwpxAutomation-v0.8.0.zip", "browser_download_url": "https://x/zip"}
        ],
        "body": "New features",
    }
    monkeypatch.setattr("src.commerce.updater.httpx.get", lambda *a, **kw: fake_resp)
    info = check_for_update("0.7.0", repo="x/y")
    assert info.available is True
    assert info.latest == "v0.8.0"
    assert info.asset_url == "https://x/zip"


def test_check_for_update_same_version(monkeypatch):
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"tag_name": "0.7.0", "html_url": "", "assets": [], "body": ""}
    monkeypatch.setattr("src.commerce.updater.httpx.get", lambda *a, **kw: fake_resp)
    info = check_for_update("0.7.0", repo="x/y")
    assert info.available is False


def test_check_for_update_network_error(monkeypatch):
    from httpx import ConnectError
    monkeypatch.setattr(
        "src.commerce.updater.httpx.get",
        lambda *a, **kw: (_ for _ in ()).throw(ConnectError("refused")),
    )
    info = check_for_update("0.7.0", repo="x/y")
    assert info.available is False
    assert "네트워크" in info.error


def test_check_for_update_404(monkeypatch):
    fake_resp = MagicMock()
    fake_resp.status_code = 404
    monkeypatch.setattr("src.commerce.updater.httpx.get", lambda *a, **kw: fake_resp)
    info = check_for_update("0.7.0", repo="nonexistent/repo")
    assert info.available is False
    assert "찾을 수 없" in info.error


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _telemetry_isolation():
    telemetry.configure(False)
    yield
    telemetry.configure(False)


def test_telemetry_noop_when_disabled(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    telemetry.configure(False)
    telemetry.record("test_event", foo="bar")
    # 파일 안 만들어졌어야 함
    assert not (tmp_path / "HwpxAutomation" / "telemetry.jsonl").exists()


def test_telemetry_records_when_enabled(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    telemetry.configure(True)
    telemetry.record("e1", a=1)
    telemetry.record("e2", b="x")
    telemetry.record("e1", a=2)   # 중복 이벤트

    path = tmp_path / "HwpxAutomation" / "telemetry.jsonl"
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3

    summary = telemetry.summary()
    assert summary["e1"] == 2
    assert summary["e2"] == 1


def test_telemetry_clear(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    telemetry.configure(True)
    telemetry.record("test")
    path = tmp_path / "HwpxAutomation" / "telemetry.jsonl"
    assert path.exists()
    assert telemetry.clear() is True
    assert not path.exists()
    # 이미 없으면 False
    assert telemetry.clear() is False


# ---------------------------------------------------------------------------
# AppConfig v0.7.0 fields
# ---------------------------------------------------------------------------

def test_appconfig_has_v070_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config

    cfg = app_config.AppConfig(
        require_login=True,
        ad_enabled=True,
        telemetry_optin=True,
        auto_update_check=False,
        update_repo="user/repo",
    )
    app_config.save(cfg)
    loaded = app_config.load()
    assert loaded.require_login is True
    assert loaded.ad_enabled is True
    assert loaded.telemetry_optin is True
    assert loaded.auto_update_check is False
    assert loaded.update_repo == "user/repo"


# ---------------------------------------------------------------------------
# LoginDialog smoke
# ---------------------------------------------------------------------------

def test_login_dialog_register_and_login(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.gui.widgets.login_dialog import LoginDialog

    store = UserStore(base_path=tmp_path)
    # 미리 사용자 등록
    store.register("bob", "password789")

    dlg = LoginDialog(store=store)
    qtbot.addWidget(dlg)

    # 로그인 모드 기본값 검증
    assert dlg._mode == "login"
    # 토글 → 회원가입 모드
    dlg._toggle_mode()
    assert dlg._mode == "register"
    dlg._toggle_mode()
    assert dlg._mode == "login"

    # 잘못된 비밀번호
    dlg.username_edit.setText("bob")
    dlg.password_edit.setText("wrong")
    dlg._on_accept()
    assert dlg.user() is None
    assert "일치하지" in dlg.status_label.text()

    # 맞는 비밀번호
    dlg.password_edit.setText("password789")
    dlg._on_accept()
    assert dlg.user() is not None
    assert dlg.user().username == "bob"


# ---------------------------------------------------------------------------
# Ad placeholder activate/deactivate
# ---------------------------------------------------------------------------

def test_ad_placeholder_activate_deactivate(qtbot):
    from src.gui.widgets.ad_placeholder import AdPlaceholder

    ad = AdPlaceholder()
    qtbot.addWidget(ad)
    # 기본 비활성
    assert ad.is_active is False
    assert ad.height() == 0

    ad.activate(text="테스트 광고", click_url="https://example.com", height=60)
    assert ad.is_active is True
    assert ad.height() == 60

    ad.deactivate()
    assert ad.is_active is False
    assert ad.height() == 0
