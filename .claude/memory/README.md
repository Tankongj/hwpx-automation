# .claude/memory/ — 계층형 세션 메모리 (A+B 패턴)

프로젝트 장기기억 시스템. **"Progressive Disclosure"** 원칙에 따라 인덱스만 자동 로드되고, 상세 내용은 on-demand 로 접근합니다.

## 구조

```
.claude/memory/
├── index.md              ← 자동 로드 (CLAUDE.md @import 경유)
├── sessions/             ← on-demand; Claude 가 필요 시 Read
│   └── YYYY-MM-DD_HHMMSS_topic.md
└── README.md             ← 이 파일
```

## 파이프라인

### A) 쓰기: PreCompact 훅 → sessions/*.md + index.md 업데이트

`/compact` 직전에 자동 실행됩니다:

```
/compact
  ↓
PreCompact 훅 호출 (.claude/hooks/pre_compact.py)
  ↓
scripts.save_session_memory.save_memory_from_hook(payload)
  ↓
세션 JSONL 파싱 → 신호 추출:
  - 첫 user message (Intent)
  - Tool 사용 횟수
  - 수정 파일 경로
  - git SHA 언급
  - 마지막 assistant message
  ↓
sessions/YYYY-MM-DD_HHMMSS_slug.md 생성
  ↓
index.md 의 마커 구간에 최신 엔트리 prepend (최근 10개만 유지)
```

### B) 읽기: SessionStart 훅 → additionalContext 주입

새 세션 시작 시 자동 실행됩니다:

```
(새 세션 시작 / compact 후 재시작)
  ↓
SessionStart 훅 호출 (.claude/hooks/session_start.py)
  ↓
scripts.load_session_memory.main()
  ↓
source 체크:
  - "compact" / "resume" / "startup" → 주입
  - "clear" → 건너뜀 (사용자가 명시적으로 clear 원함)
  ↓
index.md 내용 (~6KB cap) 을 additionalContext 로 반환
  ↓
Claude 가 현재 세션 시작 컨텍스트로 받음
```

## 수동 사용법

### 시드 엔트리 추가 (과거 작업 기록 등)

```bash
python scripts/save_session_memory.py --seed \
    --topic "v0.15.0 릴리스 완료" \
    --body "요약 내용..."
```

또는 본문을 파일에서 읽기:

```bash
python scripts/save_session_memory.py --seed \
    --topic "아키텍처 결정" \
    --body "@docs/decisions/adr-001.md"
```

### 특정 JSONL 수동 저장

```bash
python scripts/save_session_memory.py \
    --transcript "C:/Users/thekp/.claude/projects/.../abc.jsonl" \
    --session-id abc
```

## 설계 근거 (검증된 2026-04 리서치)

| 설계 결정 | 근거 |
|---|---|
| `.claude/memory/` 위치 | claude-mem (63K⭐), Anthropic auto-memory 규약 |
| 요약만 저장 (raw X) | Mem0 논문 (arXiv:2504.19413) — ~90% 토큰 절감 |
| git SHA 앵커 | DEV.to gonewx — "메모리는 코드 상태에 앵커링돼야 유용" |
| Progressive disclosure (인덱스만) | claude-mem 패턴 — dump-and-reload 대비 10× 토큰 절감 |
| API 키 regex 리댁션 | MINJA (arXiv:2503.03704) — 메모리는 보안 경계면 |
| 8KB/파일, 6KB/주입 cap | ninetwothree — CLAUDE.md 비대화 = 준수율 저하 |
| 훅 never-block 계약 | Anthropic 공식 docs — 훅 실패가 /compact 블록하면 안 됨 |

## 보안 & 프라이버시

- **API 키 자동 리댁션**: `sk-*`, `AIza*`, `ghp_*`, `Bearer *` 등
- **8KB 파일 상한**: 토큰 폭주 방지
- **6KB 주입 상한**: 세션 시작 비용 제어
- **훅 never-fail**: 실패해도 `/compact` 진행 보장
- **커밋 전 리뷰 권장**: `.claude/memory/sessions/` 는 민감 정보 포함 가능 → `.gitignore` 고려

## 참고 자료

- [Anthropic 공식 hooks docs](https://code.claude.com/docs/en/hooks)
- [claude-mem (thedotmack)](https://github.com/thedotmack/claude-mem) — 63K⭐ 참고 구현
- [claude-memory-compiler (coleam00)](https://github.com/coleam00/claude-memory-compiler) — 820⭐, 벡터 DB 없는 마크다운 인덱스 방식
- [A-MEM (NeurIPS 2025)](https://arxiv.org/abs/2502.12110) — Zettelkasten 메모리
- [Mem0 (2025)](https://arxiv.org/abs/2504.19413) — p95 지연 91%↓, 토큰 90%↓
- [LoCoMo 벤치마크](https://snap-research.github.io/locomo/) — 장기대화 평가
