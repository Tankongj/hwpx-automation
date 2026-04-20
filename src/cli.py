"""콘솔 CLI — W1 단계 E2E 스모크 테스트 용.

서브커맨드
----------
- ``build``    : **풀 파이프라인** (원고 .txt → regex_parser → [Gemini] → HWPX) — W2 기준 권장 경로
- ``convert``  : 마크다운 → HWPX 변환 (v1 호환 경로)
- ``fix``      : HWPX 네임스페이스 후처리
- ``verify``   : HWPX 검증 리포트 출력
- ``resolve``  : 애매 블록만 Gemini 로 해석 (dry-run 가능)

예)

    python -m src.cli build --template tpl.hwpx --txt input.txt --output out.hwpx
    python -m src.cli build --template tpl.hwpx --txt input.txt --output out.hwpx --use-gemini
    python -m src.cli convert --template tpl.hwpx --md scratch.md --output out.hwpx
    python -m src.cli fix out.hwpx
    python -m src.cli verify out.hwpx
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .hwpx import fix_namespaces as _fx
from .hwpx import md_to_hwpx as _md
from .hwpx import verify_hwpx as _vh
from .parser import gemini_resolver, regex_parser
from .parser.ir_schema import blocks_to_v1_paragraphs
from .template.template_analyzer import analyze as analyze_template
from .utils.logger import get_logger

_log = get_logger("cli")


def _cmd_build_batch(args: argparse.Namespace) -> int:
    """폴더 안 모든 .txt 원고를 순차 변환 (v0.9.0).

    v0.10.0: ``--pro-key`` 또는 환경변수 ``HWPX_PRO_KEY=1`` 로 pro 티어 우회 가능
    (CLI 세션에서는 GUI 로그인이 없으므로). 무료 티어면 1 개만 처리하고 프로 안내.
    """
    import os
    from datetime import datetime

    # v0.10.0: CLI 로 실행할 땐 기본적으로 "free" 티어. pro 확인되면 일괄, 아니면 첫 파일만.
    from .commerce import tier_gate
    is_pro = (
        tier_gate.is_allowed("pro")
        or getattr(args, "pro_key", False)
        or os.environ.get("HWPX_PRO_KEY") == "1"
    )

    template = Path(args.template)
    if not template.exists():
        print(f"[ERROR] 템플릿이 없습니다: {template}", file=sys.stderr)
        return 2
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"[ERROR] 원고 폴더가 없습니다: {folder}", file=sys.stderr)
        return 2

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    txts = sorted(folder.glob("**/*.txt" if args.recursive else "*.txt"))
    if not txts:
        print(f"[INFO] {folder} 에 .txt 파일 없음")
        return 0

    # v0.10.0: 무료 티어는 1 개만 처리 (일괄은 pro 전용)
    if not is_pro and len(txts) > 1:
        print(
            f"[INFO] build-batch 일괄 변환({len(txts)} 개)은 pro 티어 전용입니다. "
            f"첫 파일만 처리합니다. 전체를 돌리려면 --pro-key 또는 HWPX_PRO_KEY=1 환경변수를 설정하세요."
        )
        txts = txts[:1]

    print(f"원고 {len(txts)} 개 변환 시작")
    ok, fail = 0, 0
    for i, txt in enumerate(txts, 1):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = out_dir / f"{txt.stem}_{ts}.hwpx"
        if out.exists():
            out = out_dir / f"{txt.stem}_{ts}_{i}.hwpx"
        print(f"[{i}/{len(txts)}] {txt.name} → {out.name}")
        try:
            blocks = regex_parser.parse_file(txt)
            if args.use_gemini or args.backend:
                try:
                    client = gemini_resolver.create_default_client(backend=args.backend)
                    gemini_resolver.resolve(blocks, client=client)
                except Exception as exc:  # noqa: BLE001
                    print(f"  ⚠️ {type(exc).__name__}: 결정론 결과로 진행")
            sm = analyze_template(template)
            _md.convert(
                blocks, template=template, output=out,
                style_map=sm.to_engine_style_dict(),
                run_fix_namespaces=True,
            )
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ 실패: {type(exc).__name__}: {exc}")
            fail += 1

    print(f"\n완료: 성공 {ok} / 실패 {fail}")
    return 0 if fail == 0 else 1


def _cmd_build(args: argparse.Namespace) -> int:
    template = Path(args.template)
    if not template.exists():
        print(f"[ERROR] 템플릿이 없습니다: {template}", file=sys.stderr)
        return 2

    txt = Path(args.txt)
    if not txt.exists():
        print(f"[ERROR] 원고 파일이 없습니다: {txt}", file=sys.stderr)
        return 2

    output = Path(args.output)
    if output.exists():
        print(
            f"[ERROR] 출력 파일이 이미 존재합니다 (덮어쓰기 금지): {output}",
            file=sys.stderr,
        )
        return 2

    # 1) 원고 → IR
    blocks = regex_parser.parse_file(txt)
    _log.info("regex_parser: %d 블록 (애매 %d)", len(blocks), len(regex_parser.ambiguous_blocks(blocks)))

    # 2) (옵션) LLM 해석 — Gemini 또는 Ollama
    if args.use_gemini or args.backend:
        try:
            client = gemini_resolver.create_default_client(backend=args.backend)
            report = gemini_resolver.resolve(blocks, client=client)
            _log.info(report.human_summary())
        except Exception as exc:  # noqa: BLE001
            _log.error("해석 실패: %s — 결정론 결과로 진행합니다", type(exc).__name__)

    # 3) 템플릿 분석 → style_map
    sm = analyze_template(template)
    style_map = sm.to_engine_style_dict()

    # 4) 변환 — v0.15.0: --python-hwpx-writer 또는 config 에 따라 경로 분기
    use_py_hwpx = bool(getattr(args, "python_hwpx_writer", False))
    if not use_py_hwpx:
        try:
            from .settings import app_config as _cfg
            use_py_hwpx = _cfg.load().use_python_hwpx_writer
        except Exception:  # noqa: BLE001
            use_py_hwpx = False

    _md.convert(
        blocks,
        template=template,
        output=output,
        style_map=style_map,
        run_fix_namespaces=not args.no_fix_namespaces,
        use_python_hwpx_writer=use_py_hwpx,
    )

    # 5) (옵션) 바로 검증
    if args.verify:
        report = _vh.verify(output, doc_type=args.type)
        _vh.print_report(report)
        if not report.ok:
            return 1
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    txt = Path(args.txt)
    if not txt.exists():
        print(f"[ERROR] 원고 파일이 없습니다: {txt}", file=sys.stderr)
        return 2
    blocks = regex_parser.parse_file(txt)
    amb_count = len(regex_parser.ambiguous_blocks(blocks))
    if amb_count == 0:
        print("애매한 블록이 없습니다. 호출 생략.")
        return 0
    try:
        client = gemini_resolver.create_default_client(backend=args.backend)
        report = gemini_resolver.resolve(
            blocks, client=client, apply_changes=not args.dry_run
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 해석 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(report.human_summary())
    return 0


def _cmd_convert(args: argparse.Namespace) -> int:
    template = Path(args.template)
    if not template.exists():
        print(f"[ERROR] 템플릿 파일이 없습니다: {template}", file=sys.stderr)
        return 2

    md_arg = args.md
    if md_arg and not Path(md_arg).exists() and not args.inline:
        print(f"[ERROR] 마크다운 파일이 없습니다: {md_arg}", file=sys.stderr)
        return 2

    output = Path(args.output)
    if output.exists():
        print(
            f"[ERROR] 출력 파일이 이미 존재합니다 (덮어쓰기 금지): {output}\n"
            "        --output 을 새 경로로 지정하세요.",
            file=sys.stderr,
        )
        return 2

    _md.convert_markdown(
        template=template,
        md=md_arg,
        output=output,
        config=args.config,
        reference=args.reference,
        cover_range=args.cover_range,
        toc_range=args.toc_range,
        summary_range=args.summary_range,
        cover_keywords=args.cover_keywords or [],
        proposal_title=args.proposal_title,
        summary_mapping_path=args.summary_mapping,
        run_fix_namespaces=not args.no_fix_namespaces,
    )
    return 0


def _cmd_fix(args: argparse.Namespace) -> int:
    if not Path(args.hwpx).exists():
        print(f"[ERROR] HWPX 파일이 없습니다: {args.hwpx}", file=sys.stderr)
        return 2
    result = _fx.fix_hwpx(args.hwpx, fix_tables=args.fix_tables)
    print(
        f"[OK] {args.hwpx}: {result['modified_files']} XML 수정됨"
        + (", ns prefix 제거" if result["ns_fixed"] else "")
        + (", 표 페이지 넘김 보정" if result["tables_fixed"] else "")
    )
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    if not Path(args.hwpx).exists():
        print(f"[ERROR] HWPX 파일이 없습니다: {args.hwpx}", file=sys.stderr)
        return 2
    report = _vh.verify(args.hwpx, doc_type=args.type, company_keywords=args.company_keywords or [])
    _vh.print_report(report)
    return 0 if report.ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hwpx-cli",
        description="HWPX Automation v2 console CLI",
    )
    subp = p.add_subparsers(dest="command", required=True)

    # build (full pipeline)
    b = subp.add_parser("build", help="원고 → (regex) → [LLM] → HWPX 전 과정")
    b.add_argument("--template", required=True, help="HWPX 템플릿 경로")
    b.add_argument("--txt", required=True, help="원고 .txt 경로")
    b.add_argument("--output", required=True, help="결과 HWPX 경로")
    b.add_argument("--use-gemini", action="store_true", help="애매 블록 LLM 해석 활성 (config 에 따라 Gemini 또는 Ollama)")
    b.add_argument(
        "--backend",
        choices=["gemini", "ollama", "openai", "anthropic", "none"],
        default=None,
        help="LLM 백엔드 강제 지정 (기본은 config.resolver_backend)",
    )
    b.add_argument("--verify", action="store_true", help="변환 후 verify 리포트 출력")
    b.add_argument(
        "--type",
        choices=["qualitative", "quantitative", "auto"],
        default="auto",
        help="verify 시 문서 타입",
    )
    b.add_argument(
        "--no-fix-namespaces",
        action="store_true",
        help="변환 후 네임스페이스 후처리 생략",
    )
    b.add_argument(
        "--python-hwpx-writer",
        action="store_true",
        help="v0.15.0: python-hwpx 기반 writer 경로 사용 (단순 변환 시 더 빠름/깔끔함)",
    )
    b.set_defaults(func=_cmd_build)

    # build-batch (v0.9.0: 폴더 일괄 변환)
    bb = subp.add_parser("build-batch", help="폴더 내 모든 .txt 을 순차 HWPX 변환")
    bb.add_argument("--template", required=True, help="HWPX 템플릿 경로")
    bb.add_argument("--folder", required=True, help="원고 .txt 들이 있는 폴더")
    bb.add_argument("--output-dir", required=True, help="HWPX 결과 저장 폴더")
    bb.add_argument("--recursive", action="store_true", help="하위 폴더까지 스캔")
    bb.add_argument("--use-gemini", action="store_true")
    bb.add_argument(
        "--backend",
        choices=["gemini", "ollama", "openai", "anthropic", "none"],
        default=None,
    )
    bb.add_argument(
        "--pro-key",
        action="store_true",
        help="pro 티어 우회 (CLI 세션용). 환경변수 HWPX_PRO_KEY=1 로도 가능.",
    )
    bb.set_defaults(func=_cmd_build_batch)

    # resolve (LLM 해석만)
    r = subp.add_parser("resolve", help="애매 블록만 LLM 해석 (결과 리포트)")
    r.add_argument("txt", help="원고 .txt 경로")
    r.add_argument("--dry-run", action="store_true", help="IR 은 수정하지 않고 비용만 추정")
    r.add_argument(
        "--backend",
        choices=["gemini", "ollama", "openai", "anthropic", "none"],
        default=None,
        help="LLM 백엔드 강제 지정 (기본은 config.resolver_backend)",
    )
    r.set_defaults(func=_cmd_resolve)

    # convert (v1 markdown-compat)
    c = subp.add_parser("convert", help="마크다운 → HWPX (v1 호환)")
    c.add_argument("--template", required=True, help="HWPX 템플릿 파일 경로")
    c.add_argument("--md", required=True, help="마크다운 파일 경로(또는 --inline 문자열)")
    c.add_argument("--output", required=True, help="결과 HWPX 경로")
    c.add_argument("--inline", action="store_true", help="--md 인자를 파일이 아닌 문자열로 취급")
    c.add_argument("--config", default=None, help="YAML 스타일 설정")
    c.add_argument("--reference", default=None, help="커버/TOC 용 reference HWPX")
    c.add_argument("--cover-range", default=None, help='예: "0:20"')
    c.add_argument("--toc-range", default=None, help='예: "20:69"')
    c.add_argument("--summary-range", default=None, help='예: "69:163"')
    c.add_argument("--cover-keywords", nargs="*", help="템플릿 커버 식별 키워드")
    c.add_argument("--proposal-title", default=None, help="표지 제목")
    c.add_argument("--summary-mapping", default=None, help="조견표 매핑 JSON")
    c.add_argument(
        "--no-fix-namespaces",
        action="store_true",
        help="변환 후 자동 네임스페이스 후처리 비활성화",
    )
    c.set_defaults(func=_cmd_convert)

    # fix
    f = subp.add_parser("fix", help="HWPX 네임스페이스/표 페이지 넘김 후처리")
    f.add_argument("hwpx", help="HWPX 파일 (in-place 수정)")
    f.add_argument("--fix-tables", action="store_true", help="표 페이지 넘김까지 보정")
    f.set_defaults(func=_cmd_fix)

    # verify
    v = subp.add_parser("verify", help="HWPX 검증")
    v.add_argument("hwpx", help="HWPX 파일")
    v.add_argument(
        "--type",
        choices=["qualitative", "quantitative", "auto"],
        default="auto",
        help="문서 타입 (기본 auto)",
    )
    v.add_argument("--company-keywords", nargs="*", help="정량 모드 전용 회사 키워드")
    v.set_defaults(func=_cmd_verify)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
