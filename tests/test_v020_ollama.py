"""v0.2.0: OllamaClient + ResolverClient protocol 일반화 검증.

실제 Ollama 서버는 없어도 돌아가는 테스트 — httpx 를 monkeypatch 해서 응답을 모킹한다.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.parser import gemini_resolver, ollama_backend
from src.parser.gemini_resolver import GenerateResult, resolve
from src.parser.ir_schema import Block
from src.parser.ollama_backend import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    OllamaClient,
    ProbeResult,
    probe_server,
)


# ---------------------------------------------------------------------------
# Helpers: httpx mock
# ---------------------------------------------------------------------------

def _fake_generate_response(
    response_text: str = "[]",
    prompt_eval_count: int = 100,
    eval_count: int = 10,
    done_reason: str = "stop",
    status_code: int = 200,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {
        "response": response_text,
        "prompt_eval_count": prompt_eval_count,
        "eval_count": eval_count,
        "done_reason": done_reason,
        "done": True,
        "model": DEFAULT_MODEL,
    }
    if status_code >= 400:
        from httpx import HTTPStatusError, Response

        real_resp = MagicMock(spec=Response)
        real_resp.status_code = status_code
        real_resp.text = "bad"
        resp.raise_for_status.side_effect = HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=real_resp
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# OllamaClient.generate()
# ---------------------------------------------------------------------------

def test_ollama_client_happy_path():
    fake = _fake_generate_response(
        response_text='[{"line_no": 5, "level": 6, "reason": "□"}]',
        prompt_eval_count=1234,
        eval_count=50,
    )
    client = OllamaClient(host="http://fake", model="qwen2.5:7b")
    with patch("src.parser.ollama_backend.httpx.post", return_value=fake) as post:
        result = client.generate("dummy prompt")

    # httpx.post 호출 인자 검증
    assert post.call_args.args[0] == "http://fake/api/generate"
    payload = post.call_args.kwargs["json"]
    assert payload["model"] == "qwen2.5:7b"
    assert payload["stream"] is False
    assert "format" in payload  # structured output schema 전달 확인

    # 결과 필드
    assert result.text == '[{"line_no": 5, "level": 6, "reason": "□"}]'
    assert result.input_tokens == 1234
    assert result.output_tokens == 50
    assert result.finish_reason == "STOP"
    assert result.model == "qwen2.5:7b"
    # 로컬이므로 요금 0
    assert result.price_input_usd_per_m == 0.0
    assert result.price_output_usd_per_m == 0.0


def test_ollama_client_max_tokens_mapped():
    fake = _fake_generate_response(done_reason="length")
    client = OllamaClient(host="http://fake")
    with patch("src.parser.ollama_backend.httpx.post", return_value=fake):
        result = client.generate("x")
    assert result.finish_reason == "MAX_TOKENS"


def test_ollama_client_connection_error_raises_runtime_error():
    from httpx import ConnectError

    client = OllamaClient(host="http://nope")
    with patch("src.parser.ollama_backend.httpx.post", side_effect=ConnectError("refused")):
        with pytest.raises(RuntimeError, match="연결할 수 없습니다"):
            client.generate("x")


def test_ollama_client_timeout_raises_runtime_error():
    from httpx import TimeoutException

    client = OllamaClient(host="http://fake", timeout=1.0)
    with patch("src.parser.ollama_backend.httpx.post", side_effect=TimeoutException("slow")):
        with pytest.raises(RuntimeError, match="시간 초과"):
            client.generate("x")


def test_ollama_client_http_error_raises_runtime_error():
    fake = _fake_generate_response(status_code=500)
    client = OllamaClient(host="http://fake")
    with patch("src.parser.ollama_backend.httpx.post", return_value=fake):
        with pytest.raises(RuntimeError, match="HTTP 500"):
            client.generate("x")


def test_ollama_client_structured_output_can_be_disabled():
    fake = _fake_generate_response(response_text="[]")
    client = OllamaClient(host="http://fake", structured_output=False)
    with patch("src.parser.ollama_backend.httpx.post", return_value=fake) as post:
        client.generate("x")
    payload = post.call_args.kwargs["json"]
    assert "format" not in payload


# ---------------------------------------------------------------------------
# probe_server()
# ---------------------------------------------------------------------------

def test_probe_server_happy_path():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "models": [
            {"name": "qwen2.5:7b"},
            {"name": "llama3.1:8b"},
        ]
    }
    fake.raise_for_status = MagicMock()
    with patch("src.parser.ollama_backend.httpx.get", return_value=fake):
        result = probe_server("http://fake")
    assert result.ok is True
    assert "qwen2.5:7b" in result.models
    assert "llama3.1:8b" in result.models
    assert "정상" in result.summary()


def test_probe_server_connection_refused():
    from httpx import ConnectError

    with patch("src.parser.ollama_backend.httpx.get", side_effect=ConnectError("x")):
        result = probe_server("http://nope")
    assert result.ok is False
    assert "연결 실패" in result.error
    assert result.summary().startswith("❌")


def test_probe_server_empty_models_warns():
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"models": []}
    fake.raise_for_status = MagicMock()
    with patch("src.parser.ollama_backend.httpx.get", return_value=fake):
        result = probe_server("http://fake")
    assert result.ok is True
    assert result.models == []
    assert "설치된 모델이 없습니다" in result.summary()


# ---------------------------------------------------------------------------
# Cost 계산이 price-per-M 을 존중하는지
# ---------------------------------------------------------------------------

def test_cost_respects_price_per_m_local():
    """로컬 백엔드(요율 0)로 resolve 하면 비용 0 으로 집계."""
    blocks = [
        Block(level=6, text="A" * 100, raw_line="□ A", line_no=1, ambiguous=True),
    ]

    class FakeLocalClient:
        def generate(self, prompt: str) -> GenerateResult:
            return GenerateResult(
                text=json.dumps([{"line_no": 1, "level": 6, "reason": "x"}]),
                input_tokens=50_000,
                output_tokens=5_000,
                price_input_usd_per_m=0.0,
                price_output_usd_per_m=0.0,
                model="qwen2.5:7b",
            )

    report = resolve(blocks, client=FakeLocalClient())
    assert report.cost.usd == 0.0
    assert report.cost.krw == 0.0
    assert report.cost.is_local is True
    assert "로컬" in report.human_summary()


def test_cost_respects_price_per_m_gemini():
    """Gemini 기본 요율일 때는 계산이 이전과 동일."""
    blocks = [
        Block(level=6, text="B" * 80, raw_line="□ B", line_no=2, ambiguous=True),
    ]

    class FakeGeminiClient:
        def generate(self, prompt: str) -> GenerateResult:
            return GenerateResult(
                text=json.dumps([{"line_no": 2, "level": 6, "reason": "x"}]),
                input_tokens=10_000,
                output_tokens=1_000,
                # 기본값 유지 (Gemini 요율)
                model="gemini-2.5-flash",
            )

    report = resolve(blocks, client=FakeGeminiClient())
    expected = (
        10_000 * gemini_resolver.PRICE_INPUT_USD_PER_M / 1_000_000
        + 1_000 * gemini_resolver.PRICE_OUTPUT_USD_PER_M / 1_000_000
    )
    assert report.cost.usd == pytest.approx(expected, rel=1e-6)
    assert report.cost.is_local is False


# ---------------------------------------------------------------------------
# Backend factory (create_default_client)
# ---------------------------------------------------------------------------

def test_create_default_client_returns_ollama_when_config_says_so(tmp_path, monkeypatch):
    """config.resolver_backend='ollama' 면 OllamaClient 반환."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config

    cfg = app_config.AppConfig(
        resolver_backend="ollama",
        ollama_host="http://localhost:11434",
        ollama_model="qwen2.5:7b",
    )
    app_config.save(cfg)

    client = gemini_resolver.create_default_client()
    assert isinstance(client, OllamaClient)
    assert client.model == "qwen2.5:7b"


def test_create_default_client_backend_override_wins(tmp_path, monkeypatch):
    """명시 인자 backend='ollama' 가 config 의 gemini 설정을 무시한다."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config

    cfg = app_config.AppConfig(resolver_backend="gemini")
    app_config.save(cfg)

    client = gemini_resolver.create_default_client(backend="ollama")
    assert isinstance(client, OllamaClient)


def test_create_default_client_none_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config

    cfg = app_config.AppConfig(resolver_backend="none")
    app_config.save(cfg)

    with pytest.raises(RuntimeError, match="비활성"):
        gemini_resolver.create_default_client()


# ---------------------------------------------------------------------------
# Back-compat: GeminiClient alias
# ---------------------------------------------------------------------------

def test_gemini_client_alias_still_works():
    """기존 코드가 GeminiClient 로 import 해도 ResolverClient 로 연결돼야 한다."""
    assert gemini_resolver.GeminiClient is gemini_resolver.ResolverClient
