"""W3: API Key 저장/로드.

키링 사용 가능 여부는 CI/개발 PC 마다 달라서, **Fernet fallback 경로** 를 중심으로
테스트한다. 키링 정상 경로는 별도 integration 테스트에서 확인하면 된다.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from src.settings import api_key_manager


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path: Path):
    """각 테스트마다 fresh base 디렉토리 + ENV override 제거."""
    monkeypatch.delenv(api_key_manager.ENV_OVERRIDE, raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    yield


@pytest.fixture
def isolated_keyring_id() -> tuple[str, str]:
    """테스트별 고유 keyring (service, username) — 실제 사용자 키 보호."""
    suffix = uuid.uuid4().hex[:8]
    return (f"HwpxAutomationTest_{suffix}", f"test-{suffix}")


def _force_keyring_unavailable(monkeypatch):
    """keyring 호출을 무조건 실패시켜 Fernet 경로를 탄다.

    save / load / delete 전부 차단 — delete 누락 시 실제 사용자 keyring 엔트리가
    삭제되는 사고가 있었음.
    """
    def _fail(*a, **kw):
        raise RuntimeError("keyring unavailable (test)")

    monkeypatch.setattr(api_key_manager.ApiKeyManager, "_save_keyring",
                        lambda self, k: _fail())
    monkeypatch.setattr(api_key_manager.ApiKeyManager, "_load_keyring",
                        lambda self: None)
    monkeypatch.setattr(api_key_manager.ApiKeyManager, "_delete_keyring",
                        lambda self: None)  # no-op (not raising)


def test_save_and_load_fernet_fallback(tmp_path: Path, monkeypatch):
    _force_keyring_unavailable(monkeypatch)
    mgr = api_key_manager.ApiKeyManager(fallback_path=tmp_path)
    assert mgr.load() is None
    assert mgr.exists() is False

    storage = mgr.save("AIzaTESTKEYsample")
    assert storage == "fernet"

    # 암호화 파일이 실제로 생성됐고 plaintext 가 아니어야 한다
    enc_path = tmp_path / api_key_manager.ENCRYPTED_FILENAME
    assert enc_path.exists()
    assert b"AIzaTESTKEYsample" not in enc_path.read_bytes()

    # round-trip
    assert mgr.load() == "AIzaTESTKEYsample"
    assert mgr.exists() is True


def test_delete_removes_fernet_file(tmp_path: Path, monkeypatch):
    _force_keyring_unavailable(monkeypatch)
    mgr = api_key_manager.ApiKeyManager(fallback_path=tmp_path)
    mgr.save("AIza_another_key")
    mgr.delete()
    assert mgr.load() is None
    assert not (tmp_path / api_key_manager.ENCRYPTED_FILENAME).exists()


def test_env_override_takes_precedence(tmp_path: Path, monkeypatch):
    _force_keyring_unavailable(monkeypatch)
    mgr = api_key_manager.ApiKeyManager(fallback_path=tmp_path)
    mgr.save("AIzaSTORED")
    monkeypatch.setenv(api_key_manager.ENV_OVERRIDE, "AIzaENV_OVERRIDE")
    assert mgr.load() == "AIzaENV_OVERRIDE"


def test_empty_key_is_rejected(tmp_path: Path, monkeypatch):
    _force_keyring_unavailable(monkeypatch)
    mgr = api_key_manager.ApiKeyManager(fallback_path=tmp_path)
    with pytest.raises(ValueError):
        mgr.save("   ")
    with pytest.raises(ValueError):
        mgr.save("")


def test_keyring_path_success(monkeypatch, tmp_path: Path, isolated_keyring_id):
    """keyring 이 정상 작동하면 fernet 파일이 **생성되지 않아야** 한다.

    **실제 keyring 을 건드리지 않도록** _save_keyring/_load_keyring/_delete_keyring
    모두 dict 기반 fake 로 대체.
    """
    storage: dict[tuple[str, str], str] = {}
    svc, usr = isolated_keyring_id

    def fake_save(self, api_key):
        storage[(self.keyring_service, self.keyring_username)] = api_key

    def fake_load(self):
        return storage.get((self.keyring_service, self.keyring_username))

    def fake_delete(self):
        storage.pop((self.keyring_service, self.keyring_username), None)

    monkeypatch.setattr(api_key_manager.ApiKeyManager, "_save_keyring", fake_save)
    monkeypatch.setattr(api_key_manager.ApiKeyManager, "_load_keyring", fake_load)
    monkeypatch.setattr(api_key_manager.ApiKeyManager, "_delete_keyring", fake_delete)

    mgr = api_key_manager.ApiKeyManager(
        fallback_path=tmp_path, keyring_service=svc, keyring_username=usr
    )
    assert mgr.save("AIzaKEYRING") == "keyring"
    assert not (tmp_path / api_key_manager.ENCRYPTED_FILENAME).exists()
    assert mgr.load() == "AIzaKEYRING"


def test_module_level_convenience(monkeypatch, tmp_path: Path, isolated_keyring_id):
    """싱글턴 편의함수. 고유 keyring_service 로 격리해 실제 사용자 키를 건드리지 않음."""
    _force_keyring_unavailable(monkeypatch)
    svc, usr = isolated_keyring_id
    custom = api_key_manager.ApiKeyManager(
        fallback_path=tmp_path, keyring_service=svc, keyring_username=usr
    )
    api_key_manager.reset_singleton(custom)
    try:
        assert api_key_manager.has_key() is False
        assert api_key_manager.set_key("AIzaCONV") == "fernet"
        assert api_key_manager.get_key() == "AIzaCONV"
        api_key_manager.delete_key()
        assert api_key_manager.has_key() is False
    finally:
        api_key_manager.reset_singleton(None)
