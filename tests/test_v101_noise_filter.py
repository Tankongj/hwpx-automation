"""v0.10.1: HWP BodyText 노이즈 필터 + 릴리즈 스크립트 cp949 대응.

v0.10.0 BodyText 파싱이 HWP 레코드 tag 명 (UTF-16LE 로 잘못 해석된 ASCII 바이트) 을
추출해 결과에 ``捤獥 汤捯 氠瑢`` 류 노이즈가 섞이던 문제를 해결.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest


HWP_SAMPLE = Path(
    r"D:/03_antigravity/19_[26 귀농귀촌 아카데미]/hwpx-proposal-automation/input/"
    r"1. 입찰공고문_26아카데미.hwp"
)
REQUIRES_HWP = pytest.mark.skipif(not HWP_SAMPLE.exists(), reason="HWP sample missing")


# ---------------------------------------------------------------------------
# _looks_like_text — record-level filter
# ---------------------------------------------------------------------------

def test_looks_like_text_accepts_korean():
    from src.checklist.hwp_text import _looks_like_text
    assert _looks_like_text("우편번호 : 30148")
    assert _looks_like_text("입 찰 공 고")
    assert _looks_like_text("2026년 귀농귀촌 아카데미")


def test_looks_like_text_accepts_ascii():
    from src.checklist.hwp_text import _looks_like_text
    assert _looks_like_text("http://www.epis.or.kr")
    assert _looks_like_text("Hello World 1234")


def test_looks_like_text_rejects_pure_noise():
    """HWP 레코드 tag 명이 UTF-16LE 로 잘못 디코드된 케이스."""
    from src.checklist.hwp_text import _looks_like_text

    # "lbt " → UTF-16LE → 氠瑢 + NUL padding
    pure_noise = "氠瑢\x00\x00\x00\x00"
    assert not _looks_like_text(pure_noise)

    # "ttof dceh" → 潴景∅∅∅∅慤桥∅∅∅∅
    assert not _looks_like_text("潴景\x00\x00\x00\x00慤桥\x00\x00\x00\x00")


def test_looks_like_text_mixed_passes():
    """mixed chunk (노이즈 앞머리 + 진짜 한국어 본문) 는 통과해야."""
    from src.checklist.hwp_text import _looks_like_text
    mixed = "捤獥\x00\x00\x00\x00汤捯\x00\x00\x00\x00우편번호 : 30148"
    assert _looks_like_text(mixed)


def test_looks_like_text_empty_is_false():
    from src.checklist.hwp_text import _looks_like_text
    assert not _looks_like_text("")


# ---------------------------------------------------------------------------
# _drop_noise_tokens — token-level filter (mixed chunk 정리)
# ---------------------------------------------------------------------------

def test_drop_noise_tokens_removes_pure_cjk_short():
    """공백으로 분리했을 때 한글/ASCII 없고 rare-CJK 만 있는 짧은 토큰 드롭."""
    from src.checklist.hwp_text import _drop_noise_tokens

    # 실제 시나리오 — sanitize 에서 NUL → 공백 치환 후 상태
    text = "捤獥     汤捯     우편번호 : 30148"
    cleaned = _drop_noise_tokens(text)
    assert "우편번호" in cleaned
    assert "捤獥" not in cleaned
    assert "汤捯" not in cleaned


def test_drop_noise_tokens_preserves_long_cjk():
    """긴 CJK-only 토큰은 정상 한문일 가능성 → 유지."""
    from src.checklist.hwp_text import _drop_noise_tokens
    # 7자 이상 CJK 토큰 (정상 한문 인용)
    long_hanja = "知之爲知之不知爲不知是知也"
    text = f"공자 왈 {long_hanja} 이라 하였다"
    cleaned = _drop_noise_tokens(text)
    assert long_hanja in cleaned


def test_drop_noise_tokens_multi_line():
    from src.checklist.hwp_text import _drop_noise_tokens
    text = "첫줄 정상\n捤獥 노이즈만\n다음줄"
    cleaned = _drop_noise_tokens(text)
    assert "첫줄 정상" in cleaned
    assert "捤獥" not in cleaned
    assert "다음줄" in cleaned


def test_drop_noise_tokens_preserves_ascii_only_token():
    from src.checklist.hwp_text import _drop_noise_tokens
    text = "hello 氠瑢 world"
    cleaned = _drop_noise_tokens(text)
    assert "hello" in cleaned
    assert "world" in cleaned
    assert "氠瑢" not in cleaned


# ---------------------------------------------------------------------------
# Integration — _records_to_text 가 순수 노이즈 레코드를 스킵
# ---------------------------------------------------------------------------

def _pack_para_text(text: str) -> bytes:
    """PARA_TEXT (0x43) 레코드 바이너리 생성."""
    payload = text.encode("utf-16-le")
    size = len(payload)
    assert size < 0xFFF, "테스트용: 작은 레코드만"
    hdr = (size << 20) | 0x43
    return struct.pack("<I", hdr) + payload


def test_records_to_text_skips_pure_noise_record():
    from src.checklist.hwp_text import _records_to_text

    # 순수 노이즈 레코드만 + 정상 레코드 — 정상만 남아야
    stream = (
        _pack_para_text("氠瑢\x00\x00\x00\x00")     # drop
        + _pack_para_text("입 찰 공 고")            # keep
        + _pack_para_text("潴景\x00\x00\x00\x00")    # drop
        + _pack_para_text("2026년 아카데미")        # keep
    )
    result = _records_to_text(stream)
    assert "입 찰 공 고" in result
    assert "2026년 아카데미" in result
    assert "氠瑢" not in result
    assert "潴景" not in result


def test_records_to_text_keeps_mixed_chunks():
    """mixed chunk (앞 노이즈 + 진짜 본문) 는 유지."""
    from src.checklist.hwp_text import _records_to_text
    stream = _pack_para_text("捤獥\x00\x00\x00\x00우편번호 : 30148")
    result = _records_to_text(stream)
    # 레코드 자체는 통과 (mixed 는 _looks_like_text 통과)
    # 최종 노이즈 제거는 _sanitize_hwp_control 파이프라인에서
    assert "우편번호" in result


# ---------------------------------------------------------------------------
# _sanitize_hwp_control 전체 파이프라인 — NUL 치환 + 노이즈 토큰 드롭
# ---------------------------------------------------------------------------

def test_sanitize_pipeline_cleans_mixed_chunk():
    """mixed chunk 가 최종적으론 본문만 남아야."""
    from src.checklist.hwp_text import _sanitize_hwp_control
    raw = "捤獥\x00\x00\x00\x00汤捯\x00\x00\x00\x00우편번호 : 30148"
    cleaned = _sanitize_hwp_control(raw)
    assert "우편번호 : 30148" in cleaned
    assert "捤獥" not in cleaned
    assert "汤捯" not in cleaned


def test_sanitize_strips_surrogate_halves():
    """고립된 UTF-16 surrogate (유효한 쌍이 아닌 것) 는 공백."""
    from src.checklist.hwp_text import _sanitize_hwp_control
    # U+D800 단독 (surrogate high)
    raw = "앞\uD800뒤"
    cleaned = _sanitize_hwp_control(raw)
    assert "앞" in cleaned
    assert "뒤" in cleaned
    assert "\uD800" not in cleaned


def test_sanitize_preserves_newlines():
    from src.checklist.hwp_text import _sanitize_hwp_control
    raw = "1줄\n2줄\r\n3줄"
    cleaned = _sanitize_hwp_control(raw)
    assert cleaned.count("\n") >= 2


# ---------------------------------------------------------------------------
# Real HWP sample — 노이즈 지표가 유의미하게 낮아졌는지
# ---------------------------------------------------------------------------

@REQUIRES_HWP
def test_real_sample_noise_reduced():
    """U+6000~U+7FFF 대역 CJK 문자 수가 극히 적어야 (<10)."""
    from src.checklist.hwp_text import extract_hwp_text

    r = extract_hwp_text(HWP_SAMPLE, prefer_full=True)
    assert r.text
    assert r.source == "body_text"

    noise_count = sum(1 for c in r.text if 0x6000 <= ord(c) <= 0x7FFF)
    # v0.10.0 기준 약 25 개 → v0.10.1 에선 10 개 미만 (실측 5 개)
    assert noise_count < 10, f"노이즈 문자 {noise_count} 개 — 필터 약함"


@REQUIRES_HWP
def test_real_sample_key_korean_content_preserved():
    """필터가 과도해서 실제 본문까지 깎지 않았는지 확인."""
    from src.checklist.hwp_text import extract_hwp_text

    r = extract_hwp_text(HWP_SAMPLE, prefer_full=True)
    # 입찰공고 핵심 키워드들이 보존돼야
    for kw in ["입찰", "공고", "귀농귀촌", "아카데미", "농림수산식품교육문화정보원"]:
        assert kw in r.text, f"'{kw}' 누락됨 — 필터 과도"


# ---------------------------------------------------------------------------
# Release script: UTF-8 reconfigure 가 실행됐는지 (import side effect 검증)
# ---------------------------------------------------------------------------

def test_make_release_import_sets_utf8_stdout(monkeypatch):
    """scripts/make_release 를 fresh import 하면 stdout reconfigure 시도.

    실제 stdout 을 건드리면 다른 테스트에 영향 → io.StringIO 대체 stub 으로 검증.
    """
    import io
    import sys as _sys
    import importlib

    # 기존 모듈 캐시 제거 후 재 import 해서 top-level 코드 재실행
    for mod in list(_sys.modules):
        if mod.startswith("scripts.make_release") or mod == "scripts":
            _sys.modules.pop(mod, None)

    called = []

    class ReconfStdout:
        def reconfigure(self, **kwargs):
            called.append(kwargs)

        def write(self, *a, **kw):
            pass

        def flush(self):
            pass

    monkeypatch.setattr(_sys, "stdout", ReconfStdout())
    monkeypatch.setattr(_sys, "stderr", ReconfStdout())

    # 부모 경로에 scripts 추가
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))

    try:
        importlib.import_module("scripts.make_release")
    except Exception:
        # argparse / 기타는 호출 안 했으니 top-level 만 실행됨
        pass

    # reconfigure 호출됐어야
    assert called, "sys.stdout.reconfigure 가 import 시 호출되지 않음"
    assert any(c.get("encoding") == "utf-8" for c in called)
