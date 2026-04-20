"""SessionStart hook: inject recent session memory index as additionalContext.

Contract: MUST exit 0 even on error.

A fire-evidence line is appended to .claude/logs/hooks.log on every invocation
for diagnosis parity with pre_compact.py.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LOG_PATH = ROOT / ".claude" / "logs" / "hooks.log"


def _log(msg: str) -> None:
    """Append a timestamped line to the hook debug log. Never raises."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except Exception:
        pass


try:
    from scripts.load_session_memory import main as _inner_main  # noqa: E402
except Exception as e:  # pragma: no cover - defensive
    _log(f"session_start import-failed: {e}")
    print(f"[session_start] import failed: {e}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    _log("session_start fired")
    try:
        sys.exit(_inner_main())
    except Exception as e:
        _log(f"session_start error: {e}")
        print(f"[session_start] non-fatal: {e}", file=sys.stderr)
        sys.exit(0)
