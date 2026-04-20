"""광고 수익 텔레메트리 — v0.15.0.

로컬에 광고 노출/클릭/에러 이벤트를 JSON Lines 로 기록하고, 간단한 대시보드 통계를 제공.

**기본 비활성** — 기존 `telemetry.record` (`AppConfig.telemetry_optin`) 를 그대로 존중.
opt-in 된 경우만 기록.

**이벤트 타입**:
- ``ad_impression`` — 광고 위젯 노출 (loadFinished ok)
- ``ad_click`` — 사용자가 "자세히 보기" 같은 버튼 클릭 (쿠팡/AdSense widget 의 내부 click)
- ``ad_load_failed`` — 광고 렌더링 실패 (network / 심사 대기 등)

**프라이버시**:
- 개인정보 전혀 기록 안 함 (user id / IP 미수집)
- 외부 전송 없음 (로컬 JSONL 만)
- "통계 삭제" 버튼으로 즉시 초기화 가능

**수익 추정 모델** (매우 러프):
- 쿠팡 파트너스: 클릭당 평균 20원 (CTR 2~5%) — 업종/시즌 편차 큼
- Google AdSense: CPM $1~5, CPC $0.1~0.5 (한국 트래픽 기준)
- 이 상수들은 `ESTIMATES` 에 모아 두고 UI 에서 편집 가능하도록
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ..utils import telemetry
from ..utils.logger import get_logger


_log = get_logger("commerce.revenue")


# ---------------------------------------------------------------------------
# 이벤트 상수
# ---------------------------------------------------------------------------

EV_IMPRESSION = "ad_impression"
EV_CLICK = "ad_click"
EV_LOAD_FAILED = "ad_load_failed"

# 채널
CH_COUPANG = "coupang"
CH_ADSENSE = "adsense"
CH_TEXT = "text_ad"      # v0.9 텍스트 순환 광고

# 수익 추정치 (원) — 사용자가 자신의 실측 데이터로 갱신 가능
ESTIMATES = {
    CH_COUPANG: {"cpc_krw": 20.0,  "cpm_krw": 0.0},     # CPS 가 대부분이지만 클릭당 단순화
    CH_ADSENSE: {"cpc_krw": 300.0, "cpm_krw": 2000.0},
    CH_TEXT: {"cpc_krw": 0.0, "cpm_krw": 0.0},
}


# ---------------------------------------------------------------------------
# 기록 API
# ---------------------------------------------------------------------------


def record_impression(channel: str, *, partner_id: str = "", ad_slot: str = "") -> None:
    """광고 노출 기록. opt-in 안 했으면 no-op."""
    telemetry.record(
        EV_IMPRESSION, channel=channel,
        partner_id=str(partner_id), ad_slot=str(ad_slot),
    )


def record_click(channel: str, *, partner_id: str = "", ad_slot: str = "") -> None:
    telemetry.record(
        EV_CLICK, channel=channel,
        partner_id=str(partner_id), ad_slot=str(ad_slot),
    )


def record_load_failed(channel: str, *, reason: str = "") -> None:
    telemetry.record(
        EV_LOAD_FAILED, channel=channel, reason=str(reason)[:120],
    )


# ---------------------------------------------------------------------------
# 조회 / 대시보드
# ---------------------------------------------------------------------------


@dataclass
class ChannelStats:
    """채널별 집계."""

    channel: str
    impressions: int = 0
    clicks: int = 0
    load_failures: int = 0

    @property
    def ctr(self) -> float:
        """Click-through rate (0.0~1.0)."""
        if self.impressions <= 0:
            return 0.0
        return self.clicks / self.impressions

    def estimated_revenue_krw(self) -> float:
        """간단 추정 — CPC × 클릭 + CPM × (노출/1000)."""
        est = ESTIMATES.get(self.channel, {"cpc_krw": 0.0, "cpm_krw": 0.0})
        return (
            self.clicks * est.get("cpc_krw", 0.0)
            + (self.impressions / 1000.0) * est.get("cpm_krw", 0.0)
        )


@dataclass
class RevenueDashboard:
    """기간별 수익 요약 — GUI 대시보드에서 렌더링."""

    since: datetime
    until: datetime
    channels: dict[str, ChannelStats]

    @property
    def total_impressions(self) -> int:
        return sum(c.impressions for c in self.channels.values())

    @property
    def total_clicks(self) -> int:
        return sum(c.clicks for c in self.channels.values())

    @property
    def total_revenue_krw(self) -> float:
        return sum(c.estimated_revenue_krw() for c in self.channels.values())

    @property
    def overall_ctr(self) -> float:
        if self.total_impressions <= 0:
            return 0.0
        return self.total_clicks / self.total_impressions


def compute_dashboard(*, days: int = 30) -> RevenueDashboard:
    """`days` 일 전부터 현재까지의 수익 통계를 집계.

    telemetry.jsonl 을 전체 스캔 (n 수만에서 느려지면 v0.16 에 rollup 추가).
    """
    path = _telemetry_path()
    now = datetime.now()
    since = now - timedelta(days=int(days))
    channels: dict[str, ChannelStats] = {}

    if not path.exists():
        return RevenueDashboard(since=since, until=now, channels=channels)

    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = entry.get("event", "")
            if event not in (EV_IMPRESSION, EV_CLICK, EV_LOAD_FAILED):
                continue
            ts = float(entry.get("ts", 0) or 0)
            if ts <= 0:
                continue
            when = datetime.fromtimestamp(ts)
            if when < since:
                continue

            channel = str(entry.get("channel", "") or "unknown")
            stats = channels.setdefault(channel, ChannelStats(channel=channel))
            if event == EV_IMPRESSION:
                stats.impressions += 1
            elif event == EV_CLICK:
                stats.clicks += 1
            elif event == EV_LOAD_FAILED:
                stats.load_failures += 1
    except OSError as exc:
        _log.warning("텔레메트리 읽기 실패: %s", exc)

    return RevenueDashboard(since=since, until=now, channels=channels)


def format_dashboard(db: RevenueDashboard) -> str:
    """사람 읽기 좋은 텍스트 요약 — GUI 및 CLI 공용."""
    lines: list[str] = []
    lines.append("=" * 60)
    days = (db.until - db.since).days
    lines.append(f" 광고 수익 대시보드 — 최근 {days}일")
    lines.append(
        f" 기간: {db.since.strftime('%Y-%m-%d')} ~ {db.until.strftime('%Y-%m-%d')}"
    )
    lines.append("=" * 60)
    if not db.channels:
        lines.append(" (데이터 없음 — 광고 미노출 또는 텔레메트리 opt-out)")
        return "\n".join(lines)

    lines.append(f" {'채널':<10} {'노출':>8} {'클릭':>6} {'CTR':>6}  {'추정수익':>12}")
    lines.append("-" * 60)
    for channel, stats in sorted(
        db.channels.items(), key=lambda kv: -kv[1].estimated_revenue_krw(),
    ):
        lines.append(
            f" {channel:<10} {stats.impressions:>8,} {stats.clicks:>6,} "
            f"{stats.ctr * 100:>5.2f}%  ₩{stats.estimated_revenue_krw():>11,.0f}"
        )
    lines.append("-" * 60)
    lines.append(
        f" {'합계':<10} {db.total_impressions:>8,} {db.total_clicks:>6,} "
        f"{db.overall_ctr * 100:>5.2f}%  ₩{db.total_revenue_krw:>11,.0f}"
    )
    lines.append("")
    lines.append(
        " ℹ️ CPC/CPM 추정치는 `ESTIMATES` 에 정의 — 실측 데이터로 조정 권장"
    )
    return "\n".join(lines)


def _telemetry_path() -> Path:
    """telemetry 모듈과 동일 경로 계산."""
    return telemetry._telemetry_path()


__all__ = [
    "EV_IMPRESSION", "EV_CLICK", "EV_LOAD_FAILED",
    "CH_COUPANG", "CH_ADSENSE", "CH_TEXT",
    "ESTIMATES",
    "record_impression", "record_click", "record_load_failed",
    "ChannelStats", "RevenueDashboard",
    "compute_dashboard", "format_dashboard",
]
