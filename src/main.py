"""GUI 앱 엔트리포인트.

사용:
    python -m src.main
    # 또는 pyproject.toml 에 등록된 스크립트: hwpx-automation
"""
from __future__ import annotations

import sys

from .utils.logger import get_logger


def main(argv: list[str] | None = None) -> int:
    # v0.16.0: update helper 분기 — PyInstaller 환경에서 메인 exe 가 helper 역할도 겸함.
    # 자동 업데이트 진행 중 sys.argv 에 "--update-helper" 가 있으면 GUI 대신 helper 로직 실행.
    args_to_check = argv if argv is not None else sys.argv[1:]
    if "--update-helper" in args_to_check:
        from .commerce.update_helper import run_helper
        # run_helper 는 자신의 argparse 로 나머지 인자 처리 (--staging, --target, ...)
        helper_args = [a for a in args_to_check if a != "--update-helper"]
        return run_helper(helper_args)

    log = get_logger("main")
    log.info("HWPX Automation v2 기동 시작")

    try:
        from .app import run
        return run(argv)
    except ImportError as exc:
        if "PySide6" in str(exc) or getattr(exc, "name", "") == "PySide6":
            log.error(
                "PySide6 가 설치되어 있지 않습니다. "
                "`pip install -r requirements.txt` 실행 후 다시 시도하세요. (원인: %s)",
                exc,
            )
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
