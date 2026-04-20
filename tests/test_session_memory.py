"""Tests for hierarchical session memory system.

Covers:
- scripts/save_session_memory.py (PreCompact-side)
- scripts/load_session_memory.py (SessionStart-side)
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

# Make scripts/ importable
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import save_session_memory as sv
from scripts import load_session_memory as ld


# ---------------------------- Redaction -----------------------------------

def test_redact_openai_key():
    out = sv.redact(
        "foo sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890 bar"
    )
    assert "<REDACTED>" in out
    assert "sk-ant" not in out


def test_redact_google_key():
    out = sv.redact("token: AIzaSyBlahBlahBlahBlah1234567890abcdefghij")
    assert "<REDACTED>" in out
    assert "AIzaSy" not in out


def test_redact_github_pat():
    out = sv.redact("ghp_1234567890abcdefghijklmnopqrstuvwxyz1234")
    assert "<REDACTED>" in out


def test_redact_bearer_token():
    out = sv.redact("Authorization: Bearer eyJhbGci0iJIUzI1NiJ9.abcdefghij")
    assert "<REDACTED>" in out


def test_redact_preserves_plain_text():
    plain = "hello world 한글 테스트"
    assert sv.redact(plain) == plain


def test_redact_empty_input():
    assert sv.redact("") == ""


# ---------------------------- Slug / topic --------------------------------

def test_slugify_korean_preserved():
    assert sv._slugify("v0.15.0 릴리스 완료") == "v0150_릴리스_완료"


def test_slugify_strips_punct():
    assert sv._slugify("hello, world!") == "hello_world"


def test_slugify_empty():
    assert sv._slugify("") == ""


def test_slugify_caps_length():
    long = "a" * 100
    assert len(sv._slugify(long)) == 40


def test_guess_topic_strips_markdown():
    assert sv._guess_topic("# Hello\nworld") == "Hello"
    assert sv._guess_topic("- task item") == "task item"
    assert sv._guess_topic(">  quoted") == "quoted"


def test_guess_topic_empty():
    assert sv._guess_topic("") == "Session memory"
    assert sv._guess_topic("   \n\n") == "Session memory"


def test_guess_topic_truncates():
    long = "x" * 200
    assert len(sv._guess_topic(long)) == 80


# ---------------------------- JSONL parsing -------------------------------

def test_iter_jsonl_missing_file(tmp_path):
    assert list(sv.iter_jsonl(tmp_path / "nope.jsonl")) == []


def test_iter_jsonl_skips_bad_lines(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text(
        '{"a":1}\n'
        '<<not json>>\n'
        '\n'
        '{"b":2}\n',
        encoding="utf-8",
    )
    assert list(sv.iter_jsonl(p)) == [{"a": 1}, {"b": 2}]


def test_iter_jsonl_handles_encoding_errors(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_bytes(b'{"a":1}\n\xff\xfe invalid\n{"b":2}\n')
    out = list(sv.iter_jsonl(p))
    assert {"a": 1} in out
    assert {"b": 2} in out


# ---------------------------- Signal extraction ---------------------------

def test_extract_signals_basic():
    records = [
        {"type": "user", "message": {"content": "Build the feature"}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "On it"},
            {"type": "tool_use", "name": "Edit",
             "input": {"file_path": "src/x.py"}},
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "git log abc1234"}},
        ]}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Done!"},
        ]}},
    ]
    s = sv.extract_signals(records)
    assert s["first_user_message"] == "Build the feature"
    assert s["tool_counts"]["Edit"] == 1
    assert s["tool_counts"]["Bash"] == 1
    assert "src/x.py" in s["files_touched"]
    assert s["last_assistant_text"] == "Done!"
    assert "abc1234" in s["git_shas_mentioned"]


def test_extract_signals_empty():
    s = sv.extract_signals([])
    assert s["first_user_message"] == ""
    assert s["tool_counts"] == {}
    assert s["files_touched"] == []
    assert s["git_shas_mentioned"] == []


def test_extract_signals_user_content_list_shape():
    records = [
        {"type": "user", "message": {"content": [
            {"type": "text", "text": "Hello from list shape"}
        ]}},
    ]
    s = sv.extract_signals(records)
    assert s["first_user_message"] == "Hello from list shape"


def test_extract_signals_redacts_secrets():
    records = [
        {"type": "user", "message": {
            "content": "My key is sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ12345"
        }},
    ]
    s = sv.extract_signals(records)
    assert "<REDACTED>" in s["first_user_message"]
    assert "sk-ant" not in s["first_user_message"]


def test_extract_signals_caps_files():
    # 50 Edit calls → only MAX_FILES_LISTED=40 surfaced
    content = [
        {"type": "tool_use", "name": "Edit",
         "input": {"file_path": f"f{i}.py"}}
        for i in range(50)
    ]
    records = [
        {"type": "assistant", "message": {"content": content}},
    ]
    s = sv.extract_signals(records)
    assert len(s["files_touched"]) == sv.MAX_FILES_LISTED


# ---------------------------- /compact boundary handling ------------------
# Claude Code appends to the same JSONL across /compact calls, so tests cover
# that we anchor "first user message" to the current post-compact segment.

def test_find_session_start_index_no_compact_small_file():
    records = [{"type": "user", "message": {"content": "hi"}}]
    assert sv._find_session_start_index(records) == 0


def test_find_session_start_index_single_compact():
    records = [
        {"type": "user", "message": {"content": "old"}},
        {"type": "user", "isCompactSummary": True,
         "message": {"content": "summary"}},
        {"type": "user", "message": {"content": "new"}},
    ]
    assert sv._find_session_start_index(records) == 2


def test_find_session_start_index_multiple_compacts_uses_last():
    records = [
        {"type": "user", "message": {"content": "a"}},
        {"type": "user", "isCompactSummary": True,
         "message": {"content": "summary1"}},
        {"type": "user", "message": {"content": "b"}},
        {"type": "user", "isCompactSummary": True,
         "message": {"content": "summary2"}},
        {"type": "user", "message": {"content": "c"}},
    ]
    assert sv._find_session_start_index(records) == 4


def test_find_session_start_index_fallback_window(monkeypatch):
    monkeypatch.setattr(sv, "FALLBACK_WINDOW_SIZE", 5)
    records = [{"type": "user", "message": {"content": f"m{i}"}}
               for i in range(20)]
    # No compact marker → trails the last 5
    assert sv._find_session_start_index(records) == 15


def test_extract_signals_skips_messages_before_compact():
    records = [
        {"type": "user", "message": {"content": "days-old first prompt"}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "old reply"},
        ]}},
        # /compact boundary (as emitted by Claude Code)
        {"type": "user", "isCompactSummary": True,
         "isVisibleInTranscriptOnly": True,
         "message": {"content": "<compact summary blob>"}},
        # Post-compact meta/command framing — also skipped
        {"type": "user", "isMeta": True,
         "message": {"content": "<local-command-caveat>...</local-command-caveat>"}},
        {"type": "user", "message": {
            "content": "<command-name>/compact</command-name>"}},
        # The real first user prompt of the current session
        {"type": "user", "message": {"content": "Fresh request after compact"}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "working on it"},
        ]}},
    ]
    s = sv.extract_signals(records)
    assert s["first_user_message"] == "Fresh request after compact"
    assert "days-old" not in s["first_user_message"]
    assert s["last_assistant_text"] == "working on it"


def test_extract_signals_without_compact_keeps_first_message():
    records = [
        {"type": "user", "message": {"content": "only prompt"}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "reply"},
        ]}},
    ]
    s = sv.extract_signals(records)
    assert s["first_user_message"] == "only prompt"


def test_extract_signals_fallback_window_anchors_to_recent(monkeypatch):
    monkeypatch.setattr(sv, "FALLBACK_WINDOW_SIZE", 3)
    # 10 user messages, no compact → window should trim to last 3
    records = [
        {"type": "user", "message": {"content": f"prompt {i}"}}
        for i in range(10)
    ]
    s = sv.extract_signals(records)
    # start index = 10 - 3 = 7 → first message in window is "prompt 7"
    assert s["first_user_message"] == "prompt 7"


def test_is_real_user_input_rejects_command_framing():
    assert not sv._is_real_user_input(
        {"type": "user",
         "message": {"content": "<command-name>/compact</command-name>"}}
    )
    assert not sv._is_real_user_input(
        {"type": "user",
         "message": {"content": "<local-command-stdout>done</local-command-stdout>"}}
    )
    assert not sv._is_real_user_input(
        {"type": "user", "isMeta": True,
         "message": {"content": "real-looking text"}}
    )
    assert not sv._is_real_user_input(
        {"type": "user", "isCompactSummary": True,
         "message": {"content": "summary body"}}
    )
    assert sv._is_real_user_input(
        {"type": "user", "message": {"content": "hello human prompt"}}
    )


# ---------------------------- Summary markdown ----------------------------

def test_build_summary_md_has_yaml_frontmatter():
    signals = {
        "first_user_message": "Do a thing",
        "last_assistant_text": "Done!",
        "tool_counts": {"Read": 3, "Edit": 1},
        "files_touched": ["a.py", "b.py"],
        "git_shas_mentioned": ["abc1234"],
    }
    md = sv.build_summary_md(
        session_id="sess-xyz",
        transcript_path="/tmp/t.jsonl",
        signals=signals,
    )
    assert md.startswith("---\n")
    assert "session_id: sess-xyz" in md
    assert "# Do a thing" in md
    assert "`a.py`" in md
    assert "Read × 3" in md
    assert "`abc1234`" in md


def test_build_summary_md_size_cap():
    giant = "x" * 100_000
    signals = {
        "first_user_message": giant[:sv.MAX_INTENT_CHARS],
        "last_assistant_text": giant[:sv.MAX_CLOSING_CHARS],
        "tool_counts": {f"Tool{i}": 1 for i in range(100)},
        "files_touched": [f"file{i}.py" for i in range(sv.MAX_FILES_LISTED)],
        "git_shas_mentioned": [],
    }
    md = sv.build_summary_md(
        session_id="s",
        transcript_path="/t",
        signals=signals,
    )
    assert len(md.encode("utf-8")) <= sv.MAX_SESSION_KB * 1024 + 200


# ---------------------------- save_memory / index -------------------------

@pytest.fixture
def _sandbox_memory(tmp_path, monkeypatch):
    """Redirect module-level paths to a temp dir."""
    mem = tmp_path / ".claude" / "memory"
    monkeypatch.setattr(sv, "MEMORY_DIR", mem)
    monkeypatch.setattr(sv, "SESSIONS_DIR", mem / "sessions")
    monkeypatch.setattr(sv, "INDEX_PATH", mem / "index.md")
    return mem


def test_save_memory_creates_summary(tmp_path, _sandbox_memory):
    jsonl = tmp_path / "t.jsonl"
    jsonl.write_text(
        '{"type":"user","message":{"content":"Hello"}}\n'
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Hi"}]}}\n',
        encoding="utf-8",
    )
    out = sv.save_memory(
        transcript_path=str(jsonl), session_id="s1", trigger="test"
    )
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "Hello" in content
    assert "session_id: s1" in content
    assert "trigger: test" in content
    assert (_sandbox_memory / "index.md").exists()


def test_update_index_caps_entries(_sandbox_memory, monkeypatch, tmp_path):
    monkeypatch.setattr(sv, "MAX_INDEX_ENTRIES", 3)
    for i in range(5):
        dummy = tmp_path / f"s{i}.md"
        dummy.write_text("x", encoding="utf-8")
        sv.update_index(dummy, f"Topic {i}")
    content = (_sandbox_memory / "index.md").read_text(encoding="utf-8")
    entries = [ln for ln in content.splitlines() if ln.startswith("- ")]
    # 3 cap + retention bullet list (starting with "- Index", "- Older", "- Edit")
    session_entries = [ln for ln in entries if "Topic" in ln]
    assert len(session_entries) == 3
    assert "Topic 4" in session_entries[0]  # newest first
    assert "Topic 2" in session_entries[-1]


def test_update_index_preserves_user_text(_sandbox_memory, tmp_path):
    # Write initial index with user notes
    idx = _sandbox_memory / "index.md"
    _sandbox_memory.mkdir(parents=True, exist_ok=True)
    idx.write_text(
        "# Session Memory Index\n\n"
        "User note: IMPORTANT — do not delete\n\n"
        f"{sv.MARKER_START}\n{sv.MARKER_END}\n\n"
        "## User section below markers\n",
        encoding="utf-8",
    )
    dummy = tmp_path / "s.md"
    dummy.write_text("x", encoding="utf-8")
    sv.update_index(dummy, "New topic")

    content = idx.read_text(encoding="utf-8")
    assert "User note: IMPORTANT" in content
    assert "User section below markers" in content
    assert "New topic" in content


def test_write_seed(_sandbox_memory):
    out = sv.write_seed("Arch decision X", "We chose A because ...")
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "trigger: manual-seed" in content
    assert "# Arch decision X" in content
    assert "We chose A" in content


def test_save_memory_from_hook_empty_payload():
    assert sv.save_memory_from_hook({}) is None


def test_save_memory_from_hook_passes_through(_sandbox_memory, tmp_path):
    jsonl = tmp_path / "t.jsonl"
    jsonl.write_text(
        '{"type":"user","message":{"content":"From hook"}}\n',
        encoding="utf-8",
    )
    out = sv.save_memory_from_hook({
        "transcript_path": str(jsonl),
        "session_id": "sH",
        "matcher": "manual",
    })
    assert out is not None and out.exists()
    txt = out.read_text(encoding="utf-8")
    assert "trigger: precompact:manual" in txt


# ---------------------------- git helpers ---------------------------------

def test_git_short_sha_returns_string():
    out = sv.git_short_sha()
    assert isinstance(out, str) and out


def test_git_is_dirty_returns_bool():
    assert isinstance(sv.git_is_dirty(), bool)


# ---------------------------- load_session_memory -------------------------

def test_load_hook_skips_on_clear(tmp_path, monkeypatch):
    idx = tmp_path / "idx.md"
    idx.write_text("# Index\n- entry\n", encoding="utf-8")
    monkeypatch.setattr(ld, "INDEX_PATH", idx)
    assert ld.load_from_hook({"source": "clear"}) == ""


def test_load_hook_injects_on_compact(tmp_path, monkeypatch):
    idx = tmp_path / "idx.md"
    idx.write_text("# Index\n- entry\n", encoding="utf-8")
    monkeypatch.setattr(ld, "INDEX_PATH", idx)
    out = ld.load_from_hook({"source": "compact"})
    assert "entry" in out


def test_load_hook_injects_on_resume(tmp_path, monkeypatch):
    idx = tmp_path / "idx.md"
    idx.write_text("# Index\n- entry\n", encoding="utf-8")
    monkeypatch.setattr(ld, "INDEX_PATH", idx)
    assert "entry" in ld.load_from_hook({"source": "resume"})


def test_load_hook_injects_on_startup(tmp_path, monkeypatch):
    idx = tmp_path / "idx.md"
    idx.write_text("# Index\n- entry\n", encoding="utf-8")
    monkeypatch.setattr(ld, "INDEX_PATH", idx)
    assert "entry" in ld.load_from_hook({"source": "startup"})


def test_load_hook_truncates_large(tmp_path, monkeypatch):
    idx = tmp_path / "idx.md"
    idx.write_text("A" * 50_000, encoding="utf-8")
    monkeypatch.setattr(ld, "INDEX_PATH", idx)
    monkeypatch.setattr(ld, "MAX_CONTEXT_BYTES", 200)
    out = ld.load_from_hook({"source": "compact"})
    enc = out.encode("utf-8")
    # 200 + small truncation marker
    assert len(enc) <= 500


def test_load_hook_missing_index(tmp_path, monkeypatch):
    monkeypatch.setattr(ld, "INDEX_PATH", tmp_path / "nope.md")
    assert ld.load_from_hook({"source": "compact"}) == ""


def test_load_hook_unknown_source_skips(tmp_path, monkeypatch):
    idx = tmp_path / "idx.md"
    idx.write_text("# Index\n", encoding="utf-8")
    monkeypatch.setattr(ld, "INDEX_PATH", idx)
    assert ld.load_from_hook({"source": "unknown_value"}) == ""


# ---------------------------- End-to-end via hook scripts -----------------

def test_pre_compact_hook_module_imports():
    # Just verify the hook script can be imported without side effects
    path = ROOT / ".claude" / "hooks" / "pre_compact.py"
    assert path.exists()
    # Read first 20 lines to verify it's a python script
    text = path.read_text(encoding="utf-8")
    assert "save_memory_from_hook" in text
    assert "sys.exit(0)" in text  # never-block contract


def test_session_start_hook_module_imports():
    path = ROOT / ".claude" / "hooks" / "session_start.py"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "load_session_memory" in text
    assert "sys.exit(0)" in text


def test_settings_json_valid():
    path = ROOT / ".claude" / "settings.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "hooks" in data
    assert "PreCompact" in data["hooks"]
    assert "SessionStart" in data["hooks"]


def test_claude_md_references_memory_index():
    path = ROOT / "CLAUDE.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "@.claude/memory/index.md" in text
