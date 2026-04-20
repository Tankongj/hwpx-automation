"""Resolver 백엔드 품질/비용/속도 벤치마크.

같은 원고에 대해 Gemini / Ollama 를 각각 돌려 다음을 비교:
- 재분류 / 확인 / 응답누락 / 파싱실패 비율
- 호출 시간 (wall clock)
- 비용 (로컬은 0)
- 출력 토큰 수

사용 (실제 API/서버가 있어야 의미 있음)::

    # 둘 다
    python scripts/bench_backends.py tests/fixtures/2026_귀농귀촌아카데미_원고.txt

    # 한쪽만
    python scripts/bench_backends.py <txt> --only gemini
    python scripts/bench_backends.py <txt> --only ollama

결과는 stdout + ``bench_results/<timestamp>.json`` 으로 저장.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.parser import regex_parser
from src.parser.gemini_resolver import GoogleGenAIClient, resolve


def _run_backend(name: str, client, blocks_factory) -> dict[str, Any]:
    """한 백엔드를 한 번 돌린다. blocks 는 factory 로 매번 새로 생성 (상태 오염 방지)."""
    print(f"\n── {name} ──", flush=True)
    blocks = blocks_factory()
    amb_before = len(regex_parser.ambiguous_blocks(blocks))
    print(f"  애매 블록: {amb_before}", flush=True)

    t0 = time.perf_counter()
    try:
        report = resolve(blocks, client=client, apply_changes=False)
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        print(f"  ❌ 실패: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        return {
            "backend": name,
            "model": getattr(client, "model", "?"),
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_sec": elapsed,
        }
    elapsed = time.perf_counter() - t0

    summary = {
        "backend": name,
        "model": getattr(client, "model", "?"),
        "ok": True,
        "elapsed_sec": round(elapsed, 2),
        "total_ambiguous": report.total_ambiguous,
        "changed": report.changed,
        "confirmed": report.confirmed,
        "no_decision": report.no_decision,
        "failed_parse": report.failed_parse,
        "calls": report.call_count,
        "input_tokens": report.cost.input_tokens,
        "output_tokens": report.cost.output_tokens,
        "thinking_tokens": report.cost.thinking_tokens,
        "usd": round(report.cost.usd, 6),
        "krw": round(report.cost.krw, 2),
        "is_local": report.cost.is_local,
    }
    print(f"  ⏱️  {elapsed:.2f}s", flush=True)
    print(f"  📊 {report.human_summary()}", flush=True)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("txt", help="원고 .txt")
    ap.add_argument("--only", choices=["gemini", "ollama"], help="한쪽만 실행")
    ap.add_argument("--ollama-host", default="http://localhost:11434")
    ap.add_argument("--ollama-model", default="qwen2.5:7b")
    ap.add_argument(
        "--ambiguous-threshold",
        type=int,
        default=50,
        help="regex_parser ambiguous_long_threshold (기본 50)",
    )
    args = ap.parse_args()

    txt_path = Path(args.txt)
    if not txt_path.exists():
        print(f"❌ 원고 없음: {txt_path}", file=sys.stderr)
        return 2

    def blocks_factory():
        return regex_parser.parse_file(
            txt_path, ambiguous_long_threshold=args.ambiguous_threshold
        )

    print(f"원고: {txt_path.name}")
    print(f"총 라인: {len(txt_path.read_text(encoding='utf-8').splitlines())}")
    print(f"임계값: {args.ambiguous_threshold}")

    results: list[dict[str, Any]] = []

    if args.only != "ollama":
        try:
            gclient = GoogleGenAIClient()
            results.append(_run_backend("Gemini", gclient, blocks_factory))
        except Exception as exc:  # noqa: BLE001
            print(f"Gemini 초기화 실패: {exc}", file=sys.stderr)
            results.append({"backend": "Gemini", "ok": False, "error": str(exc)})

    if args.only != "gemini":
        from src.parser.ollama_backend import OllamaClient, probe_server

        probe = probe_server(args.ollama_host)
        print(f"\nOllama probe: {probe.summary()}")
        if probe.ok:
            oclient = OllamaClient(host=args.ollama_host, model=args.ollama_model)
            results.append(_run_backend(f"Ollama({args.ollama_model})", oclient, blocks_factory))
        else:
            results.append({"backend": "Ollama", "ok": False, "error": probe.error})

    # 출력
    out_dir = ROOT / "bench_results"
    out_dir.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"bench_{ts}.json"
    out_path.write_text(
        json.dumps(
            {
                "fixture": str(txt_path),
                "threshold": args.ambiguous_threshold,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # 요약 표
    print("\n" + "=" * 72)
    print(f"{'백엔드':<30}{'시간':>8}{'재분류':>8}{'확인':>6}{'누락':>6}{'₩':>10}")
    print("-" * 72)
    for r in results:
        if not r.get("ok"):
            print(f"{r['backend']:<30}   실패 ({r.get('error', '?')[:40]})")
            continue
        print(
            f"{r['backend']:<30}"
            f"{r['elapsed_sec']:>7.1f}s"
            f"{r['changed']:>8}"
            f"{r['confirmed']:>6}"
            f"{r['no_decision']:>6}"
            f"{r['krw']:>10.1f}"
        )
    print("=" * 72)
    print(f"\n상세: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
