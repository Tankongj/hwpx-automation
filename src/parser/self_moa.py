"""Self-Mixture-of-Agents 래퍼.

같은 (또는 다른) 모델을 **N 회 독립 호출** 후 aggregator 가 결과를 합성. 최신 연구
(Self-MoA, ICLR 2025~) 에 따르면 서로 다른 모델을 섞는 전통 MoA 보다 같은 최강 모델을
여러 번 돌리는 Self-MoA 가 MMLU/CRUX/MATH 에서 6.6% / 평균 3.8% 더 좋음.

우리 작업(애매 블록 레벨 재분류) 에 Self-MoA 를 적용하면:
- N 번 draw 각각이 JSON 배열을 돌려줌
- aggregator 가 "같은 line_no 에 대한 N 의견을 보고 최종 결정" 을 JSON 으로 반환
- 최종 결과 형식은 단일 호출과 동일 → ResolveReport 가 그대로 해석

비용은 base client 의 약 (N + 1) 배. draws=3 이면 호출당 3 + 1 = 4배 비용.
드문 정확도 개선이 비용에 값어치가 있을 때만 켜는 **옵션 플래그**.

설계 특성
- **Pluggable**: aggregator 가 base 와 다른 모델일 수도 있음 (예: Flash × 3 → Pro aggregate)
- **Graceful**: draw 중 일부가 실패하면 나머지 draw 만으로 aggregate. 모두 실패면 RuntimeError.
- **Token-accurate**: 반환된 GenerateResult 의 token count 는 모든 draw + aggregator 합산.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional, Sequence

from ..utils.logger import get_logger
from .gemini_resolver import GenerateResult, ResolverClient


_log = get_logger("parser.self_moa")


_AGG_INSTRUCTIONS = (
    "너는 제안서 계층 분류의 최종 판정관이다. 같은 애매 라인들에 대해 {n} 개의 독립 응답이 "
    "아래에 JSON 배열 형태로 왔다. 각 line_no 별로 다수결(가중치 평균) 하되, 동률이면 "
    "직전 heading 레벨과 가까운 쪽을 골라라. 출력은 원 응답과 동일한 JSON 배열 포맷:\n"
    '[{{"line_no": N, "level": L, "reason": "..."}}]\n'
    "다른 설명 금지. JSON 배열만.\n\n"
    "원래 프롬프트:\n{original_prompt}\n\n"
    "독립 응답들:\n{draws}\n"
)


@dataclass
class SelfMoAConfig:
    draws: int = 3              # 독립 draw 수
    same_model_aggregator: bool = True  # 기본 True — base client 를 aggregator 로 재사용


class SelfMoAClient:
    """:class:`ResolverClient` 프로토콜 준수. 내부적으로 N+1 번 호출.

    **v0.10.0**: pro 티어 이상에서만 인스턴스화 가능. 무료 사용자가 생성 시도하면
    ``TierDeniedError`` 발생. 테스트에선 ``_skip_tier_check=True`` 로 우회.
    """

    def __init__(
        self,
        base_client: ResolverClient,
        *,
        aggregator: Optional[ResolverClient] = None,
        draws: int = 3,
        _skip_tier_check: bool = False,
        use_batch: bool = False,
        batch_api_key: Optional[str] = None,
        batch_model: Optional[str] = None,
        batch_poll_sec: int = 60,
    ) -> None:
        if draws < 1:
            raise ValueError("draws 는 1 이상이어야 합니다")
        if not _skip_tier_check:
            # v0.10.0: Self-MoA 는 N+1 배 비용 → pro 전용
            from ..commerce import tier_gate  # lazy, 순환참조 방지
            tier_gate.require("pro", feature="Self-MoA")
        self.base_client = base_client
        self.aggregator = aggregator if aggregator is not None else base_client
        self.draws = draws
        # v0.14.0: Batch API 통합 — N draws 를 1 batch 로 50% 절감
        self.use_batch = bool(use_batch)
        self._batch_api_key = batch_api_key
        self._batch_model = batch_model or getattr(base_client, "model", "gemini-2.5-flash")
        self._batch_poll_sec = max(5, int(batch_poll_sec))

        # 표시용 모델명
        base_model = getattr(base_client, "model", "?")
        agg_model = getattr(self.aggregator, "model", base_model)
        suffix = f"+{agg_model}-agg" if agg_model != base_model else ""
        batch_tag = "+batch" if self.use_batch else ""
        self.model = f"self-moa[{base_model}×{draws}{suffix}{batch_tag}]"

    # ---- API ----

    def generate(self, prompt: str) -> GenerateResult:
        """N 번 draw + 1 번 aggregate. 모든 토큰/비용 합산.

        v0.14.0: ``use_batch=True`` 면 N draws 를 Gemini Batch API 로 묶어 **50% 절감**.
        aggregator 는 여전히 실시간 호출 (응답성 중요).
        """
        if self.use_batch:
            draws = self._draws_via_batch(prompt)
        else:
            draws = self._draws_serial(prompt)

        if not draws:
            raise RuntimeError("Self-MoA: 모든 draw 실패")

        if len(draws) == 1:
            # aggregator 호출은 의미 없음 — 단일 draw 결과를 그대로 반환하되 N=1 표기
            r = draws[0]
            _log.info("Self-MoA: 실제 1 draw 만 성공 → aggregation 생략")
            return r

        agg_prompt = _build_aggregator_prompt(prompt, [d.text for d in draws])
        agg_result = self.aggregator.generate(agg_prompt)

        # 토큰/비용 합산 — base draws 는 base client 요율, aggregator 는 자기 요율
        total_in = sum(d.input_tokens for d in draws) + agg_result.input_tokens
        total_out = sum(d.output_tokens for d in draws) + agg_result.output_tokens
        total_think = sum(d.thinking_tokens for d in draws) + agg_result.thinking_tokens

        # 요율은 가중평균 (토큰 수 가중)
        draws_out_tok = sum(d.output_tokens + d.thinking_tokens for d in draws)
        agg_out_tok = agg_result.output_tokens + agg_result.thinking_tokens
        draws_in_tok = sum(d.input_tokens for d in draws)
        agg_in_tok = agg_result.input_tokens

        base_price_in = draws[0].price_input_usd_per_m
        base_price_out = draws[0].price_output_usd_per_m
        agg_price_in = agg_result.price_input_usd_per_m
        agg_price_out = agg_result.price_output_usd_per_m

        weighted_price_in = _weighted_avg(
            [(draws_in_tok, base_price_in), (agg_in_tok, agg_price_in)]
        )
        weighted_price_out = _weighted_avg(
            [(draws_out_tok, base_price_out), (agg_out_tok, agg_price_out)]
        )

        return GenerateResult(
            text=agg_result.text,
            input_tokens=total_in,
            output_tokens=total_out,
            thinking_tokens=total_think,
            finish_reason=agg_result.finish_reason,
            model=self.model,
            price_input_usd_per_m=weighted_price_in,
            price_output_usd_per_m=weighted_price_out,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

    # ---- draw 경로 분기 (v0.14.0) ----

    def _draws_serial(self, prompt: str) -> list[GenerateResult]:
        """기존 serial 경로 — base_client 를 N 회 직접 호출."""
        draws: list[GenerateResult] = []
        failures: list[Exception] = []
        for i in range(self.draws):
            try:
                draws.append(self.base_client.generate(prompt))
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "Self-MoA draw %d/%d 실패: %s",
                    i + 1, self.draws, type(exc).__name__,
                )
                failures.append(exc)
        return draws

    def _draws_via_batch(self, prompt: str) -> list[GenerateResult]:
        """v0.14.0: Batch API 로 N draws 를 한 번에 제출 → 50% 절감.

        실패 시 자동으로 serial 경로로 폴백 (네트워크 이슈 / SDK 호환성 등).
        """
        try:
            from .gemini_batch import run_self_moa_as_batch
        except ImportError:
            _log.info("gemini_batch 모듈 없음 → serial 경로 폴백")
            return self._draws_serial(prompt)

        if not self._batch_api_key:
            # API key 가 명시 안 됐으면 api_key_manager 에서 시도
            try:
                from ..settings.api_key_manager import get_key
                self._batch_api_key = get_key("gemini")
            except Exception:  # noqa: BLE001
                pass
        if not self._batch_api_key:
            _log.info("Batch API key 없음 → serial 경로 폴백")
            return self._draws_serial(prompt)

        try:
            results = run_self_moa_as_batch(
                prompt,
                api_key=self._batch_api_key,
                model=self._batch_model,
                draws=self.draws,
                poll_sec=self._batch_poll_sec,
            )
            _log.info(
                "Self-MoA × Batch 완료: %d draws, 50%% 할인 가격 적용",
                len(results),
            )
            return results
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "Batch 경로 실패 (%s) → serial 폴백", type(exc).__name__,
            )
            return self._draws_serial(prompt)


def _build_aggregator_prompt(original_prompt: str, draw_texts: Sequence[str]) -> str:
    """N 개의 draw 텍스트를 번호 매겨 붙여 aggregator 프롬프트 조립."""
    numbered = []
    for i, t in enumerate(draw_texts, 1):
        numbered.append(f"--- 응답 {i} ---\n{t.strip()}\n")
    return _AGG_INSTRUCTIONS.format(
        n=len(draw_texts),
        original_prompt=original_prompt,
        draws="\n".join(numbered),
    )


def _weighted_avg(pairs: list[tuple[int, float]]) -> float:
    """[(weight, value), ...] 의 가중평균. 가중치 합 0 이면 0 반환."""
    total_w = sum(w for w, _ in pairs)
    if total_w <= 0:
        return 0.0
    return sum(w * v for w, v in pairs) / total_w


__all__ = ["SelfMoAClient", "SelfMoAConfig"]
