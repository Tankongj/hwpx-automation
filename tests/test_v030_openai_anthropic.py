"""v0.3.0: OpenAI / Anthropic 백엔드 + 다중 서비스 API Key 매니저.

실제 API 호출 없이 SDK 클라이언트 부분만 MagicMock 으로 대체해 ResolverClient 프로토콜
준수 + 토큰/요금 집계를 검증한다.
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.parser import gemini_resolver
from src.parser.gemini_resolver import GenerateResult, resolve
from src.parser.ir_schema import Block
from src.settings import api_key_manager


# ---------------------------------------------------------------------------
# Multi-service api_key_manager
# ---------------------------------------------------------------------------

def test_api_key_manager_supports_per_service_storage(tmp_path, monkeypatch):
    """openai/anthropic 각자의 파일에 저장되고 서로 간섭 없음."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    api_key_manager.reset_singleton(None)

    # keyring 전체 차단 → fernet 경로 고정
    def _fail(*a, **kw):
        raise RuntimeError("no keyring")

    monkeypatch.setattr(api_key_manager.ApiKeyManager, "_save_keyring", lambda self, k: _fail())
    monkeypatch.setattr(api_key_manager.ApiKeyManager, "_load_keyring", lambda self: None)
    monkeypatch.setattr(api_key_manager.ApiKeyManager, "_delete_keyring", lambda self: None)

    assert api_key_manager.has_key(service="openai") is False
    assert api_key_manager.has_key(service="anthropic") is False

    api_key_manager.set_key("sk-test-OPENAI", service="openai")
    api_key_manager.set_key("sk-test-ANT", service="anthropic")

    # 서로 독립
    assert api_key_manager.get_key(service="openai") == "sk-test-OPENAI"
    assert api_key_manager.get_key(service="anthropic") == "sk-test-ANT"
    assert api_key_manager.get_key(service="gemini") is None

    # 삭제도 격리
    api_key_manager.delete_key(service="openai")
    assert api_key_manager.get_key(service="openai") is None
    assert api_key_manager.get_key(service="anthropic") == "sk-test-ANT"

    api_key_manager.reset_singleton(None)


def test_api_key_manager_unknown_service_raises():
    with pytest.raises(ValueError, match="지원하지 않는"):
        api_key_manager._service_spec("dropbox")


