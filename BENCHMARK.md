# HWPX Automation — 성능 벤치마크

v0.9.0 기준 측정. `scripts/benchmark.py` 로 재현 가능.

## 환경

- Python 3.12.10 (Windows)
- PySide6 6.11.0 / lxml 5.4.0
- CPU: 일반 개발 랩톱 (벤치마크 장비별로 결과 차이 있음)

---

## 📊 주요 수치

### 정성 제안서 변환 (목표 10 만자, 실제 18.5 만자 합성)

| 단계 | 시간 | Peak 메모리 | 비고 |
|---|---:|---:|---|
| 원고 합성 | 0.014 s | 0.86 MB | 18.5 만자 |
| **regex_parser.parse_file** | **0.127 s** | 2.12 MB | 3,415 블록 / 애매 736 |
| template_analyzer.analyze | 0.008 s | 0.26 MB | 번들 템플릿 분석 |
| **md_to_hwpx.convert + fix_namespaces** | **0.549 s** | 9.03 MB | 51 KB HWPX 출력 |
| verify_hwpx.verify | 0.106 s | 4.10 MB | 구조 검증 7/7 통과 |
| **End-to-end (파싱 → 변환 → 검증)** | **~0.8 s** | **9.03 MB** | Gemini 호출 제외 |

**결론**: 18 만자 원고도 1 초 미만에 처리. 메모리는 10MB 수준.

Gemini 호출까지 포함하면 네트워크 레이턴시가 지배적 (~5~10 s) — v0.3~0.8.0 에서 측정한 ₩5~15 비용.

### 정량 제안서 (실샘플 기준)

| 단계 | 시간 | Peak 메모리 | 비고 |
|---|---:|---:|---|
| quant.parse_document | 0.205 s | 6.91 MB | 1,497 셀 / 4 서식 / 22 테이블 |
| **quant.save_document (10 셀 편집)** | **0.702 s** | 23.36 MB | 2.4 MB 출력 |

**결론**: 1,500 셀 규모 정량제안서도 1 초 이하로 열고 저장.

---

## 📈 확장성

원고 크기별 대략 ∝ (선형). 실험적으로:

| 원고 크기 | 블록 수 | 변환 시간 (추정) |
|---|---:|---:|
| 1 만자 | ~340 | ~0.1 s |
| 4 만자 (실샘플) | ~1,169 | ~0.35 s |
| 10 만자 (벤치) | ~3,415 | ~0.8 s |
| 30 만자 | ~10,000 | ~3 s (추정) |

병목은 **`md_to_hwpx.convert` + `fix_namespaces`** — lxml `<hp:p>` 생성·직렬화와 ZIP 재압축. 30 만자 이상은 메모리 ~30MB 로 올라갈 수 있음.

---

## 💰 Gemini 호출 비용 (구조 외)

v0.3.0+ 의 structured output + thinking-off 기준:

| 원고 | 애매 블록 | input tokens | output tokens | 비용 |
|---|---:|---:|---:|---:|
| 4 만자 (실샘플) | 226 | 54,355 | 9,416 | ~₩9.3 |
| 10 만자 (벤치) | 736 | ~175,000 | ~30,000 | ~₩30 (추정) |

비용은 기본적으로 O(애매 블록 수). 임계값(`ambiguous_long_threshold`) 을 올리면 대폭 감소.

---

## 🧪 재현

```powershell
# 기본 (10 만자, 반복 1회)
python scripts/benchmark.py

# 대용량
python scripts/benchmark.py --chars 300000

# 평균화
python scripts/benchmark.py --reps 3

# 정량 경로 포함
python scripts/benchmark.py --include-quant
```

결과 JSON 은 `bench_results/perf_<timestamp>.json` 에 저장됨.

---

## 🔍 프로파일링 팁

메모리 프로파일이 필요하면:

```powershell
pip install memray
memray run -o bench.bin scripts/benchmark.py --chars 300000
memray tree bench.bin
```

시간 프로파일:

```powershell
python -X tracemalloc -m cProfile -o bench.prof scripts/benchmark.py
```
