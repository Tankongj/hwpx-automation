"""Return recent session memory for SessionStart hook injection.

Invoked by `.claude/hooks/session_start.py`. Reads hook JSON from stdin,
emits `{"hookSpecificOutput": {"hookEventName": "SessionStart",
"additionalContext": "<index.md contents, capped>"}}` to stdout.

Injection policy (verified research, 2026-04-20):
- source=="compact"  → inject (user just lost context; most important case)
- source=="resume"   → inject (user explicitly continuing work)
- source=="startup"  → inject (fresh shell, still useful as breadcrumb)
- source=="clear"    → SKIP (user explicitly wanted clean slate)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:  # Windows cp949 guard
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = PROJECT_ROOT / ".claude" / "memory" / "index.md"

INJECT_ON_SOURCES = {"compact", "resume", "startup"}
MAX_CONTEXT_BYTES = 6144  # ~1.5K tokens; progressive-disclosure principle


def load_from_hook(payload: dict) -> str:
    """Return the string to inject, or '' to skip."""
    source = payload.get("source", "startup")
    if source not in INJECT_ON_SOURCES:
        return ""
    if not INDEX_PATH.exists():
        return ""
    try:
        content = INDEX_PATH.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    enc = content.encode("utf-8")
    if len(enc) > MAX_CONTEXT_BYTES:
        content = (
            enc[:MAX_CONTEXT_BYTES].decode("utf-8", errors="ignore")
            + "\n\n_(truncated — see `.claude/memory/index.md` for full list)_\n"
        )
    return content


def main() -> int:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}

    ctx = load_from_hook(payload)
    if not ctx:
        return 0

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": ctx,
        }
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
