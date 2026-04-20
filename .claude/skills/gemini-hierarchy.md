---
name: gemini-hierarchy
description: 애매한 원고 라인을 Gemini 2.5 Flash / 3 Flash 로 계층(Ctrl+1~0) 재분류. Structured output, thinking 비활성, 비용 계산 포함.
---

# Gemini 계층 매핑 스킬

**regex_parser 가 애매하다고 마킹한 블록만** Gemini 에 넘겨 최종 level 확정. 문서 당 호출 1 회가 원칙.

## 호출 패턴

```python
from src.parser import gemini_resolver
from src.parser.ir_schema import Block

blocks: list[Block] = ...   # regex_parser 결과

# 1) 기본 (config 의 backend 사용)
client = gemini_resolver.create_default_client()

# 2) 또는 명시
from src.parser.gemini_resolver import GoogleGenAIClient
client = GoogleGenAIClient(model="gemini-2.5-flash")  # 또는 "gemini-3-flash"

report = gemini_resolver.resolve(
    blocks, client=client, context_before=3, context_after=1,
)
print(report.human_summary())
```

## 핵심 설정

- **`thinking_budget=0`** (2.5) / **`thinking_level="minimal"`** (3.x) — chain-of-thought 출력 예산 낭비 방지
- **`response_schema`** — JSON 배열 강제, 구문 유효한 응답 보장
- **`temperature=0.1`** — 결정론에 가깝게
- **`max_output_tokens=32768`** — 최대 226 블록 × 40 토큰 여유

## 비용 (모델별)

| 모델 | Input $/1M | Output $/1M |
|---|---|---|
| gemini-2.5-flash (기본) | $0.075 | $0.30 |
| gemini-2.5-pro | $1.25 | $10.00 |
| gemini-3-flash | $0.50 | $3.00 |
| gemini-3-flash-lite | $0.25 | $1.50 |
| gemini-3-pro | $2.50 | $15.00 |

`src/parser/gemini_resolver.py::price_for_model()` 로 자동 조회됨.

## 자주 나오는 함정

1. **파싱 실패 226/32 (truncation)** — thinking 이 출력 예산 잡아먹음. 해결: thinking_budget=0 + response_schema
2. **finish_reason=MAX_TOKENS** — 애매 블록 400 개 초과 → `MAX_AMBIGUOUS_PER_CALL` 로 분할 처리
3. **rate limit** — 2.5 flash 무료 tier 는 20 req/day. 개발 중엔 mock 사용 권장
4. **API key 없음** — `src.settings.api_key_manager.get_key()` 가 None → `RuntimeError`

## Self-MoA

정성 제안서처럼 품질이 비용보다 중요하면:
```python
cfg = app_config.AppConfig(use_self_moa=True, self_moa_draws=3)
# 동일 모델 3 회 + aggregator 1 회 = 비용 4× / 정확도 ~3-7% ↑
```
**pro 티어 필수** (v0.10.0 부터 gate).
