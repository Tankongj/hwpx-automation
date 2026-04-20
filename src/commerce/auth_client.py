"""로그인 백엔드 추상화 — v0.9.0 스캐폴드, v0.10.0 Firebase REST 실장.

v0.7.0 의 로컬 `UserStore` 위에 ``AuthClient`` protocol 을 얹어, 미래에 Firebase /
자체 백엔드 등 원격 인증으로 교체할 때 UI 는 그대로 두고 구현체만 갈아끼우게 한다.

현재 구현:
- :class:`LocalAuthClient` — 기존 :class:`UserStore` 래퍼 (v0.7.0 호환)
- :class:`FirebaseAuthClient` — **v0.10.0 실장**. Firebase Identity Toolkit REST API
  를 stdlib :mod:`urllib` 만으로 호출 (외부 의존성 X). ``api_key`` 가 있고
  ``_use_stub=False`` 일 때 활성.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional, Protocol

from ..utils.logger import get_logger
from .user_db import User, UserStore


_log = get_logger("commerce.auth_client")


# v0.10.0: Firebase Identity Toolkit v1 endpoints — Web API Key 가 있으면 누구나 호출 가능
_FIREBASE_BASE = "https://identitytoolkit.googleapis.com/v1/accounts"
_FIREBASE_TIMEOUT = 10.0  # 초


# v0.10.1 실제 로그인 UX 개선 준비용: Firebase 에러 코드 → 한국어 안내
# (참고: https://firebase.google.com/docs/reference/rest/auth)
_FIREBASE_ERROR_KR: dict[str, str] = {
    # signIn (verifyPassword)
    "EMAIL_NOT_FOUND": "가입된 이메일이 아닙니다. 먼저 회원가입 해 주세요.",
    "INVALID_PASSWORD": "비밀번호가 일치하지 않습니다.",
    "INVALID_LOGIN_CREDENTIALS": "이메일 또는 비밀번호가 올바르지 않습니다.",
    "USER_DISABLED": "이 계정은 관리자에 의해 비활성화되어 있습니다.",
    # signUp
    "EMAIL_EXISTS": "이미 가입된 이메일입니다. 로그인을 시도하거나 다른 이메일을 사용해 주세요.",
    "OPERATION_NOT_ALLOWED": "이 로그인 방식은 현재 차단되어 있습니다 (관리자 설정).",
    "WEAK_PASSWORD": "비밀번호가 너무 약합니다 (6자 이상).",
    # 공통
    "TOO_MANY_ATTEMPTS_TRY_LATER": "짧은 시간에 너무 많이 시도했습니다. 잠시 후 다시 시도해 주세요.",
    "INVALID_EMAIL": "이메일 형식이 올바르지 않습니다.",
    "MISSING_PASSWORD": "비밀번호를 입력해 주세요.",
    "API_KEY_HTTP_REFERRER_BLOCKED": "Firebase API Key 가 이 환경에서 차단돼 있습니다 (설정 확인 필요).",
}


def firebase_error_to_korean(raw_msg: str) -> str:
    """Firebase 가 돌려주는 영문 에러 메시지를 한국어로 치환.

    - 코드가 정확히 매치되면 친절 문구
    - ``WEAK_PASSWORD : Password should be at least 6 characters`` 같은 세부 문구도 코드 부분만 매치
    - 매치 실패 시 원본 반환 (raw_msg)
    """
    if not raw_msg:
        return "알 수 없는 오류"
    # "CODE : 추가설명" 형태 처리
    head = raw_msg.split(":", 1)[0].strip().upper().replace(" ", "_")
    if head in _FIREBASE_ERROR_KR:
        return _FIREBASE_ERROR_KR[head]
    return raw_msg


@dataclass
class AuthSession:
    """로그인 성공 후 세션. user 는 로컬 `User` 또는 원격 백엔드의 사용자 표현."""

    user: User
    tier: str = "free"
    token: str = ""          # 원격 백엔드용 (로컬은 빈 문자열)
    expires_at: float = 0.0  # Unix epoch (원격용)


class AuthClient(Protocol):
    """인증 백엔드. UI 는 이 프로토콜만 알고 구현체는 런타임에 선택."""

    def login(self, username: str, password: str) -> Optional[AuthSession]:
        """로그인 시도. 실패 시 ``None``."""
        ...

    def register(self, username: str, password: str, email: str = "") -> AuthSession:
        """가입. 실패 시 ``ValueError``."""
        ...

    def logout(self, session: AuthSession) -> None:
        """세션 종료 (원격이면 서버에 알림)."""
        ...


# ---------------------------------------------------------------------------
# Local (기존 UserStore 래퍼)
# ---------------------------------------------------------------------------

class LocalAuthClient:
    """v0.7.0 UserStore 기반 로컬 인증. 네트워크 전송 0."""

    def __init__(self, store: Optional[UserStore] = None) -> None:
        self._store = store or UserStore()

    def login(self, username: str, password: str) -> Optional[AuthSession]:
        user = self._store.verify(username, password)
        if user is None:
            return None
        return AuthSession(user=user, tier=user.tier)

    def register(self, username: str, password: str, email: str = "") -> AuthSession:
        user = self._store.register(username, password, email)
        return AuthSession(user=user, tier=user.tier)

    def logout(self, session: AuthSession) -> None:
        # 로컬은 서버 통지 없음
        _log.info("logout: %s", session.user.username)


# ---------------------------------------------------------------------------
# Firebase Stub — 사용자가 endpoint 설정하면 동작
# ---------------------------------------------------------------------------

class FirebaseAuthClient:
    """Firebase Identity Toolkit REST 어댑터 — **v0.10.0 실장**.

    엔드포인트 (Web API Key 기반 인증 — 프론트엔드용 API):
    - 가입: ``POST /v1/accounts:signUp?key={API_KEY}``
    - 로그인: ``POST /v1/accounts:signInWithPassword?key={API_KEY}``

    Body: ``{"email": ..., "password": ..., "returnSecureToken": true}``

    응답 성공 시 ``idToken`` / ``localId`` / ``expiresIn`` 제공.

    **주의**:
    - HWPX Automation 은 이메일을 username 필드로도 받는다 (Firebase 는 email 필수).
      기존 로컬 ``UserStore`` 는 username 만 받으므로, UI 에서 로그인 폼을 갈라써야
      하지만 이 클래스는 그냥 ``username`` 을 email 로 취급한다.
    - 네트워크 실패 / 401 등은 ``login`` 에서 ``None`` 반환 (로컬과 동일),
      ``register`` 에서는 ``ValueError`` 로 올림.
    - 티어 매핑은 Firebase Custom Claims 가 필요한데 v0.10.0 에선 **전원 free 시작**.
      Firebase Console 에서 Custom Claim 을 부여하면 향후 포크에서 tier 필드로 읽도록 확장.

    ``_use_stub=True`` 를 넘기면 (테스트용) 실제 HTTP 는 안 타고 NotImplementedError.
    """

    def __init__(
        self,
        api_key: str,
        *,
        _use_stub: bool = False,
        _opener=None,
    ) -> None:
        self.api_key = api_key
        self._use_stub = _use_stub
        # 테스트에서 urlopen 대신 fake opener 주입 가능
        self._opener = _opener or urllib.request.urlopen

    # ---- internal HTTP helper ----

    def _post(self, path: str, payload: dict) -> dict:
        """`identitytoolkit` REST POST. 실패 시 예외."""
        if self._use_stub:
            raise NotImplementedError("FirebaseAuthClient: _use_stub=True")
        url = f"{_FIREBASE_BASE}:{path}?key={self.api_key}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with self._opener(req, timeout=_FIREBASE_TIMEOUT) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            # Firebase 가 JSON 에러 본문을 돌려줌 → 파싱해서 한국어로 치환
            try:
                err = json.loads(exc.read().decode("utf-8"))
                msg = err.get("error", {}).get("message", "")
            except Exception:  # noqa: BLE001
                msg = ""
            _log.info("Firebase HTTP %d: %s", exc.code, msg)
            kr = firebase_error_to_korean(msg) if msg else "알 수 없는 오류"
            raise RuntimeError(kr) from exc
        except urllib.error.URLError as exc:
            _log.warning("Firebase 네트워크 실패: %s", exc.reason)
            raise
        return json.loads(raw.decode("utf-8"))

    # ---- API ----

    def login(self, username: str, password: str) -> Optional[AuthSession]:
        try:
            data = self._post(
                "signInWithPassword",
                {"email": username, "password": password, "returnSecureToken": True},
            )
        except NotImplementedError:
            raise
        except Exception as exc:  # noqa: BLE001 - 로컬과 동일하게 실패 시 None
            _log.info("Firebase login 실패: %s", exc)
            return None

        tier = _parse_firebase_tier(data)
        # 로컬 User dataclass 를 재활용 (remote 에선 password_hash/salt 비움)
        user = User(
            username=username,
            password_hash="",
            salt="",
            email=username,
            tier=tier,
        )
        return AuthSession(
            user=user,
            tier=tier,
            token=str(data.get("idToken", "")),
            expires_at=time.time() + float(data.get("expiresIn", "3600")),
        )

    def register(self, username: str, password: str, email: str = "") -> AuthSession:
        try:
            data = self._post(
                "signUp",
                {"email": email or username, "password": password, "returnSecureToken": True},
            )
        except NotImplementedError:
            raise
        except Exception as exc:  # noqa: BLE001
            # v0.10.1+: _post 에서 이미 한국어로 치환된 메시지를 그대로 사용
            raise ValueError(str(exc)) from exc

        tier = _parse_firebase_tier(data)
        user = User(
            username=username,
            password_hash="",
            salt="",
            email=email or username,
            tier=tier,
        )
        return AuthSession(
            user=user,
            tier=tier,
            token=str(data.get("idToken", "")),
            expires_at=time.time() + float(data.get("expiresIn", "3600")),
        )

    def logout(self, session: AuthSession) -> None:
        # Firebase Identity Toolkit 엔 명시 logout 엔드포인트가 없음 (토큰 단명으로 대체)
        _log.info("Firebase logout: %s (토큰 만료에 위임)", session.user.username)


def _parse_firebase_tier(data: dict) -> str:
    """Firebase 응답에서 tier 추론. v0.10.0 에선 Custom Claim 지원은 heuristic 만.

    Firebase Auth REST signInWithPassword 응답의 ``idToken`` 은 JWT. payload 에 custom
    claim (예: ``tier=pro``) 를 심어 두면 아래 로직이 읽어냄. 없으면 ``free`` 기본.
    """
    token = data.get("idToken", "")
    if not token or token.count(".") != 2:
        return "free"
    try:
        import base64 as _b64
        _, payload_b64, _ = token.split(".")
        # JWT padding
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(_b64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return "free"
    tier = payload.get("tier") or payload.get("custom_claims", {}).get("tier")
    if tier in ("pro", "team"):
        return tier
    return "free"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_auth_client(config) -> AuthClient:
    """AppConfig 에서 설정된 백엔드로 AuthClient 반환.

    현재는 `auth_backend` 필드를 확인 (없으면 ``"local"``).
    """
    backend = getattr(config, "auth_backend", "local")
    if backend == "firebase":
        api_key = getattr(config, "firebase_api_key", "")
        if not api_key:
            _log.warning("firebase_api_key 없음 → local 로 fallback")
            return LocalAuthClient()
        return FirebaseAuthClient(api_key=api_key)
    return LocalAuthClient()


__all__ = [
    "AuthSession",
    "AuthClient",
    "LocalAuthClient",
    "FirebaseAuthClient",
    "create_auth_client",
]
