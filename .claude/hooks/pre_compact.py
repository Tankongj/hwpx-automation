"""PreCompact hook: save a condensed session memory before /compact runs.

Contract: MUST exit 0 even on error. /compact must never be blocked
by this hook. Failures are logged to stderr only.

A fire-evidence line is appended to .claude/logs/hooks.log on every invocation
so post-hoc diagnosis is possible without relying on stderr (which Claude Code
may not surface).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# Make `scripts/` importable
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
    from scripts.save_session_memory import save_memory_from_hook  # noqa: E402
except Exception as e:  # pragma: no cover - defensive
    _log(f"pre_compact import-failed: {e}")
    print(f"[pre_compact] import failed: {e}", file=sys.stderr)
    sys.exit(0)


def main() -> int:
    _log("pre_compact fired")
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
        trigger = payload.get("trigger") or payload.get("matcher") or "unknown"
        _log(f"pre_compact trigger={trigger} transcript={payload.get('transcript_path', '')[-60:]}")
        out = save_memory_from_hook(payload)
        if out:
            _log(f"pre_compact saved -> {out}")
            print(f"[pre_compact] memory saved -> {out}", file=sys.stderr)
        else:
            _log("pre_compact skipped (no transcript_path)")
    except Exception as e:
        _log(f"pre_compact error: {e}")
        print(f"[pre_compact] non-fatal: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
