"""W3: gemini_resolver 의 프롬프트 빌드 / 응답 파싱 / resolve 로직.

실제 Gemini 호출은 하지 않는다 — :class:`GeminiClient` 프로토콜을 만족하는 fake 를
주입해 결정 경로를 확인한다.
"""
from __future__ import annotations

import json
from typing import Sequence
from unittest.mock import MagicMock, patch

import pytest

from src.parser import gemini_resolver, regex_parser
from src.parser.gemini_resolver import GenerateResult, ResolveReport, build_prompt, parse_response, resolve
from src.parser.ir_schema import Block


# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------

class FakeClient:
    def __init__(self, response_text: str, *, in_tokens: int = 100, out_tokens: int = 30) -> None:
        self.response_text = response_text
        self.in_tokens = in_tokens
        self.out_tokens = out_tokens
        self.last_prompt: str = ""
        self.call_count: int = 0

    def generate(self, prompt: str) -> GenerateResult:
        self.call_count += 1
        self.last_prompt = prompt
        return GenerateResult(
            text=self.response_text,
            input_tokens=self.in_tokens,
            output_tokens=self.out_tokens,
        )


# ---------------------------------------------------------------------------
# parse_response
# ---------------------------------------------------------------------------

def test_parse_response_clean_json():
    text = '[{"line_no": 10, "level": 6, "reason": "□ 아래"}]'
    out = parse_response(text)
    assert out == [{"line_no": 10, "level": 6, "reason": "□ 아래"}]


def test_parse_response_with_code_fence():
    text = '```json\n[{"line_no": 5, "level": 0, "reason": "본문"}]\n```'
    out = parse_response(text)
    assert out == [{"line_no": 5, "level": 0, "reason": "본문"}]


def test_parse_response_with_extra_prose():
    text = (
        "다음은 결과입니다:\n"
        '[\n  {"line_no": 3, "level": 2, "reason": "절 제목"}\n]\n'
        "이상입니다."
    )
    out = parse_response(text)
    assert out == [{"line_no": 3, "level": 2, "reason": "절 제목"}]


def test_parse_response_rejects_out_of_range_level():
    text = '[{"line_no": 1, "level": 99, "reason": "bad"}]'
    out = parse_response(text)
    assert out == []


def test_parse_response_garbage_returns_empty():
    assert parse_response("") == []
    assert parse_response("not json") == []
    assert parse_response('{"wrong": "shape"}') == []


def test_parse_response_salvages_truncated_array():
    """응답이 중간에 잘려도 완전한 object 들은 살려낸다."""
    truncated = (
        "[\n"
        '  {"line_no": 1, "level": 6, "reason": "ok"},\n'
        '  {"line_no": 2, "level": 7, "reason": "also ok"},\n'
        '  {"line_no": 3, "level":'
    )
    out = parse_response(truncated)
    assert len(out) == 2
    assert [d["line_no"] for d in out] == [1, 2]


def test_parse_response_filters_malformed_items():
    text = json.dumps([
        {"line_no": 1, "level": 2, "reason": "ok"},
        {"level": 3},                          # line_no 없음 → 버림
        {"line_no": 4, "level": "abc"},        # level 숫자 아님 → 버림
        {"line_no": 5, "level": 6, "reason": "also ok"},
    ])
    out = parse_response(text)
    assert [d["line_no"] for d in out] == [1, 5]


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------

def _make_blocks() -> list[Block]:
    return [
        Block(level=1, text="Ⅰ. 장", symbol="#",  raw_line="# Ⅰ. 장",  line_no=0),
        Block(level=2, text="1 절", symbol="##", raw_line="## 1 절",  line_no=1),
        Block(level=6, text="□ 길어서 애매한 본문이에요 " * 3,
              symbol="□", raw_line="□ 길어서 애매한 본문이에요 …", line_no=2, ambiguous=True),
        Block(level=6, text="□ 짧은 제목", symbol="□",
              raw_line="□ 짧은 제목", line_no=3),
    ]


