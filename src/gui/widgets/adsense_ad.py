"""Google AdSense 광고 위젯 — v0.13.0 매출 채널 #2.

Google AdSense 의 `<ins class="adsbygoogle">` 표시 광고 태그를 QWebEngineView 로 렌더링.
Google AdSense 파트너 ID (`ca-pub-XXXX`) + 광고 슬롯 ID (`data-ad-slot`) 가 있어야 활성.

**등록 절차 (사용자)**:
1. adsense.google.com 가입 (승인까지 수일~수주)
2. 사이트/앱 등록 후 `ca-pub-XXXXXXXXXXXXXXXX` 게시자 ID 획득
3. 광고 단위 생성 후 `data-ad-slot` 숫자 ID 획득
4. 앱 설정 탭에 입력 → 즉시 활성

Script 원형::

    <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-..."
            crossorigin="anonymous"></script>
    <ins class="adsbygoogle"
         style="display:block"
         data-ad-client="ca-pub-XXXXXXXXXXXXXXXX"
         data-ad-slot="YYYYYYYYYY"
         data-ad-format="auto"
         data-full-width-responsive="true"></ins>
    <script>
         (adsbygoogle = window.adsbygoogle || []).push({});
    </script>

**쿠팡 대비 차이점**:
- 지역 독립 — 국내외 트래픽 모두 수익
- 심사/승인 기간 있음 (쿠팡은 즉시)
- CPC/CPM 혼합 — 쿠팡 쿠팡 은 CPS (구매당 수수료)

**법적 준수**:
- Google AdSense 프로그램 정책: 쿠팡 파트너스와 동시 사용 가능 (conflict 없음)
- 광고 표시 의무 없음 (쿠팡 파트너스만 공정위 고시 적용)
- 다만 transparency 차원에서 "광고" 레이블은 표시

**쿠팡 과 공존**: MainWindow._apply_ad_state 가 두 채널 중 우선순위를 결정.
 기본 우선순위: pro 비활성 > Coupang (한국 특화, CPS 직접 매출) > AdSense (글로벌, CPC) > 텍스트 fallback
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...utils.logger import get_logger


_log = get_logger("gui.adsense_ad")


# 광고 표시 레이블 — 필수는 아니지만 투명성 차원
DISCLOSURE_TEXT = "광고 | powered by Google AdSense"


# AdSense 표준 표시 광고 권장 크기
DEFAULT_WIDTH = 728   # leaderboard
DEFAULT_HEIGHT = 90


def build_html(
    publisher_id: str,
    ad_slot: str,
    *,
    ad_format: str = "auto",
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    full_width_responsive: bool = True,
) -> str:
    """AdSense 스크립트 포함 HTML 페이지.

    Parameters
    ----------
    publisher_id : ``ca-pub-XXXXXXXXXXXXXXXX`` 형식 게시자 ID
    ad_slot : AdSense 광고 단위 ID (숫자 문자열)
    ad_format : ``auto`` / ``rectangle`` / ``horizontal`` / ``vertical``
    """
    # JS injection 차단
    safe_pub = "".join(c for c in publisher_id if c.isalnum() or c == "-")[:40]
    if not safe_pub.startswith("ca-pub-"):
        # 잘못된 형식 → placeholder 표시용 HTML
        return f"<!doctype html><html><body>광고 로드 불가 (잘못된 publisher_id)</body></html>"
    safe_slot = "".join(c for c in ad_slot if c.isdigit())[:16] or "0"
    safe_format = "".join(c for c in ad_format if c.isalnum())[:20] or "auto"
    safe_responsive = "true" if full_width_responsive else "false"

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>AdSense Ad</title>
<style>
  html, body {{
    margin: 0; padding: 0; overflow: hidden;
    background: transparent;
    font-family: 'Pretendard', '맑은 고딕', sans-serif;
  }}
  .ad-wrap {{
    width: 100%; height: 100%;
    display: flex; align-items: center; justify-content: center;
  }}
</style>
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={safe_pub}"
        crossorigin="anonymous"></script>
</head>
<body>
<div class="ad-wrap">
  <ins class="adsbygoogle"
       style="display:block;width:{int(width)}px;height:{int(height)}px"
       data-ad-client="{safe_pub}"
       data-ad-slot="{safe_slot}"
       data-ad-format="{safe_format}"
       data-full-width-responsive="{safe_responsive}"></ins>
</div>
<script>
  try {{
    (adsbygoogle = window.adsbygoogle || []).push({{}});
  }} catch (e) {{
    document.querySelector('.ad-wrap').innerText = "광고를 불러올 수 없습니다.";
  }}
</script>
</body>
</html>
"""


class AdSenseWidget(QWidget):
    """Google AdSense 광고 QWebEngineView 래퍼.

    쿠팡 위젯과 동일한 interface — MainWindow 가 polymorphic 하게 다룰 수 있음.
    """

    load_finished = Signal(bool)

    def __init__(
        self,
        publisher_id: str,
        ad_slot: str,
        *,
        ad_format: str = "auto",
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._publisher_id = publisher_id
        self._ad_slot = ad_slot
        self._ad_format = ad_format
        self._width = width
        self._height = height
        self._engine = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView  # type: ignore
        except ImportError:
            _log.warning("QWebEngineView 미설치 → AdSense 폴백")
            self._fallback_label(layout, "Google AdSense (WebEngine 필요)")
        else:
            try:
                self._engine = QWebEngineView(self)
                self._engine.setFixedSize(int(width), int(height))
                html = build_html(
                    publisher_id, ad_slot,
                    ad_format=ad_format, width=width, height=height,
                )
                self._engine.setHtml(html)
                self._engine.loadFinished.connect(self._on_load_finished)
                layout.addWidget(self._engine, 0, Qt.AlignmentFlag.AlignCenter)
            except Exception as exc:  # noqa: BLE001
                _log.warning("AdSenseWidget 초기화 실패: %s", exc)
                self._engine = None
                self._fallback_label(layout, f"AdSense 실패: {type(exc).__name__}")

        # 광고 레이블
        self._disc_label = QLabel(DISCLOSURE_TEXT)
        self._disc_label.setStyleSheet(
            "QLabel { color: #999; font-size: 8pt; padding: 2px 6px; }"
        )
        self._disc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._disc_label)

    # ---- helpers ----

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
                channel=rt.CH_ADSENSE,
                partner_id=self._publisher_id,
                ad_slot=self._ad_slot,
            )
        else:
            _log.info("AdSense 광고 로드 실패 (network / 심사 대기?)")
            rt.record_load_failed(channel=rt.CH_ADSENSE, reason="loadFinished=false")
        self.load_finished.emit(bool(ok))

    # ---- properties ----

    @property
    def publisher_id(self) -> str:
        return self._publisher_id

    @property
    def ad_slot(self) -> str:
        return self._ad_slot

    @property
    def is_rendered(self) -> bool:
        return self._engine is not None


__all__ = [
    "AdSenseWidget",
    "DISCLOSURE_TEXT",
    "DEFAULT_WIDTH",
    "DEFAULT_HEIGHT",
    "build_html",
]
