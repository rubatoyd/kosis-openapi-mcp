from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def fx():
    """실응답 픽스처 로더.

    `tests/fixtures/*` 는 2026-09-08 에 라이브로 받은 **진짜 응답**이다(인증키는 응답에
    실리지 않으며, 저장 전에 `scrub()` 을 한 번 더 태웠다). 손으로 지어낸 표본이
    아니므로 여기 있는 키 이름·값 모양은 전부 실제로 관측된 것이다.
    """
    def load(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")
    return load


@pytest.fixture(autouse=True)
def _no_real_key(monkeypatch):
    """테스트가 실수로 라이브 호출을 하지 않도록 인증키를 지운다."""
    for k in ("KOSIS_API_KEY", "KOSIS_KEY"):
        monkeypatch.delenv(k, raising=False)