def test_build_prompt_includes_level_spec_and_target_line():
    blocks = _make_blocks()
    amb = [b for b in blocks if b.ambiguous]
    prompt = build_prompt(amb, blocks)
    assert "Ⅰ. 대장" in prompt
    assert "line_no" in prompt
    # 애매 블록의 line_no 가 프롬프트에 들어가 있어야 한다
    assert str(amb[0].line_no) in prompt


def test_build_prompt_passes_surrounding_context():
    blocks = _make_blocks()
    amb = [blocks[2]]
    prompt = build_prompt(amb, blocks, context_before=2, context_after=1)
    # 앞 줄 (Ⅰ. 장, 1 절) 이 context_before 에 포함
    assert "# Ⅰ. 장" in prompt
    assert "## 1 절" in prompt


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------

def test_resolve_no_ambiguous_returns_zero_cost():
    blocks = [Block(level=1, text="Ⅰ.")]
    report = resolve(blocks, client=FakeClient("[]"))
    assert report.total_ambiguous == 0
    assert report.call_count == 0
    assert report.cost.usd == 0.0


def test_resolve_applies_decisions_and_clears_ambiguous():
    blocks = _make_blocks()
    amb = [b for b in blocks if b.ambiguous]
    # fake 응답: 애매 블록(line_no=2) 를 레벨 0 (본문) 로 재분류
    fake = FakeClient(
        json.dumps([{"line_no": amb[0].line_no, "level": 0, "reason": "긴 문장 → 본문"}]),
        in_tokens=500,
        out_tokens=50,
    )
    report = resolve(blocks, client=fake)
    assert fake.call_count == 1
    assert report.total_ambiguous == 1
    assert report.resolved == 1
    # 원 블록 레벨이 바뀌었고 ambiguous=False 가 됐다
    b = blocks[2]
    assert b.level == 0
    assert b.ambiguous is False
    assert b.meta.get("gemini_source") is True
    assert b.meta.get("gemini_reason")


def test_resolve_dry_run_does_not_mutate():
    blocks = _make_blocks()
    amb = [b for b in blocks if b.ambiguous]
    original_level = amb[0].level
    fake = FakeClient(json.dumps([{"line_no": amb[0].line_no, "level": 0, "reason": "x"}]))
    report = resolve(blocks, client=fake, apply_changes=False)
    assert fake.call_count == 1
    assert report.total_ambiguous == 1
    # 레벨은 그대로여야 함
    assert amb[0].level == original_level
    assert amb[0].ambiguous is True


def test_resolve_cost_meter_matches_price_table():
    blocks = _make_blocks()
    fake = FakeClient(
        json.dumps([{"line_no": 2, "level": 0, "reason": "x"}]),
        in_tokens=10_000,
        out_tokens=1_000,
    )
    report = resolve(blocks, client=fake)
    expected_usd = (
        10_000 * gemini_resolver.PRICE_INPUT_USD_PER_M / 1_000_000
        + 1_000 * gemini_resolver.PRICE_OUTPUT_USD_PER_M / 1_000_000
    )
    assert report.cost.usd == pytest.approx(expected_usd, rel=1e-6)


def test_resolve_handles_bad_response_gracefully():
    blocks = _make_blocks()
    fake = FakeClient("not json at all")
    report = resolve(blocks, client=fake)
    # 파싱 실패 시 블록 레벨 유지
    for b in blocks:
        if b.ambiguous is True:
            # 원래 애매였던 블록은 그대로
            assert b.level == 6
    assert report.failed_parse > 0


def test_resolve_human_summary_contains_basic_fields():
    blocks = _make_blocks()
    fake = FakeClient(
        json.dumps([{"line_no": 2, "level": 0, "reason": "x"}]),
        in_tokens=100,
        out_tokens=10,
    )
    report = resolve(blocks, client=fake)
    summary = report.human_summary()
    assert "애매 블록" in summary
    assert "비용" in summary
    assert "tokens" in summary


