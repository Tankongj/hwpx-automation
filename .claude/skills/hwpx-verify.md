---
name: hwpx-verify
description: HWPX 파일 구조·네임스페이스·스타일 참조 검증. 변환 결과가 한/글에서 열리기 전에 리스크를 잡아낸다.
---

# HWPX 검증 스킬

HWPX 출력의 구조적 유효성을 검증한다. 다음을 체크:

## 체크 항목

1. **ZIP 무결성**: `zipfile.ZipFile(...).testzip()` 이 None 이어야 함
2. **필수 엔트리**: `mimetype`, `version.xml`, `Contents/header.xml`, `Contents/section0.xml`, `META-INF/container.xml`
3. **네임스페이스 prefix**: `ns0:`, `ns1:` 등 lxml 자동 prefix 가 남아있지 않아야 함
4. **스타일 참조**: section0.xml 의 `paraPrIDRef` / `charPrIDRef` 가 header.xml 에 실제 존재해야 함
5. **빈 블록**: 연속 빈 `<hp:p>` 가 10 개 이상이면 경고

## 사용

```python
from src.hwpx.verify_hwpx import verify, print_report

report = verify("output.hwpx", doc_type="qualitative")
print_report(report)
assert report.ok, "검증 실패 — 로그 확인"
```

## 일반적인 실패 + 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `ns0:p` 발견 | lxml Serialization | `src.hwpx.fix_namespaces.fix_hwpx(path)` |
| `paraPrIDRef="3"` 인데 header 에 없음 | 스타일 map 미동기 | `template_analyzer.analyze()` 재실행 |
| ZIP 손상 | atomic write 실패 | `md_to_hwpx.convert(..., run_fix_namespaces=True)` |
| 표 페이지 넘김 깨짐 | 표 내부 `<hp:p>` 분할 | `fix_namespaces.fix_hwpx(path, fix_tables=True)` |

## 참고
- 스펙: OWPML 1.3 (한컴)
- v1 엔진: `v2_engine/writer.py`, `v2_engine/fix_namespaces.py`
