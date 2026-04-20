---
name: pro-tier-gate
description: Pro 전용 기능에 tier_gate 를 적용하는 패턴. Self-MoA / OpenAI / Anthropic / batch CLI / 광고 제거 이미 게이트됨.
---

# Pro 티어 게이트 스킬

상업화 단계에서 특정 기능을 pro 이상에만 허용. 무료 사용자가 호출하면 `TierDeniedError`.

## 3 가지 적용 방식

### 1) 데코레이터 (함수 레벨)

```python
from src.commerce.tier_gate import requires_tier

@requires_tier("pro", feature="Self-MoA")
def use_expensive_feature(...):
    ...
```

### 2) 명시 체크 (생성자 / 진입점)

```python
from src.commerce import tier_gate

def __init__(self, ..., _skip_tier_check: bool = False):
    if not _skip_tier_check:
        tier_gate.require("pro", feature="Self-MoA")
```

`_skip_tier_check` 백도어는 **단위 테스트 전용**.

### 3) 분기 (조건부 동작)

```python
from src.commerce import tier_gate

if tier_gate.is_allowed("pro"):
    # 고급 경로
else:
    # 무료 경로 (제한된 기능)
```

## 현재 게이트된 기능 (v0.10.0+)

| 기능 | 위치 |
|---|---|
| Self-MoA | `src/parser/self_moa.py::SelfMoAClient.__init__` |
| OpenAI 백엔드 | `src/parser/gemini_resolver.py::create_default_client` |
| Anthropic 백엔드 | 동일 |
| CLI `build-batch` | `src/cli.py::_cmd_build_batch` (무료는 1 파일만) |
| 광고 제거 | `src/gui/main_window.py::_apply_ad_state` |
| HWP BodyText 전체 파싱 | `src/checklist/rfp_extractor.py` (pro 는 `prefer_full=True`) |

## 세션 등록 (로그인 후)

```python
from src.commerce import tier_gate
from src.commerce.auth_client import AuthSession

tier_gate.set_current_session(AuthSession(user=user, tier=user.tier))
# 로그아웃 시
tier_gate.set_current_session(None)
```

## 티어 계층 (0 < 1 < 2)

- `free` (0) — 기본, 광고 표시
- `pro` (1) — 광고 제거 + Self-MoA + 유료 백엔드 + 일괄 변환
- `team` (2) — 추후 조직 기능 (미정)

**`is_allowed("pro")` 는 team 사용자도 통과** (상위 포함).

## 테스트

```python
import pytest
from src.commerce import tier_gate

def test_feature_blocked_for_free():
    tier_gate.set_current_session(None)
    with pytest.raises(tier_gate.TierDeniedError):
        expensive_feature()
```

## 관련 법규

- **AI 기본법 (2026-01-22)** — Pro 기능이 AI 이면 `src.commerce.ai_disclosure.make_disclosure()` 호출 결과를 UI/메타에 반영
- **PIPA** — Pro 의 클라우드 AI (Gemini/OpenAI/Claude) 사용 시 **제3자 제공 동의** 별도 필요 (v0.12 에 다이얼로그 추가 예정)