# ---------------------------------------------------------------------------
# Integration: real regex_parser output → resolve (still mocked client)
# ---------------------------------------------------------------------------

def test_resolve_distinguishes_changed_confirmed_no_decision():
    """changed / confirmed / no_decision 을 정확히 분리해서 집계한다."""
    blocks = [
        # 애매 블록 3 개: 원래 레벨 6
        Block(level=6, text="A", raw_line="□ A", line_no=10, ambiguous=True),
        Block(level=6, text="B", raw_line="□ B", line_no=20, ambiguous=True),
        Block(level=6, text="C", raw_line="□ C", line_no=30, ambiguous=True),
    ]
    # Gemini: 10번은 레벨 0 으로 재분류(changed), 20번은 6 유지(confirmed), 30번은 응답 누락
    fake = FakeClient(
        json.dumps([
            {"line_no": 10, "level": 0, "reason": "본문"},
            {"line_no": 20, "level": 6, "reason": "□ 맞음"},
        ])
    )
    report = resolve(blocks, client=fake)
    assert report.total_ambiguous == 3
    assert report.changed == 1       # line 10: 6 → 0
    assert report.confirmed == 1     # line 20: 6 → 6
    assert report.no_decision == 1   # line 30: 응답에 없음
    assert report.failed_parse == 0
    # backward-compat: resolved = changed + confirmed
    assert report.resolved == 2

    # 응답 누락된 블록은 여전히 ambiguous=True
    assert blocks[2].ambiguous is True
    # 응답 받은 블록은 flag 해제
    assert blocks[0].ambiguous is False
    assert blocks[1].ambiguous is False


def test_google_genai_client_passes_schema_config():
    """GoogleGenAIClient.generate() 가 response_schema 를 포함한 config 로 호출하는지."""
    from google.genai import types

    fake_response = MagicMock()
    fake_response.text = "[]"
    fake_response.usage_metadata = MagicMock(
        prompt_token_count=10, candidates_token_count=5, thoughts_token_count=0
    )
    fake_response.candidates = [MagicMock(finish_reason="STOP")]

    fake_models = MagicMock()
    fake_models.generate_content.return_value = fake_response

    fake_sdk_client = MagicMock()
    fake_sdk_client.models = fake_models

    with patch("google.genai.Client", return_value=fake_sdk_client):
        client = gemini_resolver.GoogleGenAIClient(api_key="dummy-key")
        client.generate("test prompt")

    # generate_content 가 호출됐는지, config 에 response_schema 가 있는지 검증
    assert fake_models.generate_content.called
    _, kwargs = fake_models.generate_content.call_args
    config = kwargs.get("config")
    assert config is not None

    # response_schema 가 배열 타입이어야 함
    assert config.response_schema is not None
    assert config.response_schema.type == types.Type.ARRAY

    # items 가 line_no/level 필드를 가진 OBJECT 여야 함
    item = config.response_schema.items
    assert item.type == types.Type.OBJECT
    assert "line_no" in item.properties
    assert "level" in item.properties
    assert "line_no" in item.required
    assert "level" in item.required

    # thinking 비활성 확인
    assert config.thinking_config.thinking_budget == 0
    # max_output_tokens 상향 확인
    assert config.max_output_tokens >= 16384


def test_resolver_integrates_with_real_parser_output():
    text = "# Ⅰ. 장\n□ 평범\n□ " + ("길고 설명적인 문장 " * 10) + "\n"
    blocks = regex_parser.parse(text)
    amb = regex_parser.ambiguous_blocks(blocks)
    assert len(amb) >= 1

    decisions = [
        {"line_no": b.line_no, "level": 0, "reason": "본문"}
        for b in amb
    ]
    fake = FakeClient(json.dumps(decisions))

    report = resolve(blocks, client=fake)
    assert report.call_count == 1
    assert report.resolved == len(amb)
    assert regex_parser.ambiguous_blocks(blocks) == []
