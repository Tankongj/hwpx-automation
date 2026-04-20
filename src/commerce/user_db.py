"""로컬 사용자 DB — v0.7.0 회원제 placeholder.

MVP 단계의 "로컬 전용" 회원 시스템. 실제 상업화 단계에선 Firebase Auth 나 자체 백엔드
로 교체 예정이지만, UI/UX 흐름은 이 모듈 뒤로 숨길 수 있도록 :class:`UserStore` 인터페이스
고정.

저장 위치: ``%APPDATA%\\HwpxAutomation\\users.json``

형식::

    {
      "users": [
        {
          "username": "alice",
          "password_hash": "<hex>",
          "salt": "<hex>",
          "email": "alice@example.com",
          "created_at": "2026-04-19",
          "tier": "free"
        }
      ]
    }

비밀번호 해싱: PBKDF2-HMAC-SHA256 + 16바이트 salt + 200_000 iterations.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional, Union


PathLike = Union[str, Path]

USERS_FILENAME = "users.json"
_KDF_ITERATIONS = 200_000
_SALT_BYTES = 16


def _base_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "HwpxAutomation"
    return Path.home() / ".hwpx-automation"


def _hash_password(password: str, salt_hex: str) -> str:
    salt = bytes.fromhex(salt_hex)
    h = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _KDF_ITERATIONS
    )
    return h.hex()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class User:
    username: str
    password_hash: str
    salt: str
    email: str = ""
    created_at: str = field(default_factory=lambda: date.today().isoformat())
    tier: str = "free"    # "free" / "pro" / "team" (미래 확장)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "User":
        return cls(
            username=str(d["username"]),
            password_hash=str(d["password_hash"]),
            salt=str(d["salt"]),
            email=str(d.get("email", "")),
            created_at=str(d.get("created_at", date.today().isoformat())),
            tier=str(d.get("tier", "free")),
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class UserStore:
    """로컬 JSON 기반 사용자 저장소."""

    def __init__(self, base_path: Optional[PathLike] = None) -> None:
        self.path = (Path(base_path) if base_path else _base_dir()) / USERS_FILENAME

    # ---- load/save ----

    def _load(self) -> list[User]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return [User.from_dict(u) for u in (raw.get("users") or []) if isinstance(u, dict)]

    def _save(self, users: list[User]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"users": [u.to_dict() for u in users]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)

    # ---- api ----

    def list_usernames(self) -> list[str]:
        return [u.username for u in self._load()]

    def get(self, username: str) -> Optional[User]:
        for u in self._load():
            if u.username.lower() == username.lower():
                return u
        return None

    def register(self, username: str, password: str, email: str = "") -> User:
        """새 사용자 등록. 이미 있으면 :class:`ValueError`."""
        username = username.strip()
        if not username:
            raise ValueError("사용자명이 비어 있습니다")
        if len(password) < 6:
            raise ValueError("비밀번호는 6 자 이상이어야 합니다")
        if self.get(username) is not None:
            raise ValueError(f"이미 존재하는 사용자: {username}")

        users = self._load()
        salt_hex = secrets.token_hex(_SALT_BYTES)
        new_user = User(
            username=username,
            password_hash=_hash_password(password, salt_hex),
            salt=salt_hex,
            email=email.strip(),
        )
        users.append(new_user)
        self._save(users)
        return new_user

    def verify(self, username: str, password: str) -> Optional[User]:
        """로그인 검증. 성공 시 User, 실패 시 ``None``."""
        u = self.get(username)
        if u is None:
            return None
        expected = _hash_password(password, u.salt)
        if hmac.compare_digest(expected, u.password_hash):
            return u
        return None

    def delete(self, username: str) -> bool:
        users = self._load()
        remaining = [u for u in users if u.username.lower() != username.lower()]
        if len(remaining) == len(users):
            return False
        self._save(remaining)
        return True


__all__ = ["User", "UserStore", "USERS_FILENAME"]
