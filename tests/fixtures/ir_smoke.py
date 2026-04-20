"""IR → HWPX 변환이 가능한지 확인하는 최소 스크립트.

W1 DoD ("콘솔에서 IR→HWPX 변환 성공") 검증용. 실제 사용자는 CLI 를 쓰지만,
함수형 API 가 IR Block 리스트를 바로 받아 처리함을 보여 준다.
"""
from __future__ import annotations

import sys
from pathlib import Path

# 프로젝트 루트를 sys.path 에 추가
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.hwpx.md_to_hwpx import convert
from src.parser.ir_schema import Block


def build_ir() -> list[Block]:
    """4만자 샘플 대신, 각 레벨을 한 번씩 사용하는 짧은 스모크용 IR."""
    return [
        Block(level=1, text="Ⅰ. 사업 개요", symbol="Ⅰ."),
        Block(level=2, text="1 추진 배경", symbol="1"),
        Block(level=3, text="추진 필요성", symbol="1)"),
        Block(level=4, text="사업 목적 요약", symbol="(1)"),
        Block(level=5, text="핵심 가치 제안", symbol="①"),
        Block(level=6, text="□ 귀농귀촌 아카데미 운영 역량 확보", symbol="□"),
        Block(level=7, text="❍ 지역 연계 협력체계 구축", symbol="❍"),
        Block(level=8, text="전문 강사 14명 풀 확보", symbol="-"),
        Block(level=9, text="실무경험 5년 이상 위주", symbol="·"),
        Block(level=10, text="* 세부 스펙은 별첨", symbol="*"),
        Block(level=0, text="본 사업은 귀농귀촌 인구 확산을 위한 종합 교육체계를 목표로 함."),
    ]


def main() -> int:
    template = Path(
        r"D:/03_antigravity/19_[26 귀농귀촌 아카데미]/hwpx-proposal-automation/templates/정성제안서 서식.hwpx"
    )
    output = ROOT / "tests" / "tmp" / "ir_smoke_out.hwpx"

    if output.exists():
        output.unlink()

    blocks = build_ir()
    print(f"IR blocks: {len(blocks)} (levels {[b.level for b in blocks]})")

    convert(blocks, template=template, output=output)

    assert output.exists(), "변환 출력이 생성되지 않음"
    print(f"\n✅ IR→HWPX 변환 OK: {output} ({output.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
