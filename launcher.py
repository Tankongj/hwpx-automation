"""PyInstaller 용 엔트리 래퍼.

PyInstaller 는 spec 에 지정된 스크립트를 top-level ``__main__`` 으로 실행한다. 그래서
``src/main.py`` 를 그대로 entry 로 두면 내부 ``from .app import run`` 같은 relative
import 가 깨진다. 이 launcher 는 루트 레벨이므로 ``src`` 를 정상 패키지로 임포트할 수 있다.

개발용 엔트리(``python -m src.main``) 는 그대로 유지.
"""
from __future__ import annotations

import sys


def _main() -> int:
    from src.main import main

    return main()


if __name__ == "__main__":
    sys.exit(_main())
