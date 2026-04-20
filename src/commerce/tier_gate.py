"""프로 티어 기능 게이트 — v0.9.0 scaffolding.

MVP 단계에선 모든 기능이 무료. 상업화 진입 시 ``@requires_tier("pro")`` 데코레이터 또는
``is_allowed(feature)`` 체크로 프로 전용 기능을 잠글 수 있게 구조만 준비.

현재 동작:
- 전역 현재 세션(:class:`AuthSession`) 을 :func:`set_current_session` 으로 등록
- 티어 계층: ``free < pro < team`` (수치화: 0 < 1 < 2)
- 데코레이터 적용 함수 호출 시 세션이 None 이거나 티어 부족이면 ``TierDeniedError``

v0.10+ 에서 실제 프로 기능 (Self-MoA / 광고 제거 / 무제한 템플릿 등) 을 이 훅으로 잠글 예정.
"""
from __future__ import annotations

import functools
from typing import Any, Callable, Optional

from ..utils.logger import get_logger
from .auth_client import AuthSession


_log = get_logger("commerce.tier_gate")


_TIER_RANK = {"free": 0, "pro": 1, "team": 2}
_current_session: Optional[AuthSession] = None


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def set_current_session(session: Optional[AuthSession]) -> None:
    """로그인한 세션 등록. None 을 넘기면 로그아웃."""
    global _current_session
    _current_session = session


def current_session() -> Optional[AuthSession]:
    return _current_session


def current_tier() -> str:
    """세션 없으면 'free'."""
    if _current_session is None:
        return "free"
    return _current_session.tier or "free"


# ---------------------------------------------------------------------------
# Tier checks
# ---------------------------------------------------------------------------

class TierDeniedError(Exception):
    """프로/팀 티어가 아니어서 기능 거부."""

    def __init__(self, required: str, actual: str, feature: str = "") -> None:
        self.required = required
        self.actual = actual
        self.feature = feature
        msg = (
            f"{feature + ' 는 ' if feature else ''}'{required}' 티어 이상에서 사용 가능합니다. "
            f"현재 티어: '{actual}'."
        )
        super().__init__(msg)


def is_allowed(required_tier: str, *, tier: Optional[str] = None) -> bool:
    """required_tier 이상인지. tier 파라미터 없으면 global current tier 사용."""
    if tier is None:
        tier = current_tier()
    return _TIER_RANK.get(tier, 0) >= _TIER_RANK.get(required_tier, 0)


def require(required_tier: str, *, feature: str = "") -> None:
    """프로그래밍 방식으로 체크 (데코레이터 못 쓰는 곳에서). 부족하면 예외."""
    actual = current_tier()
    if not is_allowed(required_tier, tier=actual):
        raise TierDeniedError(required=required_tier, actual=actual, feature=feature)


def requires_tier(required_tier: str, *, feature: str = "") -> Callable[[Callable], Callable]:
    """함수 레벨 데코레이터.

    사용 예::

        from src.commerce.tier_gate import requires_tier

        @requires_tier("pro", feature="Self-MoA")
        def use_self_moa(...):
            ...
    """

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            actual = current_tier()
            if not is_allowed(required_tier, tier=actual):
                raise TierDeniedError(
                    required=required_tier, actual=actual,
                    feature=feature or fn.__name__,
                )
            return fn(*args, **kwargs)
        return wrapper
    return deco


__all__ = [
    "TierDeniedError",
    "set_current_session",
    "current_session",
    "current_tier",
    "is_allowed",
    "require",
    "requires_tier",
]
