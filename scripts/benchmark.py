"""v0.9.0 성능 벤치마크 스크립트.

대용량 원고 (기본 10 만자) 를 생성해 regex_parser → md_to_hwpx → verify 의
시간 / 메모리 / 산출 크기를 측정. Gemini 호출은 선택 (기본 off).

사용::

    python scripts/benchmark.py
    python scripts/benchmark.py --chars 200000
    python scripts/benchmark.py --reps 3
    python scripts/benchmark.py --include-quant        # 정량 경로도 측정

결과는 stdout 표 + ``bench_results/perf_<timestamp>.json`` 으로 저장.
"""
from __future__ import annotations

import argparse
import gc
import json
import random
import sys
import tempfile
import time
import tracemalloc
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLE_QUANT = ROOT / "tests" / "fixtures" / "quant_samples" / "[정량제안서] 2026년 아카데미.hwpx"
BUNDLED_TEMPLATE = ROOT / "templates" / "00_기본_10단계스타일.hwpx"


# ---------------------------------------------------------------------------
# 원고 생성기
# ---------------------------------------------------------------------------

_TOPICS = [
    "귀농귀촌", "스마트팜", "청년농업인", "후계농", "농촌관광",
    "농산물 유통", "친환경농업", "농업교육", "원격교육", "마을공동체",
]
_BODY = (
    "본 사업은 귀농귀촌 희망자의 수요에 대응하여 온·오프라인을 연계한 맞춤형 교육지원으로 "
    "효율적인 귀농귀촌 교육지원체계를 구축·운영함. 본 사업의 추진목표는 정책 수요자 중심 "
    "교육 프로그램 기획 운영, 전문성 있는 강사 풀 구축, 지역연계 현장실습 강화, 온라인 "
    "콘텐츠 고도화, 수료생 사후관리 프로그램 확대로 구성됨."
)


def synthesize_proposal(target_chars: int, seed: int = 0) -> str:
    """가짜 정성제안서 원고 생성 — 기호 + 본문 섞어서.

    약 ``target_chars`` 자에 도달할 때까지 레벨 1~10 과 본문을 번갈아 생성.
    """
    rng = random.Random(seed)
    lines: list[str] = ["# 가상 제안서 (벤치마크용)"]
    total = 0
    chapter = 0
    while total < target_chars:
        chapter += 1
        lines.append(f"# Ⅰ. {rng.choice(_TOPICS)} (장 {chapter})")
        for sec in range(1, rng.randint(3, 5) + 1):
            lines.append(f"## {sec} {rng.choice(_TOPICS)} 절")
            for sub in range(1, rng.randint(2, 4) + 1):
                lines.append(f"### {sub}) {rng.choice(_TOPICS)} 소절")
                for par in range(1, rng.randint(2, 4) + 1):
                    lines.append(f"({par}) {rng.choice(_TOPICS)} 단락")
                    for item in range(1, rng.randint(3, 6) + 1):
                        circled = chr(0x2460 + (item - 1) % 20)
                        lines.append(f"{circled} {rng.choice(_TOPICS)} 항목")
                        lines.append(f"□ {rng.choice(_TOPICS)} 대주제")
                        lines.append(f"❍ {rng.choice(_TOPICS)} 중주제")
                        for det in range(rng.randint(2, 4)):
                            lines.append(f"- {_BODY[:rng.randint(30, 70)]}")
                            lines.append(f"· {_BODY[:rng.randint(20, 40)]}")
                        lines.append(f"※ {_BODY[:60]}")
                        lines.append(_BODY * rng.randint(1, 2))
        total = sum(len(l) for l in lines)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

@dataclass
class RunMetrics:
    name: str
    elapsed_sec: float
    peak_mb: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


