"""원격 에러 트래킹 — v0.11.0 Sentry opt-in 스캐폴드.

설계 원칙
---------
- **기본 off** — AppConfig 에서 opt-in 할 때만 켜짐
- **의존성 옵셔널** — `sentry-sdk` 미설치 환경에서도 앱은 정상 동작 (import 실패 무시)
- **DSN 없으면 no-op** — 유료 Sentry 계정 없이도 코드베이스 안전
- **PIPA / GDPR 대비** — 사용자 PII (이메일/이름) 는 기본 스크러빙

사용자 입장:
1. 설정 탭 → "원격 에러 보고 (옵션)" 체크 + DSN 입력
2. 예외 발생 시 자동으로 Sentry 에 전송, 개발자에게 스택트레이스 도달
3. 언제든 해지 가능 → 전송 즉시 중단

의존성 설치 (옵션):
    pip install sentry-sdk>=2.15
"""
from __future__ import annotations

from typing import Optional

from .logger import get_logger


_log = get_logger("utils.error_reporter")


_initialized: bool = False
_dsn: Optional[str] = None


def init(
    dsn: Optional[str],
    *,
    release: Optional[str] = None,
    environment: str = "production",
    sample_rate: float = 1.0,
    traces_sample_rate: float = 0.0,
) -> bool:
    """Sentry 초기화. 성공 시 True, 실패 / skip 시 False.

    Parameters
    ----------
    dsn : Sentry 프로젝트 DSN. None 또는 빈 문자열이면 no-op.
    release : 앱 버전 (예: "0.11.0"). 이슈 리포트의 release 필드에 들어감.
    environment : "production" / "development" / "test".
    sample_rate : 에러 샘플링 (0.0~1.0). 기본 모두 전송.
    traces_sample_rate : 성능 추적 샘플링 (기본 0 — 무료 플랜 아끼기).

    실패 원인 3 가지:
    1. `dsn` 비어 있음 → no-op (정상)
    2. `sentry-sdk` 미설치 → no-op + 경고 로그
    3. `sentry_sdk.init()` 이 raise → no-op + 에러 로그
    """
    global _initialized, _dsn

    if not dsn:
        _log.debug("Sentry DSN 없음 — 에러 리포팅 비활성")
        _initialized = False
        return False

    try:
        import sentry_sdk  # type: ignore
    except ImportError:
        _log.info("sentry-sdk 미설치 — 에러 리포팅 비활성. `pip install sentry-sdk` 후 재시도.")
        _initialized = False
        return False

    try:
        sentry_sdk.init(
            dsn=dsn,
            release=release,
            environment=environment,
            sample_rate=sample_rate,
            traces_sample_rate=traces_sample_rate,
            # PIPA / GDPR: PII 는 기본 스크러빙 (사용자가 명시 opt-in 안 한 이상)
            send_default_pii=False,
            # 추가 스크러빙 — 이메일/계정 정보가 포함된 환경변수 제거
            before_send=_scrub_pii,
        )
        _initialized = True
        _dsn = dsn
        _log.info("Sentry 초기화 완료 (release=%s, env=%s)", release, environment)
        return True
    except Exception as exc:  # noqa: BLE001
        _log.warning("Sentry 초기화 실패: %s", exc)
        _initialized = False
        return False


def is_initialized() -> bool:
    return _initialized


def capture_exception(exc: Exception, **tags) -> None:
    """수동 에러 보고 (예: except 블록에서).

    초기화 안 됐으면 로컬 로그만. 태그는 Sentry 필터링/검색에 쓰임.
    """
    if not _initialized:
        _log.debug("Sentry 미초기화 — capture_exception 무시: %s", exc)
        return
    try:
        import sentry_sdk  # type: ignore

        with sentry_sdk.push_scope() as scope:
            for k, v in tags.items():
                scope.set_tag(k, str(v))
            sentry_sdk.capture_exception(exc)
    except Exception as e:  # noqa: BLE001
        _log.warning("capture_exception 실패: %s", e)


def capture_message(msg: str, level: str = "info", **tags) -> None:
    """정보성 이벤트 전송 (예: 변환 성공 메트릭)."""
    if not _initialized:
        return
    try:
        import sentry_sdk  # type: ignore

        with sentry_sdk.push_scope() as scope:
            for k, v in tags.items():
                scope.set_tag(k, str(v))
            sentry_sdk.capture_message(msg, level=level)
    except Exception as e:  # noqa: BLE001
        _log.warning("capture_message 실패: %s", e)


def set_user(user_id: Optional[str] = None, username: Optional[str] = None) -> None:
    """사용자 식별자 붙이기 (PII 포함 버전 — 명시 opt-in 필요).

    대부분의 경우엔 `user_id` 해시만 넘기는 게 안전. `username` 은 PIPA 리스크.
    """
    if not _initialized:
        return
    try:
        import sentry_sdk  # type: ignore
        sentry_sdk.set_user({"id": user_id, "username": username})
    except Exception:  # noqa: BLE001
        pass


def _scrub_pii(event: dict, hint: dict) -> Optional[dict]:
    """before_send 훅 — 이메일·환경변수 스크러빙."""
    try:
        # 환경변수 스크러빙
        req = event.get("request", {})
        env = req.get("env", {})
        for k in list(env.keys()):
            lk = k.lower()
            if any(sensitive in lk for sensitive in ("key", "token", "password", "secret")):
                env[k] = "[Filtered]"
        # exception value 에서 이메일 패턴 가리기 (heuristic — 완벽하지 않음)
        for exc in event.get("exception", {}).get("values", []):
            value = exc.get("value", "")
            if "@" in value:
                exc["value"] = _mask_email(value)
    except Exception:  # noqa: BLE001
        pass
    return event


def _mask_email(text: str) -> str:
    """아주 단순한 이메일 마스킹 — a@b.com → a***@b.com."""
    import re
    def _mask(m):
        user, domain = m.group(1), m.group(2)
        if len(user) <= 1:
            return f"{user[:1]}***@{domain}"
        return f"{user[0]}***@{domain}"
    return re.sub(r"([A-Za-z0-9._+-]+)@([A-Za-z0-9.-]+)", _mask, text)


__all__ = [
    "init",
    "is_initialized",
    "capture_exception",
    "capture_message",
    "set_user",
]
