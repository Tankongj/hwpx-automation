"""API Key 저장/로드.

**우선순위** (기획안 4.7):
1. Windows ``keyring`` (자격 증명 관리자) — 가장 안전.
2. Fallback: ``%APPDATA%\\HwpxAutomation\\keys.enc`` 에 Fernet 으로 암호화 저장.
   암호화 키는 OS 별로 유도(Derived via PBKDF2 w/ fixed salt + user-specific seed) 하되,
   최소한 plaintext 저장은 피한다.

주의
----
- 코드 어디서도 로그/에러/예외 메시지에 API Key 를 출력하지 않는다.
- ``.env`` 환경변수(``GEMINI_API_KEY``) 는 **개발 편의용 override** 로만 읽는다.
- 저장 전 간단한 형식 검증(빈 문자열 거부, 공백 trim).

Public API
----------
- :class:`ApiKeyManager` — exists/load/save/delete
- :func:`get_key()` / :func:`set_key(k)` / :func:`has_key()` / :func:`delete_key()`
  (싱글톤 기반 편의 함수)
"""
from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from ..utils.logger import get_logger


_log = get_logger("settings.api_key")


PathLike = Union[str, Path]


KEYRING_SERVICE = "HwpxAutomation"
KEYRING_USERNAME = "gemini-api-key"           # Gemini 기본값 (back-compat)
ENCRYPTED_FILENAME = "keys.enc"               # Gemini 용 (레거시)
ENV_OVERRIDE = "GEMINI_API_KEY"               # Gemini 기본값 (back-compat)

# v0.3.0 — 다중 서비스 지원. 각 service 마다 keyring username / env 변수 / fernet 파일 분리.
SERVICE_CONFIG: dict[str, dict[str, str]] = {
    "gemini": {
        "username": "gemini-api-key",
        "env": "GEMINI_API_KEY",
        "enc_file": "keys.enc",
        "display": "Gemini",
    },
    "openai": {
        "username": "openai-api-key",
        "env": "OPENAI_API_KEY",
        "enc_file": "keys.openai.enc",
        "display": "OpenAI",
    },
    "anthropic": {
        "username": "anthropic-api-key",
        "env": "ANTHROPIC_API_KEY",
        "enc_file": "keys.anthropic.enc",
        "display": "Anthropic",
    },
}


def _service_spec(service: str) -> dict[str, str]:
    s = (service or "gemini").lower()
    if s not in SERVICE_CONFIG:
        raise ValueError(f"지원하지 않는 서비스: {service} (허용: {list(SERVICE_CONFIG)})")
    return SERVICE_CONFIG[s]

# Fernet 용 KDF 파라미터 (고정 salt — 이 파일 단독으로는 복호화 불가하게 user id 도 섞음)
_KDF_SALT = b"hwpx-automation-v2/salt/2026"
_KDF_ITERATIONS = 200_000


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _base_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "HwpxAutomation"
    return Path.home() / ".hwpx-automation"


def _encrypted_file(base: Optional[PathLike] = None, filename: str = ENCRYPTED_FILENAME) -> Path:
    root = Path(base) if base else _base_dir()
    return root / filename


# ---------------------------------------------------------------------------
# Fernet helpers (fallback path)
# ---------------------------------------------------------------------------

def _machine_seed() -> bytes:
    """OS 사용자 이름 + 환경 hash — 같은 PC 같은 사용자에서만 복호화 가능하게."""
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
    # Python 의 uuid.getnode() 는 MAC 주소 해시를 주지만 VM 에선 바뀔 수 있음 → user 로도 충분
    return f"{user}/hwpx-automation-v2".encode("utf-8")