def _run_timed(name: str, fn, *args, **kwargs) -> RunMetrics:
    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = time.perf_counter() - t0
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    gc.collect()
    return RunMetrics(
        name=name, elapsed_sec=round(elapsed, 3),
        peak_mb=round(peak / 1024 / 1024, 2),
        extra={"result": result} if not isinstance(result, (bytes, bytearray)) else {},
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def bench_qualitative(chars: int, template: Path, tmp_dir: Path) -> list[RunMetrics]:
    from src.hwpx import md_to_hwpx, verify_hwpx
    from src.parser import regex_parser
    from src.parser.ir_schema import blocks_to_v1_paragraphs
    from src.template.template_analyzer import analyze as analyze_template

    metrics: list[RunMetrics] = []

    # 1. 원고 생성
    def _gen():
        return synthesize_proposal(chars)
    m = _run_timed(f"synthesize({chars:,} chars)", _gen)
    text = m.extra.pop("result", "")
    m.extra["actual_chars"] = len(text)
    metrics.append(m)

    # 저장
    txt_path = tmp_dir / f"synth_{chars}.txt"
    txt_path.write_text(text, encoding="utf-8")

    # 2. 파싱
    def _parse():
        return regex_parser.parse_file(txt_path)
    m = _run_timed("regex_parser.parse_file", _parse)
    blocks = m.extra.pop("result", [])
    amb = len(regex_parser.ambiguous_blocks(blocks))
    m.extra.update({"blocks": len(blocks), "ambiguous": amb})
    metrics.append(m)

    # 3. 템플릿 분석
    def _analyze():
        return analyze_template(template)
    m = _run_timed("template_analyzer.analyze", _analyze)
    sm = m.extra.pop("result")
    metrics.append(m)

    # 4. 변환
    out_path = tmp_dir / f"synth_{chars}_out.hwpx"
    if out_path.exists():
        out_path.unlink()

    def _convert():
        md_to_hwpx.convert(
            blocks, template=template, output=out_path,
            style_map=sm.to_engine_style_dict(),
            run_fix_namespaces=True,
        )
        return out_path.stat().st_size
    m = _run_timed("md_to_hwpx.convert + fix_namespaces", _convert)
    out_size = m.extra.pop("result", 0)
    m.extra["output_bytes"] = out_size
    m.extra["output_mb"] = round(out_size / 1024 / 1024, 2)
    metrics.append(m)

    # 5. verify
    def _verify():
        return verify_hwpx.verify(out_path, doc_type="qualitative")
    m = _run_timed("verify_hwpx.verify", _verify)
    report = m.extra.pop("result")
    m.extra.update({
        "rate_pct": round(report.rate, 1),
        "structural_ok": sum(
            1 for c in report.checks
            if c.category in ("common", "advanced") and c.passed
        ),
    })
    metrics.append(m)
    return metrics


def bench_quant(sample: Path, tmp_dir: Path) -> list[RunMetrics]:
    if not sample.exists():
        return []
    from src.quant.converter import save_document
    from src.quant.parser import parse_document

    metrics: list[RunMetrics] = []

    def _parse():
        return parse_document(sample)
    m = _run_timed("quant.parse_document", _parse)
    doc = m.extra.pop("result")
    m.extra["cells"] = len(doc.cells)
    m.extra["forms"] = len(doc.form_labels)
    metrics.append(m)

    # 편집 시뮬레이션 (10 셀 수정)
    for i, cell in enumerate(doc.cells[:10]):
        cell.text = f"수정값_{i}"

    out_path = tmp_dir / "quant_bench_out.hwpx"
    if out_path.exists():
        out_path.unlink()

    def _save():
        return save_document(doc, out_path)
    m = _run_timed("quant.save_document", _save)
    _ = m.extra.pop("result", None)
    m.extra["output_mb"] = round(out_path.stat().st_size / 1024 / 1024, 2)
    metrics.append(m)
    return metrics


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chars", type=int, default=100_000, help="원고 목표 문자 수")
    ap.add_argument("--reps", type=int, default=1, help="반복 횟수 (평균 계산)")
    ap.add_argument("--include-quant", action="store_true", help="정량 경로도 측정")
    ap.add_argument("--template", default=str(BUNDLED_TEMPLATE))
    args = ap.parse_args()

    template = Path(args.template)
    if not template.exists():
        print(f"❌ 템플릿 없음: {template}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="hwpx_bench_") as tmp:
        tmp_dir = Path(tmp)
        print(f"📁 임시: {tmp_dir}")
        print(f"🎯 목표: {args.chars:,} 자 × {args.reps} 반복")
        print(f"📄 템플릿: {template.name}")
        print()

        runs: list[list[RunMetrics]] = []
        for rep in range(args.reps):
            print(f"━━━ 반복 {rep + 1}/{args.reps} — 정성 ━━━")
            metrics = bench_qualitative(args.chars, template, tmp_dir)
            for m in metrics:
                print(f"  {m.name:<40} {m.elapsed_sec:>7.3f}s  peak {m.peak_mb:>7.2f} MB  {m.extra}")
            runs.append(metrics)

            if args.include_quant and SAMPLE_QUANT.exists():
                print(f"━━━ 반복 {rep + 1}/{args.reps} — 정량 ━━━")
                qmetrics = bench_quant(SAMPLE_QUANT, tmp_dir)
                for m in qmetrics:
                    print(f"  {m.name:<40} {m.elapsed_sec:>7.3f}s  peak {m.peak_mb:>7.2f} MB  {m.extra}")
                runs[-1].extend(qmetrics)
            print()

    # 결과 저장
    out_dir = ROOT / "bench_results"
    out_dir.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"perf_{ts}.json"
    payload = {
        "chars": args.chars,
        "reps": args.reps,
        "template": str(template),
        "include_quant": args.include_quant,
        "runs": [[asdict(m) for m in run] for run in runs],
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"💾 결과: {out_path}")

    # 평균 표
    if args.reps > 1:
        print("\n━━━ 평균 ━━━")
        names = [m.name for m in runs[0]]
        for i, name in enumerate(names):
            avg_sec = sum(r[i].elapsed_sec for r in runs) / args.reps
            avg_mem = sum(r[i].peak_mb for r in runs) / args.reps
            print(f"  {name:<40} {avg_sec:>7.3f}s  peak {avg_mem:>7.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
