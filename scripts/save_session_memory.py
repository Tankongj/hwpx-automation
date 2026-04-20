"""Save a condensed session memory summary.

Invoked by:
- `.claude/hooks/pre_compact.py` (before `/compact`)
- Manually via `python scripts/save_session_memory.py --transcript <path>`
- Manually via `python scripts/save_session_memory.py --seed --topic "..." --body "..."`

Design rationale (verified 2026-04-20 research):
- Store in `.claude/memory/` not `docs/` — matches claude-mem / Anthropic auto-memory convention
- Store extracted SIGNALS, not raw transcripts — Mem0 shows ~90% token savings
- Anchor to git HEAD SHA — DEV.to gonewx's finding: memory needs code-state anchor
- Progressive disclosure: short index auto-loaded, details on-demand (claude-mem pattern)
- Basic API-key redaction — MINJA-aware, not trusting blindly

Never-fail contract:
- Called from PreCompact hook → must exit 0 even on error
- Errors should degrade gracefully to a minimal placeholder entry
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

try:  # Windows cp949 guard
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ------------------------------- Constants --------------------------------

MAX_INDEX_ENTRIES = 10
MAX_SESSION_KB = 8
MAX_INTENT_CHARS = 600
MAX_CLOSING_CHARS = 1500
MAX_FILES_LISTED = 40
MAX_TOOLS_LISTED = 10
MAX_SHAS_LISTED = 10

# `/compact` appends to the same JSONL rather than rotating the file. Records
# after the last `isCompactSummary: True` marker are the "current" session; the
# fallback window guards against very long append-only files with no marker.
COMPACT_BOUNDARY_FIELD = "isCompactSummary"
FALLBACK_WINDOW_SIZE = 200

# Local command framing emitted by Claude Code — not real user prompts.
_COMMAND_FRAMING_PREFIXES = (
    "<command-name>",
    "<command-message>",
    "<command-stdout>",
    "<local-command-",
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEMORY_DIR = PROJECT_ROOT / ".claude" / "memory"
SESSIONS_DIR = MEMORY_DIR / "sessions"
INDEX_PATH = MEMORY_DIR / "index.md"

MARKER_START = "<!-- SESSION_MEMORY_START -->"
MARKER_END = "<!-- SESSION_MEMORY_END -->"

# Conservative secret patterns (not exhaustive; SENTRY_DSN etc. covered by bearer)
SECRET_PATTERNS = [
    re.compile(r"sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}"),  # OpenAI / Anthropic
    re.compile(r"AIza[A-Za-z0-9_\-]{30,}"),                # Google
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),                   # GitHub PAT
    re.compile(r"gho_[A-Za-z0-9]{30,}"),                   # GitHub OAuth
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),          # Slack
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._\-]{20,}"),      # Bearer token
]


# ------------------------------- Helpers ----------------------------------

def redact(text: str) -> str:
    """Replace detected secrets with <REDACTED>."""
    if not text:
        return text
    for pat in SECRET_PATTERNS:
        text = pat.sub("<REDACTED>", text)
    return text


def git_short_sha(cwd: Path = PROJECT_ROOT) -> str:
    """Return short HEAD SHA, or '(not a git repo)' if unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "(not a git repo)"


def git_is_dirty(cwd: Path = PROJECT_ROOT) -> bool:
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.returncode == 0 and bool(out.stdout.strip())
    except Exception:
        return False


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    """Yield parsed JSONL records, skipping unparseable lines. Never raises."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return


def _get_content(msg_obj: Any) -> Any:
    """Extract 'content' from a record's 'message' field, handling nesting."""
    if isinstance(msg_obj, dict):
        return msg_obj.get("content")
    return None


def _record_content(rec: dict[str, Any]) -> Any:
    """Return the content payload for a record, handling both nesting shapes."""
    if rec.get("message"):
        return _get_content(rec.get("message"))
    return rec.get("content")


def _find_session_start_index(records: list[dict[str, Any]]) -> int:
    """Return the index of the first record in the *current* session.

    `/compact` appends an `isCompactSummary: True` marker to the existing
    session JSONL rather than rotating the file, so without this filter the
    "first user message" ends up being days old on heavily-compacted sessions.

    Strategy:
      1. Walk records in reverse, return `last_compact_index + 1` if found.
      2. Otherwise fall back to a trailing window of the last N records
         so we don't anchor to a very old message in long append-only files.
    """
    for i in range(len(records) - 1, -1, -1):
        if records[i].get(COMPACT_BOUNDARY_FIELD):
            return i + 1
    if len(records) > FALLBACK_WINDOW_SIZE:
        return len(records) - FALLBACK_WINDOW_SIZE
    return 0


