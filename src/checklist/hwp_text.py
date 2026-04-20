"""HWP (구형 바이너리) 에서 텍스트 추출 — v0.9.0 pure Python + v0.10.0 BodyText experimental.

HWP 는 OLE 컨테이너 + 압축 레코드 포맷. 전체 텍스트(BodyText/SectionN)는 **raw deflate**
(zlib 헤더 없는 DEFLATE) 로 압축된 바이너리 레코드 스트림이다.

레코드 포맷 (HWP 5.0 기준):
- 각 레코드: HWPTAG(4바이트) + size(4바이트) + data(size바이트)
- HWPTAG 의 하위 10bit 가 ``tag_id``, 0x43 (``HWPTAG_PARA_TEXT``) 인 레코드 data 는 UTF-16LE
  텍스트 + 제어 문자 (char code < 32 는 inline object / field 마커) 구조.

이 모듈의 역할:
- **v0.9.0**: HWP 에서 **PrvText** (미리보기 ~2,000 자) 를 UTF-16 으로 디코드 — 안전하고 빠름
- **v0.10.0**: ``prefer_full=True`` 옵션으로 **BodyText/Section0** 까지 best-effort 파싱 시도.
  성공하면 전체 본문, 실패하면 자동으로 PrvText 로 폴백.

제약:
- BodyText 파싱은 레코드 포맷에 깊이 의존 → 손상/특이 HWP 에서 깨질 수 있음 → 항상 폴백 경로 존재
- 한글 표/그림 내 텍스트는 별도 레코드 ID 를 가져 일부는 유실
- 길고 복잡한 HWP 는 LibreOffice 변환 경로 권장 (`hwp_converter.py`)

의존성
------
- olefile (BSD, pure Python)
- zlib (stdlib)
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Union

from ..utils.logger import get_logger


_log = get_logger("checklist.hwp_text")


PathLike = Union[str, Path]

# HWP 레코드 태그 — 공식 스펙 기준 (HWPTAG_BEGIN = 0x10)
_HWPTAG_PARA_HEADER = 0x42     # 문단 헤더
_HWPTAG_PARA_TEXT = 0x43       # 문단 텍스트 (UTF-16LE)
_HWPTAG_PARA_CHAR_SHAPE = 0x44 # 문자 속성

# 파일 헤더 플래그 (BIT 0 = compressed)
_HEADER_COMPRESS_BIT = 0x01


@dataclass
class HwpTextResult:
    """HWP 텍스트 추출 결과."""

    text: str = ""
    source: str = "unknown"      # "prv_text" / "body_text" / "unknown"
    is_full: bool = False        # True 면 전체 본문, False 면 미리보기만
    error: str = ""


def extract_hwp_text(
    hwp_path: PathLike,
    *,
    max_chars: int = 50_000,
    prefer_full: bool = False,
) -> HwpTextResult:
    """HWP → 텍스트.

    Parameters
    ----------
    hwp_path : HWP 파일 경로
    max_chars : 반환 텍스트 상한 (넘으면 말미에 "[… 이하 N자 생략]" 삽입)
    prefer_full : **v0.10.0**. True 면 BodyText/Section0 파싱 시도 → 실패 시 PrvText 로 폴백.
        False (기본) 면 PrvText 만 사용 (안전, 빠름, 2KB 정도).

    실패해도 예외 대신 :class:`HwpTextResult` 로 반환 (UI 에서 처리 편의).
    """
    path = Path(hwp_path)
    if not path.exists():
        return HwpTextResult(error=f"파일 없음: {path}")
    if path.suffix.lower() != ".hwp":
        return HwpTextResult(error=f"HWP 가 아닙니다: {path.suffix}")

    try:
        import olefile
    except ImportError:
        return HwpTextResult(
            error="olefile 패키지가 설치돼 있지 않습니다. `pip install olefile` 후 재시도하세요."
        )

    try:
        ole = olefile.OleFileIO(str(path))
    except Exception as exc:  # noqa: BLE001
        return HwpTextResult(error=f"OLE 파일 열기 실패: {type(exc).__name__}: {exc}")

    try:
        # v0.10.0: prefer_full 이면 먼저 BodyText 시도, 실패 시 PrvText 로 폴백
        if prefer_full:
            body_result = _try_extract_body_text(ole, max_chars=max_chars)
            if body_result.text:
                _log.info(
                    "HWP BodyText 추출: %s → %d 자", path.name, len(body_result.text),
                )
                return body_result
            _log.info("HWP BodyText 실패 (%s) → PrvText 폴백", body_result.error)

        # PrvText 경로 (v0.9.0 원본)
        return _extract_prv_text(ole, max_chars=max_chars, path=path)
    finally:
        ole.close()


def _extract_prv_text(ole, *, max_chars: int, path: Path) -> HwpTextResult:
    if not ole.exists("PrvText"):
        return HwpTextResult(
            error="PrvText 스트림이 없습니다 (오래된 HWP 또는 손상?)",
        )
    try:
        raw = ole.openstream("PrvText").read()
    except Exception as exc:  # noqa: BLE001
        return HwpTextResult(error=f"PrvText 읽기 실패: {exc}")

    # UTF-16LE 디코드, BOM 제거
    try:
        text = raw.decode("utf-16-le", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return HwpTextResult(error=f"UTF-16 디코드 실패: {exc}")

    # Null byte 제거 (HWP가 간혹 \x00 패딩)
    text = text.replace("\x00", "")
    # BOM 제거
    text = text.lstrip("\ufeff")
    # 상한
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n[… 이하 {len(text) - max_chars:,} 자 생략]"

    if not text.strip():
        return HwpTextResult(error="PrvText 가 비어 있음")

    _log.info("HWP PrvText 추출: %s → %d 자", path.name, len(text))
    return HwpTextResult(text=text, source="prv_text", is_full=False)


def _try_extract_body_text(ole, *, max_chars: int) -> HwpTextResult:
    """BodyText/Section0~N 전체를 best-effort 파싱.

    실패 케이스 (레코드 깨짐, 제어문자 과다 등) 는 :class:`HwpTextResult` 의 ``error``
    필드로 반환. 호출자가 PrvText 로 폴백한다.
    """
    # 1) FileHeader 에서 compressed 비트 확인
    compressed = _is_compressed(ole)

    # 2) BodyText/Section* 모두 수집 (Section0, Section1, ...)
    section_paths: list[list[str]] = []
    try:
        for entry in ole.listdir():
            if (
                len(entry) == 2
                and entry[0].lower() == "bodytext"
                and entry[1].lower().startswith("section")
            ):
                section_paths.append(entry)
    except Exception as exc:  # noqa: BLE001
        return HwpTextResult(error=f"listdir 실패: {exc}")

    if not section_paths:
        return HwpTextResult(error="BodyText/Section* 스트림 없음")

    # Section 번호순 정렬 (section0, section1, ...)
    section_paths.sort(key=lambda p: _section_num(p[1]))

    chunks: list[str] = []
    total = 0
    for sp in section_paths:
        try:
            raw = ole.openstream("/".join(sp)).read()
        except Exception as exc:  # noqa: BLE001
            _log.debug("section 읽기 실패 %s: %s", sp, exc)
            continue
        data = _maybe_decompress(raw, compressed=compressed)
        if data is None:
            continue
        text = _records_to_text(data)
        if text:
            chunks.append(text)
            total += len(text)
            if total > max_chars * 2:  # 너무 커지면 끊음
                break

    if not chunks:
        return HwpTextResult(error="BodyText 디코드 실패 (레코드 파싱 실패)")

    text = "\n".join(chunks)
    # 제어문자 정리 — HWP 는 0x00~0x1F 를 inline object / field 마커로 씀
    text = _sanitize_hwp_control(text)

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n[… 이하 {len(text) - max_chars:,} 자 생략]"
    if not text.strip():
        return HwpTextResult(error="BodyText 디코드 결과가 비어 있음")

    return HwpTextResult(text=text, source="body_text", is_full=True)


def _is_compressed(ole) -> bool:
    """FileHeader 스트림 36 번째 바이트의 BIT0 가 compressed 플래그."""
    if not ole.exists("FileHeader"):
        return True  # 기본값: 압축됐다고 가정 (대부분 HWP 파일)
    try:
        header = ole.openstream("FileHeader").read()
    except Exception:  # noqa: BLE001
        return True
    # HWP 5.0 스펙: signature(32) + version(4) + properties(4)
    # properties[0] 의 비트 0 이 압축 여부
    if len(header) < 40:
        return True
    return bool(header[36] & _HEADER_COMPRESS_BIT)


def _maybe_decompress(raw: bytes, *, compressed: bool) -> Optional[bytes]:
    """compressed 이면 raw DEFLATE (wbits=-15) 로 풀고, 아니면 그대로 반환.

    zlib 해제 실패하면 None.
    """
    if not compressed:
        return raw
    try:
        return zlib.decompress(raw, -15)
    except zlib.error:
        # 혹시 헤더 있는 경우도 시도
        try:
            return zlib.decompress(raw)
        except zlib.error as exc:
            _log.debug("zlib 실패: %s", exc)
            return None


def _section_num(name: str) -> int:
    """'Section0' → 0, 'Section12' → 12, 그 외 → 큰 값 (뒤로)."""
    digits = "".join(ch for ch in name if ch.isdigit())
    return int(digits) if digits else 999


def _records_to_text(data: bytes) -> str:
    """HWP 레코드 스트림을 순회하며 PARA_TEXT 만 추출.

    레코드 헤더 4바이트: bit 0~9 = tag_id, bit 10~19 = level, bit 20~31 = size
    size == 0xFFF (12비트 최대) 이면 다음 4바이트가 real size.

    **v0.10.1**: ``_looks_like_text`` 를 통과하지 못하는 레코드는 드롭
    (HWP 레코드 tag 명 "lbt ", "ttof" 등이 UTF-16LE 로 디코드돼 생기는 희귀 CJK 노이즈 방지).
    """
    out: list[str] = []
    pos = 0
    n = len(data)
    while pos + 4 <= n:
        (hdr,) = struct.unpack_from("<I", data, pos)
        pos += 4
        tag_id = hdr & 0x3FF
        size = (hdr >> 20) & 0xFFF
        if size == 0xFFF:
            if pos + 4 > n:
                break
            (size,) = struct.unpack_from("<I", data, pos)
            pos += 4
        if pos + size > n:
            break
        record = data[pos : pos + size]
        pos += size

        if tag_id == _HWPTAG_PARA_TEXT and size > 0:
            # UTF-16LE. 0x00~0x1F 는 inline object / field marker — 공백으로 치환.
            try:
                text = record.decode("utf-16-le", errors="replace")
            except Exception:  # noqa: BLE001
                continue
            if not _looks_like_text(text):
                # 순수 노이즈 레코드 (ASCII tag 명이 UTF-16LE 로 잘못 디코드된 경우)
                continue
            out.append(text)

    return "".join(out)


def _looks_like_text(text: str, *, min_good_ratio: float = 0.2) -> bool:
    """디코드된 UTF-16LE 문자열이 실제 한국어/영어 텍스트처럼 보이는지.

    판정 기준 — 한글 음절 (U+AC00~U+D7A3) 또는 인쇄 가능 ASCII (0x20~0x7E) 문자가
    전체 길이의 ``min_good_ratio`` 이상인가.

    예:
    - "우편번호 : 30148"                 → 한글+ASCII 100% → True
    - "捤獥\\x00\\x00\\x00\\x00汤捯\\x00\\x00"    → 희귀 CJK + NUL 만 → False (노이즈)
    - "氠瑢\\x00\\x00"                   → CJK 2자만 → False (노이즈)
    """
    if not text:
        return False
    hangul = sum(1 for c in text if 0xAC00 <= ord(c) <= 0xD7A3)
    ascii_p = sum(1 for c in text if 0x20 <= ord(c) <= 0x7E)
    good = hangul + ascii_p
    return good / len(text) >= min_good_ratio


def _sanitize_hwp_control(text: str) -> str:
    """HWP inline control char (< 0x20) 를 공백/개행으로 정리 + v0.10.1 노이즈 토큰 제거.

    v0.10.1: mixed chunk (``捤獥∅∅∅∅우편번호``) 에서 leading/trailing 의 rare-CJK 노이즈
    토큰을 제거. 한글도 ASCII 도 없고 희귀 CJK 만 있는 짧은 토큰은 드롭.
    """
    # 1) 제어문자 정리 — 0x00~0x1F 는 공백 / 개행은 유지
    buf: list[str] = []
    for ch in text:
        code = ord(ch)
        if code == 0x0A or code == 0x0D:
            buf.append("\n")
        elif code < 0x20:
            # 대부분 마커 — 공백으로 치환
            buf.append(" ")
        elif 0xD800 <= code <= 0xDFFF:
            # 살아 있는 surrogate (유효 UTF-16 대응 못 찾은 것) 는 공백
            buf.append(" ")
        else:
            buf.append(ch)
    result = "".join(buf)

    # 2) v0.10.1: 토큰 레벨 노이즈 필터 — 한글/ASCII 전무한 rare-CJK 짧은 토큰 드롭
    result = _drop_noise_tokens(result)

    # 3) 연속 공백 정리
    while "  " in result:
        result = result.replace("  ", " ")
    return result


def _drop_noise_tokens(text: str, *, max_drop_len: int = 6) -> str:
    """공백 기준 토큰 중 "한글 0 + ASCII 0 + rare-CJK 만" 있는 짧은 토큰 제거.

    HWP 레코드 tag 명 (``"lbt "``, ``"ttof"`` 등) 이 UTF-16LE 로 디코드되면 U+6???
    대역의 희귀 한자가 2~4자 연속으로 나타난다. 실제 텍스트엔 이런 패턴이 드물고,
    길이 ``max_drop_len`` 이하면 거의 확실히 노이즈.

    정상 한문 인용은 보통 더 길거나 (예: 제목 "知之爲知之 不知爲不知") 전후로 한글 문맥이 있어
    공백 분리 시 별개 토큰이 되지 않는다 → 보수적으로 짧은 고립 토큰만 드롭.
    """
    lines_out: list[str] = []
    for line in text.split("\n"):
        good: list[str] = []
        for tok in line.split(" "):
            if not tok:
                good.append(tok)  # 공백 분리 때 생긴 빈 문자열 — join 으로 복구
                continue
            has_hangul = any(0xAC00 <= ord(c) <= 0xD7A3 for c in tok)
            has_ascii = any(0x20 <= ord(c) <= 0x7E for c in tok)
            if has_hangul or has_ascii:
                good.append(tok)
            elif len(tok) > max_drop_len:
                # 긴 CJK-only 토큰은 정상 한문일 수 있으므로 유지
                good.append(tok)
            # else: 짧은 rare-CJK-only → 드롭
        lines_out.append(" ".join(good))
    return "\n".join(lines_out)


__all__ = [
    "HwpTextResult",
    "extract_hwp_text",
    # 하위 헬퍼 — 테스트에서 직접 검증하기 쉽게 공개
    "_records_to_text",
    "_sanitize_hwp_control",
    "_drop_noise_tokens",
    "_looks_like_text",
]
