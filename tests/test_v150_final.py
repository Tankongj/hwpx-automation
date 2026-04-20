"""v0.15.0: md_to_hwpx python-hwpx 경로 + Self-MoA GUI + 수익 대시보드."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUNDLED = ROOT / "templates" / "00_기본_10단계스타일.hwpx"
REQUIRES_BUNDLED = pytest.mark.skipif(not BUNDLED.exists(), reason="bundled missing")


# ---------------------------------------------------------------------------
# Track 1: md_to_hwpx python-hwpx 경로
# ---------------------------------------------------------------------------


@REQUIRES_BUNDLED
def test_convert_fast_path_blocks_only(tmp_path):
    """use_python_hwpx_writer=True + 단순 blocks → python-hwpx 경로."""
    from src.hwpx.md_to_hwpx import convert
    from src.parser.ir_schema import Block
    from src.checklist.rfp_extractor import extract_hwpx_text

    out = tmp_path / "fast.hwpx"
    blocks = [
        Block(line_no=1, level=-1, text="제안서 제목"),
        Block(line_no=2, level=1, text="Ⅰ. 개요"),
        Block(line_no=3, level=0, text="본문 FAST-UNIQUE-TOKEN"),
    ]
    convert(blocks, template=BUNDLED, output=out, use_python_hwpx_writer=True)
    assert out.exists()
    # 내용이 실제로 담겼는지
    text = extract_hwpx_text(out)
    assert "FAST-UNIQUE-TOKEN" in text


@REQUIRES_BUNDLED
def test_convert_falls_back_to_legacy_when_advanced_used(tmp_path):
    """reference 가 있으면 고급 경로 필요 → python-hwpx 스킵."""
    from src.hwpx.md_to_hwpx import convert
    from src.parser.ir_schema import Block

    # reference 를 지정 (python-hwpx 는 reference 병합 지원 안 함 → legacy 경로 타야)
    out = tmp_path / "legacy.hwpx"
    blocks = [Block(line_no=1, level=0, text="본문")]

    # 실제 legacy 경로는 reference 도 필요로 하는 등 조건이 많아 실패할 수 있음 — 이 테스트는
    # "python-hwpx 경로로 타지 않고 legacy 가 불리는지" 까지만 검증.
    called_fast = {"used": False}

    original_write = None
    try:
        from src.hwpx import hwpx_writer
        original_write = hwpx_writer.write_ir_blocks
        def spy(*a, **kw):
            called_fast["used"] = True
            return original_write(*a, **kw)
        hwpx_writer.write_ir_blocks = spy

        try:
            convert(
                blocks, template=BUNDLED, output=out,
                use_python_hwpx_writer=True,
                reference=BUNDLED,  # advanced feature
            )
        except Exception:
            # legacy 경로가 reference 처리 과정에서 실패해도 OK — 포인트는 fast 가 호출 안 됨
            pass
    finally:
        if original_write is not None:
            hwpx_writer.write_ir_blocks = original_write

    assert called_fast["used"] is False, "advanced feature 사용 시 fast 경로 안 타야"


@REQUIRES_BUNDLED
def test_convert_fast_path_accepts_v1_paragraphs(tmp_path):
    """v1 paragraph dict (type/text) 도 수용."""
    from src.hwpx.md_to_hwpx import convert

    out = tmp_path / "v1dict.hwpx"
    paras = [
        {"type": "heading1", "text": "Ⅰ. V1-DICT-HEADING"},
        {"type": "body", "text": "V1-DICT-BODY"},
    ]
    convert(paras, template=BUNDLED, output=out, use_python_hwpx_writer=True)
    assert out.exists()


def test_v1_type_to_level_mapping():
    from src.hwpx.hwpx_writer import _v1_type_to_level
    assert _v1_type_to_level("heading1") == 1
    assert _v1_type_to_level("h3") == 3
    assert _v1_type_to_level("body") == 0
    assert _v1_type_to_level("title") == -1
    assert _v1_type_to_level("unknown") == 0  # default


def test_app_config_use_python_hwpx_writer_field(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.settings import app_config

    cfg = app_config.AppConfig(use_python_hwpx_writer=True)
    app_config.save(cfg)
    loaded = app_config.load()
    assert loaded.use_python_hwpx_writer is True


def test_cli_build_exposes_python_hwpx_writer_flag():
    from src.cli import build_parser
    parser = build_parser()
    ns = parser.parse_args([
        "build",
        "--template", "x.hwpx",
        "--txt", "in.txt",
        "--output", "out.hwpx",
        "--python-hwpx-writer",
    ])
    assert ns.python_hwpx_writer is True


# ---------------------------------------------------------------------------
# Track 2: Self-MoA × Batch GUI heartbeat
# ---------------------------------------------------------------------------


def test_conversion_worker_emits_batch_signals_when_selfmoa_batch(tmp_path, qtbot, monkeypatch):
    """Self-MoA × Batch 이 create_default_client 에서 돌아오면 batch_started/finished 시그널 emit."""
    monkeypatch.setenv("APPDATA", str(tmp_path))

    from src.gui.workers.conversion_worker import ConversionRequest, ConversionWorker
    from src.parser.gemini_resolver import GenerateResult
    from src.parser import gemini_resolver, regex_parser

    # 가짜 Self-MoA client (use_batch=True 속성)
    class _FakeMoAClient:
        use_batch = True
        draws = 3
        model = "self-moa[fake×3+batch]"
        def generate(self, prompt: str):
            return GenerateResult(text='[]')

    fake_client = _FakeMoAClient()

    # create_default_client 를 가짜로 바꿔 — 실 네트워크 없음
    monkeypatch.setattr(
        gemini_resolver, "create_default_client",
        lambda backend=None: fake_client,
    )

    # resolve 도 더미 (호출되면 바로 반환)
    fake_report = MagicMock()
    fake_report.human_summary = lambda: "fake summary"
    monkeypatch.setattr(
        gemini_resolver, "resolve",
        lambda blocks, client=None, **kw: fake_report,
    )

    # 원고: 최소한 1 개 ambiguous 필요
    txt = tmp_path / "draft.txt"
    txt.write_text(
        "제안서 제목\n\n"
        "단순 본문\n"
        "?????????????????????????????????????????? 이것은 애매한 긴 줄입니다\n",
        encoding="utf-8",
    )

    # 템플릿 분석 / 변환 단계는 skip — Worker 가 거기까지 안 가도 batch 시그널은 emit
    from src.template import template_analyzer
    from src.hwpx import md_to_hwpx
    monkeypatch.setattr(
        template_analyzer, "analyze",
        lambda p: MagicMock(
            to_engine_style_dict=lambda: {},
            fallback_used_levels=[],
        ),
    )
    monkeypatch.setattr(
        md_to_hwpx, "convert",
        lambda blocks, *, template, output, style_map, run_fix_namespaces, **kw:
            Path(output).write_bytes(b"fake"),
    )

    req = ConversionRequest(
        template_path=tmp_path / "tpl.hwpx",
        txt_path=txt,
        output_path=tmp_path / "out.hwpx",
        use_gemini=True,
        resolver_backend="gemini",
        run_fix_namespaces=False,
        verify_after=False,
    )
    (tmp_path / "tpl.hwpx").write_bytes(b"x")

    worker = ConversionWorker(req)
    started_events: list[int] = []
    finished_events: list[bool] = []
    worker.signals.batch_started.connect(started_events.append)
    worker.signals.batch_finished.connect(finished_events.append)

    worker.run()
    # resolve 가 mock 이라 즉시 끝남
    assert started_events == [3]
    assert finished_events == [True]


def test_conversion_worker_no_batch_signal_when_not_selfmoa_batch(tmp_path, qtbot, monkeypatch):
    """일반 resolver 면 batch_started 안 emit."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.gui.workers.conversion_worker import ConversionRequest, ConversionWorker
    from src.parser.gemini_resolver import GenerateResult
    from src.parser import gemini_resolver

    class _Plain:
        use_batch = False
        model = "plain"
        def generate(self, prompt): return GenerateResult(text='[]')

    monkeypatch.setattr(
        gemini_resolver, "create_default_client",
        lambda backend=None: _Plain(),
    )
    fake_report = MagicMock(human_summary=lambda: "ok")
    monkeypatch.setattr(
        gemini_resolver, "resolve",
        lambda blocks, client=None, **kw: fake_report,
    )

    txt = tmp_path / "d.txt"
    txt.write_text(
        "제안서 제목\n\n? 애매한 긴 줄입니다 " + "x" * 60,
        encoding="utf-8",
    )

    from src.template import template_analyzer
    from src.hwpx import md_to_hwpx
    monkeypatch.setattr(
        template_analyzer, "analyze",
        lambda p: MagicMock(
            to_engine_style_dict=lambda: {}, fallback_used_levels=[],
        ),
    )
    monkeypatch.setattr(
        md_to_hwpx, "convert",
        lambda blocks, *, template, output, style_map, run_fix_namespaces, **kw:
            Path(output).write_bytes(b"fake"),
    )

    (tmp_path / "tpl.hwpx").write_bytes(b"x")
    req = ConversionRequest(
        template_path=tmp_path / "tpl.hwpx",
        txt_path=txt,
        output_path=tmp_path / "o.hwpx",
        use_gemini=True,
        resolver_backend="gemini",
        run_fix_namespaces=False,
        verify_after=False,
    )
    worker = ConversionWorker(req)
    started: list[int] = []
    worker.signals.batch_started.connect(started.append)
    worker.run()
    assert started == []


