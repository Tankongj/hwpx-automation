"""v0.11.0: Hypothesis 기반 property-based testing.

**철학**: 결정론 fixture 기반 테스트는 "우리가 생각한 케이스" 만 잡는다. PBT 는 무작위
입력으로 invariant 를 깨뜨리는 케이스를 자동 탐색해 edge case 를 드러낸다.

대상:
- ``_sanitize_hwp_control`` — 어떤 입력이 와도 안전한 텍스트 반환
- ``_drop_noise_tokens`` — 한글/ASCII 있는 토큰은 절대 드롭 안 함
- ``_looks_like_text`` — 순수 한글 / 순수 ASCII 는 항상 True
- ``g2b._parse_g2b_response`` — 임의 dict 가 와도 크래시 없음

Hypothesis profile: 기본 100 examples. CI 에서 ``--hypothesis-profile=ci`` 로 500 사용 가능.
"""
from __future__ import annotations

import pytest
from hypothesis import given, strategies as st, settings


# ---------------------------------------------------------------------------
# HWP 파서 속성들
# ---------------------------------------------------------------------------


# 한글 음절 strategy
hangul = st.characters(min_codepoint=0xAC00, max_codepoint=0xD7A3)
# 인쇄 ASCII
ascii_p = st.characters(min_codepoint=0x20, max_codepoint=0x7E)
# HWP 제어문자
hwp_control = st.characters(min_codepoint=0, max_codepoint=0x1F)


@given(st.text(alphabet=hangul, min_size=1, max_size=50))
@settings(max_examples=100)
def test_looks_like_text_always_true_for_pure_hangul(s):
    """순수 한글은 항상 텍스트로 인정."""
    from src.checklist.hwp_text import _looks_like_text
    assert _looks_like_text(s) is True


@given(st.text(alphabet=ascii_p, min_size=1, max_size=50))
@settings(max_examples=100)
def test_looks_like_text_always_true_for_pure_ascii(s):
    """순수 인쇄 ASCII 도 항상 True."""
    from src.checklist.hwp_text import _looks_like_text
    # 빈 문자열 대비 (min_size=1 로 보호하지만 공백만 올 수도 있음)
    assert _looks_like_text(s) is True


@given(st.text(min_size=0, max_size=500))
@settings(max_examples=200)
def test_sanitize_never_raises(s):
    """어떤 입력이 와도 _sanitize_hwp_control 이 예외를 던지지 않음."""
    from src.checklist.hwp_text import _sanitize_hwp_control
    result = _sanitize_hwp_control(s)
    assert isinstance(result, str)


@given(st.text(min_size=0, max_size=500))
@settings(max_examples=200)
def test_sanitize_never_grows_length(s):
    """sanitize 는 텍스트를 줄이거나 같은 길이로 만듬 — 늘어나지 않음.

    정확히: 제어문자 → 공백 (1:1), NUL+인접 → 토큰 드롭, 길어지지 않음.
    """
    from src.checklist.hwp_text import _sanitize_hwp_control
    result = _sanitize_hwp_control(s)
    assert len(result) <= len(s), f"입력 {len(s)}자 → 출력 {len(result)}자 (늘어남)"


@given(st.lists(st.text(alphabet=hangul, min_size=2, max_size=8), min_size=1, max_size=10))
@settings(max_examples=100)
def test_drop_noise_never_drops_hangul_tokens(tokens):
    """한글 포함 토큰은 _drop_noise_tokens 가 절대 제거 안 함."""
    from src.checklist.hwp_text import _drop_noise_tokens
    text = " ".join(tokens)
    result = _drop_noise_tokens(text)
    for tok in tokens:
        assert tok in result, f"한글 토큰 '{tok}' 이 {result!r} 에서 사라짐"


@given(st.lists(st.text(alphabet=ascii_p.filter(lambda c: c != " "), min_size=1, max_size=8), min_size=1, max_size=10))
@settings(max_examples=100)
def test_drop_noise_never_drops_ascii_tokens(tokens):
    """ASCII 포함 토큰도 보존."""
    from src.checklist.hwp_text import _drop_noise_tokens
    text = " ".join(tokens)
    result = _drop_noise_tokens(text)
    for tok in tokens:
        assert tok in result, f"ASCII 토큰 '{tok}' 이 사라짐 (입력: {text!r}, 출력: {result!r})"


# ---------------------------------------------------------------------------
# G2B 파서 robustness
# ---------------------------------------------------------------------------


@given(st.one_of(
    st.just({}),
    st.just({"response": {}}),
    st.just({"response": {"header": {}, "body": {}}}),
    st.just({"response": {"header": {"resultCode": "99", "resultMsg": "ERROR"}}}),
    st.just({"response": {"body": {"items": [], "totalCount": 0}}}),
    st.just({"response": {"body": {"items": {"item": {"bidNtceNo": "X", "bidNtceNm": "Y"}}}}}),
))
@settings(max_examples=20)
def test_g2b_parse_handles_any_shape(data):
    """임의 형태의 G2B 응답도 크래시 없이 :class:`G2BSearchResult` 반환."""
    from src.checklist.g2b_adapter import _parse_g2b_response, G2BSearchResult
    r = _parse_g2b_response(data, page=1, per_page=10)
    assert isinstance(r, G2BSearchResult)


# ---------------------------------------------------------------------------
# Firebase tier parser robustness
# ---------------------------------------------------------------------------


@given(st.one_of(
    st.just({}),
    st.just({"idToken": ""}),
    st.just({"idToken": "not.a.jwt"}),
    st.just({"idToken": "only.onesegment"}),
    st.text(max_size=100).map(lambda s: {"idToken": s}),
))
@settings(max_examples=50)
def test_parse_firebase_tier_always_returns_valid_tier(payload):
    """파싱 실패 어떤 경우에도 'free'/'pro'/'team' 중 하나 반환."""
    from src.commerce.auth_client import _parse_firebase_tier
    tier = _parse_firebase_tier(payload)
    assert tier in ("free", "pro", "team")


# ---------------------------------------------------------------------------
# AI disclosure invariants
# ---------------------------------------------------------------------------


@given(st.text(min_size=1, max_size=30))
@settings(max_examples=50)
def test_make_disclosure_always_has_version_and_backend(backend):
    """어떤 backend 이름을 넘겨도 format_file_meta 가 메타 구성 가능."""
    from src.commerce.ai_disclosure import make_disclosure
    disc = make_disclosure(backend=backend, ai_used=True, version="0.11.0")
    meta = disc.format_file_meta()
    if disc.enabled:
        assert "0.11.0" in meta
        assert backend in meta