def _extract_text_from_content(content: Any) -> str:
    """Return the first text block from a content payload, or '' if none."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "") or ""
    return ""


def _is_real_user_input(rec: dict[str, Any]) -> bool:
    """True only if the record is an actual human prompt.

    Filters out Claude Code-injected meta records (post-compact caveats,
    `<command-name>/...`, stdout echoes, compact summaries themselves).
    """
    if rec.get("isMeta") or rec.get(COMPACT_BOUNDARY_FIELD):
        return False
    text = _extract_text_from_content(_record_content(rec)).lstrip()
    if not text.strip():
        return False
    if text.startswith(_COMMAND_FRAMING_PREFIXES):
        return False
    return True


def extract_signals(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Extract condensed signals from a session JSONL stream.

    Scans only the current post-`/compact` segment (see
    :func:`_find_session_start_index`) so topic extraction stays anchored to
    the active session rather than the oldest user message in the file.

    Returns dict with keys:
      first_user_message, last_assistant_text, tool_counts (dict),
      files_touched (list[str]), git_shas_mentioned (list[str]).
    """
    records_list = list(records)
    start = _find_session_start_index(records_list)

    first_user = ""
    last_assistant = ""
    tool_counts: dict[str, int] = {}
    files_touched: set[str] = set()
    git_shas: set[str] = set()

    for rec in records_list[start:]:
        rec_type = rec.get("type") or rec.get("role")
        content = _record_content(rec)

        if rec_type == "user" and not first_user and _is_real_user_input(rec):
            txt = _extract_text_from_content(content)
            if txt:
                first_user = txt[:MAX_INTENT_CHARS]

        if rec_type == "assistant":
            if isinstance(content, str) and content.strip():
                last_assistant = content[:MAX_CLOSING_CHARS]
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        txt = block.get("text", "")
                        if txt.strip():
                            last_assistant = txt[:MAX_CLOSING_CHARS]
                    elif btype == "tool_use":
                        tname = block.get("name", "unknown") or "unknown"
                        tool_counts[tname] = tool_counts.get(tname, 0) + 1
                        inp = block.get("input") or {}
                        if isinstance(inp, dict):
                            for key in ("file_path", "path", "notebook_path"):
                                v = inp.get(key)
                                if isinstance(v, str) and v:
                                    files_touched.add(v)
                            cmd = inp.get("command", "") or ""
                            if isinstance(cmd, str) and "git" in cmd:
                                for m in re.finditer(r"\b[0-9a-f]{7,40}\b", cmd):
                                    git_shas.add(m.group()[:10])

    return {
        "first_user_message": redact(first_user),
        "last_assistant_text": redact(last_assistant),
        "tool_counts": tool_counts,
        "files_touched": sorted(files_touched)[:MAX_FILES_LISTED],
        "git_shas_mentioned": sorted(git_shas)[:MAX_SHAS_LISTED],
    }


def _slugify(text: str) -> str:
    """ASCII/한글 slug for filenames; drops punctuation & collapses whitespace."""
    if not text:
        return ""
    s = re.sub(r"[^A-Za-z0-9\uac00-\ud7af_\- ]", "", text).strip()
    s = re.sub(r"\s+", "_", s)
    return s[:40]


def _guess_topic(first_user: str) -> str:
    """Guess a short human-readable topic from the first user message."""
    if not first_user or not first_user.strip():
        return "Session memory"
    first_line = first_user.strip().splitlines()[0]
    first_line = re.sub(r"^[#*\->\s`]+", "", first_line).strip()
    return first_line[:80] or "Session memory"


# ---------------------------- Markdown builders ---------------------------

def build_summary_md(
    *,
    session_id: str,
    transcript_path: str,
    signals: dict[str, Any],
    trigger: str = "manual",
) -> str:
    now = datetime.now()
    sha = git_short_sha()
    status = "dirty" if git_is_dirty() else "clean"
    topic = _guess_topic(signals["first_user_message"])

    files_md = (
        "\n".join(f"- `{p}`" for p in signals["files_touched"]) or "_(none)_"
    )
    tools_md = (
        ", ".join(
            f"{k} × {v}"
            for k, v in sorted(
                signals["tool_counts"].items(), key=lambda x: -x[1]
            )[:MAX_TOOLS_LISTED]
        )
        or "_(none)_"
    )
    shas_md = (
        ", ".join(f"`{s}`" for s in signals["git_shas_mentioned"]) or "_(none)_"
    )

    body = f"""---
session_id: {session_id}
date: {now.strftime("%Y-%m-%d %H:%M:%S")}
trigger: {trigger}
git_sha: {sha}
git_status: {status}
transcript: {transcript_path}
---

# {topic}

## 💬 Intent (first user message)

> {signals["first_user_message"].strip() or "_(empty)_"}

## 🛠 Tool activity

{tools_md}

## 📂 Files touched

{files_md}

## 🔗 Git references mentioned

{shas_md}

## 📍 Closing state (last assistant message, truncated)

{signals["last_assistant_text"].strip() or "_(empty)_"}
"""

    max_bytes = MAX_SESSION_KB * 1024
    enc = body.encode("utf-8")
    if len(enc) > max_bytes:
        body = (
            enc[:max_bytes].decode("utf-8", errors="ignore")
            + "\n\n_(truncated — exceeded size cap)_\n"
        )
    return body


# ------------------------------ Index update ------------------------------

