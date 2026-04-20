"""상업화 모듈 (v0.7.0).

회원제 / 광고 / 텔레메트리 / 자동 업데이트 훅. MVP 기본 설치에서는 **기본 OFF** 로 두고,
Settings 탭에서 사용자가 명시적으로 켜야 동작한다.

현재 버전은 로컬-전용 placeholder:
- :mod:`user_db` — 로컬 SQLite/JSON 에 bcrypt 해시된 비밀번호
- :mod:`updater` — GitHub Releases API 로 새 버전 체크
- 텔레메트리는 :mod:`src.utils.telemetry` 에 위치
"""

from . import updater, user_db

__all__ = ["user_db", "updater"]
