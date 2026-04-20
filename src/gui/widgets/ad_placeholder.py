"""광고 영역 placeholder (v0.1~v0.6) → v0.7.0 실제 활성화 → v0.9.0 순환.

MVP 단계 기본: ``setFixedHeight(0)`` 으로 숨김. 상업화 단계에서 ``activate()`` 호출하면
높이 확보 + 간단한 광고 영역 표시. v0.9.0 부터 ``activate_rotating()`` 으로 여러 URL 순환.

실제 ad network SDK 통합은 별도 단계에서 (QWebEngineView 로 교체).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class AdPlaceholder(QWidget):
    """광고 슬롯. 기본 숨김, ``activate()`` 로 활성."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._active = False
        self._click_url = ""
        self._rotation_items: list[tuple[str, str]] = []  # [(text, url), ...]
        self._rotation_idx = 0
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)

        self._ad_label = QLabel(
            "광고 영역 (v0.7.0 placeholder) — 설정에서 비활성화 가능"
        )
        self._ad_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ad_label.setStyleSheet(
            "QLabel { background-color: #f5f5f5; border: 1px dashed #ccc; "
            "padding: 8px; color: #666; font-size: 10pt; }"
        )
        self._layout.addWidget(self._ad_label, stretch=1)

        self._click_btn = QPushButton("자세히 보기")
        self._click_btn.clicked.connect(self._on_click)
        self._click_btn.setVisible(False)
        self._layout.addWidget(self._click_btn)

        # v0.9.0: 순환 타이머 (inactive 상태에서는 정지)
        self._rotate_timer = QTimer(self)
        self._rotate_timer.timeout.connect(self._advance_rotation)

        # MVP 기본 숨김
        self.setFixedHeight(0)
        for child in (self._ad_label, self._click_btn):
            child.setVisible(False)

    # ---- activation ----

    def activate(
        self,
        *,
        text: str = "광고 영역 (v0.7.0 placeholder) — 설정에서 비활성화 가능",
        click_url: str = "",
        height: int = 80,
    ) -> None:
        """광고 활성화. 사용자가 Settings 에서 ad_enabled=True 로 켰을 때만 호출."""
        self._active = True
        self._click_url = click_url
        self._ad_label.setText(text)
        self._ad_label.setVisible(True)
        self._click_btn.setVisible(bool(click_url))
        self.setFixedHeight(height)

    def deactivate(self) -> None:
        """광고 비활성화 — 0px 로 축소."""
        self._active = False
        self._rotate_timer.stop()
        self._rotation_items = []
        self._ad_label.setVisible(False)
        self._click_btn.setVisible(False)
        # v0.12.0: 쿠팡 위젯 정리
        if getattr(self, "_coupang_widget", None) is not None:
            try:
                self._layout.removeWidget(self._coupang_widget)
                self._coupang_widget.deleteLater()
            except Exception:  # noqa: BLE001
                pass
            self._coupang_widget = None
        # v0.13.0: AdSense 위젯 정리
        if getattr(self, "_adsense_widget", None) is not None:
            try:
                self._layout.removeWidget(self._adsense_widget)
                self._adsense_widget.deleteLater()
            except Exception:  # noqa: BLE001
                pass
            self._adsense_widget = None
        self.setFixedHeight(0)

    def activate_adsense(
        self,
        publisher_id: str,
        ad_slot: str,
        *,
        ad_format: str = "auto",
        width: int = 728,
        height: int = 90,
    ) -> bool:
        """Google AdSense 광고 활성화 (v0.13.0 매출 채널 #2).

        Returns
        -------
        bool — QWebEngineView 엔진이 뜨면 True. 미설치 / 인자 오류 시 False.
        """
        if not publisher_id or not ad_slot:
            self.deactivate()
            return False
        if not publisher_id.startswith("ca-pub-"):
            from ...utils.logger import get_logger
            get_logger("gui.ad_placeholder").warning(
                "AdSense publisher_id 형식 오류 (ca-pub- 로 시작해야 함): %s", publisher_id,
            )
            self.deactivate()
            return False

        # 기존 순환/쿠팡 정리
        self._rotate_timer.stop()
        self._rotation_items = []
        self._click_url = ""
        self._ad_label.setVisible(False)
        self._click_btn.setVisible(False)

        if getattr(self, "_coupang_widget", None) is not None:
            old = self._coupang_widget
            self._layout.removeWidget(old)
            old.deleteLater()
            self._coupang_widget = None
        if getattr(self, "_adsense_widget", None) is not None:
            old = self._adsense_widget
            self._layout.removeWidget(old)
            old.deleteLater()
            self._adsense_widget = None

        try:
            from .adsense_ad import AdSenseWidget
            widget = AdSenseWidget(
                publisher_id=publisher_id,
                ad_slot=ad_slot,
                ad_format=ad_format,
                width=width, height=height,
                parent=self,
            )
        except Exception as exc:  # noqa: BLE001
            from ...utils.logger import get_logger
            get_logger("gui.ad_placeholder").warning(
                "AdSense 위젯 생성 실패: %s → 텍스트 fallback", exc,
            )
            self.activate(
                text="Google AdSense (WebEngine 필요 — 텍스트 대체)",
                click_url="",
                height=height,
            )
            return False

        self._adsense_widget = widget
        self._layout.addWidget(widget, stretch=1)
        self.setFixedHeight(int(height) + 20)
        self._active = True
        return widget.is_rendered

    def activate_coupang(
        self,
        partner_id: int,
        tracking_code: str,
        *,
        template: str = "carousel",
        width: int = 680,
        height: int = 80,
    ) -> bool:
        """쿠팡 파트너스 광고 활성화 (v0.12.0 매출 채널 #1).

        QWebEngineView 로 실제 carousel 광고 렌더링. QWebEngine 미설치면 False 반환.

        사전조건:
        - ``partner_id`` > 0
        - ``tracking_code`` 비어 있지 않음

        Returns
        -------
        bool — 실제 광고 엔진이 뜨면 True, fallback placeholder 면 False.
        """
        if not partner_id or not tracking_code:
            self.deactivate()
            return False

        # 기존 정적/순환 상태 초기화
        self._rotate_timer.stop()
        self._rotation_items = []
        self._click_url = ""
        self._ad_label.setVisible(False)
        self._click_btn.setVisible(False)

        # 쿠팡 위젯 삽입 (기존에 있으면 교체)
        if getattr(self, "_coupang_widget", None) is not None:
            old = self._coupang_widget
            self._layout.removeWidget(old)
            old.deleteLater()
            self._coupang_widget = None

        try:
            from .coupang_ad import CoupangAdWidget
            widget = CoupangAdWidget(
                partner_id=partner_id,
                tracking_code=tracking_code,
                template=template,
                width=width, height=height,
                parent=self,
            )
        except Exception as exc:  # noqa: BLE001
            # 의존성 / 초기화 실패 — 텍스트 fallback
            from ...utils.logger import get_logger
            get_logger("gui.ad_placeholder").warning(
                "쿠팡 위젯 생성 실패: %s → 텍스트 fallback", exc,
            )
            self.activate(
                text="쿠팡 파트너스 광고 (WebEngine 필요 — 텍스트 대체)",
                click_url="",
                height=height,
            )
            return False

        self._coupang_widget = widget
        self._layout.addWidget(widget, stretch=1)
        # +~20px for disclosure text under the engine
        self.setFixedHeight(int(height) + 20)
        self._active = True
        return widget.is_rendered

    def activate_rotating(
        self,
        items: list[tuple[str, str]],
        *,
        interval_sec: int = 30,
        height: int = 80,
    ) -> None:
        """여러 광고 순환 활성화. ``items`` 는 ``[(text, url), ...]`` 리스트.

        ``interval_sec=0`` 이면 첫 광고만 정적으로 표시.
        """
        if not items:
            self.deactivate()
            return
        self._rotation_items = list(items)
        self._rotation_idx = 0
        self._active = True
        text, url = self._rotation_items[0]
        self.activate(text=text, click_url=url, height=height)
        if interval_sec > 0 and len(items) > 1:
            self._rotate_timer.start(int(interval_sec * 1000))

    def _advance_rotation(self) -> None:
        if not self._rotation_items:
            self._rotate_timer.stop()
            return
        self._rotation_idx = (self._rotation_idx + 1) % len(self._rotation_items)
        text, url = self._rotation_items[self._rotation_idx]
        self._click_url = url
        self._ad_label.setText(text)
        self._click_btn.setVisible(bool(url))

    @property
    def is_active(self) -> bool:
        return self._active

    # ---- internals ----

    def _on_click(self) -> None:
        if self._click_url:
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(self._click_url))


__all__ = ["AdPlaceholder"]