def _empty_index_content() -> str:
    return (
        "# Session Memory Index\n\n"
        "This file is auto-updated by `.claude/hooks/pre_compact.py`.\n"
        "Latest session summaries appear first. Full details in `sessions/`.\n\n"
        f"{MARKER_START}\n{MARKER_END}\n\n"
        "## Retention\n\n"
        f"- Index keeps latest {MAX_INDEX_ENTRIES} entries (auto-rolled)\n"
        "- Older summaries remain on disk under `sessions/`\n"
        "- Edit freely — only the region between the markers above is touched by the hook\n"
    )


def update_index(new_entry_path: Path, topic: str) -> None:
    """Prepend entry to index.md between the markers, cap to MAX_INDEX_ENTRIES."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    try:
        rel = new_entry_path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        rel = str(new_entry_path)
    date = datetime.now().strftime("%Y-%m-%d")
    new_line = f"- {date} · {topic} · [{rel}]({rel})"

    if INDEX_PATH.exists():
        content = INDEX_PATH.read_text(encoding="utf-8", errors="replace")
    else:
        content = _empty_index_content()

    if MARKER_START not in content or MARKER_END not in content:
        content = _empty_index_content()

    before, rest = content.split(MARKER_START, 1)
    middle, after = rest.split(MARKER_END, 1)
    existing_lines = [ln for ln in middle.strip().splitlines() if ln.startswith("- ")]

    seen: set[str] = set()
    capped: list[str] = []
    for ln in [new_line] + existing_lines:
        if ln in seen:
            continue
        seen.add(ln)
        capped.append(ln)
        if len(capped) >= MAX_INDEX_ENTRIES:
            break

    new_middle = "\n" + "\n".join(capped) + "\n"
    INDEX_PATH.write_text(
        before + MARKER_START + new_middle + MARKER_END + after,
        encoding="utf-8",
    )


# ------------------------------- Entry points -----------------------------

def save_memory(
    *,
    transcript_path: str,
    session_id: str = "",
    trigger: str = "manual",
) -> Path:
    """Parse transcript JSONL and write a summary to sessions/. Returns path."""
    records = list(iter_jsonl(Path(transcript_path))) if transcript_path else []
    signals = extract_signals(records)
    body = build_summary_md(
        session_id=session_id or Path(transcript_path).stem,
        transcript_path=transcript_path,
        signals=signals,
        trigger=trigger,
    )

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    topic = _guess_topic(signals["first_user_message"])
    slug = _slugify(topic)
    fn = (
        f"{now.strftime('%Y-%m-%d_%H%M%S')}_{slug}.md"
        if slug
        else f"{now.strftime('%Y-%m-%d_%H%M%S')}.md"
    )
    out_path = SESSIONS_DIR / fn
    out_path.write_text(body, encoding="utf-8")
    update_index(out_path, topic)
    return out_path


def save_memory_from_hook(payload: dict[str, Any]) -> Optional[Path]:
    """Entry point used by .claude/hooks/pre_compact.py."""
    transcript_path = payload.get("transcript_path", "")
    if not transcript_path:
        return None
    return save_memory(
        transcript_path=transcript_path,
        session_id=payload.get("session_id", ""),
        trigger=f"precompact:{payload.get('matcher') or 'auto'}",
    )


def write_seed(topic: str, body: str) -> Path:
    """Write a manual seed memory entry (not derived from a transcript)."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    topic = topic or "Manual seed"
    slug = _slugify(topic)
    fn = f"{now.strftime('%Y-%m-%d_%H%M%S')}_{slug or 'seed'}.md"
    out = SESSIONS_DIR / fn
    sha = git_short_sha()
    content = f"""---
session_id: seed-{now.strftime('%Y%m%d%H%M%S')}
date: {now.strftime('%Y-%m-%d %H:%M:%S')}
trigger: manual-seed
git_sha: {sha}
---

# {topic}

{body or "_(no body provided)_"}
"""
    out.write_text(content, encoding="utf-8")
    update_index(out, topic)
    return out


# --------------------------------- CLI ------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="Save condensed session memory summary."
    )
    p.add_argument("--transcript", help="Path to session JSONL")
    p.add_argument("--session-id", default="")
    p.add_argument("--trigger", default="manual")
    p.add_argument(
        "--seed", action="store_true",
        help="Write a manual seed entry instead of parsing a transcript",
    )
    p.add_argument("--topic", default="", help="With --seed: short topic line")
    p.add_argument(
        "--body", default="",
        help="With --seed: markdown body (or path via @filename)",
    )
    args = p.parse_args()

    if args.seed:
        body = args.body
        if body.startswith("@"):
            body = Path(body[1:]).read_text(encoding="utf-8")
        out = write_seed(args.topic, body)
        print(f"Saved seed -> {out}")
        return 0

    if not args.transcript:
        p.error("--transcript is required unless --seed")
        return 2

    out = save_memory(
        transcript_path=args.transcript,
        session_id=args.session_id,
        trigger=args.trigger,
    )
    print(f"Saved -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
