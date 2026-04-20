"""애매한 IR 블록을 Gemini 2.5 Flash 에 문의해 레벨을 최종 결정.

기획안 4.2 의 프롬프트를 배치 형태로 확장. **문서당 호출 1 회** 가 원칙이며, 그 한
호출 안에서 모든 애매 블록을 JSON 배열로 한꺼번에 해결한다.

아키텍처
--------
- 네트워크 호출은 :class:`GeminiClient` 프로토콜로 추상화 → 테스트에 fake client 주입 가능
- 실제 호출은 :class:`GoogleGenAIClient` 가 ``google.genai`` SDK 를 사용
- 응답 파싱 실패 시 ``ambiguous=True`` 를 유지하고 기존 level 을 그대로 둔다 (안전장치)
- 토큰 사용량 + 추정 비용을 :class:`ResolveReport` 로 돌려줌

공개 API
--------
- :func:`resolve(blocks, *, client=..., context_before=3, context_after=1) -> ResolveReport`
- :class:`GoogleGenAIClient(api_key=None, model=DEFAULT_MODEL)`

비용 상수 (Gemini 2.5 Flash, 2025 Q4 공식 가격)
- Input  : $0.075 / 1M tokens
- Output : $0.30  / 1M tokens
변경 시 :data:`PRICE_INPUT_USD_PER_M` / :data:`PRICE_OUTPUT_USD_PER_M` 만 갱신하면 된다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Protocol, Sequence

from ..utils.logger import get_logger
from .ir_schema import LEVEL_BODY, LEVEL_MAX, LEVEL_MIN, LEVEL_TITLE, Block


_log = get_logger("parser.gemini")


DEFAULT_MODEL = "gemini-2.5-flash"

# v0.11.0: 2025-12 이후 Gemini 3 Flash 시리즈 추가 — 기본값은 아직 2.5 유지 (GA 안정성)
# 사용자가 config.gemini_model 을 "gemini-3-flash" 또는 "gemini-3-flash-lite" 로 바꾸면 자동 전환.
AVAILABLE_MODELS: tuple[str, ...] = (
    "gemini-2.5-flash",       # 기본 — GA, 안정, $0.075/$0.30 per 1M
    "gemini-2.5-pro",         # 고품질 — $1.25/$10 per 1M
    "gemini-3-flash",         # 2025-12 프리뷰, 2.5 Pro 수준 + 3× 속도 — $0.50/$3.00
    "gemini-3-flash-lite",    # 2026 — RFP 추출 최적, $0.25/$1.50
    "gemini-3-pro",           # 2026 — 최고 품질
)

# Gemini 2.5 Flash pricing (USD per 1M tokens, ≤128K context) — DEFAULT_MODEL 기준
PRICE_INPUT_USD_PER_M = 0.075
PRICE_OUTPUT_USD_PER_M = 0.30
USD_TO_KRW = 1350.0  # 근사치 (UI 표시 용)

# v0.11.0: 모델별 가격 테이블 — 호출 비용 정확 계산
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash":      (0.075, 0.30),
    "gemini-2.5-pro":        (1.25, 10.00),
    "gemini-3-flash":        (0.50, 3.00),
    "gemini-3-flash-lite":   (0.25, 1.50),
    "gemini-3-pro":          (2.50, 15.00),
}


def price_for_model(model: str) -> tuple[float, float]:
    """(input, output) USD per 1M tokens. 알 수 없는 모델은 2.5 Flash 요율."""
    return _MODEL_PRICING.get(model, (PRICE_INPUT_USD_PER_M, PRICE_OUTPUT_USD_PER_M))

# Hard caps — 단일 호출이 너무 커지는 걸 방지
MAX_AMBIGUOUS_PER_CALL = 400


# ---------------------------------------------------------------------------
# Client protocol
# ---------------------------------------------------------------------------

@dataclass
class GenerateResult:
    """LLM 호출 결과."""

    text: str                           # 원본 응답 텍스트
    input_tokens: int = 0
    output_tokens: int = 0               # response.candidates 토큰
    thinking_tokens: int = 0             # Gemini 2.5 thinking 토큰 (있으면 output 요금 청구)
    finish_reason: str = ""              # "STOP" / "MAX_TOKENS" / ...
    model: str = DEFAULT_MODEL
    # 토큰 요율 (USD per 1M) — 백엔드마다 다름. 로컬은 0.
    price_input_usd_per_m: float = PRICE_INPUT_USD_PER_M
    price_output_usd_per_m: float = PRICE_OUTPUT_USD_PER_M


class ResolverClient(Protocol):
    """LLM 호출 추상화. Gemini/Ollama/그 외 백엔드가 이 프로토콜을 만족하면 된다.

    테스트 시 이 protocol 을 만족하는 fake 를 주입.
    """

    def generate(self, prompt: str) -> GenerateResult:
        ...


# Back-compat alias (W3 이전에 'GeminiClient' 로 쓰였음)
GeminiClient = ResolverClient


# ---------------------------------------------------------------------------
# Default implementation (google.genai SDK)
# ---------------------------------------------------------------------------

class GoogleGenAIClient:
    """``google.genai`` SDK 래퍼.

    API Key 가 넘어오지 않으면 :mod:`src.settings.api_key_manager` 에서 로드한다.
    SDK 미설치 시엔 생성 시점에 :class:`ImportError` 발생.
    """

    def __init__(self, api_key: Optional[str] = None, model: str = DEFAULT_MODEL) -> None:
        if api_key is None:
            # lazy import to keep module loadable without settings package
            from ..settings.api_key_manager import get_key

            api_key = get_key()
        if not api_key:
            raise RuntimeError("Gemini API Key 가 설정되지 않았습니다")

        try:
            from google import genai  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "google-genai 가 설치되어 있지 않습니다. "
                "`pip install google-genai` 후 다시 시도하세요."
            ) from exc

        self._client = genai.Client(api_key=api_key)
        self.model = model

    def generate(self, prompt: str) -> GenerateResult:
        """Gemini 호출.

        Config 메모:
        - ``response_mime_type="application/json"`` + ``response_schema`` 로 **JSON 배열 구조 보장**.
          Gemini 2.5 Flash 는 structured output 을 완전 지원 — 구문 유효한 JSON 을 내줌.
          (Pydantic 의존성 없이 SDK 네이티브 :class:`types.Schema` 사용)
        - ``temperature=0.1`` 로 결정론에 가깝게
        - ``max_output_tokens=32768`` — 226 블록 × ~40 토큰 ≈ 9K 면 충분하지만 여유 확보
        - **thinking_budget=0** — 2.5 Flash 의 chain-of-thought 비활성.
          규칙 기반 매핑 작업이라 thinking 이 거의 무익하고, 활성 시 출력 예산을
          30K 이상 잡아먹어 응답이 잘리는 문제가 관찰됨 (최초 호출 때 31K 토큰 소진).
        """
        from google.genai import types  # type: ignore

        item_schema = types.Schema(
            type=types.Type.OBJECT,
            properties={
                "line_no": types.Schema(
                    type=types.Type.INTEGER,
                    description="판정 대상 블록의 line_no (원본 원고 기준 줄 번호)",
                ),
                "level": types.Schema(
                    type=types.Type.INTEGER,
                    description="결정한 계층 레벨. -1(표지) / 0(본문) / 1~10(계층)",
                ),
                "reason": types.Schema(
                    type=types.Type.STRING,
                    description="짧은 판정 근거 (20자 이내)",
                ),
            },
            required=["line_no", "level"],
        )
        array_schema = types.Schema(type=types.Type.ARRAY, items=item_schema)

        # v0.11.0: Gemini 3.x 는 thinking_level enum, 2.5 는 thinking_budget int.
        # 안정성 위해 우선 thinking_budget=0 을 시도 → 지원 안 하면 생략 (SDK 가 알려줌).
        thinking_cfg = None
        try:
            if self.model.startswith("gemini-3"):
                # 3.x: thinking_level = "minimal" 이 2.5 의 budget=0 에 해당
                thinking_cfg = types.ThinkingConfig(thinking_level="minimal")  # type: ignore[call-arg]
            else:
                thinking_cfg = types.ThinkingConfig(thinking_budget=0)
        except (TypeError, AttributeError):
            # SDK 버전이 해당 필드 모르면 None 으로 → 기본 thinking 동작
            thinking_cfg = None

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=array_schema,
            temperature=0.1,
            max_output_tokens=32768,
            thinking_config=thinking_cfg,
        )
        resp = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )
        text = getattr(resp, "text", "") or ""

        usage = getattr(resp, "usage_metadata", None)
        # usage_metadata 의 필드가 None 으로 올 수 있어 (thinking 비활성 시 등) 전부 int 로 정규화
        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
        thinking_tokens = int(getattr(usage, "thoughts_token_count", 0) or 0) if usage else 0

        finish_reason = ""
        try:
            candidates = getattr(resp, "candidates", None) or []
            if candidates:
                fr = getattr(candidates[0], "finish_reason", None)
                finish_reason = getattr(fr, "name", str(fr)) if fr is not None else ""
        except Exception:  # noqa: BLE001
            finish_reason = ""

        # v0.11.0: 선택된 모델별 가격 반영 (기본 2.5 Flash 이지만 Pro / 3.x 에선 다름)
        p_in, p_out = price_for_model(self.model)

        return GenerateResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thinking_tokens,
            finish_reason=finish_reason,
            model=self.model,
            price_input_usd_per_m=p_in,
            price_output_usd_per_m=p_out,
        )


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

LEVEL_SPEC = """[레벨 스펙]
-1: 문서 표지 제목 (로마숫자 없는 최상단 # 한 줄)
 0: 본문 (직전 제목의 하위)
 1: Ⅰ. 대장 (휴먼명조 20pt)
 2: 1 절 (HY견고딕 18pt)
 3: 1) 소절
 4: (1) 단락 제목
 5: ① 원숫자
 6: □ 대주제
 7: ❍ 중주제
 8: - 하이픈 글머리
 9: · 가운뎃점
