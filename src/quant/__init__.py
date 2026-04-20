"""정량제안서 (quantitative proposal) 모듈 — v0.5.0 foundation scaffold.

정량제안서는 ``[서식 1]``, ``[서식 2]`` 같은 **양식 채우기** 구조가 핵심. 정성제안서와
완전히 다른 파서·UI·출력 로직이 필요해서 별도 모듈로 분리.

v0.5.0 범위 (foundation)
------------------------
- 데이터 모델: :class:`QuantForm`, :class:`QuantField`
- 파서 stub: HWPX 템플릿 → form 구조 추출 (TODO — 사용자 샘플 필요)
- 변환 stub: form + 값 → HWPX 출력 (TODO)
- UI 는 W4 스타일의 탭 placeholder 만 (실제 UI 는 v0.5.0 본편에서)

실제 구현에는 **사용자가 제공해야 할 샘플** 이 필요:
- 실제 기관 공고의 정량제안서 HWPX 파일 3~5종
- 해당 양식에 채워진 완성본도 가능하면 같이

설계 상세: :file:`docs/QUANT_DESIGN.md` 참고.
"""

from .models import QuantField, QuantForm, QuantProposal

__all__ = ["QuantField", "QuantForm", "QuantProposal"]