def test_api_key_manager_env_var_is_per_service(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    api_key_manager.reset_singleton(None)
    try:
        assert api_key_manager.get_key(service="openai") == "sk-env-openai"
        assert api_key_manager.get_key(service="anthropic") is None
    finally:
        api_key_manager.reset_singleton(None)


# ---------------------------------------------------------------------------
# OpenAIClient
# ---------------------------------------------------------------------------

def _install_fake_openai_module(monkeypatch, *, content: str, prompt_tokens=100, completion_tokens=50, finish="stop"):
    """sys.modules 에 openai 모듈 스텁을 주입해 OpenAIClient 가 쓰게 만든다."""
    fake_message = SimpleNamespace(content=content)
    fake_choice = SimpleNamespace(message=fake_message, finish_reason=finish)
    fake_usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    fake_response = SimpleNamespace(choices=[fake_choice], usage=fake_usage)

    fake_completions = MagicMock()
    fake_completions.create.return_value = fake_response
    fake_chat = SimpleNamespace(completions=fake_completions)
    fake_client = SimpleNamespace(chat=fake_chat)

    fake_OpenAI = MagicMock(return_value=fake_client)
    fake_module = SimpleNamespace(OpenAI=fake_OpenAI)
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    return fake_completions


def test_openai_client_happy_path(monkeypatch):
    payload = json.dumps({"items": [{"line_no": 5, "level": 6, "reason": "□"}]})
    mock_completions = _install_fake_openai_module(
        monkeypatch, content=payload, prompt_tokens=1000, completion_tokens=120
    )

    from src.parser.openai_backend import OpenAIClient

    client = OpenAIClient(api_key="sk-test", model="gpt-4o-mini")
    result = client.generate("hi")

    # create() 호출 인자 검증
    kwargs = mock_completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"]["strict"] is True

    # result 필드
    data = json.loads(result.text)
    assert data[0]["line_no"] == 5
    assert result.input_tokens == 1000
    assert result.output_tokens == 120
    assert result.finish_reason == "STOP"
    assert result.model == "gpt-4o-mini"
    # gpt-4o-mini 가격 반영
    assert result.price_input_usd_per_m == 0.15
    assert result.price_output_usd_per_m == 0.60


def test_openai_client_max_tokens_mapping(monkeypatch):
    _install_fake_openai_module(monkeypatch, content='{"items":[]}', finish="length")
    from src.parser.openai_backend import OpenAIClient

    client = OpenAIClient(api_key="sk-x")
    result = client.generate("x")
    assert result.finish_reason == "MAX_TOKENS"


def test_openai_client_missing_key_raises(monkeypatch):
    monkeypatch.setattr(api_key_manager, "get_key", lambda service=None: None)
    from src.parser.openai_backend import OpenAIClient

    with pytest.raises(RuntimeError, match="OpenAI API Key"):
        OpenAIClient(api_key=None)


# ---------------------------------------------------------------------------
# AnthropicClient
# ---------------------------------------------------------------------------

def _install_fake_anthropic_module(monkeypatch, *, items, input_tokens=100, output_tokens=50, stop_reason="tool_use"):
    tool_block = SimpleNamespace(type="tool_use", name="submit_resolution", input={"items": items})
    fake_usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    fake_response = SimpleNamespace(content=[tool_block], usage=fake_usage, stop_reason=stop_reason)

    fake_messages = MagicMock()
    fake_messages.create.return_value = fake_response
    fake_Anthropic_instance = SimpleNamespace(messages=fake_messages)

    fake_Anthropic = MagicMock(return_value=fake_Anthropic_instance)
    fake_module = SimpleNamespace(Anthropic=fake_Anthropic)
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    return fake_messages


def test_anthropic_client_happy_path(monkeypatch):
    mock_messages = _install_fake_anthropic_module(
        monkeypatch,
        items=[{"line_no": 10, "level": 7, "reason": "❍"}],
        input_tokens=800,
        output_tokens=60,
    )
    from src.parser.anthropic_backend import AnthropicClient

    client = AnthropicClient(api_key="sk-ant-test", model="claude-haiku-4-5-20251001")
    result = client.generate("prompt")

    kwargs = mock_messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-haiku-4-5-20251001"
    assert kwargs["tools"][0]["name"] == "submit_resolution"
    assert kwargs["tool_choice"]["name"] == "submit_resolution"

    data = json.loads(result.text)
    assert data[0]["line_no"] == 10
    assert data[0]["level"] == 7
    assert result.input_tokens == 800
    assert result.output_tokens == 60
    assert result.finish_reason == "STOP"   # tool_use → STOP
    # Haiku 가격
    assert result.price_input_usd_per_m == 1.00
    assert result.price_output_usd_per_m == 5.00


def test_anthropic_client_missing_key(monkeypatch):
    monkeypatch.setattr(api_key_manager, "get_key", lambda service=None: None)
    from src.parser.anthropic_backend import AnthropicClient

    with pytest.raises(RuntimeError, match="Anthropic API Key"):
        AnthropicClient(api_key=None)


# ---------------------------------------------------------------------------
# create_default_client factory — 새 backend 들
# ---------------------------------------------------------------------------

def _grant_pro(monkeypatch):
    """v0.10.0: OpenAI/Anthropic 백엔드가 pro 전용 → 테스트 세션에 pro 부여."""
    from src.commerce import tier_gate
    from src.commerce.auth_client import AuthSession
    from src.commerce.user_db import User

    user = User(username="tester", password_hash="x", salt="y", tier="pro")
    tier_gate.set_current_session(AuthSession(user=user, tier="pro"))
    # 테스트 끝나면 세션 초기화
    monkeypatch.setattr(
        tier_gate, "_current_session", tier_gate._current_session, raising=False,
    )


def test_factory_openai_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    _grant_pro(monkeypatch)
    _install_fake_openai_module(monkeypatch, content='{"items":[]}')
    from src.settings import app_config

    cfg = app_config.AppConfig(resolver_backend="openai", openai_model="gpt-4o-mini")
    app_config.save(cfg)

    monkeypatch.setattr(api_key_manager, "get_key", lambda service=None: "sk-test")

    client = gemini_resolver.create_default_client()
    from src.parser.openai_backend import OpenAIClient

    assert isinstance(client, OpenAIClient)
    assert client.model == "gpt-4o-mini"

    # 정리
    from src.commerce import tier_gate
    tier_gate.set_current_session(None)


def test_factory_anthropic_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    _grant_pro(monkeypatch)
    _install_fake_anthropic_module(monkeypatch, items=[])
    from src.settings import app_config

    cfg = app_config.AppConfig(
        resolver_backend="anthropic", anthropic_model="claude-haiku-4-5-20251001"
    )
    app_config.save(cfg)

    monkeypatch.setattr(api_key_manager, "get_key", lambda service=None: "sk-ant-test")

    client = gemini_resolver.create_default_client()
    from src.parser.anthropic_backend import AnthropicClient

    assert isinstance(client, AnthropicClient)

    # 정리
    from src.commerce import tier_gate
    tier_gate.set_current_session(None)


# ---------------------------------------------------------------------------
# End-to-end: resolve() with each new client (mocked) integrates cost correctly
# ---------------------------------------------------------------------------

def test_resolve_with_openai_client_cost_uses_openai_pricing(monkeypatch):
    payload = json.dumps({"items": [{"line_no": 1, "level": 6, "reason": "x"}]})
    _install_fake_openai_module(
        monkeypatch, content=payload, prompt_tokens=10_000, completion_tokens=500
    )
    from src.parser.openai_backend import OpenAIClient

    blocks = [Block(level=6, text="□ " + "x" * 80, raw_line="□ ...", line_no=1, ambiguous=True)]
    client = OpenAIClient(api_key="sk-test", model="gpt-4o-mini")
    report = resolve(blocks, client=client)

    expected_usd = 10_000 * 0.15 / 1_000_000 + 500 * 0.60 / 1_000_000
    assert report.cost.usd == pytest.approx(expected_usd, rel=1e-6)
    assert report.cost.is_local is False