# ---------------------------------------------------------------------------
# Track 3: 수익 텔레메트리
# ---------------------------------------------------------------------------


def test_revenue_record_impression_respects_optin(tmp_path, monkeypatch):
    """opt-in 안 했을 땐 no-op — telemetry.jsonl 생성 안 됨."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.utils import telemetry
    from src.commerce import revenue_telemetry as rt

    telemetry.configure(False)
    rt.record_impression(channel="coupang", partner_id="982081")
    # telemetry 파일 미생성
    assert not telemetry._telemetry_path().exists()


def test_revenue_record_impression_writes_when_optin(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.utils import telemetry
    from src.commerce import revenue_telemetry as rt

    telemetry.configure(True)
    rt.record_impression(channel="coupang", partner_id="982081", ad_slot="AF7480765")
    rt.record_click(channel="coupang", partner_id="982081")
    rt.record_load_failed(channel="adsense", reason="network")

    path = telemetry._telemetry_path()
    assert path.exists()
    entries = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(entries) == 3
    assert entries[0]["event"] == "ad_impression"
    assert entries[0]["channel"] == "coupang"
    assert entries[1]["event"] == "ad_click"
    assert entries[2]["event"] == "ad_load_failed"


def test_channel_stats_ctr_and_revenue():
    from src.commerce.revenue_telemetry import ChannelStats, ESTIMATES, CH_COUPANG

    s = ChannelStats(channel=CH_COUPANG, impressions=1000, clicks=30)
    assert abs(s.ctr - 0.03) < 1e-6
    # CPC 20 × 30 = 600
    expected = 30 * ESTIMATES[CH_COUPANG]["cpc_krw"]
    assert abs(s.estimated_revenue_krw() - expected) < 0.01


def test_channel_stats_ctr_zero_impressions():
    from src.commerce.revenue_telemetry import ChannelStats
    s = ChannelStats(channel="x")
    assert s.ctr == 0.0
    assert s.estimated_revenue_krw() == 0.0


def test_compute_dashboard_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.commerce.revenue_telemetry import compute_dashboard

    db = compute_dashboard(days=30)
    assert db.channels == {}
    assert db.total_impressions == 0


def test_compute_dashboard_with_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.utils import telemetry
    from src.commerce import revenue_telemetry as rt

    telemetry.configure(True)
    # 노출 10, 클릭 3
    for _ in range(10):
        rt.record_impression(channel="coupang")
    for _ in range(3):
        rt.record_click(channel="coupang")
    rt.record_impression(channel="adsense")

    db = rt.compute_dashboard(days=30)
    assert set(db.channels) == {"coupang", "adsense"}
    assert db.channels["coupang"].impressions == 10
    assert db.channels["coupang"].clicks == 3
    assert db.channels["adsense"].impressions == 1
    assert abs(db.overall_ctr - 3 / 11) < 1e-6


def test_format_dashboard_empty_message(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.commerce.revenue_telemetry import compute_dashboard, format_dashboard
    db = compute_dashboard(days=7)
    text = format_dashboard(db)
    assert "데이터 없음" in text
    assert "7일" in text


def test_format_dashboard_contains_channel_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.utils import telemetry
    from src.commerce import revenue_telemetry as rt

    telemetry.configure(True)
    rt.record_impression(channel="coupang")
    rt.record_click(channel="coupang")

    db = rt.compute_dashboard(days=30)
    text = rt.format_dashboard(db)
    assert "coupang" in text
    assert "노출" in text
    assert "클릭" in text
    assert "추정수익" in text or "CTR" in text


def test_compute_dashboard_ignores_old_entries(tmp_path, monkeypatch):
    """since 이전 기록은 제외."""
    import time as _time
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.utils import telemetry
    from src.commerce import revenue_telemetry as rt

    telemetry.configure(True)
    path = telemetry._telemetry_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # 90 일 전 노출 1 + 오늘 노출 2
    ninety_days_ago = _time.time() - 90 * 86400
    entries = [
        {"ts": ninety_days_ago, "event": "ad_impression", "channel": "coupang"},
        {"ts": _time.time(), "event": "ad_impression", "channel": "coupang"},
        {"ts": _time.time(), "event": "ad_impression", "channel": "coupang"},
    ]
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")

    # 최근 30 일 → 오래된 1 건 제외
    db = rt.compute_dashboard(days=30)
    assert db.channels["coupang"].impressions == 2


def test_coupang_widget_records_impression_on_load_ok(qtbot, tmp_path, monkeypatch):
    """CoupangAdWidget 이 loadFinished(True) 시 record_impression 호출."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.utils import telemetry
    from src.commerce import revenue_telemetry as rt
    from src.gui.widgets.coupang_ad import CoupangAdWidget

    telemetry.configure(True)

    w = CoupangAdWidget(partner_id=982081, tracking_code="AF7480765")
    qtbot.addWidget(w)
    # load 는 실 네트워크 — 직접 _on_load_finished 호출로 ok=True 시뮬
    w._on_load_finished(True)

    db = rt.compute_dashboard(days=1)
    assert db.channels.get("coupang") is not None
    assert db.channels["coupang"].impressions >= 1


def test_adsense_widget_records_impression_on_load_ok(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.utils import telemetry
    from src.commerce import revenue_telemetry as rt
    from src.gui.widgets.adsense_ad import AdSenseWidget

    telemetry.configure(True)

    w = AdSenseWidget(
        publisher_id="ca-pub-1234567890123456",
        ad_slot="1234567890",
    )
    qtbot.addWidget(w)
    w._on_load_finished(True)

    db = rt.compute_dashboard(days=1)
    assert db.channels.get("adsense") is not None
    assert db.channels["adsense"].impressions >= 1


def test_ad_widget_records_load_failed(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.utils import telemetry
    from src.commerce import revenue_telemetry as rt
    from src.gui.widgets.coupang_ad import CoupangAdWidget

    telemetry.configure(True)
    w = CoupangAdWidget(partner_id=1, tracking_code="X")
    qtbot.addWidget(w)
    w._on_load_finished(False)  # 실패

    db = rt.compute_dashboard(days=1)
    stats = db.channels.get("coupang")
    assert stats is not None
    assert stats.load_failures >= 1
    assert stats.impressions == 0
