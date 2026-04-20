"""v0.14.0: Self-MoA × Batch + writer 표 + G2B 첨부 다운로드."""
from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUNDLED = ROOT / "templates" / "00_기본_10단계스타일.hwpx"
REQUIRES_BUNDLED = pytest.mark.skipif(not BUNDLED.exists(), reason="bundled missing")


# ---------------------------------------------------------------------------
# Track 1: Self-MoA × Batch 통합
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _grant_pro(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from src.commerce import tier_gate
    from src.commerce.auth_client import AuthSession
    from src.commerce.user_db import User

    user = User(username="tester", password_hash="x", salt="y", tier="pro")
    tier_gate.set_current_session(AuthSession(user=user, tier="pro"))
    yield
    tier_gate.set_current_session(None)


def test_self_moa_use_batch_false_uses_serial_path():
    """use_batch=False (기본) 이면 serial 경로."""
    from src.parser.self_moa import SelfMoAClient
    from src.parser.gemini_resolver import GenerateResult

    fake_base = MagicMock()
    fake_base.generate = MagicMock(return_value=GenerateResult(text='[]'))
    fake_base.model = "fake"

    moa = SelfMoAClient(fake_base, draws=3, use_batch=False, _skip_tier_check=True)
    assert moa.use_batch is False
    # model 표기에 batch tag 없음
    assert "+batch" not in moa.model

    # draws 경로 호출
    moa._draws_serial("prompt")
    assert fake_base.generate.call_count == 3


def test_self_moa_use_batch_true_tag_in_model_name():
    from src.parser.self_moa import SelfMoAClient

    fake = MagicMock()
    fake.model = "gemini-2.5-flash"

    moa = SelfMoAClient(
        fake, draws=3,
        use_batch=True,
        batch_api_key="fake",
        _skip_tier_check=True,
    )
    assert moa.use_batch is True
    assert "+batch" in moa.model


def test_self_moa_batch_falls_back_to_serial_without_key():
    """batch_api_key 없으면 자동 serial 폴백."""
    from src.parser.self_moa import SelfMoAClient
    from src.parser.gemini_resolver import GenerateResult

    fake_base = MagicMock()
    fake_base.generate = MagicMock(return_value=GenerateResult(text='[]'))
    fake_base.model = "fake"

    moa = SelfMoAClient(
        fake_base, draws=3,
        use_batch=True,
        batch_api_key=None,   # key 없음
        _skip_tier_check=True,
    )
    # api_key_manager.get_key 도 None 반환
    from src.settings import api_key_manager
    with patch.object(api_key_manager, "get_key", return_value=None):
        results = moa._draws_via_batch("prompt")
    # serial 경로로 떨어져서 3 번 호출됨
    assert fake_base.generate.call_count == 3
    assert len(results) == 3


def test_factory_passes_batch_config(monkeypatch):
    """create_default_client 이 use_gemini_batch 를 SelfMoAClient 에 전달."""
    from src.parser import gemini_resolver
    from src.settings import app_config, api_key_manager

    # Gemini client mock — 실 호출 방지
    class _FakeGenai:
        def __init__(self, *a, **kw): pass
        def generate(self, *a, **kw): return None

    monkeypatch.setattr(
        gemini_resolver.GoogleGenAIClient, "__init__",
        lambda self, api_key=None, model="gemini-2.5-flash": (
            setattr(self, "_client", None),
            setattr(self, "model", model),
        )[0],
    )

    cfg = app_config.AppConfig(
        resolver_backend="gemini",
        use_self_moa=True,
        self_moa_draws=2,
        use_gemini_batch=True,
    )
    app_config.save(cfg)
    monkeypatch.setattr(api_key_manager, "get_key", lambda service=None: "fake-key")

    client = gemini_resolver.create_default_client()
    # SelfMoAClient 래핑됐고 use_batch=True 여야
    assert hasattr(client, "use_batch")
    assert client.use_batch is True
    assert "+batch" in client.model


# ---------------------------------------------------------------------------
# Track 2: python-hwpx writer 표 삽입
# ---------------------------------------------------------------------------


@REQUIRES_BUNDLED
def test_write_paragraphs_with_table(tmp_path):
    from src.hwpx.hwpx_writer import WriteBlock, WriteTable, write_paragraphs

    out = tmp_path / "with_table.hwpx"
    blocks = [
        WriteBlock(text="표 위 단락", level=1),
        WriteTable(rows=[
            ["항목", "상태"],
            ["사업자등록증", "OK"],
            ["재무제표", "누락"],
        ]),
        WriteBlock(text="표 아래 단락", level=0),
    ]
    report = write_paragraphs(BUNDLED, blocks, out)
    assert out.exists()
    assert report.paragraphs_added == 2
    assert report.tables_added == 1
    assert not report.errors


@REQUIRES_BUNDLED
def test_write_paragraphs_table_roundtrip_structural(tmp_path):
    """table 삽입 후 HWPX 가 여전히 유효 ZIP + section XML 에 table 요소 포함되어야."""
    import zipfile
    from lxml import etree
    from src.hwpx.hwpx_writer import WriteTable, write_paragraphs

    out = tmp_path / "table_roundtrip.hwpx"
    report = write_paragraphs(
        BUNDLED,
        [WriteTable(rows=[["C11", "C12"], ["C21", "C22"]])],
        out,
    )
    assert out.exists()
    assert report.tables_added == 1

    # ZIP 무결성 + section0.xml 에 <hp:tbl> 요소가 추가됐는지
    with zipfile.ZipFile(out) as z:
        assert z.testzip() is None
        sec = z.read("Contents/section0.xml")
    # hp namespace 의 tbl 또는 tableEl 이 존재해야
    assert b"tbl" in sec or b"table" in sec.lower()


def test_write_table_empty_rows_raises_in_report(tmp_path):
    """빈 rows → 예외 대신 report.errors 에 기록."""
    from src.hwpx.hwpx_writer import WriteTable, write_paragraphs

    # BUNDLED 이 없으면 skip — REQUIRES_BUNDLED 적용
    if not BUNDLED.exists():
        pytest.skip("bundled missing")

    out = tmp_path / "empty.hwpx"
    blocks = [WriteTable(rows=[])]
    report = write_paragraphs(BUNDLED, blocks, out)
    # 표 삽입 실패 → skipped + errors 에 기록
    assert report.skipped >= 1
    assert any("Table" in e for e in report.errors)


# ---------------------------------------------------------------------------
# Track 3: G2B 첨부 다운로드
# ---------------------------------------------------------------------------


def _fake_json_response(body: dict):
    class _Resp:
        def __init__(self, data):
            self._raw = json.dumps(data).encode("utf-8")
        def read(self, size=-1):
            if size == -1:
                return self._raw
            chunk, self._raw = self._raw[:size], self._raw[size:]
            return chunk
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    return _Resp(body)


def _fake_binary_response(payload: bytes):
    class _Resp:
        def __init__(self, d):
            self._raw = d
        def read(self, size=-1):
            if size == -1:
                r, self._raw = self._raw, b""
                return r
            chunk, self._raw = self._raw[:size], self._raw[size:]
            return chunk
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    return _Resp(payload)


def test_extract_attachment_urls_finds_known_fields():
    from src.checklist.g2b_adapter import _extract_attachment_urls

    item = {
        "bidNtceNo": "X",
        "ntceSpecFileDwldUrl1": "https://example.com/a.hwp",
        "ntceSpecFileDwldUrl2": "https://example.com/b.pdf",
        "etcField": "not a url",
        "dtlsBidNtceDocUrl1": "https://other.com/c.hwpx",
    }
    urls = _extract_attachment_urls(item)
    assert len(urls) == 3
    assert "https://example.com/a.hwp" in urls
    assert "https://other.com/c.hwpx" in urls


def test_extract_attachment_urls_ignores_unknown_fields():
    from src.checklist.g2b_adapter import _extract_attachment_urls

    item = {"someOtherField": "https://not-an-attachment.com/x"}
    urls = _extract_attachment_urls(item)
    assert urls == []


def test_guess_filename_from_url():
    from src.checklist.g2b_adapter import _guess_filename

    assert _guess_filename(
        "https://example.com/path/입찰공고문.hwp",
        default="fallback.bin",
    ) == "입찰공고문.hwp"


def test_guess_filename_sanitizes_invalid_chars():
    from src.checklist.g2b_adapter import _guess_filename

    name = _guess_filename(
        "https://example.com/path/bad<name>.hwp",
        default="fallback.bin",
    )
    assert "<" not in name
    assert ">" not in name


def test_guess_filename_fallback():
    from src.checklist.g2b_adapter import _guess_filename

    # 경로 없음 → default
    assert _guess_filename("https://host/", default="X.bin") == "X.bin"


def test_download_bid_attachments_end_to_end(tmp_path):
    from src.checklist.g2b_adapter import G2BClient, download_bid_attachments

    # 응답 시퀀스: 첫 호출은 상세 조회 (JSON), 다음은 첨부 파일들 (binary)
    calls = {"count": 0}
    sample_detail = {
        "response": {
            "header": {"resultCode": "00"},
            "body": {
                "totalCount": 1,
                "items": [{
                    "bidNtceNo": "TEST-001",
                    "bidNtceNm": "테스트 공고",
                    "ntceSpecFileDwldUrl1": "https://example.com/rfp.hwp",
                    "ntceSpecFileDwldUrl2": "https://example.com/attachments.pdf",
                }],
            },
        },
    }

    def fake_opener(url_or_req, timeout):
        calls["count"] += 1
        url = url_or_req if isinstance(url_or_req, str) else getattr(url_or_req, "full_url", str(url_or_req))
        if "getBidPblancListInfoServc" in url:
            return _fake_json_response(sample_detail)
        # attachment URLs — 작은 바이너리
        if "rfp.hwp" in url:
            return _fake_binary_response(b"HWP-CONTENT-" * 100)
        if "attachments.pdf" in url:
            return _fake_binary_response(b"PDF-CONTENT-" * 50)
        return _fake_binary_response(b"")

    client = G2BClient("TEST-KEY", _skip_tier_check=True, _opener=fake_opener)
    out_dir = tmp_path / "downloads"

    result = download_bid_attachments(client, "TEST-001", str(out_dir))
    assert not result.error
    assert len(result.files) == 2
    assert result.total_bytes > 0
    # 파일 실제 존재
    assert (out_dir / "rfp.hwp").exists()
    assert (out_dir / "attachments.pdf").exists()


def test_download_bid_attachments_max_bytes_respected(tmp_path):
    """max_bytes 초과 파일은 스킵."""
    from src.checklist.g2b_adapter import G2BClient, download_bid_attachments

    detail = {
        "response": {
            "header": {"resultCode": "00"},
            "body": {
                "totalCount": 1,
                "items": [{
                    "bidNtceNo": "BIG",
                    "ntceSpecFileDwldUrl1": "https://example.com/huge.bin",
                }],
            },
        },
    }

    def fake_opener(url_or_req, timeout):
        url = url_or_req if isinstance(url_or_req, str) else getattr(url_or_req, "full_url", str(url_or_req))
        if "getBid" in url:
            return _fake_json_response(detail)
        # 큰 파일 — 1 MB
        return _fake_binary_response(b"X" * (1024 * 1024))

    client = G2BClient("K", _skip_tier_check=True, _opener=fake_opener)
    out_dir = tmp_path / "big"
    result = download_bid_attachments(
        client, "BIG", str(out_dir),
        max_bytes=100,  # 100 byte 상한
    )
    # 파일은 스킵됨
    assert len(result.skipped) == 1
    assert "huge.bin" in result.skipped[0]
    # 하지만 전체 다운로드 에러는 아님
    assert result.error == ""


def test_download_bid_attachments_detail_missing(tmp_path):
    """공고 상세가 없으면 error 반환."""
    from src.checklist.g2b_adapter import G2BClient, download_bid_attachments

    def fake_opener(url_or_req, timeout):
        # 빈 items 반환
        return _fake_json_response({
            "response": {"header": {"resultCode": "00"}, "body": {"totalCount": 0, "items": []}},
        })

    client = G2BClient("K", _skip_tier_check=True, _opener=fake_opener)
    result = download_bid_attachments(client, "NONEXIST", str(tmp_path / "o"))
    assert result.error
    assert "상세 없음" in result.error or "첨부" in result.error
