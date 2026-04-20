"""쿠팡 파트너스 광고 위젯 — v0.12.0 매출 채널 #1.

쿠팡 파트너스 의 공식 carousel 스크립트를 QWebEngineView 로 렌더링.
사용자가 설정 탭에서 자신의 **파트너 ID** 와 **tracking code** 를 입력하면 활성화.

출처: https://partners.coupang.com/  (카테고리 > 광고 > 쿠팡 파트너스 전용 배너)

Script 원형::

    <script src="https://ads-partners.coupang.com/g.js"></script>
    <script>
        new PartnersCoupang.G({
            "id": 982081,
            "template": "carousel",
            "trackingCode": "AF7480765",
            "width": "680",
            "height": "80",
            "tsource": ""
        });
    </script>

설계 원칙
---------
- **opt-in**: AppConfig.coupang_partner_id 비어 있으면 비활성
- **pro 제외**: tier_gate.is_allowed('pro') 면 광고 자동 숨김 (상위 AdPlaceholder 가 처리)
- **HTTPS only**: 쿠팡 g.js 는 HTTPS 로만 서빙, 외부 네트워크 필요
- **빈 Network**: QWebEngineView 가 로드 실패하면 placeholder 텍스트로 fallback
- **PIPA**: 쿠팡으로 IP / User-Agent / 쿠키 는 제3자 전송됨 → 사용자에게 활성화 시 고지

법적 안전 장치
--------------
- 쿠팡 파트너스 활동 참여자 의무 표시 문구 ("이 포스팅은 쿠팡 파트너스 활동의 일환으로,
  이에 따른 일정액의 수수료를 제공받습니다.") 를 `DISCLOSURE_TEXT` 로 항상 표시.
- **공정거래위원회 표시·광고 공정화법** 준수.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...utils.logger import get_logger


_log = get_logger("gui.coupang_ad")


# 쿠팡 파트너스 광고주 의무 표기 (공정위 고시 기준)
DISCLOSURE_TEXT = (
    "이 광고는 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
)


# 카루셀 광고 권장 사이즈 (쿠팡 파트너스 기본값)
DEFAULT_WIDTH = 680
DEFAULT_HEIGHT = 80


def build_html(
    partner_id: int,
    tracking_code: str,
    *,
    template: str = "carousel",
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> str:
    """쿠팡 파트너스 carousel 스크립트가 포함된 HTML 페이지 생성.

    Parameters
    ----------
    partner_id : 쿠팡 파트너스 계정 ID (숫자)
    tracking_code : AF__________ 형식 추적 코드
    template : "carousel" / "image" / "text" 등 (쿠팡 스펙 참고)
    width, height : 배너 크기

    Returns
    -------
    str — QWebEngineView.setHtml() 에 바로 넣을 HTML.
    """
    # JS 주입 방지: partner_id 는 int 로 강제, tracking_code 는 간단한 ascii 검증
    partner_id = int(partner_id)
    safe_tracking = "".join(
        c for c in tracking_code
        if c.isalnum() or c in "-_"
    )[:64]
    safe_template = "".join(c for c in template if c.isalnum())[:20] or "carousel"

    # 배경/마진 리셋으로 QWebEngineView 안에서 깔끔히 렌더
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>Coupang Ad</title>
<style>
  html, body {{
    margin: 0; padding: 0; overflow: hidden;
    background: transparent;
    font-family: 'Pretendard', '맑은 고딕', sans-serif;
  }}
  .ad-container {{
    width: 100%; height: 100%;
    display: flex; align-items: center; justify-content: center;
  }}
</style>
</head>
<body>
<div class="ad-container" id="coupangAdContainer"></div>
<script src="https://ads-partners.coupang.com/g.js"></script>
<script>
  try {{
    new PartnersCoupang.G({{
      "id": {partner_id},
      "template": "{safe_template}",
      "trackingCode": "{safe_tracking}",
      "width": "{int(width)}",
      "height": "{int(height)}",
      "tsource": ""
    }});
  }} catch (e) {{
    document.getElementById("coupangAdContainer").innerText =
      "광고를 불러올 수 없습니다.";
  }}
</script>
</body>
</html>
"""


class CoupangAdWidget(QWidget):
    """쿠팡 파트너스 광고 렌더링 QWebEngineView 래퍼.

    네트워크 또는 엔진 실패 시 placeholder 레이블로 우아하게 fallback.
    """

    # 광고 로드 성공/실패 Signal — 상위에서 로깅/telemetry 용
    load_finished = Signal(bool)

    def __init__(
        self,
        partner_id: int,
        tracking_code: str,
        *,
        template: str = "carousel",
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._partner_id = partner_id
        self._tracking_code = tracking_code
        self._template = template
        self._width = width
        self._height = height
        self._engine = None  # 생성 실패 시 None 으로 폴백

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 1) QWebEngineView 생성 시도 — 번들 미포함 환경에선 그냥 placeholder
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView  # type: ignore
        except ImportError:
            _log.warning("QWebEngineView 미설치 → Coupang 광고 fallback")
            self._fallback_label(layout, "쿠팡 파트너스 (WebEngine 미설치)")
        else:
            try:
                self._engine = QWebEngineView(self)
                self._engine.setFixedSize(int(width), int(height))
                html = build_html(
                    partner_id, tracking_code,
                    template=template, width=width, height=height,
                )
                self._engine.setHtml(html)
                self._engine.loadFinished.connect(self._on_load_finished)
                layout.addWidget(self._engine, 0, Qt.AlignmentFlag.AlignCenter)
            except Exception as exc:  # noqa: BLE001
                _log.warning("CoupangAdWidget 초기화 실패: %s", exc)
                self._engine = None
                self._fallback_label(layout, f"쿠팡 파트너스 (초기화 실패: {type(exc).__name__})")

        # 2) 의무 고지 라벨 — 항상 표시 (공정위)
        self._disc_label = QLabel(DISCLOSURE_TEXT)
        self._disc_label.setStyleSheet(
            "QLabel { color: #999; font-size: 8pt; padding: 2px 6px; }"
        )
        self._disc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._disc_label.setWordWrap(True)
        layout.addWidget(self._disc_label)

    # ---- ----

    def _fallback_label(self, layout, text: str) -> None:
        lab = QLabel(text)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lab.setStyleSheet(
            "QLabel { background: #fafafa; border: 1px dashed #ccc; "
            "padding: 12px; color: #666; font-size: 10pt; }"
        )
        lab.setFixedSize(self._width, self._height)
        layout.addWidget(lab, 0, Qt.AlignmentFlag.AlignCenter)

    def _on_load_finished(self, ok: bool) -> None:
        from ...commerce import revenue_telemetry as rt
        if ok:
            rt.record_impression(
                channel=rt.CH_COUPANG,
                partner_id=str(self._partner_id),
                ad_slot=self._tracking_code,
            )
        else:
            _log.info("쿠팡 광고 로드 실패 (network?)")
            rt.record_load_failed(channel=rt.CH_COUPANG, reason="loadFinished=false")
        self.load_finished.emit(bool(ok))

    # ---- properties ----

    @property
    def partner_id(self) -> int:
        return self._partner_id

    @property
    def tracking_code(self) -> str:
        return self._tracking_code

    @property
    def is_rendered(self) -> bool:
        return self._engine is not None


__all__ = [
    "CoupangAdWidget",
    "DISCLOSURE_TEXT",
    "DEFAULT_WIDTH",
    "DEFAULT_HEIGHT",
    "build_html",
]
