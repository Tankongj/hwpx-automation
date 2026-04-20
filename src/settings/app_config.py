"""앱 설정 (JSON) 로드/저장.

``%APPDATA%\\HwpxAutomation\\config.json`` 에 사용자 선호(기본 저장 경로, 로그 레벨,
Gemini 사용 여부, 하드 캡 등) 를 기록한다. API Key 같은 비밀은 여기에 **넣지 않는다**
(→ :mod:`src.settings.api_key_manager` 사용).

Public API
----------
- :class:`AppConfig`        : dataclass
- :func:`load()`            : 디스크 → :class:`AppConfig`
- :func:`save(config)`      : :class:`AppConfig` → 디스크
- :func:`config_path()`     : 기본 설정 파일 경로
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional, Union

from ..utils.logger import get_logger


_log = get_logger("settings.config")

PathLike = Union[str, Path]

CONFIG_FILENAME = "config.json"
CONFIG_VERSION = 1


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def _default_output_dir() -> str:
    home = Path.home()
    return str(home / "Documents" / "HwpxAutomation")


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class AppConfig:
    """앱 설정. 모든 필드는 옵셔널하게 기본값이 있다."""

    version: int = CONFIG_VERSION
    use_gemini: bool = True
    gemini_daily_cap: int = 1000        # 기획안 리스크 7.5: 하드 캡
    gemini_model: str = "gemini-2.5-flash"
    ambiguous_long_threshold: int = 50  # 기호 매치됐을 때 본문 길이 N 이상이면 애매로 마킹
    # AI 백엔드 선택 (v0.2.0+): "gemini" / "ollama" / "openai" / "anthropic" / "none"
    resolver_backend: str = "gemini"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    # v0.3.0: 추가 클라우드 백엔드
    openai_model: str = "gpt-4o-mini"
    anthropic_model: str = "claude-haiku-4-5-20251001"
    # v0.4.0: Self-MoA (동일 모델 N회 + aggregator) — 비용 N+1배, 정확도 3~7% ↑
    use_self_moa: bool = False
    self_moa_draws: int = 3
    default_output_dir: str = field(default_factory=_default_output_dir)
    log_level: str = "INFO"             # DEBUG / INFO / WARNING / ERROR
    first_run_completed: bool = False
    default_template_id: str = "default_10"
    # 상업화 단계 훅 (v0.7.0~v0.9.0)
    telemetry_optin: bool = False
    ad_enabled: bool = False            # True 면 AdPlaceholder 활성화
    ad_url: str = ""                    # 단일 광고 URL (v0.7.0, back-compat)
    ad_urls: list[str] = field(default_factory=list)  # v0.9.0: 여러 URL 순환
    ad_rotation_sec: int = 30           # 순환 주기 (0 이면 정적)
    ad_texts: list[str] = field(default_factory=list)  # URL 과 1:1 매칭 광고 텍스트
    require_login: bool = False         # True 면 앱 시작 시 LoginDialog 강제
    auth_backend: str = "local"         # v0.9.0: "local" / "firebase"
    firebase_api_key: str = ""          # v0.9.0: Firebase Auth 용 Web API Key
    auto_update_check: bool = True      # 앱 시작 시 새 버전 체크
    # v0.16.0: Firebase Hosting manifest 기반 자동 업데이트.
    # 프로젝트: hwpx-automation (GCP 조직: farmlearning.co.kr, 무료 Spark 요금제)
    update_manifest_url: str = "https://hwpx-automation.web.app/api/manifest.json"
    update_prefer_patch: bool = True    # True 면 patch update 우선 (작은 용량). False 면 항상 full
    update_repo: str = "Tankongj/hwpx-automation"  # (legacy) GitHub {owner}/{repo} — manifest_url 미설정 시 fallback
    # v0.11.0: 원격 에러 리포팅 (Sentry) — opt-in, DSN 없으면 무동작
    error_reporting_optin: bool = False
    sentry_dsn: str = ""                # 유료 Sentry 계정 DSN. 빈 문자열이면 비활성
    # v0.12.0: 쿠팡 파트너스 광고 (매출 채널 #1) — opt-in, partner_id 없으면 비활성
    # v0.15.x: 확보된 계정 기본 내장 (신규 설치부터 즉시 수익화). 기존 사용자 config 는 보존됨.
    coupang_partner_id: int = 982081    # 쿠팡 파트너스 계정 ID (숫자). 0 이면 비활성
    coupang_tracking_code: str = "AF7480765"  # AF____ 형식 추적 코드
    coupang_template: str = "carousel"  # "carousel" / "image" / "text" 등
    coupang_width: int = 680
    coupang_height: int = 80
    # v0.13.0: Google AdSense (매출 채널 #2) — publisher_id 비어 있으면 비활성
    adsense_publisher_id: str = ""      # ca-pub-XXXXXXXXXXXXXXXX 형식
    adsense_ad_slot: str = ""           # 광고 단위 ID (숫자 문자열)
    adsense_format: str = "auto"        # auto / rectangle / horizontal / vertical
    adsense_width: int = 728
    adsense_height: int = 90
    # 광고 채널 우선순위 — "coupang_first" / "adsense_first" / "coupang_only" / "adsense_only"
    ad_channel_priority: str = "coupang_first"
    # v0.12.0: instructor 라이브러리 기반 unified resolver (opt-in, 기본 False)
    use_instructor_resolver: bool = False
    # v0.12.0: Gemini Batch API (50% 할인) — 긴 작업 / Self-MoA 전용
    use_gemini_batch: bool = False
    gemini_batch_poll_sec: int = 60     # polling 간격 (초)
    # v0.15.0: python-hwpx 기반 writer 경로 (기본 False — lxml 경로 유지).
    # reference/cover_range 등 고급 기능 사용하면 자동으로 legacy 경로로.
    use_python_hwpx_writer: bool = False

    # 여유 스페이스
    extras: dict = field(default_factory=dict)

    # ---- (역)직렬화 ----

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        known_fields = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        extras: dict[str, Any] = {}
        for k, v in (data or {}).items():
            if k in known_fields:
                kwargs[k] = v
            else:
                extras[k] = v
        if extras:
            # 알려지지 않은 필드는 extras 로 보존 (버전 호환)
            kwargs.setdefault("extras", {}).update(extras)
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _base_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "HwpxAutomation"
    return Path.home() / ".hwpx-automation"


def config_path(base: Optional[PathLike] = None) -> Path:
    """기본 ``config.json`` 경로."""
    root = Path(base) if base else _base_dir()
    return root / CONFIG_FILENAME


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load(path: Optional[PathLike] = None) -> AppConfig:
    """설정 파일 로드. 없거나 손상됐으면 기본값으로 반환."""
    target = Path(path) if path else config_path()
    if not target.exists():
        return AppConfig()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _log.error("config.json 로드 실패 (%s). 기본값 사용.", exc)
        return AppConfig()
    if not isinstance(raw, dict):
        _log.error("config.json 형식 오류: dict 아님. 기본값 사용.")
        return AppConfig()
    return AppConfig.from_dict(raw)


def save(config: AppConfig, path: Optional[PathLike] = None) -> Path:
    """설정을 파일에 기록. 디렉토리는 자동 생성."""
    target = Path(path) if path else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = config.to_dict()
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(target)
    return target


__all__ = [
    "AppConfig",
    "CONFIG_FILENAME",
    "CONFIG_VERSION",
    "config_path",
    "load",
    "save",
]
