"""조달청 나라장터 (G2B) Open API 어댑터 — v0.11.0 스캐폴드.

**목적**: 사용자가 입력한 "입찰공고번호 / 공고명" 으로 RFP 를 자동 다운로드 → 체크리스트
분석으로 바로 연결. Pro 전용 훅.

엔드포인트 (공공데이터포털 무료 API — 2025 기준):
- 목록: https://apis.data.go.kr/1230000/BidPublicInfoService05/getBidPblancListInfoServc
- 상세: https://apis.data.go.kr/1230000/BidPublicInfoService05/getBidPblancListInfoServcDetail

요청 파라미터: `ServiceKey` (필수) + `inqryDiv`, `numOfRows`, `pageNo`, `inqryBgnDt` 등.
응답 형식: JSON (type=json) 또는 XML (기본). JSON 사용.

**v0.11.0 스캐폴드 범위**:
- HTTP 호출 구조 + 쿼리 빌더 + 응답 파서만
- 실제 호출 테스트는 사용자가 ServiceKey 발급 후 진행 (공공데이터포털 무료)
- Pro 티어 게이트 (무료 사용자는 키 있어도 호출 불가 — API 부담 분산)

**v0.12+ 계획**:
- "이 공고로 새 작업 시작" 버튼
- 공고 첨부파일 자동 다운로드 (HWP/HWPX/PDF)
- 캐싱 (APPDATA/g2b_cache/*.json, 24h TTL)
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from urllib.error import HTTPError, URLError

from ..utils.logger import get_logger


_log = get_logger("checklist.g2b")


# 공공데이터포털 — 나라장터 입찰공고정보서비스 v5 (2025~)
_G2B_BASE = "https://apis.data.go.kr/1230000/BidPublicInfoService05"
_G2B_LIST = f"{_G2B_BASE}/getBidPblancListInfoServc"
_G2B_DETAIL = f"{_G2B_BASE}/getBidPblancListInfoServcDetail"
_G2B_TIMEOUT = 15.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BidAnnouncement:
    """나라장터 입찰공고 1 건."""

    bid_no: str = ""                # 입찰공고번호 (예: 20260416001-00)
    title: str = ""                 # 공고명
    agency: str = ""                # 수요기관 / 공고기관
    open_date: str = ""             # 게시 일시 (ISO 8601)
    close_date: str = ""            # 마감 일시
    amount_krw: int = 0             # 기초금액 / 예정가격 (원)
    detail_url: str = ""            # 상세보기 URL
    raw: dict = field(default_factory=dict)  # 원본 JSON (디버깅용)


@dataclass
class G2BSearchResult:
    items: list[BidAnnouncement] = field(default_factory=list)
    total_count: int = 0
    page: int = 1
    per_page: int = 10
    error: str = ""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class G2BClient:
    """나라장터 Open API 클라이언트.

    사용 예::

        client = G2BClient(service_key="YOUR_KEY")
        result = client.search_bids(keyword="귀농귀촌", days=30)
        for bid in result.items:
            print(bid.bid_no, bid.title)

    **pro 티어 필수** — 무료 사용자는 `TierDeniedError`.
    """

    def __init__(
        self,
        service_key: str,
        *,
        _opener=None,
        _skip_tier_check: bool = False,
    ) -> None:
        if not service_key:
            raise ValueError("ServiceKey 가 필요합니다 (공공데이터포털에서 무료 발급).")
        if not _skip_tier_check:
            from ..commerce import tier_gate  # lazy
            tier_gate.require("pro", feature="나라장터 API 조회")
        self.service_key = service_key
        self._opener = _opener or urllib.request.urlopen

    # ---- public API ----

    def search_bids(
        self,
        *,
        keyword: str = "",
        days: int = 30,
        page: int = 1,
        per_page: int = 10,
    ) -> G2BSearchResult:
        """입찰공고 목록 검색.

        Parameters
        ----------
        keyword : 공고명 포함 키워드 (예: "귀농귀촌 아카데미")
        days : 조회 기간 (오늘부터 N 일 이내 게시물)
        page, per_page : 페이징
        """
        today = datetime.now()
        start = (today - timedelta(days=days)).strftime("%Y%m%d%H%M")
        end = today.strftime("%Y%m%d%H%M")

        params = {
            "serviceKey": self.service_key,
            "numOfRows": str(per_page),
            "pageNo": str(page),
            "inqryDiv": "1",                   # 1 = 공고게시일시 기준
            "inqryBgnDt": start,
            "inqryEndDt": end,
            "bidNtceNm": keyword,
            "type": "json",
        }
        return self._get(_G2B_LIST, params, page=page, per_page=per_page)

    def get_bid_detail(self, bid_no: str) -> Optional[BidAnnouncement]:
        """단일 공고 상세. 없으면 None."""
        params = {
            "serviceKey": self.service_key,
            "bidNtceNo": bid_no,
            "type": "json",
        }
        result = self._get(_G2B_DETAIL, params, page=1, per_page=1)
        return result.items[0] if result.items else None

    # ---- internals ----

    def _get(
        self,
        url: str,
        params: dict,
        *,
        page: int,
        per_page: int,
    ) -> G2BSearchResult:
        qs = urllib.parse.urlencode(params, safe=":%+")
        full_url = f"{url}?{qs}"
        try:
            with self._opener(full_url, timeout=_G2B_TIMEOUT) as resp:
                raw = resp.read()
        except HTTPError as exc:
            return G2BSearchResult(error=f"G2B HTTP {exc.code}: {exc.reason}")
        except URLError as exc:
            return G2BSearchResult(error=f"G2B 네트워크 실패: {exc.reason}")

        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return G2BSearchResult(error=f"G2B 응답 파싱 실패: {exc}")

        return _parse_g2b_response(data, page=page, per_page=per_page)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _parse_g2b_response(data: dict, *, page: int, per_page: int) -> G2BSearchResult:
    """공공데이터포털 JSON 응답 → :class:`G2BSearchResult`.

    포맷 참고::

        {
          "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
            "body": {
              "items": [{"bidNtceNo": ..., "bidNtceNm": ..., ...}, ...],
              "totalCount": 123,
              "numOfRows": 10,
              "pageNo": 1
            }
          }
        }
    """
    result = G2BSearchResult(page=page, per_page=per_page)
    try:
        resp = data.get("response", {})
        header = resp.get("header", {})
        code = header.get("resultCode", "")
        if code and code != "00":
            result.error = f"G2B API 에러: {header.get('resultMsg', code)}"
            return result

        body = resp.get("body", {})
        result.total_count = int(body.get("totalCount", 0) or 0)
        items = body.get("items", [])
        # items 가 dict 일 때 (단건) 도 처리
        if isinstance(items, dict):
            items = items.get("item", [])
            if isinstance(items, dict):
                items = [items]
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            result.items.append(_make_announcement(raw_item))
    except (KeyError, TypeError, ValueError) as exc:
        result.error = f"응답 구조 이상: {exc}"
    return result


def _make_announcement(item: dict) -> BidAnnouncement:
    """단건 dict → :class:`BidAnnouncement`. 한글 필드명 매핑."""
    return BidAnnouncement(
        bid_no=str(item.get("bidNtceNo", "")),
        title=str(item.get("bidNtceNm", "")),
        agency=str(item.get("ntceInsttNm", "") or item.get("dminsttNm", "")),
        open_date=str(item.get("bidNtceDt", "") or item.get("rgstDt", "")),
        close_date=str(item.get("bidClseDt", "")),
        amount_krw=_safe_int(item.get("asignBdgtAmt", 0)),
        detail_url=str(item.get("bidNtceDtlUrl", "")),
        raw=item,
    )


def _safe_int(v) -> int:
    try:
        return int(float(str(v).replace(",", "")))
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# v0.14.0: 공고 첨부 파일 다운로드
# ---------------------------------------------------------------------------


@dataclass
class AttachmentFile:
    """공고 첨부 파일 1 개."""

    filename: str
    url: str = ""
    size_bytes: int = 0
    local_path: Optional[str] = None  # 다운로드 후 로컬 경로


@dataclass
class DownloadResult:
    """공고 첨부 일괄 다운로드 결과."""

    bid_no: str = ""
    output_dir: str = ""
    files: list[AttachmentFile] = field(default_factory=list)
    total_bytes: int = 0
    skipped: list[str] = field(default_factory=list)  # 다운 실패 파일명
    error: str = ""


def download_bid_attachments(
    client: "G2BClient",
    bid_no: str,
    output_dir: str,
    *,
    max_bytes: int = 50 * 1024 * 1024,   # 기본 50 MB 상한
    overwrite: bool = False,
) -> DownloadResult:
    """입찰공고 상세에서 첨부 URL 을 추출해 로컬로 다운로드.

    Parameters
    ----------
    client : G2BClient — 인증된 클라이언트 (pro 필수)
    bid_no : 입찰공고번호
    output_dir : 저장 폴더 (없으면 생성)
    max_bytes : 파일 1 개당 최대 크기 (초과 시 skip)
    overwrite : True 면 같은 이름 파일 덮어씀

    Returns
    -------
    DownloadResult

    **주의**: 실제 G2B 상세 응답 구조는 공고별로 다양. 현재 구현은 3 가지 흔한 포맷을
    시도하고 실패 시 skipped 에 기록. 운영 환경에서 실패 사례 모이면 v0.15 에 정형화.
    """
    import os
    import urllib.parse
    import urllib.request
    from pathlib import Path as _Path

    result = DownloadResult(bid_no=bid_no, output_dir=output_dir)
    out = _Path(output_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result.error = f"폴더 생성 실패: {exc}"
        return result

    # 상세 조회
    bid = client.get_bid_detail(bid_no)
    if bid is None:
        result.error = f"공고 {bid_no} 상세 없음"
        return result

    # raw dict 에서 첨부 URL 추출 — 필드명은 공고별로 다름
    urls = _extract_attachment_urls(bid.raw)
    if not urls:
        result.error = "첨부 URL 없음 (또는 스키마 인식 실패)"
        return result

    for i, url in enumerate(urls, start=1):
        filename = _guess_filename(url, default=f"{bid_no}_{i}.bin")
        target = out / filename
        if target.exists() and not overwrite:
            _log.info("이미 존재 — 스킵: %s", filename)
            result.files.append(AttachmentFile(
                filename=filename, url=url,
                size_bytes=target.stat().st_size,
                local_path=str(target),
            ))
            continue

        try:
            with client._opener(url, timeout=_G2B_TIMEOUT) as resp:
                size = 0
                chunk = 65536
                with open(target, "wb") as f:
                    while True:
                        data = resp.read(chunk)
                        if not data:
                            break
                        size += len(data)
                        if size > max_bytes:
                            f.close()
                            target.unlink(missing_ok=True)
                            result.skipped.append(f"{filename} (>{max_bytes} bytes)")
                            break
                        f.write(data)
                    else:
                        pass
            if target.exists():
                result.total_bytes += target.stat().st_size
                result.files.append(AttachmentFile(
                    filename=filename, url=url,
                    size_bytes=target.stat().st_size,
                    local_path=str(target),
                ))
        except Exception as exc:  # noqa: BLE001
            _log.warning("%s 다운로드 실패: %s", filename, exc)
            result.skipped.append(f"{filename} ({type(exc).__name__})")

    return result


def _extract_attachment_urls(item: dict) -> list[str]:
    """raw 공고 dict 에서 첨부 URL 리스트 추출.

    G2B 응답은 필드명이 다양 — ``ntceSpecFileDwldUrl{N}``, ``dtlsBidNtceDocUrl1`` 등.
    """
    urls: list[str] = []
    for k, v in item.items():
        if not isinstance(v, str) or not v:
            continue
        if not v.startswith(("http://", "https://")):
            continue
        lk = k.lower()
        # 알려진 첨부 필드명 패턴
        if any(tag in lk for tag in (
            "filedwld", "fileurl", "file_url", "doc_url",
            "attachfile", "ntcespec", "bidntcedoc",
        )):
            urls.append(v)
    # 중복 제거
    return list(dict.fromkeys(urls))


def _guess_filename(url: str, *, default: str) -> str:
    """URL 에서 파일명 추출. 실패 시 default."""
    from urllib.parse import urlparse, unquote

    try:
        path = urlparse(url).path
        name = unquote(path.rsplit("/", 1)[-1])
        if name:
            # Windows 금지 문자 정리
            safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
            return safe or default
    except Exception:  # noqa: BLE001
        pass
    return default


__all__ = [
    "BidAnnouncement",
    "G2BSearchResult",
    "G2BClient",
    "AttachmentFile",
    "DownloadResult",
    "download_bid_attachments",
]