def _derive_fernet_key() -> bytes:
    """PBKDF2 로 32 바이트 키 → base64url 인코딩 (Fernet 요구 포맷)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_KDF_SALT,
        iterations=_KDF_ITERATIONS,
    )
    raw = kdf.derive(_machine_seed())
    return base64.urlsafe_b64encode(raw)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

@dataclass
class ApiKeyManager:
    """Keyring 우선, 실패 시 Fernet 파일 fallback. 하나의 서비스(Gemini/OpenAI/Anthropic) 를
    담당.

    Parameters
    ----------
    fallback_path : 개발/테스트 시 base 디렉토리를 override. 기본은 APPDATA.
    keyring_service / keyring_username : keyring 식별자 커스터마이즈(테스트 용).
    env_var : ENV override 변수 이름.
    enc_filename : Fernet fallback 파일명.
    """

    fallback_path: Optional[Path] = None
    keyring_service: str = KEYRING_SERVICE
    keyring_username: str = KEYRING_USERNAME
    env_var: str = ENV_OVERRIDE
    enc_filename: str = ENCRYPTED_FILENAME

    @classmethod
    def for_service(cls, service: str, *, fallback_path: Optional[Path] = None) -> "ApiKeyManager":
        """서비스명(gemini/openai/anthropic) 으로 매니저 생성."""
        spec = _service_spec(service)
        return cls(
            fallback_path=fallback_path,
            keyring_service=KEYRING_SERVICE,
            keyring_username=spec["username"],
            env_var=spec["env"],
            enc_filename=spec["enc_file"],
        )

    # ---- public api ----

    def exists(self) -> bool:
        return self.load() is not None

    def load(self) -> Optional[str]:
        """저장된 키 반환. 없으면 ``None``.

        ENV override (개발 편의) > keyring > Fernet 파일 순으로 탐색.
        """
        env_key = os.environ.get(self.env_var)
        if env_key and env_key.strip():
            _log.debug("API Key: env override 사용 (%s)", self.env_var)
            return env_key.strip()

        k = self._load_keyring()
        if k:
            return k

        k = self._load_encrypted_file()
        if k:
            return k

        return None

    def save(self, api_key: str) -> str:
        """키 저장. 반환: 사용된 스토리지 ('keyring' | 'fernet')."""
        cleaned = (api_key or "").strip()
        if not cleaned:
            raise ValueError("API Key 가 비어 있습니다")

        try:
            self._save_keyring(cleaned)
            _log.info("API Key 저장 완료 (keyring)")
            return "keyring"
        except Exception as exc:  # noqa: BLE001
            _log.warning("keyring 저장 실패 (%s) → Fernet 파일로 fallback", exc)

        self._save_encrypted_file(cleaned)
        _log.info("API Key 저장 완료 (Fernet)")
        return "fernet"

    def delete(self) -> None:
        """키 제거 (두 스토리지 모두)."""
        try:
            self._delete_keyring()
        except Exception:  # noqa: BLE001 - 이미 없거나 미지원이면 무시
            pass

        enc_file = _encrypted_file(self.fallback_path, filename=self.enc_filename)
        if enc_file.exists():
            try:
                enc_file.unlink()
            except OSError as exc:
                _log.warning("Fernet 파일 삭제 실패: %s", exc)

    # ---- internal ----

    def _load_keyring(self) -> Optional[str]:
        try:
            import keyring  # lazy

            val = keyring.get_password(self.keyring_service, self.keyring_username)
            return val if val and val.strip() else None
        except Exception as exc:  # noqa: BLE001
            _log.debug("keyring 조회 실패 (%s)", exc)
            return None

    def _save_keyring(self, api_key: str) -> None:
        import keyring

        keyring.set_password(self.keyring_service, self.keyring_username, api_key)

    def _delete_keyring(self) -> None:
        import keyring

        keyring.delete_password(self.keyring_service, self.keyring_username)

    def _load_encrypted_file(self) -> Optional[str]:
        path = _encrypted_file(self.fallback_path, filename=self.enc_filename)
        if not path.exists():
            return None
        try:
            from cryptography.fernet import Fernet, InvalidToken

            blob = path.read_bytes()
            f = Fernet(_derive_fernet_key())
            return f.decrypt(blob).decode("utf-8")
        except InvalidToken:
            _log.error("Fernet 복호화 실패 — 사용자 프로필/환경이 바뀌었을 수 있음")
        except Exception as exc:  # noqa: BLE001
            _log.error("Fernet 파일 읽기 실패 (%s)", exc)
        return None

    def _save_encrypted_file(self, api_key: str) -> None:
        from cryptography.fernet import Fernet

        path = _encrypted_file(self.fallback_path, filename=self.enc_filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        f = Fernet(_derive_fernet_key())
        blob = f.encrypt(api_key.encode("utf-8"))
        tmp = path.with_suffix(".enc.tmp")
        tmp.write_bytes(blob)
        tmp.replace(path)


# ---------------------------------------------------------------------------
# Module-level convenience (per-service singletons)
# ---------------------------------------------------------------------------

_default_manager: Optional[ApiKeyManager] = None       # Gemini back-compat
_service_managers: dict[str, ApiKeyManager] = {}        # v0.3.0+ per-service


def _singleton() -> ApiKeyManager:
    """Gemini 기본 싱글턴 (back-compat)."""
    global _default_manager
    if _default_manager is None:
        _default_manager = ApiKeyManager.for_service("gemini")
    return _default_manager


def _manager(service: Optional[str]) -> ApiKeyManager:
    """서비스별 매니저. service=None 이면 Gemini 싱글턴."""
    if service is None or service.lower() == "gemini":
        return _singleton()
    key = service.lower()
    if key not in _service_managers:
        _service_managers[key] = ApiKeyManager.for_service(key)
    return _service_managers[key]


def get_key(service: Optional[str] = None) -> Optional[str]:
    return _manager(service).load()


def set_key(api_key: str, service: Optional[str] = None) -> str:
    return _manager(service).save(api_key)


def has_key(service: Optional[str] = None) -> bool:
    return _manager(service).exists()


def delete_key(service: Optional[str] = None) -> None:
    _manager(service).delete()


def reset_singleton(manager: Optional[ApiKeyManager] = None) -> None:
    """테스트에서 Gemini 기본 싱글턴을 교체하기 위한 훅. 다른 서비스 캐시도 초기화."""
    global _default_manager
    _default_manager = manager
    _service_managers.clear()


__all__ = [
    "ApiKeyManager",
    "KEYRING_SERVICE",
    "KEYRING_USERNAME",
    "ENV_OVERRIDE",
    "get_key",
    "set_key",
    "has_key",
    "delete_key",
    "reset_singleton",
]
