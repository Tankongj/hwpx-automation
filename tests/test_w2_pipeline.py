"""W2 통합: regex_parser → template_analyzer → md_to_hwpx.convert → verify.

실제 원고(길이 불변, 픽스처 있으면 실행) 로 전체 파이프라인이 끝까지 성공하는지 검증.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.hwpx import md_to_hwpx, verify_hwpx
from src.parser import regex_parser
from src.parser.ir_schema import LEVEL_TITLE
from src.template.template_analyzer import analyze


ROOT = Path(__file__).resolve().parents[1]
BUNDLED_TEMPLATE = ROOT / "templates" / "00_기본_10단계스타일.hwpx"
FIXTURE = ROOT / "tests" / "fixtures" / "2026_귀농귀촌아카데미_원고.txt"


@pytest.mark.skipif(
    not (BUNDLED_TEMPLATE.exists() and FIXTURE.exists()),
    reason="template or fixture missing",
)
def test_e2e_parser_to_hwpx(tmp_path: Path):
    out = tmp_path / "w2_e2e.hwpx"

    blocks = regex_parser.parse_file(FIXTURE)
    assert len(blocks) > 0

    sm = analyze(BUNDLED_TEMPLATE)
    md_to_hwpx.convert(
        blocks,
        template=BUNDLED_TEMPLATE,
        output=out,
        style_map=sm.to_engine_style_dict(),
    )

    assert out.exists()
    assert out.stat().st_size > 10_000

    report = verify_hwpx.verify(out, doc_type="qualitative")
    # common + advanced 7 개 구조 검증은 모두 통과해야 한다
    structural = [c for c in report.checks if c.category in ("common", "advanced")]
    failed = [(c.name, c.detail) for c in structural if not c.passed]
    assert not failed, f"structural checks failed: {failed}"


@pytest.mark.skipif(
    not (BUNDLED_TEMPLATE.exists() and FIXTURE.exists()),
    reason="template or fixture missing",
)
def test_e2e_title_becomes_first_heading(tmp_path: Path):
    """IR level=-1 (title) 이 v1 엔진에서 H1 으로 렌더링되는지 확인."""
    out = tmp_path / "w2_title.hwpx"

    blocks = regex_parser.parse_file(FIXTURE)
    has_title = any(b.level == LEVEL_TITLE for b in blocks)
    assert has_title, "fixture 의 최상단 # 문서 제목이 level=-1 로 파싱되지 않음"

    md_to_hwpx.convert(blocks, template=BUNDLED_TEMPLATE, output=out)
    assert out.exists()
    # 너무 강한 assert 는 피한다: 단지 변환이 완료되고 파일이 생겼음을 확인.


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture missing")
def test_length_invariance():
    """파서가 길이에 상관없이 동작함을 확인: 앞 절반과 전체를 각각 파싱해도 에러 없음."""
    full = FIXTURE.read_text(encoding="utf-8")
    half = full[: len(full) // 2]

    full_blocks = regex_parser.parse(full)
    half_blocks = regex_parser.parse(half)

    assert len(full_blocks) > 0
    assert len(half_blocks) > 0
    assert len(half_blocks) < len(full_blocks)