10: * 주석/참고
"""


def build_prompt(
    ambiguous: Sequence[Block],
    all_blocks: Sequence[Block],
    context_before: int = 3,
    context_after: int = 1,
) -> str:
    """판정할 블록들과 주변 문맥을 묶어 단일 프롬프트로 만든다."""
    # line_no → 해당 블록의 all_blocks 인덱스
    line_to_idx = {b.line_no: i for i, b in enumerate(all_blocks)}

    items: list[dict] = []
    for amb in ambiguous:
        idx = line_to_idx.get(amb.line_no)
        before: list[str] = []
        after: list[str] = []
        if idx is not None:
            start = max(0, idx - context_before)
            end = min(len(all_blocks), idx + 1 + context_after)
            before = [b.raw_line or b.text for b in all_blocks[start:idx]]
            after = [b.raw_line or b.text for b in all_blocks[idx + 1 : end]]
        items.append(
            {
                "line_no": amb.line_no,
                "current_level": amb.level,
                "raw": amb.raw_line or amb.text,
                "context_before": before,
                "context_after": after,
            }
        )

    body = json.dumps(items, ensure_ascii=False, indent=2)

    return (
        "너는 한국 공공기관 제안서 문서 편집 전문가다.\n"
        "아래 '판정 대상 배열' 의 각 라인이 어느 계층 레벨(-1~10)에 해당하는지 결정하라.\n\n"
        f"{LEVEL_SPEC}\n"
        "[판정 대상 배열 (JSON)]\n"
        f"{body}\n\n"
        "[응답 형식]\n"
        "아래 JSON 배열만 출력하고 다른 설명은 금지한다. reason 은 20자 이내 한국어.\n"
        '[{"line_no": <int>, "level": <int -1~10>, "reason": "<짧은 근거>"}]\n'
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _salvage_truncated_array(text: str) -> list[dict]:
    """응답이 ``[ {...}, {...}, {... (잘림)`` 형태로 끝났을 때 완전한 object 들만 추출.

    단순 brace-depth 카운터로 최상위 배열 안의 완전한 ``{ ... }`` 경계를 찾아 각각
    json.loads 로 파싱. 따옴표 안의 중괄호는 제외.
    """
    try:
        start = text.index("[")
    except ValueError:
        return []

    depth = 0
    in_str = False
    escape = False
    obj_start = -1
    objects: list[str] = []

    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue

        if ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start >= 0:
                objects.append(text[obj_start : i + 1])
                obj_start = -1

    salvaged: list[dict] = []
    for raw in objects:
        try:
            salvaged.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return salvaged


def parse_response(text: str) -> list[dict]:
    """응답 텍스트에서 JSON 배열 추출. 실패 시 빈 리스트.

    3단 복구:
        1. 통째로 ``json.loads``
        2. 바깥 ``[...]`` 만 잘라 재시도 (코드 펜스/부가 설명 제거)
        3. 절단된 배열이면 완전한 ``{...}`` 항목만 brace-depth 로 구조 복구
    """
    if not text:
        return []
    cleaned = _CODE_FENCE.sub("", text).strip()

    data = None
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\[[\s\S]*\]", cleaned)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                data = None

    if data is None:
        # 3차: salvage truncated array
        data = _salvage_truncated_array(cleaned)

    if not isinstance(data, list):
        return []

    results: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if "line_no" not in item or "level" not in item:
            continue
        try:
            line_no = int(item["line_no"])
            level = int(item["level"])
        except (TypeError, ValueError):
            continue
        if not (level == LEVEL_TITLE or level == LEVEL_BODY or LEVEL_MIN <= level <= LEVEL_MAX):
            continue
        results.append(
            {
                "line_no": line_no,
                "level": level,
                "reason": str(item.get("reason", "")),
            }
        )
    return results


# ---------------------------------------------------------------------------
# Cost meter
# ---------------------------------------------------------------------------

@dataclass
class Cost:
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0            # billed at output rate (2.5 계열)
    model: str = DEFAULT_MODEL
    # 요율: 기본은 Gemini 2.5 Flash. 로컬 백엔드는 0 으로 내려보냄.
    price_input_usd_per_m: float = PRICE_INPUT_USD_PER_M
    price_output_usd_per_m: float = PRICE_OUTPUT_USD_PER_M

    @property
    def usd(self) -> float:
        return (
            self.input_tokens * self.price_input_usd_per_m / 1_000_000
            + (self.output_tokens + self.thinking_tokens) * self.price_output_usd_per_m / 1_000_000
        )

    @property
    def krw(self) -> float:
        return self.usd * USD_TO_KRW

    @property
    def is_local(self) -> bool:
        return self.price_input_usd_per_m == 0 and self.price_output_usd_per_m == 0


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

@dataclass
class ResolveReport:
    """resolve() 결과 보고서.

    3가지 범주로 분리해서 집계:
      - ``changed``      : Gemini 가 다른 레벨로 재분류 (실제로 값이 바뀐 블록)
      - ``confirmed``    : Gemini 가 원 레벨이 맞다고 확인 (ambiguous 플래그만 내려감)
      - ``no_decision``  : 응답에 포함되지 않은 블록 (네트워크/truncation 등)
    ``resolved`` 는 ``changed + confirmed`` (= Gemini 가 명시적으로 답한 총 개수).
    """

    total_ambiguous: int = 0
    changed: int = 0
    confirmed: int = 0
    no_decision: int = 0
    failed_parse: int = 0            # 응답 자체가 파싱 불가였던 경우
    call_count: int = 0
    cost: Cost = field(default_factory=Cost)
    raw_response: str = ""  # 디버깅 용 (API Key 는 절대 포함 안 됨)

    # Backward-compat aliases (W3 초기 이름)
    @property
    def resolved(self) -> int:
        """Gemini 가 응답한 블록 수 (changed + confirmed)."""
        return self.changed + self.confirmed

    @property
    def unchanged(self) -> int:
        """값이 바뀌지 않은 블록 수 (confirmed + no_decision)."""
        return self.confirmed + self.no_decision

    def human_summary(self) -> str:
        cost_part = (
            "비용 0 (로컬)"
            if self.cost.is_local
            else f"비용 ≈ ${self.cost.usd:.4f} (₩{self.cost.krw:.1f})"
        )
        return (
            f"애매 블록 {self.total_ambiguous} 개 / "
            f"재분류 {self.changed} / 확인 {self.confirmed} / "
            f"응답누락 {self.no_decision} / 파싱실패 {self.failed_parse} · "
            f"호출 {self.call_count} 회 · "
            f"{cost_part} · "
            f"tokens in={self.cost.input_tokens} "
            f"out={self.cost.output_tokens} think={self.cost.thinking_tokens} "
            f"[{self.cost.model}]"
        )


def resolve(
    blocks: Sequence[Block],
    *,
    client: Optional[ResolverClient] = None,
    context_before: int = 3,
    context_after: int = 1,
    apply_changes: bool = True,
) -> ResolveReport:
    """애매한 블록만 골라 LLM 에 배치 문의하고, 응답대로 레벨을 덮어쓴다.

    Parameters
    ----------
    blocks : regex_parser 가 만든 전체 IR.
    client : 테스트에 fake 를 넣을 때 사용. None 이면 :func:`create_default_client` 로
        AppConfig 를 참조해 Gemini 또는 Ollama 클라이언트를 자동 생성.
    apply_changes : False 면 dry-run (보고서만 생성).
    """
    amb = [b for b in blocks if b.ambiguous]
    report = ResolveReport(total_ambiguous=len(amb))

    if not amb:
        return report

    if len(amb) > MAX_AMBIGUOUS_PER_CALL:
        _log.warning(
            "애매 블록이 %d 개로 MAX_AMBIGUOUS_PER_CALL(%d) 초과 — 앞쪽 %d 개만 처리",
            len(amb), MAX_AMBIGUOUS_PER_CALL, MAX_AMBIGUOUS_PER_CALL,
        )
        amb = amb[:MAX_AMBIGUOUS_PER_CALL]

    if client is None:
        client = create_default_client()

    prompt = build_prompt(amb, blocks, context_before=context_before, context_after=context_after)
    _log.debug("Gemini prompt 크기: %d chars, %d 블록", len(prompt), len(amb))

    result = client.generate(prompt)
    report.call_count = 1
    report.cost = Cost(
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        thinking_tokens=result.thinking_tokens,
        model=result.model,
        price_input_usd_per_m=result.price_input_usd_per_m,
        price_output_usd_per_m=result.price_output_usd_per_m,
    )
    report.raw_response = result.text

    if result.finish_reason == "MAX_TOKENS":
        _log.warning(
            "Gemini 응답이 max_output_tokens 로 잘림 — thinking_tokens=%d, "
            "output_tokens=%d. 일부 블록은 미해결 상태로 남을 수 있음.",
            result.thinking_tokens, result.output_tokens,
        )

    parsed = parse_response(result.text)
    if not parsed:
        _log.error("Gemini 응답 파싱 실패 (길이 %d)", len(result.text))
        report.failed_parse = len(amb)
        return report

    decisions = {item["line_no"]: item for item in parsed}
    amb_by_line = {b.line_no: b for b in amb}

    changed = 0
    confirmed = 0
    no_decision = 0

    for line_no, block in amb_by_line.items():
        decision = decisions.get(line_no)
        if decision is None:
            no_decision += 1
            # Gemini 가 답을 안 줬으면 ambiguous 플래그는 그대로 유지 (추후 재시도 가능)
            continue

        new_level = decision["level"]
        reason = decision.get("reason", "")
        original_level = block.level

        if apply_changes:
            block.level = new_level
            block.ambiguous = False
            block.meta = {
                **(block.meta or {}),
                "gemini_reason": reason,
                "gemini_source": True,
                "original_level": original_level,
            }

        if new_level != original_level:
            changed += 1
        else:
            confirmed += 1

    report.changed = changed
    report.confirmed = confirmed
    report.no_decision = no_decision
    _log.info(report.human_summary())
    return report


def create_default_client(backend: Optional[str] = None) -> ResolverClient:
    """AppConfig 에 설정된 백엔드로 기본 클라이언트 생성.

    지원 백엔드 (v0.3.0):
        - ``gemini``    : Google Gemini (기본)     — 무료 티어 OK
        - ``ollama``    : 로컬 Ollama               — 무료 티어 OK
        - ``openai``    : OpenAI GPT                — **pro 전용** (v0.10.0)
        - ``anthropic`` : Anthropic Claude          — **pro 전용** (v0.10.0)
        - ``none``      : 비활성 (RuntimeError)

    Parameters
    ----------
    backend : 명시 오버라이드. None 이면 config.resolver_backend 사용.

    Raises
    ------
    TierDeniedError
        무료 티어에서 openai/anthropic 또는 Self-MoA 를 요청했을 때.
    """
    from ..settings import app_config

    cfg = app_config.load()
    chosen = (backend or cfg.resolver_backend or "gemini").lower()

    if chosen in ("none", "off", "disabled"):
        raise RuntimeError("Resolver 백엔드가 비활성 (resolver_backend=none)")

    # v0.12.0: instructor 기반 unified resolver opt-in
    if getattr(cfg, "use_instructor_resolver", False):
        from .instructor_resolver import is_available as _instr_ok
        if _instr_ok():
            from .instructor_resolver import create_instructor_client
            model_map = {
                "gemini": cfg.gemini_model,
                "ollama": cfg.ollama_model,
                "openai": cfg.openai_model,
                "anthropic": cfg.anthropic_model,
            }
            if chosen in model_map:
                # openai/anthropic 은 여전히 pro 전용
                if chosen in ("openai", "anthropic"):
                    from ..commerce import tier_gate
                    tier_gate.require("pro", feature=f"{chosen} 백엔드")
                _log.info("instructor unified resolver 활성 (%s:%s)", chosen, model_map[chosen])
                base = create_instructor_client(
                    provider=chosen, model=model_map[chosen],
                )
                if cfg.use_self_moa and cfg.self_moa_draws >= 2:
                    from .self_moa import SelfMoAClient
                    return SelfMoAClient(base, draws=cfg.self_moa_draws)
                return base
        else:
            _log.info("instructor 미설치 → 기존 경로 사용")

    # 1) base client 선택
    if chosen == "ollama":
        from .ollama_backend import OllamaClient  # lazy
        base: ResolverClient = OllamaClient(host=cfg.ollama_host, model=cfg.ollama_model)
    elif chosen == "openai":
        # v0.10.0: 유료 백엔드 → pro 티어 필요
        from ..commerce import tier_gate
        tier_gate.require("pro", feature="OpenAI 백엔드")
        from .openai_backend import OpenAIClient  # lazy
        base = OpenAIClient(model=cfg.openai_model)
    elif chosen == "anthropic":
        # v0.10.0: 유료 백엔드 → pro 티어 필요
        from ..commerce import tier_gate
        tier_gate.require("pro", feature="Anthropic 백엔드")
        from .anthropic_backend import AnthropicClient  # lazy
        base = AnthropicClient(model=cfg.anthropic_model)
    else:
        base = GoogleGenAIClient(model=cfg.gemini_model)

    # 2) v0.4.0: Self-MoA 감싸기 (선택, draws>=2 여야 의미 있음)
    # v0.10.0: Self-MoA 는 pro 전용 (SelfMoAClient 생성자에서 재검증)
    # v0.14.0: use_gemini_batch 활성 + gemini 백엔드일 때 Batch API 경로 사용 (50% 절감)
    if cfg.use_self_moa and cfg.self_moa_draws >= 2:
        from .self_moa import SelfMoAClient  # lazy
        use_batch = bool(
            getattr(cfg, "use_gemini_batch", False)
            and chosen == "gemini"
        )
        batch_key = None
        if use_batch:
            try:
                from ..settings.api_key_manager import get_key
                batch_key = get_key("gemini")
            except Exception:  # noqa: BLE001
                batch_key = None
        return SelfMoAClient(
            base,
            draws=cfg.self_moa_draws,
            use_batch=use_batch,
            batch_api_key=batch_key,
            batch_model=cfg.gemini_model,
            batch_poll_sec=getattr(cfg, "gemini_batch_poll_sec", 60),
        )
    return base


__all__ = [
    "DEFAULT_MODEL",
    "AVAILABLE_MODELS",
    "PRICE_INPUT_USD_PER_M",
    "PRICE_OUTPUT_USD_PER_M",
    "price_for_model",
    "ResolverClient",
    "GeminiClient",         # back-compat alias
    "GenerateResult",
    "GoogleGenAIClient",
    "build_prompt",
    "parse_response",
    "Cost",
    "ResolveReport",
    "resolve",
    "create_default_client",
]
