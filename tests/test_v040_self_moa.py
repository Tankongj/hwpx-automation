"""v0.4.0: Self-MoA 래퍼 검증.

fake base client 로 N draw + aggregator 동작 확인. 실제 네트워크 호출은 전혀 없음.

v0.10.0: Self-MoA 가 pro 티어 게이트됨 → 각 테스트에서 pro 세션을 설정하거나
``_skip_tier_check=True`` 를 넘긴다.
"""
from __future__ import annotations

import json

import pytest

from src.parser import gemini_resolver
from src.parser.gemini_resolver import GenerateResult
from src.parser.self_moa import SelfMoAClient


@pytest.fixture(autouse=True)
def _grant_pro_tier_for_all_tests(tmp_path, monkeypatch):
    """v0.10.0: Self-MoA 는 pro 전용. 테스트 세션에 pro 부여."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.commerce import tier_gate
    from src.commerce.auth_client import AuthSession
    from src.commerce.user_db import User

    user = User(username="tester", password_hash="x", salt="y", tier="pro")
    tier_gate.set_current_session(AuthSession(user=user, tier="pro"))
    try:
        yield
    finally:
        tier_gate.set_current_session(None)


class SequencedFakeClient:
    """호출 순서대로 다른 응답을 내는 fake client."""

    def __init__(self, responses: list[GenerateResult]) -> None:
        self.responses = list(responses)
        self.model = "fake-model"
        self.calls: list[str] = []

    def generate(self, prompt: str) -> GenerateResult:
        self.calls.append(prompt)
        if not self.responses:
            raise RuntimeError("no more scripted responses")
        return self.responses.pop(0)


def _mk(text="[]", in_tok=100, out_tok=50, price_in=0.15, price_out=0.60):
    return GenerateResult(
        text=text,
        input_tokens=in_tok,
        output_tokens=out_tok,
        price_input_usd_per_m=price_in,
        price_output_usd_per_m=price_out,
        model="fake-model",
    )


# ---------------------------------------------------------------------------
# Basic flow
# ---------------------------------------------------------------------------

def test_self_moa_invokes_base_n_plus_one_times():
    """draws=3 → base 3회 + aggregator 1회 = 총 4회 호출."""
    client = SequencedFakeClient([
        _mk(text='[{"line_no":1,"level":6,"reason":"a"}]'),
        _mk(text='[{"line_no":1,"level":6,"reason":"b"}]'),
        _mk(text='[{"line_no":1,"level":6,"reason":"c"}]'),
        _mk(text='[{"line_no":1,"level":6,"reason":"final"}]'),   # aggregator
    ])
    moa = SelfMoAClient(client, draws=3)
    result = moa.generate("입력 프롬프트")

    assert len(client.calls) == 4
    # 첫 3번 호출은 원 prompt 그대로
    assert client.calls[0] == "입력 프롬프트"
    assert client.calls[1] == "입력 프롬프트"
    assert client.calls[2] == "입력 프롬프트"
    # 4번째(aggregator) 호출에는 원 prompt 와 independent 응답들이 포함
    agg_prompt = client.calls[3]
    assert "입력 프롬프트" in agg_prompt
    assert "응답 1" in agg_prompt
    assert "응답 3" in agg_prompt


def test_self_moa_tokens_are_summed():
    """모든 draw + aggregator 의 토큰이 합산돼야 한다."""
    client = SequencedFakeClient([
        _mk(in_tok=100, out_tok=50),
        _mk(in_tok=110, out_tok=60),
        _mk(in_tok=120, out_tok=70),
        _mk(in_tok=500, out_tok=30),   # aggregator
    ])
    moa = SelfMoAClient(client, draws=3)
    result = moa.generate("x")

    assert result.input_tokens == 100 + 110 + 120 + 500
    assert result.output_tokens == 50 + 60 + 70 + 30


def test_self_moa_model_string_reflects_config():
    client = SequencedFakeClient([_mk(), _mk(), _mk(), _mk()])
    moa = SelfMoAClient(client, draws=3)
    moa.generate("x")
    assert "self-moa" in moa.model
    assert "×3" in moa.model or "x3" in moa.model.lower()


def test_self_moa_minimum_draws_requires_two():
    with pytest.raises(ValueError):
        SelfMoAClient(SequencedFakeClient([]), draws=0)


def test_self_moa_single_draw_would_be_pointless_but_still_works():
    """draws=1 은 이상하지만 허용. aggregator 생략하고 draw 그대로 반환."""
    client = SequencedFakeClient([_mk(text="[]")])
    moa = SelfMoAClient(client, draws=1)
    result = moa.generate("x")
    # 1 draw 만 성공 → 그대로 반환, aggregator 호출 없음
    assert len(client.calls) == 1
    assert result.text == "[]"


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

def test_self_moa_partial_draw_failure_still_aggregates():
    """3 draws 중 1 실패 → 나머지 2 개로 aggregate."""
    class FlakyClient:
        def __init__(self) -> None:
            self.model = "flaky"
            self.n = 0
            self.calls = 0

        def generate(self, prompt: str) -> GenerateResult:
            self.calls += 1
            self.n += 1
            if self.n == 2:
                raise RuntimeError("중간 네트워크 오류")
            return _mk(text=f'[{{"line_no":1,"level":6,"reason":"#{self.n}"}}]')

    client = FlakyClient()
    moa = SelfMoAClient(client, draws=3)
    result = moa.generate("x")
    # 3 draws 시도 + 2 성공 + 1 aggregator = 4 calls
    assert client.calls == 4
    # 결과는 aggregator 응답
    assert "#" in result.text or "line_no" in result.text


def test_self_moa_all_draws_fail_raises():
    class AlwaysFail:
        model = "fail"

        def generate(self, prompt: str) -> GenerateResult:
            raise RuntimeError("항상 실패")

    moa = SelfMoAClient(AlwaysFail(), draws=3)
    with pytest.raises(RuntimeError, match="모든 draw 실패"):
        moa.generate("x")


# ---------------------------------------------------------------------------
# Factory integration
# ---------------------------------------------------------------------------

def test_factory_wraps_base_when_self_moa_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config

    cfg = app_config.AppConfig(
        resolver_backend="gemini",
        gemini_model="gemini-2.5-flash",
        use_self_moa=True,
        self_moa_draws=3,
    )
    app_config.save(cfg)

    # GoogleGenAIClient 생성자가 키를 요구하므로 patch
    from src.parser import gemini_resolver as gr

    class _FakeGemini:
        model = "gemini-2.5-flash"

        def generate(self, prompt):  # pragma: no cover
            return _mk()

    monkeypatch.setattr(gr, "GoogleGenAIClient", lambda model=None, **kw: _FakeGemini())

    client = gemini_resolver.create_default_client()
    assert isinstance(client, SelfMoAClient)
    assert client.draws == 3


def test_factory_does_not_wrap_when_self_moa_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config

    cfg = app_config.AppConfig(resolver_backend="gemini", use_self_moa=False)
    app_config.save(cfg)

    from src.parser import gemini_resolver as gr

    class _FakeGemini:
        model = "gemini-2.5-flash"

    monkeypatch.setattr(gr, "GoogleGenAIClient", lambda model=None, **kw: _FakeGemini())
    client = gemini_resolver.create_default_client()
    assert not isinstance(client, SelfMoAClient)
